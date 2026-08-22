"""Tests for the ops command (/status)."""

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
        check_interval_min=10, tcdd_mode="stub",
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


async def test_status_shows_mode_and_host_label(store, monkeypatch):
    monkeypatch.setenv("HOST_LABEL", "tcdd-prod")
    upd, replies = make_update(42)
    await ops.status_cmd(upd, make_ctx(make_settings(tcdd_mode="live"), store))
    assert "tcdd-prod" in replies[0]
    assert "Mod: live" in replies[0]


async def test_status_falls_back_to_hostname(store, monkeypatch):
    """No HOST_LABEL (bare `python -m tcdd_bot.main`) still names the machine."""
    monkeypatch.delenv("HOST_LABEL", raising=False)
    monkeypatch.setattr(ops.socket, "gethostname", lambda: "some-box")
    upd, replies = make_update(42)
    await ops.status_cmd(upd, make_ctx(make_settings(), store))
    assert "some-box" in replies[0]
