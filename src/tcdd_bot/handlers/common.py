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
    prefix: str, selected: set[str] | None = None, days: int = 14
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


MAX_PASSENGERS = 4


def passenger_picker_kb(prefix: str) -> InlineKeyboardMarkup:
    # 2 per row so each button is ~half-width (wide + easy to tap) rather than a
    # cramped single row. TCDD alarms cap at MAX_PASSENGERS passengers.
    nums = list(range(1, MAX_PASSENGERS + 1))
    rows = [
        [
            InlineKeyboardButton(f"{n} yolcu", callback_data=f"{prefix}:pax:{n}")
            for n in nums[i:i + 2]
        ]
        for i in range(0, len(nums), 2)
    ]
    return InlineKeyboardMarkup(rows)


# Common İstanbul <-> Eskişehir trips offered as one-tap shortcuts on "Nereden?".
# from_name/to_name are the exact TCDD catalog strings (search() sends them to the
# API as departureStationName/arrivalStationName); `label` is the friendly button
# text. (from_id, from_name, to_id, to_name, label)
STATIC_ROUTES: list[tuple[int, str, int, str, str]] = [
    (93, "ESKİŞEHİR", 1325, "İSTANBUL(SÖĞÜTLÜÇEŞME)",
     "Eskişehir → İstanbul (Söğütlüçeşme)"),
    (1325, "İSTANBUL(SÖĞÜTLÜÇEŞME)", 93, "ESKİŞEHİR",
     "İstanbul (Söğütlüçeşme) → Eskişehir"),
]


def route_picker_kb(prefix: str) -> InlineKeyboardMarkup:
    """One full-width button per preset route; callback carries the STATIC_ROUTES
    index (`{prefix}_route:{idx}`)."""
    rows = [
        [InlineKeyboardButton(f"🚄 {label}", callback_data=f"{prefix}_route:{idx}")]
        for idx, (_, _, _, _, label) in enumerate(STATIC_ROUTES)
    ]
    return InlineKeyboardMarkup(rows)


def trainmode_kb(prefix: str) -> InlineKeyboardMarkup:
    """Ask whether the alarm watches all trains or specific ones."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔔 Tüm trenler", callback_data=f"{prefix}_tm:all")],
            [InlineKeyboardButton("🎯 Belirli tren seç", callback_data=f"{prefix}_tm:pick")],
        ]
    )


def train_picker_kb(
    prefix: str, options: list[tuple[str, str]], selected: set[str] | None = None
) -> InlineKeyboardMarkup:
    """Multi-select train picker. `options` is [(train_no, label)]; `selected` is the
    set of chosen train numbers (shown with ✅). A trailing 'Onayla' confirms."""
    chosen = set(selected or ())
    rows = [
        [
            InlineKeyboardButton(
                ("✅ " if train_no in chosen else "") + label,
                callback_data=f"{prefix}_t:train:{train_no}",
            )
        ]
        for train_no, label in options
    ]
    confirm = f"✅ Onayla ({len(chosen)})" if chosen else "Onayla"
    rows.append([InlineKeyboardButton(confirm, callback_data=f"{prefix}_t:traindone")])
    return InlineKeyboardMarkup(rows)
