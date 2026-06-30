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
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert len(labels) == 8  # today + 7
    english = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    for label in labels:
        assert label.split()[0] not in english
