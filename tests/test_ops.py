"""Tests for the ops commands (/status, /stop) and the Fly self-stop call."""

from __future__ import annotations

import types
from datetime import date, timedelta

from tcdd_bot.config import Settings
from tcdd_bot.handlers import ops


def make_settings(**overrides) -> Settings:
    base = dict(
        bot_token="t", redis_url="redis://x", admin_chat_id=None,
        allowed_chat_ids=frozenset(), timezone="Europe/Istanbul",
        log_level="INFO", max_alarms_per_user=5, search_rate_per_hour=10,
        check_interval_min=10, tcdd_mode="stub", fly_api_token=None,
    )
    base.update(overrides)
    return Settings(**base)


def make_update(chat_id):
    replies: list[str] = []

    async def reply_text(text, **kw):
        replies.append(text)

    async def reply_markdown(text, **kw):
        replies.append(text)

    msg = types.SimpleNamespace(reply_text=reply_text, reply_markdown=reply_markdown)
    upd = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=chat_id), message=msg
    )
    return upd, replies


def make_ctx(settings, store):
    app = types.SimpleNamespace(bot_data={"settings": settings, "store": store})
    return types.SimpleNamespace(application=app)


# --- /status ---

async def test_status_reports_active_alarm_count(store):
    await store.create_alarm(42, 1, 2, "A", "B", [date.today() + timedelta(days=3)], 1)
    upd, replies = make_update(42)
    await ops.status_cmd(upd, make_ctx(make_settings(check_interval_min=7), store))
    assert len(replies) == 1
    card = replies[0]
    assert "Bot durumu" in card
    assert "Aktif alarm: 1" in card
    assert "7 dk" in card  # check interval echoed


# --- /stop gating ---

async def test_stop_refuses_non_admin(store, monkeypatch):
    called = False

    async def fake_stop(_token):
        nonlocal called
        called = True
        return True, "ok"

    monkeypatch.setattr(ops, "_stop_self_machine", fake_stop)
    upd, replies = make_update(999)
    settings = make_settings(admin_chat_id=555, fly_api_token="tok")
    await ops.stop_cmd(upd, make_ctx(settings, store))
    assert called is False
    assert "yönetici" in replies[0]


async def test_stop_admin_without_token_says_not_configured(store, monkeypatch):
    called = False

    async def fake_stop(_token):
        nonlocal called
        called = True
        return True, "ok"

    monkeypatch.setattr(ops, "_stop_self_machine", fake_stop)
    upd, replies = make_update(555)
    await ops.stop_cmd(upd, make_ctx(make_settings(admin_chat_id=555), store))
    assert called is False
    assert "yapılandırılmamış" in replies[0]


async def test_stop_admin_stops_machine(store, monkeypatch):
    tokens_seen = []

    async def fake_stop(token):
        tokens_seen.append(token)
        return True, "ok"

    monkeypatch.setattr(ops, "_stop_self_machine", fake_stop)
    upd, replies = make_update(555)
    settings = make_settings(admin_chat_id=555, fly_api_token="tok")
    await ops.stop_cmd(upd, make_ctx(settings, store))
    assert tokens_seen == ["tok"]
    assert "durduruyorum" in replies[0].lower()


async def test_stop_reports_failure(store, monkeypatch):
    async def fake_stop(_token):
        return False, "Fly API 401"

    monkeypatch.setattr(ops, "_stop_self_machine", fake_stop)
    upd, replies = make_update(555)
    settings = make_settings(admin_chat_id=555, fly_api_token="tok")
    await ops.stop_cmd(upd, make_ctx(settings, store))
    assert any("başarısız" in r and "401" in r for r in replies)


# --- Fly Machines API self-stop ---

class _FakeResp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp
        self.calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None):
        self.calls.append((url, headers))
        return self._resp


async def test_self_stop_posts_to_machines_api(monkeypatch):
    client = _FakeClient(_FakeResp(200))
    monkeypatch.setattr(ops.httpx, "AsyncClient", lambda *a, **k: client)
    monkeypatch.setenv("FLY_APP_NAME", "tcdd-telegram")
    monkeypatch.setenv("FLY_MACHINE_ID", "abc123")

    ok, detail = await ops._stop_self_machine("tok")
    assert ok is True and detail == "ok"
    url, headers = client.calls[0]
    assert url == "https://api.machines.dev/v1/apps/tcdd-telegram/machines/abc123/stop"
    assert headers["Authorization"] == "Bearer tok"


async def test_self_stop_surfaces_http_error(monkeypatch):
    client = _FakeClient(_FakeResp(401, "unauthorized"))
    monkeypatch.setattr(ops.httpx, "AsyncClient", lambda *a, **k: client)
    monkeypatch.setenv("FLY_APP_NAME", "tcdd-telegram")
    monkeypatch.setenv("FLY_MACHINE_ID", "abc123")

    ok, detail = await ops._stop_self_machine("tok")
    assert ok is False and "401" in detail


async def test_self_stop_without_fly_env(monkeypatch):
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.delenv("FLY_MACHINE_ID", raising=False)
    ok, detail = await ops._stop_self_machine("tok")
    assert ok is False and "FLY_APP_NAME" in detail
