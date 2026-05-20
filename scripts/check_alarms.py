"""Periodic alarm checker — run from GitHub Actions on a 30-min cron.

Flow:
1. Random 0–15 min sleep at start (anti-ban jitter).
2. Cleanup stale alarms (travel_date + 2 days < today).
3. Read active alarms, expand each into (from, to, date±1) tuples,
   dedupe across all alarms.
4. Query TCDD once per unique tuple, filter wheelchair seats.
5. For each alarm, send a Telegram message about NEW trains
   (not in its :notified set) that have >= passengers seats.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tcdd_bot import format as fmt  # noqa: E402
from tcdd_bot.config import load_settings  # noqa: E402
from tcdd_bot.store import Alarm, Store  # noqa: E402
from tcdd_bot.tcdd import TrainResult, build_backend  # noqa: E402

log = logging.getLogger("checker")


async def send_telegram(token: str, chat_id: int, text: str) -> None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
        )
        if r.status_code >= 400:
            log.warning("Telegram send failed %s: %s", r.status_code, r.text[:200])


async def cleanup_stale(store: Store, tz: ZoneInfo) -> int:
    today_local = datetime.now(tz).date()
    n = 0
    for aid in await store.active_alarm_ids():
        a = await store.get_alarm(aid)
        if not a:
            await store.r.srem("alarms:active", aid)
            continue
        if a.travel_date + timedelta(days=2) <= today_local:
            await store.delete_alarm(aid)
            n += 1
    return n


async def run() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if os.getenv("SKIP_JITTER") != "1":
        jitter = random.randint(0, 900)
        log.info("jitter sleep %ds", jitter)
        time.sleep(jitter)

    store = Store(settings.upstash_url, settings.upstash_token)
    tz = ZoneInfo(settings.timezone)
    tcdd = build_backend(settings.tcdd_mode)

    cleaned = await cleanup_stale(store, tz)
    if cleaned:
        log.info("cleaned %d stale alarms", cleaned)

    aids = await store.active_alarm_ids()
    log.info("%d active alarms", len(aids))
    if not aids:
        return

    alarms: list[Alarm] = []
    for aid in aids:
        a = await store.get_alarm(aid)
        if a and a.active:
            alarms.append(a)

    # Build set of (from_id, to_id, date) tuples to query, expanded ±1 day.
    # Track names so we can pass them on the request.
    queries: set[tuple[int, int, date]] = set()
    names: dict[int, str] = {}
    for a in alarms:
        names[a.from_id] = a.from_name
        names[a.to_id] = a.to_name
        for delta in (-1, 0, 1):
            d = a.travel_date + timedelta(days=delta)
            if d < datetime.now(tz).date():
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
        for delta in (-1, 0, 1):
            d = a.travel_date + timedelta(days=delta)
            trains = results.get((a.from_id, a.to_id, d), [])
            for t in trains:
                if t.available_seats >= a.passengers and t.train_no not in notified:
                    per_day_hits[d].append(t)
        new_train_nos: list[str] = []
        for d, hits in sorted(per_day_hits.items()):
            await send_telegram(
                settings.bot_token,
                a.chat_id,
                fmt.render_alert(a, d, hits),
            )
            new_train_nos.extend(t.train_no for t in hits)
        if new_train_nos:
            await store.mark_alerted(a.id, new_train_nos)
            log.info("alarm %s alerted %d trains", a.id, len(new_train_nos))


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
