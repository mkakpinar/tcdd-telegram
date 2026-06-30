"""Periodic alarm checker. Runs inside the bot process via PTB JobQueue
(see main.py) and also from scripts/check_alarms.py for ad-hoc runs.

Flow:
1. Cleanup stale alarms (travel_date + 2 days < today).
2. Read active alarms, expand each into (from, to, date±1) tuples, dedupe.
3. Query TCDD once per unique tuple.
4. For each alarm, send a Telegram message about NEW trains
   (train_no not in :notified set) that have >= passengers seats.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from . import format as fmt
from .store import Alarm, Store
from .tcdd import TcddBackend, TrainResult

log = logging.getLogger(__name__)


MAX_SEND_ATTEMPTS = 3


async def send_telegram(token: str, chat_id: int, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
            try:
                r = await client.post(url, json=payload)
            except Exception as exc:
                log.warning("Telegram send attempt %d errored: %r", attempt, exc)
            else:
                if r.status_code < 400:
                    return
                if r.status_code == 429:
                    # Respect Telegram's requested cool-off, then retry.
                    retry_after = (r.json().get("parameters") or {}).get("retry_after", 1)
                    log.warning("Telegram 429, retry_after=%s", retry_after)
                    await asyncio.sleep(float(retry_after))
                    continue
                if r.status_code < 500:
                    # e.g. user blocked the bot — retrying won't help.
                    log.warning("Telegram send failed %s: %s", r.status_code, r.text[:200])
                    return
                log.warning("Telegram send attempt %d -> HTTP %s", attempt, r.status_code)
            if attempt < MAX_SEND_ATTEMPTS:
                await asyncio.sleep(0.5 * 2 ** (attempt - 1))
        log.warning("Telegram send gave up after %d attempts (chat %s)", MAX_SEND_ATTEMPTS, chat_id)


async def cleanup_stale(store: Store, tz: ZoneInfo) -> int:
    # Scan every user's alarm set (not just alarms:active) so that paused
    # alarms are also reaped once their travel date passes, and orphaned
    # references (alarm hash already gone) are pruned.
    today_local = datetime.now(tz).date()
    n = 0
    async for key in store.r.scan_iter(match="user:*:alarms"):
        for aid in await store.r.smembers(key):
            a = await store.get_alarm(aid)
            if not a:
                await store.r.srem(key, aid)
                await store.r.srem("alarms:active", aid)
                continue
            if max(a.travel_dates) + timedelta(days=2) <= today_local:
                await store.delete_alarm(aid)
                n += 1
    return n


async def run_once(
    store: Store, tcdd: TcddBackend, bot_token: str, tz: ZoneInfo
) -> None:
    cleaned = await cleanup_stale(store, tz)
    if cleaned:
        log.info("cleaned %d stale alarms", cleaned)

    aids = await store.active_alarm_ids()
    log.info("checker: %d active alarms", len(aids))
    if not aids:
        return

    alarms: list[Alarm] = []
    for aid in aids:
        a = await store.get_alarm(aid)
        if a and a.active:
            alarms.append(a)

    # Build set of (from_id, to_id, date) tuples to query: every selected day
    # of every alarm, expanded ±1 day, deduped.
    today = datetime.now(tz).date()
    queries: set[tuple[int, int, date]] = set()
    names: dict[int, str] = {}
    for a in alarms:
        names[a.from_id] = a.from_name
        names[a.to_id] = a.to_name
        for base in a.travel_dates:
            for delta in (-1, 0, 1):
                d = base + timedelta(days=delta)
                if d < today:
                    continue
                queries.add((a.from_id, a.to_id, d))

    results: dict[tuple[int, int, date], list[TrainResult]] = {}
    for q in queries:
        try:
            results[q] = await tcdd.search(
                q[0], q[1], q[2],
                passengers=1,
                from_name=names.get(q[0], ""),
                to_name=names.get(q[1], ""),
            )
        except Exception:
            log.exception("search failed for %s", q)
            results[q] = []
        await asyncio.sleep(random.uniform(2.0, 6.0))

    for a in alarms:
        notified = await store.already_notified(a.id)
        per_day_hits: dict[date, list[TrainResult]] = defaultdict(list)
        # Unique days to check across all selected dates (±1), deduped so
        # adjacent picks don't double up.
        days = {
            base + timedelta(days=delta)
            for base in a.travel_dates
            for delta in (-1, 0, 1)
        }
        for d in days:
            trains = results.get((a.from_id, a.to_id, d), [])
            for t in trains:
                # Notified set is keyed by day so the same train number on a
                # different day still alerts.
                key = f"{d.isoformat()}:{t.train_no}"
                if t.available_seats >= a.passengers and key not in notified:
                    per_day_hits[d].append(t)
        new_keys: list[str] = []
        for d, hits in sorted(per_day_hits.items()):
            await send_telegram(bot_token, a.chat_id, fmt.render_alert(a, d, hits))
            new_keys.extend(f"{d.isoformat()}:{t.train_no}" for t in hits)
        if new_keys:
            await store.mark_alerted(a.id, new_keys)
            log.info("alarm %s alerted %d trains", a.id, len(new_keys))
