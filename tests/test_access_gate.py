import types

import pytest
from telegram.ext import ApplicationHandlerStop

from tcdd_bot.config import Settings
from tcdd_bot.main import _make_access_gate, _post_shutdown


def make_settings(**overrides) -> Settings:
    base = dict(
        bot_token="t", redis_url="redis://x", admin_chat_id=None,
        allowed_chat_ids=frozenset(), timezone="Europe/Istanbul",
        log_level="INFO", max_alarms_per_user=5, search_rate_per_hour=10,
        check_interval_min=10, tcdd_mode="stub",
    )
    base.update(overrides)
    return Settings(**base)


def _update(chat_id):
    replies = []

    async def reply_text(text):
        replies.append(text)

    msg = types.SimpleNamespace(reply_text=reply_text)
    upd = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=chat_id),
        effective_message=msg,
        callback_query=None,
    )
    return upd, replies


async def test_gate_open_when_allowlist_empty():
    gate = _make_access_gate(make_settings(allowed_chat_ids=frozenset()))
    upd, replies = _update(999)
    await gate(upd, None)  # must not raise
    assert replies == []


async def test_gate_blocks_unlisted_and_reports_chat_id():
    gate = _make_access_gate(make_settings(allowed_chat_ids=frozenset({111, 222})))
    upd, replies = _update(999)
    with pytest.raises(ApplicationHandlerStop):
        await gate(upd, None)
    assert len(replies) == 1 and "999" in replies[0]


async def test_gate_allows_listed():
    gate = _make_access_gate(make_settings(allowed_chat_ids=frozenset({111})))
    upd, replies = _update(111)
    await gate(upd, None)
    assert replies == []


async def test_gate_always_allows_admin():
    gate = _make_access_gate(
        make_settings(allowed_chat_ids=frozenset({111}), admin_chat_id=555)
    )
    upd, replies = _update(555)
    await gate(upd, None)
    assert replies == []


async def test_post_shutdown_closes_backends():
    closed = {"tcdd": False, "store": False}

    class FakeTcdd:
        async def aclose(self):
            closed["tcdd"] = True

    class FakeStore:
        async def aclose(self):
            closed["store"] = True

    app = types.SimpleNamespace(bot_data={"tcdd": FakeTcdd(), "store": FakeStore()})
    await _post_shutdown(app)
    assert closed == {"tcdd": True, "store": True}


async def test_post_shutdown_handles_backend_without_aclose():
    # StubBackend has no aclose() — guard must not raise.
    closed = {"store": False}

    class FakeStore:
        async def aclose(self):
            closed["store"] = True

    app = types.SimpleNamespace(bot_data={"tcdd": object(), "store": FakeStore()})
    await _post_shutdown(app)  # must not raise
    assert closed["store"] is True
