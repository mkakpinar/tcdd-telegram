from datetime import date, timedelta


async def _mk(store, chat_id=42, days=5):
    return await store.create_alarm(
        chat_id, 1, 2, "A", "B", [date.today() + timedelta(days=days)], 2
    )


async def test_create_alarm_with_multiple_dates(store):
    d1 = date.today() + timedelta(days=2)
    d2 = date.today() + timedelta(days=5)
    aid = await store.create_alarm(42, 1, 2, "A", "B", [d2, d1], 1)  # unsorted in
    a = await store.get_alarm(aid)
    assert a.travel_dates == [d1, d2]  # stored sorted


async def test_get_alarm_backward_compat_single_date(store):
    # An alarm written under the old schema (single travel_date field).
    old = date.today() + timedelta(days=3)
    await store.r.hset("alarm:legacy1", mapping={
        "chat_id": "42", "from_id": "1", "to_id": "2",
        "from_name": "A", "to_name": "B",
        "travel_date": old.isoformat(), "passengers": "1",
        "active": "1", "created_at": "", "last_alerted_at": "",
    })
    a = await store.get_alarm("legacy1")
    assert a.travel_dates == [old]


async def test_upsert_user_preserves_created_at(store):
    await store.upsert_user(42, "old")
    created = await store.r.hget("user:42", "created_at")
    await store.upsert_user(42, "new")
    assert await store.r.hget("user:42", "created_at") == created  # unchanged
    assert await store.r.hget("user:42", "username") == "new"
    assert await store.is_paused(42) is False


async def test_create_list_get_delete_alarm(store):
    aid = await _mk(store)
    alarms = await store.list_user_alarms(42)
    assert len(alarms) == 1 and alarms[0].id == aid
    got = await store.get_alarm(aid)
    assert got.from_name == "A" and got.passengers == 2 and got.active is True
    assert aid in await store.active_alarm_ids()

    await store.delete_alarm(aid)
    assert await store.get_alarm(aid) is None
    assert await store.list_user_alarms(42) == []
    assert aid not in await store.active_alarm_ids()


async def test_count_active_alarms(store):
    await _mk(store, days=5)
    await _mk(store, days=6)
    assert await store.count_active_alarms(42) == 2


async def test_pause_resume_toggles_active_set(store):
    aid = await _mk(store)
    await store.set_paused(42, True)
    assert await store.is_paused(42) is True
    assert await store.active_alarm_ids() == []
    await store.set_paused(42, False)
    assert await store.is_paused(42) is False
    assert aid in await store.active_alarm_ids()


async def test_rate_limit_sliding_window(store):
    results = [await store.check_search_rate(42, 3) for _ in range(5)]
    assert results == [True, True, True, False, False]
    # a different user is independent
    assert await store.check_search_rate(99, 3) is True


async def test_notified_dedupe(store):
    aid = await _mk(store)
    assert await store.already_notified(aid) == set()
    await store.mark_alerted(aid, ["81002", "81032"])
    assert await store.already_notified(aid) == {"81002", "81032"}
    await store.mark_alerted(aid, ["81002", "90000"])
    assert await store.already_notified(aid) == {"81002", "81032", "90000"}


async def test_clear_user_alarms(store):
    await _mk(store, days=5)
    await _mk(store, days=6)
    n = await store.clear_user_alarms(42)
    assert n == 2
    assert await store.list_user_alarms(42) == []
    assert await store.active_alarm_ids() == []
