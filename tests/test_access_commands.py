"""Tests for the admin access commands and the request/approve flow."""

from __future__ import annotations

import types

from tcdd_bot.config import Settings
from tcdd_bot.handlers import access

ADMIN = 555
OUTSIDER = 999


def make_settings(**overrides) -> Settings:
    base = dict(
        bot_token="t", redis_url="redis://x", admin_chat_id=ADMIN,
        allowed_chat_ids=frozenset(), timezone="Europe/Istanbul",
        log_level="INFO", max_alarms_per_user=5, search_rate_per_hour=10,
        check_interval_min=10, tcdd_mode="stub",
    )
    base.update(overrides)
    return Settings(**base)


class _Bot:
    """Captures what the bot would have sent to whom."""

    def __init__(self, fail_for: set[int] | None = None):
        self.sent: list[tuple[int, str]] = []
        self._fail_for = fail_for or set()

    async def send_message(self, chat_id, text, **kw):
        if chat_id in self._fail_for:
            raise RuntimeError("bot blocked by user")
        self.sent.append((chat_id, text))


def make_ctx(settings, store, args=None, bot=None):
    app = types.SimpleNamespace(bot_data={"settings": settings, "store": store})
    return types.SimpleNamespace(application=app, args=args or [], bot=bot or _Bot())


def make_update(chat_id):
    replies: list[str] = []

    async def reply_text(text, **kw):
        replies.append(text)

    msg = types.SimpleNamespace(reply_text=reply_text)
    upd = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=chat_id),
        effective_message=msg,
        message=msg,
    )
    return upd, replies


def make_callback(chat_id, data, user=None):
    """An update carrying a button press."""
    edits: list[str] = []
    answers: list[dict] = []

    async def edit_message_text(text, **kw):
        edits.append(text)

    async def answer(text=None, **kw):
        answers.append({"text": text, **kw})

    cq = types.SimpleNamespace(
        data=data, edit_message_text=edit_message_text, answer=answer
    )
    upd = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=chat_id),
        effective_user=user,
        callback_query=cq,
    )
    return upd, edits, answers


def _user(uid=42, username="ahmety", first="Ahmet", last="Yılmaz"):
    return types.SimpleNamespace(
        id=uid, username=username, first_name=first, last_name=last
    )


# --- /allow ---


async def test_allow_adds_chat(store):
    upd, replies = make_update(ADMIN)
    await access.allow_cmd(upd, make_ctx(make_settings(), store, args=["12345"]))
    assert await store.is_allowed(12345)
    assert "eklendi" in replies[0]


async def test_allow_is_admin_only(store):
    upd, replies = make_update(OUTSIDER)
    await access.allow_cmd(upd, make_ctx(make_settings(), store, args=["12345"]))
    assert not await store.is_allowed(12345)
    assert "yönetici" in replies[0]


async def test_allow_rejects_non_numeric(store):
    upd, replies = make_update(ADMIN)
    await access.allow_cmd(upd, make_ctx(make_settings(), store, args=["@ahmety"]))
    assert "Kullanım" in replies[0]
    assert await store.list_allowed() == set()


async def test_allow_reports_when_already_present(store):
    await store.add_allowed(12345)
    upd, replies = make_update(ADMIN)
    await access.allow_cmd(upd, make_ctx(make_settings(), store, args=["12345"]))
    assert "zaten" in replies[0]


async def test_allow_clears_any_pending_request(store):
    """Granting by hand settles the request too, so it stops showing up."""
    await store.create_access_request(12345, 42, "ahmety", "Ahmet")
    upd, _ = make_update(ADMIN)
    await access.allow_cmd(upd, make_ctx(make_settings(), store, args=["12345"]))
    assert await store.list_access_requests() == []


# --- /deny ---


async def test_deny_removes_chat(store):
    await store.add_allowed(12345)
    upd, replies = make_update(ADMIN)
    await access.deny_cmd(upd, make_ctx(make_settings(), store, args=["12345"]))
    assert not await store.is_allowed(12345)
    assert "çıkarıldı" in replies[0]


async def test_deny_refuses_env_entries(store):
    """Env-configured IDs are the deployment's, not the chat's, to revoke."""
    settings = make_settings(allowed_chat_ids=frozenset({777}))
    upd, replies = make_update(ADMIN)
    await access.deny_cmd(upd, make_ctx(settings, store, args=["777"]))
    assert ".env" in replies[0]


async def test_deny_warns_when_list_becomes_empty(store):
    await store.add_allowed(12345)
    upd, replies = make_update(ADMIN)
    await access.deny_cmd(upd, make_ctx(make_settings(), store, args=["12345"]))
    assert "herkese açık" in replies[0]


async def test_deny_does_not_warn_while_others_remain(store):
    await store.add_allowed(12345)
    await store.add_allowed(67890)
    upd, replies = make_update(ADMIN)
    await access.deny_cmd(upd, make_ctx(make_settings(), store, args=["12345"]))
    assert "herkese açık" not in replies[0]


# --- /allowed ---


