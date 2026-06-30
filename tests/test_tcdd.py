from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from tcdd_bot.tcdd import (
    StubBackend,
    _epoch_ms_to_dt,
    _parse_response,
    build_backend,
)


def _train(number, dep_ms, arr_ms, cabins):
    """cabins: list of (name, count)."""
    return {
        "number": number,
        "segments": [{"departureTime": dep_ms}, {"arrivalTime": arr_ms}],
        "availableFareInfo": [
            {"cabinClasses": [
                {"cabinClass": {"name": n}, "availabilityCount": c} for n, c in cabins
            ]}
        ],
    }


def _wrap(trains):
    return {"trainLegs": [{"trainAvailabilities": [{"trains": trains}]}]}


def test_parse_excludes_wheelchair_and_disallowed_cabins():
    data = _wrap([_train("1", 1000, 2000, [
        ("EKONOMİ", 5),
        ("TEKERLEKLİ SANDALYE", 3),
        ("BUSİNESS", 2),
        ("ENGELLİ", 4),
    ])])
    res = _parse_response(data)
    assert len(res) == 1
    assert res[0].cabin_breakdown == {"EKONOMİ": 5, "BUSİNESS": 2}
    assert res[0].available_seats == 7


def test_parse_drops_zero_count_and_empty_trains():
    data = _wrap([
        _train("zero", 1000, 2000, [("EKONOMİ", 0)]),
        _train("good", 3000, 4000, [("EKONOMİ", 1)]),
    ])
    res = _parse_response(data)
    assert [t.train_no for t in res] == ["good"]


def test_parse_skips_trains_without_segments():
    data = _wrap([{"number": "x", "segments": [],
                   "availableFareInfo": [{"cabinClasses": [
                       {"cabinClass": {"name": "EKONOMİ"}, "availabilityCount": 5}]}]}])
    assert _parse_response(data) == []


def test_parse_sorts_by_departure():
    data = _wrap([
        _train("late", 5000, 6000, [("EKONOMİ", 1)]),
        _train("early", 1000, 2000, [("EKONOMİ", 1)]),
    ])
    assert [t.train_no for t in _parse_response(data)] == ["early", "late"]


def test_parse_empty_payload():
    assert _parse_response({}) == []
    assert _parse_response({"trainLegs": None}) == []


def test_epoch_conversion_is_naive_istanbul():
    ms = 1700000000000
    expected = (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        .astimezone(ZoneInfo("Europe/Istanbul"))
        .replace(tzinfo=None)
    )
    got = _epoch_ms_to_dt(ms)
    assert got == expected
    assert got.tzinfo is None


def test_epoch_conversion_none():
    assert _epoch_ms_to_dt(None) == datetime.min
    assert _epoch_ms_to_dt(0) == datetime.min


def test_build_backend_stub():
    assert isinstance(build_backend("stub"), StubBackend)
    assert isinstance(build_backend("anything-not-live"), StubBackend)


async def test_stub_backend_deterministic_and_wheelchair_free():
    b = StubBackend()
    r1 = await b.search(1, 2, date(2026, 7, 5), 1, "A", "B")
    r2 = await b.search(1, 2, date(2026, 7, 5), 1, "A", "B")
    assert [t.train_no for t in r1] == [t.train_no for t in r2]  # seeded RNG
    for t in r1:
        assert "TEKERLEKLİ SANDALYE" not in t.cabin_breakdown
        assert t.available_seats == sum(t.cabin_breakdown.values())
