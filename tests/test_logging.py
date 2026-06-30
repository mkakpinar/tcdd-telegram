import logging

from tcdd_bot.main import configure_logging


def test_configure_logging_silences_httpx_token_leak():
    # Pretend httpx was chatty before configuration.
    logging.getLogger("httpx").setLevel(logging.INFO)
    configure_logging("INFO")
    # httpx must be pinned to WARNING so request URLs (which embed the bot
    # token) never get logged.
    assert logging.getLogger("httpx").level == logging.WARNING
