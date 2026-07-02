from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tcdd_bot import checker
from tcdd_bot.tcdd import TrainResult

TZ = ZoneInfo("Europe/Istanbul")


async def test_cleanup_removes_active_and_paused_stale_keeps_future(store):
    past = date.today() - timedelta(days=3)
    fut = date.today() + timedelta(days=5)
    # user 42: one stale (active), one future
    await store.create_alarm(42, 1, 2, "A", "B", [past], 1)
    fut_id = await store.create_alarm(42, 1, 2, "A", "B", [fut], 1)
    # user 99: a stale alarm that is PAUSED (not in alarms:active)
    await store.create_alarm(99, 3, 4, "C", "D", [past], 1)
    await store.set_paused(99, True)
    # an orphaned reference (set membership with no alarm hash)
    await store.r.sadd("user:42:alarms", "orphan-id")

    cleaned = await checker.cleanup_stale(store, TZ)

    assert cleaned == 2  # both stale alarms (active + paused) removed
    assert [a.id for a in await store.list_user_alarms(42)] == [fut_id]
    assert await store.list_user_alarms(99) == []
    assert "orphan-id" not in await store.r.smembers("user:42:alarms")


class _FakeBackend:
    """Returns a train only for a specific date; empty otherwise."""

    def __init__(self, hit_day: date, seats: int = 4):
        self.hit_day = hit_day
        self.seats = seats
        self.calls = 0

    async def search(self, from_id, to_id, day, passengers, from_name="", to_name=""):
        self.calls += 1
        if day != self.hit_day:
            return []
        return [TrainResult(
            train_no="81002",
            departure_time=datetime.combine(day, datetime.min.time()).replace(hour=8),
            arrival_time=datetime.combine(day, datetime.min.time()).replace(hour=12),
            available_seats=self.seats,
            cabin_breakdown={"EKONOMİ": self.seats},
        )]


async def test_run_once_alerts_then_dedupes(store, monkeypatch):
    sent: list[tuple[int, str]] = []

    async def fake_send(token, chat_id, text):
        sent.append((chat_id, text))

    monkeypatch.setattr(checker, "send_telegram", fake_send)
    monkeypatch.setattr(checker.random, "uniform", lambda a, b: 0)  # no real sleeps

    travel = date.today() + timedelta(days=3)
    aid = await store.create_alarm(42, 1, 2, "A", "B", [travel], 2)
    backend = _FakeBackend(hit_day=travel)

    await checker.run_once(store, backend, "tok", TZ)

    assert len(sent) == 1
    assert sent[0][0] == 42
    assert "81002" in sent[0][1]
    assert await store.already_notified(aid) == {f"{travel.isoformat()}:81002"}

    # second run: same train is already notified -> no new alert
    sent.clear()
    await checker.run_once(store, backend, "tok", TZ)
    assert sent == []


async def test_run_once_multi_date_alerts_only_matching_day(store, monkeypatch):
    sent = []

    async def fake_send(token, chat_id, text):
        sent.append((chat_id, text))

    monkeypatch.setattr(checker, "send_telegram", fake_send)
    monkeypatch.setattr(checker.random, "uniform", lambda a, b: 0)

    d1 = date.today() + timedelta(days=3)
    d2 = date.today() + timedelta(days=7)
    aid = await store.create_alarm(42, 1, 2, "A", "B", [d1, d2], 1)
    backend = _FakeBackend(hit_day=d2)  # seats only on the second day

    await checker.run_once(store, backend, "tok", TZ)

    assert len(sent) == 1
    assert d2.strftime("%d.%m.%Y") in sent[0][1]
    assert await store.already_notified(aid) == {f"{d2.isoformat()}:81002"}


class _TwoTrainBackend:
    """Returns a target and a non-target train, both with seats, on hit_day."""

    def __init__(self, hit_day: date):
        self.hit_day = hit_day

    async def search(self, from_id, to_id, day, passengers, from_name="", to_name="",
                     include_unavailable=False):
        if day != self.hit_day:
            return []

        def mk(no):
            return TrainResult(
                train_no=no,
                departure_time=datetime.combine(day, datetime.min.time()).replace(hour=8),
                arrival_time=datetime.combine(day, datetime.min.time()).replace(hour=12),
                available_seats=4,
                cabin_breakdown={"EKONOMİ": 4},
            )

        return [mk("12002"), mk("81002")]


async def test_run_once_targeted_alarm_alerts_only_target_train(store, monkeypatch):
    sent = []

    async def fake_send(token, chat_id, text):
        sent.append((chat_id, text))

    monkeypatch.setattr(checker, "send_telegram", fake_send)
    monkeypatch.setattr(checker.random, "uniform", lambda a, b: 0)

    travel = date.today() + timedelta(days=3)
    aid = await store.create_alarm(42, 1, 2, "A", "B", [travel], 1, target_trains=["12002"])

    await checker.run_once(store, _TwoTrainBackend(hit_day=travel), "tok", TZ)

    assert len(sent) == 1
    assert "12002" in sent[0][1] and "81002" not in sent[0][1]
    assert await store.already_notified(aid) == {f"{travel.isoformat()}:12002"}


