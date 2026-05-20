"""Shared inline keyboards used by /search and /alarm."""

from __future__ import annotations

from datetime import date, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..stations import Station


def station_picker_kb(stations: list[Station], prefix: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(s.name, callback_data=f"{prefix}:station:{s.id}")]
        for s in stations
    ]
    return InlineKeyboardMarkup(rows)


def date_picker_kb(prefix: str, days: int = 14) -> InlineKeyboardMarkup:
    today = date.today()
    rows = []
    row = []
    for i in range(days + 1):
        d = today + timedelta(days=i)
        label = d.strftime("%a %d.%m")
        row.append(
            InlineKeyboardButton(label, callback_data=f"{prefix}:date:{d.isoformat()}")
        )
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def passenger_picker_kb(prefix: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(str(n), callback_data=f"{prefix}:pax:{n}")
            for n in (1, 2, 3, 4, 5, 6)
        ]
    ]
    return InlineKeyboardMarkup(rows)
