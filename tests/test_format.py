from datetime import date, datetime

from tcdd_bot import format as fmt
from tcdd_bot.store import Alarm
from tcdd_bot.tcdd import TrainResult


def _train(no="12345", seats=5):
    return TrainResult(
        train_no=no,
        departure_time=datetime(2026, 7, 5, 8, 30),
        arrival_time=datetime(2026, 7, 5, 12, 0),
        available_seats=seats,
        cabin_breakdown={"EKONOMİ": seats},
    )


def test_deeplink_encodes_params():
    link = fmt.tcdd_deeplink("İstanbul (Söğütlüçeşme)", "Ankara", date(2026, 7, 5))
    assert link.startswith("https://ebilet.tcddtasimacilik.gov.tr/sefer-listesi?")
    assert "trtarih=05.07.2026" in link
    assert " " not in link  # spaces percent-encoded


def test_render_search_results_with_matches():
    out = fmt.render_search_results("A", "B", date(2026, 7, 5), 2, [_train(seats=5)])
    assert "A" in out and "B" in out
    assert "12345" in out
    assert "08:30" in out and "12:00" in out
    assert "sefer-listesi" in out
    # cabin breakdown reads "<count> <name>" (Turkish-natural), not "<name> <count>"
    assert "5 EKONOMİ" in out


def test_render_search_results_filters_insufficient_seats():
    out = fmt.render_search_results("A", "B", date(2026, 7, 5), 4, [_train(seats=2)])
    assert "Uygun boş koltuk yok" in out


def test_render_search_results_caps_at_ten():
    trains = [_train(no=str(i), seats=5) for i in range(20)]
    out = fmt.render_search_results("A", "B", date(2026, 7, 5), 1, trains)
    assert out.count("\n•") == 10


def _alarm(travel_dates=None):
    return Alarm(
        id="abc123", chat_id=1, from_id=10, to_id=20, from_name="A", to_name="B",
        travel_dates=travel_dates or [date(2026, 7, 5)], passengers=2, active=True,
        created_at=datetime(2026, 6, 1), last_alerted_at=None,
    )


def test_render_alert():
    out = fmt.render_alert(_alarm(), date(2026, 7, 5), [_train()])
    assert "BOŞ YER BULUNDU" in out
    assert "12345" in out
    assert "Hemen bilet al" in out


def test_render_alarm_list_empty():
    assert "Hiç aktif" in fmt.render_alarm_list([])


def test_render_alarm_list_shows_each_alarm():
    out = fmt.render_alarm_list([_alarm()])
    assert "abc123" in out
    assert "A → B" in out
    assert "05.07" in out


def test_render_alarm_list_shows_multiple_dates():
    out = fmt.render_alarm_list([_alarm([date(2026, 7, 5), date(2026, 7, 7)])])
    assert "05.07" in out and "07.07" in out


def test_render_alarm_list_shows_target_trains():
    a = Alarm(
        id="abc123", chat_id=1, from_id=10, to_id=20, from_name="A", to_name="B",
        travel_dates=[date(2026, 7, 5)], passengers=2, active=True,
        created_at=datetime(2026, 6, 1), last_alerted_at=None,
        target_trains={"12002", "12004"},
    )
    out = fmt.render_alarm_list([a])
    assert "🎯" in out and "12002" in out and "12004" in out


def test_render_alert_marks_targeted_alarm():
    a = Alarm(
        id="abc123", chat_id=1, from_id=10, to_id=20, from_name="A", to_name="B",
        travel_dates=[date(2026, 7, 5)], passengers=2, active=True,
        created_at=datetime(2026, 6, 1), last_alerted_at=None,
        target_trains={"12002"},
    )
    out = fmt.render_alert(a, date(2026, 7, 5), [_train()])
    assert "🎯" in out


def test_alarm_button_label_compact_with_extra_count():
    one = fmt.alarm_button_label(_alarm([date(2026, 7, 5)]))
    assert "05.07" in one and "+" not in one
    many = fmt.alarm_button_label(_alarm([date(2026, 7, 5), date(2026, 7, 7), date(2026, 7, 9)]))
    assert "05.07" in many and "+2" in many
