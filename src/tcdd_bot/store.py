"""Upstash Redis store for users, alarms, and rate limits.

Schema (see plan):
  user:{chat_id}                hash  username, paused (0/1), created_at
  user:{chat_id}:alarms         set   alarm IDs
  alarm:{id}                    hash  chat_id, from_code, to_code, from_name,
                                      to_name, travel_date (YYYY-MM-DD),
                                      passengers, active, created_at,
                                      last_alerted_at
  alarm:{id}:notified           set   train numbers already alerted for
  alarms:active                 set   all currently active alarm IDs
  ratelimit:search:{chat_id}    list  timestamps of /search calls in last hour
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from upstash_redis.asyncio import Redis


@dataclass(frozen=True)
class Alarm:
    id: str
    chat_id: int
    from_id: int
    to_id: int
    from_name: str
    to_name: str
    travel_date: date
    passengers: int
    active: bool
    created_at: datetime
    last_alerted_at: datetime | None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class Store:
    def __init__(self, url: str, token: str):
        self.r = Redis(url=url, token=token)

    # --- users ---

    async def upsert_user(self, chat_id: int, username: str | None) -> None:
        key = f"user:{chat_id}"
        existing = await self.r.hget(key, "created_at")
        fields = {"username": username or ""}
        if not existing:
            fields["created_at"] = _now_iso()
            fields["paused"] = "0"
        await self.r.hset(key, values=fields)

    async def is_paused(self, chat_id: int) -> bool:
        return (await self.r.hget(f"user:{chat_id}", "paused")) == "1"

    async def set_paused(self, chat_id: int, paused: bool) -> None:
        await self.r.hset(f"user:{chat_id}", values={"paused": "1" if paused else "0"})
        alarm_ids = await self.r.smembers(f"user:{chat_id}:alarms") or []
        if paused:
            for aid in alarm_ids:
                await self.r.srem("alarms:active", aid)
        else:
            for aid in alarm_ids:
                active = await self.r.hget(f"alarm:{aid}", "active")
                if active == "1":
                    await self.r.sadd("alarms:active", aid)

    # --- alarms ---

    async def count_active_alarms(self, chat_id: int) -> int:
        ids = await self.r.smembers(f"user:{chat_id}:alarms") or []
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
        travel_date: date,
        passengers: int,
    ) -> str:
        aid = uuid.uuid4().hex[:12]
        await self.r.hset(
            f"alarm:{aid}",
            values={
                "chat_id": str(chat_id),
                "from_id": str(from_id),
                "to_id": str(to_id),
                "from_name": from_name,
                "to_name": to_name,
                "travel_date": travel_date.isoformat(),
                "passengers": str(passengers),
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
        ids = await self.r.smembers(f"user:{chat_id}:alarms") or []
        out: list[Alarm] = []
        for aid in ids:
            a = await self.get_alarm(aid)
            if a:
                out.append(a)
        out.sort(key=lambda a: a.travel_date)
        return out

    async def get_alarm(self, aid: str) -> Alarm | None:
        h = await self.r.hgetall(f"alarm:{aid}")
        if not h:
            return None
        try:
            return Alarm(
                id=aid,
                chat_id=int(h["chat_id"]),
                from_id=int(h["from_id"]),
                to_id=int(h["to_id"]),
                from_name=h["from_name"],
                to_name=h["to_name"],
                travel_date=date.fromisoformat(h["travel_date"]),
                passengers=int(h["passengers"]),
                active=h.get("active") == "1",
                created_at=_parse_iso(h.get("created_at")) or datetime.min,
                last_alerted_at=_parse_iso(h.get("last_alerted_at")),
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
        ids = await self.r.smembers(f"user:{chat_id}:alarms") or []
        for aid in ids:
            await self.delete_alarm(aid)
        return len(ids)

    async def active_alarm_ids(self) -> list[str]:
        return list(await self.r.smembers("alarms:active") or [])

    async def mark_alerted(self, aid: str, train_nos: list[str]) -> None:
        if not train_nos:
            return
        await self.r.sadd(f"alarm:{aid}:notified", *train_nos)
        await self.r.hset(
            f"alarm:{aid}", values={"last_alerted_at": _now_iso()}
        )

    async def already_notified(self, aid: str) -> set[str]:
        return set(await self.r.smembers(f"alarm:{aid}:notified") or [])

    # --- rate limiting ---

    async def check_search_rate(self, chat_id: int, per_hour: int) -> bool:
        """Returns True if under the limit (and records the call), False if over."""
        key = f"ratelimit:search:{chat_id}"
        now = int(time.time())
        cutoff = now - 3600
        # Prune old entries
        items = await self.r.lrange(key, 0, -1) or []
        kept = [int(t) for t in items if int(t) > cutoff]
        if len(kept) >= per_hour:
            return False
        kept.append(now)
        # Replace list
        await self.r.delete(key)
        if kept:
            await self.r.rpush(key, *[str(t) for t in kept])
            await self.r.expire(key, 3600)
        return True

    async def heartbeat(self) -> None:
        await self.r.set("bot:last_seen", _now_iso(), ex=300)
