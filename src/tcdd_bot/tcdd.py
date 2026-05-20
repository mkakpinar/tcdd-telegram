"""TCDD search client.

Two backends behind one interface:
- StubBackend: deterministic fake trains for local development. No network.
- LiveBackend: hits TCDD's internal JSON API. Currently blocked by the WAF
  in front of web-api-prod-ytp; flip TCDD_MODE=live once the recon is unstuck.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

import httpx

log = logging.getLogger(__name__)

TMS_BASE = "https://web-api-prod-ytp.tcddtasimacilik.gov.tr/tms"
SEARCH_PATH = "/train/train-availability"
WHEELCHAIR_CABIN_KEYWORDS = ("ENGELLI", "TEKERLEKLI", "WHEELCHAIR")
ALLOWED_CABIN_KEYWORDS = ("EKONOMI", "BUSINESS", "EKONOMİ", "BUSİNESS")


@dataclass(frozen=True)
class TrainResult:
    train_no: str
    departure_time: datetime
    arrival_time: datetime
    available_seats: int
    cabin_breakdown: dict[str, int]  # cabin name -> seats, wheelchair excluded


class TcddBackend(Protocol):
    async def search(
        self, from_id: int, to_id: int, day: date, passengers: int
    ) -> list[TrainResult]: ...


class StubBackend:
    """Returns deterministic fake trains so the bot can be developed end-to-end
    before the live API is wired up."""

    async def search(
        self, from_id: int, to_id: int, day: date, passengers: int
    ) -> list[TrainResult]:
        await asyncio.sleep(0.2)
        # Seed RNG by route+date so results are stable across calls
        rng = random.Random(f"{from_id}-{to_id}-{day.isoformat()}")
        out: list[TrainResult] = []
        for i in range(5):
            hour = 6 + i * 3
            dep = datetime.combine(day, datetime.min.time()).replace(hour=hour)
            travel_h = rng.randint(2, 6)
            eco = rng.choice([0, 0, 2, 8, 24])
            bus = rng.choice([0, 1, 4, 12])
            cabins: dict[str, int] = {}
            if eco:
                cabins["EKONOMİ"] = eco
            if bus:
                cabins["BUSİNESS"] = bus
            out.append(
                TrainResult(
                    train_no=f"YHT{rng.randint(10000, 99999)}",
                    departure_time=dep,
                    arrival_time=dep.replace(hour=(hour + travel_h) % 24),
                    available_seats=eco + bus,
                    cabin_breakdown=cabins,
                )
            )
        return out


class LiveBackend:
    """Real TCDD API. NOTE: currently the search endpoint returns 403 from the
    edge — additional headers / cookies / TLS fingerprint required. See
    `docs/api-recon.md` once we crack it."""

    def __init__(self, bearer_token: str, unit_id: int = 3895):
        self._client = httpx.AsyncClient(
            base_url=TMS_BASE,
            timeout=15.0,
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "unit-id": str(unit_id),
                "Content-Type": "application/json",
                "Origin": "https://ebilet.tcddtasimacilik.gov.tr",
                "Referer": "https://ebilet.tcddtasimacilik.gov.tr/",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(
        self, from_id: int, to_id: int, day: date, passengers: int
    ) -> list[TrainResult]:
        # Payload shape inferred from JS bundle; refine after first successful call.
        payload = {
            "searchRoutes": [
                {
                    "departureStationId": from_id,
                    "arrivalStationId": to_id,
                    "departureDate": day.strftime("%b %d, %Y 00:00:00 AM"),
                }
            ],
            "passengerTypeCounts": [{"id": 0, "count": passengers}],
            "searchReservation": False,
        }
        r = await self._client.post(SEARCH_PATH, json=payload)
        if r.status_code >= 400:
            log.warning("TCDD search %s -> %s", r.status_code, r.text[:200])
            r.raise_for_status()
        return _parse_search_response(r.json())


def _parse_search_response(data: dict) -> list[TrainResult]:
    """Map raw TCDD response to TrainResult list. Wheelchair cabins excluded.

    The exact response shape is still TBD until we can make a real call. This
    function will need adjustment then — keep all changes in this one place."""
    out: list[TrainResult] = []
    for leg in data.get("trainLegs", []):
        for train in leg.get("trainAvailabilities", []):
            train_no = str(train.get("trainNumber") or train.get("trainId") or "?")
            cabins: dict[str, int] = {}
            for cabin in train.get("cabinClassAvailabilities", []):
                name = (cabin.get("cabinClass", {}).get("name") or "").upper()
                avail = int(cabin.get("availabilityCount") or 0)
                if any(k in name for k in WHEELCHAIR_CABIN_KEYWORDS):
                    continue
                if not any(k in name for k in ALLOWED_CABIN_KEYWORDS):
                    continue
                if avail > 0:
                    cabins[name] = avail
            if not cabins:
                continue
            out.append(
                TrainResult(
                    train_no=train_no,
                    departure_time=_iso(train.get("departureTime")),
                    arrival_time=_iso(train.get("arrivalTime")),
                    available_seats=sum(cabins.values()),
                    cabin_breakdown=cabins,
                )
            )
    return out


def _iso(s: str | None) -> datetime:
    if not s:
        return datetime.min
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min


def build_backend(mode: str) -> TcddBackend:
    if mode == "live":
        import os

        token = os.environ.get("TCDD_BEARER_TOKEN", "")
        if not token:
            log.warning("TCDD_MODE=live but TCDD_BEARER_TOKEN missing — using stub")
            return StubBackend()
        return LiveBackend(bearer_token=token)
    return StubBackend()
