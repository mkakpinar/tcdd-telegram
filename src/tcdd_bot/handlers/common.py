"""Shared inline keyboards used by /search and /alarm."""

from __future__ import annotations

from datetime import date, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..stations import Station

# Turkish weekday abbreviations (Mon..Sun). strftime("%a") would emit English
# under the container's C locale, so we map by weekday() instead.
_TR_GUNLER = ("Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz")


def _tr_date_label(d: date) -> str:
    return f"{_TR_GUNLER[d.weekday()]} {d.strftime('%d.%m')}"


def station_picker_kb(stations: list[Station], prefix: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(s.name, callback_data=f"{prefix}:station:{s.id}")]
        for s in stations
    ]
    return InlineKeyboardMarkup(rows)


def date_picker_kb(
    prefix: str, selected: "set[str] | None" = None, days: int = 14
) -> InlineKeyboardMarkup:
    """Multi-select day picker. `selected` is the set of already-chosen ISO
    dates (shown with a ✅). A trailing 'Onayla' button confirms the choice."""
    chosen = set(selected or ())
    today = date.today()
    rows = []
    row = []
    for i in range(days + 1):
        d = today + timedelta(days=i)
        iso = d.isoformat()
        label = ("✅ " if iso in chosen else "") + _tr_date_label(d)
        row.append(
            InlineKeyboardButton(label, callback_data=f"{prefix}:date:{iso}")
        )
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    confirm = f"✅ Onayla ({len(chosen)})" if chosen else "Onayla"
    rows.append([InlineKeyboardButton(confirm, callback_data=f"{prefix}:datedone")])
    return InlineKeyboardMarkup(rows)


def passenger_picker_kb(prefix: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(str(n), callback_data=f"{prefix}:pax:{n}")
            for n in (1, 2, 3, 4, 5, 6)
        ]
    ]
    return InlineKeyboardMarkup(rows)
