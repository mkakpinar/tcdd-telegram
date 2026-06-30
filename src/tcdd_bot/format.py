"""Render search results, alerts, and alarm lists into Telegram messages."""

from __future__ import annotations

from datetime import date
from urllib.parse import quote

from .store import Alarm
from .tcdd import TrainResult


def tcdd_deeplink(from_name: str, to_name: str, day: date) -> str:
    # ebilet.tcddtasimacilik.gov.tr accepts route params via the search page
    q = (
        f"nereden={quote(from_name)}"
        f"&nereye={quote(to_name)}"
        f"&trtarih={day.strftime('%d.%m.%Y')}"
    )
    return f"https://ebilet.tcddtasimacilik.gov.tr/sefer-listesi?{q}"


def render_search_results(
    from_name: str,
    to_name: str,
    day: date,
    passengers: int,
    trains: list[TrainResult],
) -> str:
    link = tcdd_deeplink(from_name, to_name, day)
    header = (
        f"🚂 *{from_name}* → *{to_name}*\n"
        f"📅 {day.strftime('%d.%m.%Y')}  ·  👥 {passengers} yolcu\n"
    )
    matching = [t for t in trains if t.available_seats >= passengers]
    if not matching:
        return header + f"\n❌ Uygun boş koltuk yok.\n\n🔗 {link}"
    lines = [header]
    for t in matching[:10]:
        cabins = ", ".join(f"{count} {name}" for name, count in t.cabin_breakdown.items())
        lines.append(
            f"• {t.train_no} · {t.departure_time.strftime('%H:%M')}"
            f" → {t.arrival_time.strftime('%H:%M')}"
            f" · {t.available_seats} koltuk ({cabins})"
        )
    lines.append(f"\n🔗 {link}")
    return "\n".join(lines)


def render_alert(alarm: Alarm, day: date, new_trains: list[TrainResult]) -> str:
    link = tcdd_deeplink(alarm.from_name, alarm.to_name, day)
    lines = [
        "🚨 *BOŞ YER BULUNDU!*",
        f"🚂 {alarm.from_name} → {alarm.to_name}",
        f"📅 {day.strftime('%d.%m.%Y')}  ·  👥 {alarm.passengers} yolcu",
        "",
    ]
    for t in new_trains[:10]:
        cabins = ", ".join(f"{count} {name}" for name, count in t.cabin_breakdown.items())
        lines.append(
            f"• {t.train_no} · {t.departure_time.strftime('%H:%M')}"
            f" · {t.available_seats} koltuk ({cabins})"
        )
    lines += ["", f"🔗 {link}", "", "Hemen bilet al! 🎫"]
    return "\n".join(lines)


def render_alarm_list(alarms: list[Alarm]) -> str:
    if not alarms:
        return "Hiç aktif alarmın yok. /alarm ile yeni alarm kur."
    lines = ["🔔 *Aktif alarmların:*", ""]
    for a in alarms:
        status = "▶️" if a.active else "⏸"
        lines.append(
            f"{status} `{a.id}` · {a.from_name} → {a.to_name}"
            f" · {a.travel_date.strftime('%d.%m.%Y')} · {a.passengers} yolcu"
        )
    lines += ["", "Bir alarmı iptal etmek için aşağıdaki butona bas."]
    return "\n".join(lines)
