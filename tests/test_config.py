from tcdd_bot.config import _parse_chat_ids, load_settings


def test_parse_chat_ids_empty():
    assert _parse_chat_ids(None) == frozenset()
    assert _parse_chat_ids("") == frozenset()
    assert _parse_chat_ids("  ,  ") == frozenset()


def test_parse_chat_ids_values_and_whitespace():
    assert _parse_chat_ids("111, 222 ,333") == frozenset({111, 222, 333})
    assert _parse_chat_ids("-100123") == frozenset({-100123})  # group chat ids are negative


def test_load_settings_allowlist(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "x")
    monkeypatch.setenv("REDIS_URL", "redis://x")
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "111, 222")
    monkeypatch.setenv("ADMIN_CHAT_ID", "555")
    s = load_settings()
    assert s.allowed_chat_ids == frozenset({111, 222})
    assert s.admin_chat_id == 555

    monkeypatch.delenv("ALLOWED_CHAT_IDS")
    assert load_settings().allowed_chat_ids == frozenset()