async def test_run_once_ignores_adjacent_days(store, monkeypatch):
    # Regression: an alarm must alert ONLY for its selected day(s) — no ±1
    # expansion. Seats on travel+1 must not fire, and only the exact day is
    # queried.
    sent = []

    async def fake_send(token, chat_id, text):
        sent.append((chat_id, text))

    monkeypatch.setattr(checker, "send_telegram", fake_send)
    monkeypatch.setattr(checker.random, "uniform", lambda a, b: 0)

    travel = date.today() + timedelta(days=3)
    await store.create_alarm(42, 1, 2, "A", "B", [travel], 1)
    backend = _FakeBackend(hit_day=travel + timedelta(days=1))  # seats a day later

    await checker.run_once(store, backend, "tok", TZ)

    assert sent == []            # adjacent-day availability does not alert
    assert backend.calls == 1    # only the exact selected day was queried


class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


class _FakeHttpClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self.calls += 1
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


async def _instant(*a, **k):
    return None


async def test_send_telegram_retries_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _instant)
    fake = _FakeHttpClient([_Resp(500), Exception("conn reset"), _Resp(200, {"ok": True})])
    monkeypatch.setattr(checker.httpx, "AsyncClient", lambda *a, **k: fake)
    await checker.send_telegram("tok", 1, "hi")
    assert fake.calls == 3


async def test_send_telegram_honors_429_retry_after(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _instant)
    fake = _FakeHttpClient([
        _Resp(429, {"parameters": {"retry_after": 0}}),
        _Resp(200, {"ok": True}),
    ])
    monkeypatch.setattr(checker.httpx, "AsyncClient", lambda *a, **k: fake)
    await checker.send_telegram("tok", 1, "hi")
    assert fake.calls == 2


async def test_send_telegram_no_retry_on_client_error(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _instant)
    fake = _FakeHttpClient([_Resp(403, text="bot was blocked by the user")])
    monkeypatch.setattr(checker.httpx, "AsyncClient", lambda *a, **k: fake)
    await checker.send_telegram("tok", 1, "hi")
    assert fake.calls == 1  # 403 is terminal


async def test_send_telegram_gives_up_after_max(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _instant)
    fake = _FakeHttpClient([_Resp(500)] * checker.MAX_SEND_ATTEMPTS)
    monkeypatch.setattr(checker.httpx, "AsyncClient", lambda *a, **k: fake)
    await checker.send_telegram("tok", 1, "hi")  # must not raise
    assert fake.calls == checker.MAX_SEND_ATTEMPTS


class _DownBackend:
    async def search(self, *a, **k):
        raise RuntimeError("WAF 403")


async def test_run_once_alerts_admin_when_tcdd_down_then_recovers(store, monkeypatch):
    notes = []

    async def fake_send(token, chat_id, text):
        notes.append((chat_id, text))

    monkeypatch.setattr(checker, "send_telegram", fake_send)
    monkeypatch.setattr(checker.random, "uniform", lambda a, b: 0)

    travel = date.today() + timedelta(days=3)
    await store.create_alarm(42, 1, 2, "A", "B", [travel], 1)

    # all searches fail -> degraded + admin alerted once
    await checker.run_once(store, _DownBackend(), "tok", TZ, admin_chat_id=999)
    assert await store.is_checker_degraded() is True
    assert any(cid == 999 and "ulaşılamıyor" in t for cid, t in notes)

    # still down -> no duplicate alert (transition-based)
    notes.clear()
    await checker.run_once(store, _DownBackend(), "tok", TZ, admin_chat_id=999)
    assert [n for n in notes if n[0] == 999] == []

    # recovers -> degraded cleared + recovery alert
    notes.clear()
    await checker.run_once(store, _FakeBackend(hit_day=travel), "tok", TZ, admin_chat_id=999)
    assert await store.is_checker_degraded() is False
    assert any(cid == 999 and "normale" in t for cid, t in notes)


async def test_run_once_skips_when_seats_below_passengers(store, monkeypatch):
    sent = []

    async def fake_send(token, chat_id, text):
        sent.append((chat_id, text))

    monkeypatch.setattr(checker, "send_telegram", fake_send)
    monkeypatch.setattr(checker.random, "uniform", lambda a, b: 0)

    travel = date.today() + timedelta(days=3)
    await store.create_alarm(42, 1, 2, "A", "B", [travel], 5)  # needs 5 seats
    backend = _FakeBackend(hit_day=travel, seats=2)  # only 2 available

    await checker.run_once(store, backend, "tok", TZ)
    assert sent == []
