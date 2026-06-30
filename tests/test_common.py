from datetime import date

from tcdd_bot.handlers.common import _tr_date_label, date_picker_kb


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
