from datetime import date

from tcdd_bot.handlers.common import (
    MAX_PASSENGERS,
    STATIC_ROUTES,
    _tr_date_label,
    date_picker_kb,
    passenger_picker_kb,
    route_picker_kb,
    train_picker_kb,
    trainmode_kb,
)


def test_tr_date_label_uses_turkish_weekdays():
    # 2024-01-01 is a Monday; 01-06 Saturday; 01-07 Sunday.
    assert _tr_date_label(date(2024, 1, 1)) == "Pzt 01.01"
    assert _tr_date_label(date(2024, 1, 6)) == "Cmt 06.01"
    assert _tr_date_label(date(2024, 1, 7)) == "Paz 07.01"
    assert _tr_date_label(date(2024, 1, 3)) == "Çar 03.01"


def test_date_picker_has_no_english_weekday():
    kb = date_picker_kb("s_d", days=7)
    buttons = [b for row in kb.inline_keyboard for b in row]
    day_btns = [b for b in buttons if ":date:" in b.callback_data]
    assert len(day_btns) == 8  # today + 7
    english = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    for b in day_btns:
        assert b.text.split()[0] not in english


def test_date_picker_marks_selected_and_has_confirm():
    from datetime import date, timedelta

    d1 = date.today().isoformat()
    d2 = (date.today() + timedelta(days=2)).isoformat()
    kb = date_picker_kb("s_d", {d1, d2}, days=7)
    buttons = [b for row in kb.inline_keyboard for b in row]
    # exactly the two selected day-buttons carry the ✅ mark
    checked = [b for b in buttons if b.text.startswith("✅") and ":date:" in b.callback_data]
    assert len(checked) == 2
    # a single confirm button exists with the datedone callback and the count
    confirm = [b for b in buttons if b.callback_data == "s_d:datedone"]
    assert len(confirm) == 1
    assert "2" in confirm[0].text


def test_passenger_picker_caps_at_four_and_is_two_per_row():
    kb = passenger_picker_kb("a_p")
    assert MAX_PASSENGERS == 4
    # 2x2 grid: two rows of two (wide buttons), values 1..4 only.
    assert [len(r) for r in kb.inline_keyboard] == [2, 2]
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert [b.callback_data for b in buttons] == [f"a_p:pax:{n}" for n in (1, 2, 3, 4)]
    assert all(b.text.endswith("yolcu") for b in buttons)


def test_route_picker_offers_preset_routes():
    kb = route_picker_kb("a")
    rows = kb.inline_keyboard
    # one full-width button per static route
    assert len(rows) == len(STATIC_ROUTES)
    assert all(len(r) == 1 for r in rows)
    for idx, row in enumerate(rows):
        assert row[0].callback_data == f"a_route:{idx}"
    # both directions of the İstanbul<->Eskişehir trip are present
    labels = " | ".join(r[0].text for r in rows)
    assert "Eskişehir" in labels and "Söğütlüçeşme" in labels


def test_trainmode_kb_has_all_and_pick():
    kb = trainmode_kb("a")
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert data == ["a_tm:all", "a_tm:pick"]


def test_train_picker_marks_selected_and_has_confirm():
    options = [("12002", "12002 · 22:47 · dolu"), ("81002", "81002 · 08:00 · 5 koltuk")]
    kb = train_picker_kb("a", options, {"12002"})
    buttons = [b for row in kb.inline_keyboard for b in row]
    train_btns = [b for b in buttons if ":train:" in b.callback_data]
    assert [b.callback_data for b in train_btns] == ["a_t:train:12002", "a_t:train:81002"]
    checked = [b for b in train_btns if b.text.startswith("✅")]
    assert len(checked) == 1 and "12002" in checked[0].text
    confirm = [b for b in buttons if b.callback_data == "a_t:traindone"]
    assert len(confirm) == 1 and "1" in confirm[0].text
