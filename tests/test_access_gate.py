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


def _update(chat_id, callback_data=None):
    replies = []

    async def reply_text(text, **kw):
        replies.append(text)

    async def answer(*a, **kw):
        pass

    msg = types.SimpleNamespace(reply_text=reply_text)
    cq = (
        types.SimpleNamespace(data=callback_data, answer=answer)
        if callback_data is not None
        else None
    )
    upd = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=chat_id),
        effective_message=msg,
        callback_query=cq,
    )
    return upd, replies


def _ctx(store=None):
    """Gate reads the store off bot_data; None models "not wired up yet"."""
    app = types.SimpleNamespace(bot_data={"store": store} if store else {})
    return types.SimpleNamespace(application=app)


class _FakeStore:
    """Minimal stand-in; `raises` makes every call blow up so the fallback
    path can be exercised."""

    def __init__(self, allowed=(), raises=False):
        self._allowed = set(allowed)
        self._raises = raises

    async def is_allowed(self, chat_id):
        if self._raises:
            raise RuntimeError("redis down")
        return chat_id in self._allowed

    async def any_allowed(self):
        if self._raises:
            raise RuntimeError("redis down")
        return bool(self._allowed)


async def test_gate_open_when_allowlist_empty():
    gate = _make_access_gate(make_settings(allowed_chat_ids=frozenset()))
    upd, replies = _update(999)
    await gate(upd, _ctx())  # must not raise
    assert replies == []


async def test_gate_blocks_unlisted_and_reports_chat_id():
    gate = _make_access_gate(make_settings(allowed_chat_ids=frozenset({111, 222})))
    upd, replies = _update(999)
    with pytest.raises(ApplicationHandlerStop):
        await gate(upd, _ctx())
    assert len(replies) == 1 and "999" in replies[0]


async def test_gate_allows_listed():
    gate = _make_access_gate(make_settings(allowed_chat_ids=frozenset({111})))
    upd, replies = _update(111)
    await gate(upd, _ctx())
    assert replies == []


async def test_gate_always_allows_admin():
    gate = _make_access_gate(
        make_settings(allowed_chat_ids=frozenset({111}), admin_chat_id=555)
    )
    upd, replies = _update(555)
    await gate(upd, _ctx())
    assert replies == []


# --- runtime allow-list (Redis) ---


async def test_gate_allows_chat_granted_at_runtime():
    """Not in ALLOWED_CHAT_IDS, but the admin granted it from Telegram."""
    gate = _make_access_gate(make_settings(allowed_chat_ids=frozenset({111})))
    upd, replies = _update(999)
    await gate(upd, _ctx(_FakeStore(allowed={999})))
    assert replies == []


async def test_gate_blocks_when_on_neither_list():
    gate = _make_access_gate(make_settings(allowed_chat_ids=frozenset({111})))
    upd, replies = _update(999)
    with pytest.raises(ApplicationHandlerStop):
        await gate(upd, _ctx(_FakeStore(allowed={222})))
    assert "999" in replies[0]


async def test_gate_restricted_by_runtime_list_alone():
    """Empty env list is no longer "open" once someone was granted in Redis."""
    gate = _make_access_gate(make_settings(allowed_chat_ids=frozenset()))
    upd, replies = _update(999)
    with pytest.raises(ApplicationHandlerStop):
        await gate(upd, _ctx(_FakeStore(allowed={111})))
    assert "999" in replies[0]


# --- Redis failure: fall back to the environment list ---


async def test_gate_falls_back_to_env_list_when_redis_fails():
    gate = _make_access_gate(make_settings(allowed_chat_ids=frozenset({111})))
    upd, replies = _update(111)
    await gate(upd, _ctx(_FakeStore(raises=True)))
    assert replies == []


async def test_gate_still_blocks_outsiders_when_redis_fails():
    """A broken Redis must not throw the doors open."""
    gate = _make_access_gate(make_settings(allowed_chat_ids=frozenset({111})))
    upd, replies = _update(999)
    with pytest.raises(ApplicationHandlerStop):
        await gate(upd, _ctx(_FakeStore(raises=True)))
    assert "999" in replies[0]


async def test_gate_open_when_redis_fails_and_no_env_list():
    """Nothing configured anywhere: stay open rather than lock everyone out."""
    gate = _make_access_gate(make_settings(allowed_chat_ids=frozenset()))
    upd, replies = _update(999)
    await gate(upd, _ctx(_FakeStore(raises=True)))
    assert replies == []


# --- the access-request button must survive the gate ---


async def test_gate_lets_access_request_callback_through():
    from tcdd_bot.handlers.access import REQUEST_CB

    gate = _make_access_gate(make_settings(allowed_chat_ids=frozenset({111})))
    upd, replies = _update(999, callback_data=REQUEST_CB)
    await gate(upd, _ctx(_FakeStore()))  # must not raise
    assert replies == []


async def test_gate_blocks_other_callbacks_from_outsiders():
    gate = _make_access_gate(make_settings(allowed_chat_ids=frozenset({111})))
    upd, replies = _update(999, callback_data="alm:del:abc")
    with pytest.raises(ApplicationHandlerStop):
        await gate(upd, _ctx(_FakeStore()))


async def test_blocked_user_is_offered_a_request_button():
    gate = _make_access_gate(make_settings(allowed_chat_ids=frozenset({111})))
    markups = []

    async def reply_text(text, **kw):
        markups.append(kw.get("reply_markup"))

    upd, _ = _update(999)
    upd.effective_message.reply_text = reply_text
    with pytest.raises(ApplicationHandlerStop):
        await gate(upd, _ctx(_FakeStore()))
    assert markups and markups[0] is not None


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