async def test_allowed_lists_both_sources(store):
    await store.add_allowed(12345)
    settings = make_settings(allowed_chat_ids=frozenset({777}))
    upd, replies = make_update(ADMIN)
    await access.allowed_cmd(upd, make_ctx(settings, store))
    body = replies[0]
    assert "12345" in body and "777" in body
    assert ".env" in body  # the static one is marked


async def test_allowed_reports_open_bot(store):
    upd, replies = make_update(ADMIN)
    await access.allowed_cmd(upd, make_ctx(make_settings(), store))
    assert "herkese açık" in replies[0]


# --- request flow ---


async def test_request_records_and_notifies_admin(store):
    bot = _Bot()
    upd, edits, _ = make_callback(OUTSIDER, access.REQUEST_CB, user=_user())
    await access.request_access_cb(upd, make_ctx(make_settings(), store, bot=bot))

    pending = await store.list_access_requests()
    assert len(pending) == 1 and pending[0].chat_id == OUTSIDER
    assert "iletildi" in edits[0]

    to, text = bot.sent[0]
    assert to == ADMIN
    # The admin needs to see who is asking, not just a number.
    assert "Ahmet Yılmaz" in text and "@ahmety" in text
    assert str(OUTSIDER) in text and "42" in text


async def test_request_shows_placeholder_without_username(store):
    bot = _Bot()
    upd, _, _ = make_callback(OUTSIDER, access.REQUEST_CB, user=_user(username=None))
    await access.request_access_cb(upd, make_ctx(make_settings(), store, bot=bot))
    assert "kullanıcı adı yok" in bot.sent[0][1]


async def test_second_request_is_refused_while_one_is_pending(store):
    bot = _Bot()
    ctx = make_ctx(make_settings(), store, bot=bot)
    upd, _, _ = make_callback(OUTSIDER, access.REQUEST_CB, user=_user())
    await access.request_access_cb(upd, ctx)

    upd2, _, answers = make_callback(OUTSIDER, access.REQUEST_CB, user=_user())
    await access.request_access_cb(upd2, ctx)
    assert "bekleyen" in answers[0]["text"]
    assert len(bot.sent) == 1  # admin not pinged twice


async def test_approve_grants_access_and_tells_the_user(store):
    bot = _Bot()
    await store.create_access_request(OUTSIDER, 42, "ahmety", "Ahmet Yılmaz")
    upd, edits, _ = make_callback(ADMIN, f"{access.APPROVE_CB}{OUTSIDER}")
    await access.decide_cb(upd, make_ctx(make_settings(), store, bot=bot))

    assert await store.is_allowed(OUTSIDER)
    assert await store.list_access_requests() == []
    assert "Onaylandı" in edits[0]
    assert bot.sent[0][0] == OUTSIDER and "onaylandı" in bot.sent[0][1]


async def test_reject_denies_and_starts_cooldown(store):
    bot = _Bot()
    await store.create_access_request(OUTSIDER, 42, "ahmety", "Ahmet Yılmaz")
    upd, edits, _ = make_callback(ADMIN, f"{access.REJECT_CB}{OUTSIDER}")
    await access.decide_cb(upd, make_ctx(make_settings(), store, bot=bot))

    assert not await store.is_allowed(OUTSIDER)
    assert "Reddedildi" in edits[0]
    # Cooldown blocks an immediate retry.
    assert await store.create_access_request(OUTSIDER, 42, "ahmety", "Ahmet") is False


async def test_only_admin_can_decide(store):
    bot = _Bot()
    await store.create_access_request(OUTSIDER, 42, "ahmety", "Ahmet")
    upd, edits, answers = make_callback(12345, f"{access.APPROVE_CB}{OUTSIDER}")
    await access.decide_cb(upd, make_ctx(make_settings(), store, bot=bot))

    assert not await store.is_allowed(OUTSIDER)
    assert edits == []
    assert "yönetici" in answers[0]["text"]


async def test_decision_survives_user_blocking_the_bot(store):
    """The grant must stick even if we can't deliver the good news."""
    bot = _Bot(fail_for={OUTSIDER})
    await store.create_access_request(OUTSIDER, 42, "ahmety", "Ahmet")
    upd, edits, _ = make_callback(ADMIN, f"{access.APPROVE_CB}{OUTSIDER}")
    await access.decide_cb(upd, make_ctx(make_settings(), store, bot=bot))
    assert await store.is_allowed(OUTSIDER)
    assert "Onaylandı" in edits[0]


# --- /requests ---


async def test_requests_lists_pending(store):
    await store.create_access_request(OUTSIDER, 42, "ahmety", "Ahmet Yılmaz")
    upd, replies = make_update(ADMIN)
    await access.requests_cmd(upd, make_ctx(make_settings(), store))
    assert "Ahmet Yılmaz" in replies[0] and str(OUTSIDER) in replies[0]


async def test_requests_reports_empty(store):
    upd, replies = make_update(ADMIN)
    await access.requests_cmd(upd, make_ctx(make_settings(), store))
    assert "Bekleyen erişim talebi yok" in replies[0]


async def test_requests_is_admin_only(store):
    upd, replies = make_update(OUTSIDER)
    await access.requests_cmd(upd, make_ctx(make_settings(), store))
    assert "yönetici" in replies[0]
