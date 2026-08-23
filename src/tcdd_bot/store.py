"""Redis store for users, alarms, and rate limits.

Uses the native Redis protocol via redis-py async. In production this points
at the Redis container next to the bot (`redis://redis:6379/0`); any standard
`redis://` URL (including a hosted one) works unchanged.

Schema (see plan):
  user:{chat_id}                hash  username, paused (0/1), created_at
  user:{chat_id}:alarms         set   alarm IDs
  alarm:{id}                    hash  chat_id, from_id, to_id, from_name,
                                      to_name, travel_date (YYYY-MM-DD),
                                      passengers, active, created_at,
                                      last_alerted_at
  alarm:{id}:notified           set   train numbers already alerted for
  alarms:active                 set   all currently active alarm IDs
  ratelimit:search:{chat_id}    list  timestamps of /search calls in last hour
  access:allowed                set   chat IDs granted access at runtime
  access:request:{chat_id}      hash  username, full_name, user_id, created_at
  access:requests               set   chat IDs with a pending request
  access:cooldown:{chat_id}     str   set after a rejection; blocks re-requesting
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import redis.asyncio as redis


@dataclass(frozen=True)
class Alarm:
    id: str
    chat_id: int
    from_id: int
    to_id: int
    from_name: str
    to_name: str
    travel_dates: list[date]  # one alarm can watch several days
    passengers: int
    active: bool
    created_at: datetime
    last_alerted_at: datetime | None
    # Train numbers to watch. Empty = alert on ANY train (the default). When set,
    # only these train numbers alert, on whichever selected days they run.
    target_trains: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class AccessRequest:
    chat_id: int
    user_id: int
    username: str | None  # Telegram usernames are optional
    full_name: str
    created_at: datetime | None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class Store:
    def __init__(self, url: str):
        self.r = redis.from_url(url, decode_responses=True)

    async def aclose(self) -> None:
        await self.r.aclose()

    # --- users ---

    async def upsert_user(self, chat_id: int, username: str | None) -> None:
        key = f"user:{chat_id}"
        existing = await self.r.hget(key, "created_at")
        fields: dict[str, str] = {"username": username or ""}
        if not existing:
            fields["created_at"] = _now_iso()
            fields["paused"] = "0"
        await self.r.hset(key, mapping=fields)

    async def is_paused(self, chat_id: int) -> bool:
        return (await self.r.hget(f"user:{chat_id}", "paused")) == "1"

    async def set_paused(self, chat_id: int, paused: bool) -> None:
        await self.r.hset(f"user:{chat_id}", "paused", "1" if paused else "0")
        alarm_ids = await self.r.smembers(f"user:{chat_id}:alarms")
        if paused:
            for aid in alarm_ids:
                await self.r.srem("alarms:active", aid)
        else:
            for aid in alarm_ids:
                if (await self.r.hget(f"alarm:{aid}", "active")) == "1":
                    await self.r.sadd("alarms:active", aid)

    # --- alarms ---

    async def count_active_alarms(self, chat_id: int) -> int:
        ids = await self.r.smembers(f"user:{chat_id}:alarms")
        n = 0
        for aid in ids:
            if (await self.r.hget(f"alarm:{aid}", "active")) == "1":
                n += 1
        return n

    async def create_alarm(
        self,
        chat_id: int,
        from_id: int,
        to_id: int,
        from_name: str,
        to_name: str,
        travel_dates: list[date],
        passengers: int,
        target_trains: list[str] | None = None,
    ) -> str:
        aid = uuid.uuid4().hex[:12]
        await self.r.hset(
            f"alarm:{aid}",
            mapping={
                "chat_id": str(chat_id),
                "from_id": str(from_id),
                "to_id": str(to_id),
                "from_name": from_name,
                "to_name": to_name,
                "travel_dates": ",".join(d.isoformat() for d in sorted(travel_dates)),
                "passengers": str(passengers),
                # Empty string = any train (the default).
                "target_trains": ",".join(target_trains or []),
                "active": "1",
                "created_at": _now_iso(),
                "last_alerted_at": "",
            },
        )
        await self.r.sadd(f"user:{chat_id}:alarms", aid)
        if not await self.is_paused(chat_id):
            await self.r.sadd("alarms:active", aid)
        return aid

    async def list_user_alarms(self, chat_id: int) -> list[Alarm]:
        ids = await self.r.smembers(f"user:{chat_id}:alarms")
        out: list[Alarm] = []
        for aid in ids:
            a = await self.get_alarm(aid)
            if a:
                out.append(a)
        out.sort(key=lambda a: a.travel_dates[0])
        return out

    async def get_alarm(self, aid: str) -> Alarm | None:
        h = await self.r.hgetall(f"alarm:{aid}")
        if not h:
            return None
        try:
            raw_dates = h.get("travel_dates")
            if raw_dates:
                travel_dates = [date.fromisoformat(s) for s in raw_dates.split(",") if s]
            elif h.get("travel_date"):  # backward-compat: old single-date alarms
                travel_dates = [date.fromisoformat(h["travel_date"])]
            else:
                return None
            return Alarm(
                id=aid,
                chat_id=int(h["chat_id"]),
                from_id=int(h["from_id"]),
                to_id=int(h["to_id"]),
                from_name=h["from_name"],
                to_name=h["to_name"],
                travel_dates=travel_dates,
                passengers=int(h["passengers"]),
                active=h.get("active") == "1",
                created_at=_parse_iso(h.get("created_at")) or datetime.min,
                last_alerted_at=_parse_iso(h.get("last_alerted_at")),
                # Absent (old alarms) or empty -> any train.
                target_trains={t for t in (h.get("target_trains") or "").split(",") if t},
            )
        except (KeyError, ValueError):
            return None

    async def delete_alarm(self, aid: str) -> None:
        a = await self.get_alarm(aid)
        if not a:
            return
        await self.r.delete(f"alarm:{aid}", f"alarm:{aid}:notified")
        await self.r.srem(f"user:{a.chat_id}:alarms", aid)
        await self.r.srem("alarms:active", aid)

    async def clear_user_alarms(self, chat_id: int) -> int:
        ids = list(await self.r.smembers(f"user:{chat_id}:alarms"))
        for aid in ids:
            await self.delete_alarm(aid)
        return len(ids)

    async def active_alarm_ids(self) -> list[str]:
        return list(await self.r.smembers("alarms:active"))

    async def mark_alerted(self, aid: str, train_nos: list[str]) -> None:
        if not train_nos:
            return
        await self.r.sadd(f"alarm:{aid}:notified", *train_nos)
        await self.r.hset(f"alarm:{aid}", "last_alerted_at", _now_iso())

    async def already_notified(self, aid: str) -> set[str]:
        return set(await self.r.smembers(f"alarm:{aid}:notified"))

    # --- rate limiting ---

    async def check_search_rate(self, chat_id: int, per_hour: int) -> bool:
        """Returns True if under the limit (and records the call), False if over."""
        key = f"ratelimit:search:{chat_id}"
        now = int(time.time())
        cutoff = now - 3600
        items = await self.r.lrange(key, 0, -1)
        kept = [int(t) for t in items if int(t) > cutoff]
        if len(kept) >= per_hour:
            return False
        kept.append(now)
        await self.r.delete(key)
        if kept:
            await self.r.rpush(key, *[str(t) for t in kept])
            await self.r.expire(key, 3600)
        return True

    async def heartbeat(self) -> None:
        await self.r.set("bot:last_seen", _now_iso(), ex=300)

    async def last_seen(self) -> datetime | None:
        return _parse_iso(await self.r.get("bot:last_seen"))

    # --- checker health ---

    async def is_checker_degraded(self) -> bool:
        return (await self.r.get("checker:degraded")) == "1"

    async def set_checker_degraded(self, degraded: bool) -> None:
        await self.r.set("checker:degraded", "1" if degraded else "0")

    # --- access control ---
    #
    # The runtime allow-list lives here rather than in the environment so the
    # admin can grant access from Telegram without a redeploy. It is the union
    # with ALLOWED_CHAT_IDS, never a replacement: settings stay authoritative
    # for whoever the deployment hardcoded (see main._make_access_gate).

    async def is_allowed(self, chat_id: int) -> bool:
        return bool(await self.r.sismember("access:allowed", str(chat_id)))

    async def add_allowed(self, chat_id: int) -> bool:
        """Grant access. True if this actually added someone new."""
        added = await self.r.sadd("access:allowed", str(chat_id))
        return bool(added)

    async def remove_allowed(self, chat_id: int) -> bool:
        """Revoke access. True if the chat was on the list."""
        removed = await self.r.srem("access:allowed", str(chat_id))
        return bool(removed)

    async def list_allowed(self) -> set[int]:
        members = await self.r.smembers("access:allowed")
        return {int(m) for m in members}

    async def any_allowed(self) -> bool:
        """Whether the runtime list has anyone on it.

        Distinguishes "restricted, but this chat isn't on the list" from
        "no allow-list configured at all", which leaves the bot open.
        """
        return bool(await self.r.scard("access:allowed"))

    # --- access requests ---

    async def create_access_request(
        self, chat_id: int, user_id: int, username: str | None, full_name: str
    ) -> bool:
        """Record a pending request. False if one is already pending or the
        chat is in the cooldown that follows a rejection."""
        if await self.r.exists(f"access:cooldown:{chat_id}"):
            return False
        if not await self.r.sadd("access:requests", str(chat_id)):
            return False
        await self.r.hset(
            f"access:request:{chat_id}",
            mapping={
                "user_id": str(user_id),
                "username": username or "",
                "full_name": full_name,
                "created_at": _now_iso(),
            },
        )
        return True

    async def get_access_request(self, chat_id: int) -> AccessRequest | None:
        d = await self.r.hgetall(f"access:request:{chat_id}")
        if not d:
            return None
        return AccessRequest(
            chat_id=chat_id,
            user_id=int(d.get("user_id") or 0),
            username=d.get("username") or None,
            full_name=d.get("full_name", ""),
            created_at=_parse_iso(d.get("created_at")),
        )

    async def list_access_requests(self) -> list[AccessRequest]:
        ids = await self.r.smembers("access:requests")
        out = []
        for cid in ids:
            req = await self.get_access_request(int(cid))
            if req is not None:
                out.append(req)
        out.sort(key=lambda r: (r.created_at is None, r.created_at))
        return out

    async def clear_access_request(self, chat_id: int, cooldown_s: int = 0) -> None:
        """Drop a request. A cooldown stops a rejected user from immediately
        asking again; approvals pass 0 since the question is settled."""
        await self.r.srem("access:requests", str(chat_id))
        await self.r.delete(f"access:request:{chat_id}")
        if cooldown_s > 0:
            await self.r.set(f"access:cooldown:{chat_id}", "1", ex=cooldown_s)
