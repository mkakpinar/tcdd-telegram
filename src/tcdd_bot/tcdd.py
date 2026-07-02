"""TCDD search client.

The TCDD edge (web-api-prod-ytp.tcddtasimacilik.gov.tr) ja3-fingerprints
clients — Python's default TLS stack gets 403. We use curl_cffi with a
chrome120 impersonation profile to mimic real Chrome.

Two backends behind one interface:
- StubBackend: deterministic fake trains for local development.
- LiveBackend: real TCDD POST /tms/train/train-availability.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")
from typing import Protocol

log = logging.getLogger(__name__)

TMS_URL = (
    "https://web-api-prod-ytp.tcddtasimacilik.gov.tr"
    "/tms/train/train-availability?environment=dev&userId=1"
)

MAX_SEARCH_ATTEMPTS = 3

# JWT extracted from the TCDD production JS bundle. The exp claim is in the
# past, but the TCDD gateway doesn't validate it — the page in production
# uses the same hardcoded token. If TCDD ever rotates it, re-extract from
# https://ebilet.tcddtasimacilik.gov.tr/js/index~c92480b7.*.js (`case"TCDD-PROD"`).
TCDD_BAKED_TOKEN = (
    "eyJhbGciOiJSUzI1NiIsInR5cCIgOiAiSldUIiwia2lkIiA6ICJlVFFicDhDMmpiakp1cnUzQVk2a0ZnV196U29MQXZIMmJ5bTJ2OUg5THhRIn0"
    ".eyJleHAiOjE3MjEzODQ0NzAsImlhdCI6MTcyMTM4NDQxMCwianRpIjoiYWFlNjVkNzgtNmRkZS00ZGY4LWEwZWYtYjRkNzZiYjZlODNjIiwiaXNzIjoiaHR0cDovL3l0cC1wcm9kLW1hc3RlcjEudGNkZHRhc2ltYWNpbGlrLmdvdi50cjo4MDgwL3JlYWxtcy9tYXN0ZXIiLCJhdWQiOiJhY2NvdW50Iiwic3ViIjoiMDAzNDI3MmMtNTc2Yi00OTBlLWJhOTgtNTFkMzc1NWNhYjA3IiwidHlwIjoiQmVhcmVyIiwiYXpwIjoidG1zIiwic2Vzc2lvbl9zdGF0ZSI6IjAwYzM4NTJiLTg1YjEtNDMxNS04OGIwLWQ0MWMxMTcyYzA0MSIsImFjciI6IjEiLCJyZWFsbV9hY2Nlc3MiOnsicm9sZXMiOlsiZGVmYXVsdC1yb2xlcy1tYXN0ZXIiLCJvZmZsaW5lX2FjY2VzcyIsInVtYV9hdXRob3JpemF0aW9uIl19LCJyZXNvdXJjZV9hY2Nlc3MiOnsiYWNjb3VudCI6eyJyb2xlcyI6WyJtYW5hZ2UtYWNjb3VudCIsIm1hbmFnZS1hY2NvdW50LWxpbmtzIiwidmlldy1wcm9maWxlIl19fSwic2NvcGUiOiJvcGVuaWQgZW1haWwgcHJvZmlsZSIsInNpZCI6IjAwYzM4NTJiLTg1YjEtNDMxNS04OGIwLWQ0MWMxMTcyYzA0MSIsImVtYWlsX3ZlcmlmaWVkIjpmYWxzZSwicHJlZmVycmVkX3VzZXJuYW1lIjoid2ViIiwiZ2l2ZW5fbmFtZSI6IiIsImZhbWlseV9uYW1lIjoiIn0"
    ".AIW_4Qws2wfwxyVg8dgHRT9jB3qNavob2C4mEQIQGl3urzW2jALPx-e51ZwHUb-TXB-X2RPHakonxKnWG6tDIP5aKhiidzXDcr6pDDoYU5DnQhMg1kywyOaMXsjLFjuYN5PAyGUMh6YSOVsg1PzNh-5GrJF44pS47JnB9zk03Pr08napjsZPoRB-5N4GQ49cnx7ePC82Y7YIc-gTew2baqKQPz9_v381Gbm2V38PZDH9KldlcWut7kqQYJFMJ7dkM_entPJn9lFk7R5h5j_06OlQEpWRMQTn9SQ1AYxxmZxBu5XYMKDkn4rzIIVCkdTPJNCt5PvjENjClKFeUA1DOg"
)

# Cabin names we care about. TCDD also returns "TEKERLEKLİ SANDALYE" (wheelchair)
# which must be excluded from any "seats available" count.
ALLOWED_CABIN_NAMES = {"EKONOMİ", "BUSİNESS"}
WHEELCHAIR_CABIN_NAMES = {"TEKERLEKLİ SANDALYE", "ENGELLİ"}


@dataclass(frozen=True)
class TrainResult:
    train_no: str
    departure_time: datetime
    arrival_time: datetime
    available_seats: int
    cabin_breakdown: dict[str, int]  # cabin name -> seats, wheelchair excluded


class TcddBackend(Protocol):
    async def search(
        self,
        from_id: int,
        to_id: int,
        day: date,
        passengers: int,
        from_name: str = "",
        to_name: str = "",
        include_unavailable: bool = False,
    ) -> list[TrainResult]: ...


class StubBackend:
    """Deterministic fake trains for local development."""

    async def search(
        self,
        from_id: int,
        to_id: int,
        day: date,
        passengers: int,
        from_name: str = "",
        to_name: str = "",
        include_unavailable: bool = False,
    ) -> list[TrainResult]:
        await asyncio.sleep(0.2)
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
    """Real TCDD API via curl_cffi (chrome120 impersonation to defeat WAF ja3)."""

    def __init__(self, bearer_token: str | None = None, unit_id: int = 3895):
        from curl_cffi.requests import AsyncSession

        self._session = AsyncSession(impersonate="chrome120", timeout=20.0)
        self._token = bearer_token or TCDD_BAKED_TOKEN
        self._unit_id = str(unit_id)

    async def aclose(self) -> None:
        await self._session.close()

    async def search(
        self,
        from_id: int,
        to_id: int,
        day: date,
        passengers: int,
        from_name: str = "",
        to_name: str = "",
        include_unavailable: bool = False,
    ) -> list[TrainResult]:
        # TCDD wants DD-MM-YYYY HH:MM:SS — keep the time at 00:00:00, the
        # response returns trains for the whole day.
        dep_str = day.strftime("%d-%m-%Y") + " 00:00:00"
        payload = {
            "searchRoutes": [
                {
                    "departureStationId": from_id,
                    "departureStationName": from_name,
                    "arrivalStationId": to_id,
                    "arrivalStationName": to_name,
                    "departureDate": dep_str,
                }
            ],
            "passengerTypeCounts": [{"id": 0, "count": passengers}],
            "searchReservation": False,
            "searchType": "DOMESTIC",
            "blTrainTypes": ["TURISTIK_TREN"],
        }
        headers = {
            "authorization": self._token,
            "unit-id": self._unit_id,
            "accept-language": "tr",
            "content-type": "application/json",
            "accept": "application/json, text/plain, */*",
            "origin": "https://ebilet.tcddtasimacilik.gov.tr",
            "referer": "https://ebilet.tcddtasimacilik.gov.tr/",
        }
        # TCDD's edge is flaky (WAF blips, timeouts, 5xx). Retry transient
        # failures with exponential backoff; surface client errors (4xx) at once.
        last_exc: Exception | None = None
        for attempt in range(1, MAX_SEARCH_ATTEMPTS + 1):
            try:
                r = await self._session.post(TMS_URL, json=payload, headers=headers)
            except Exception as exc:  # network / timeout / TLS
                last_exc = exc
                log.warning(
                    "TCDD search attempt %d/%d errored: %r",
                    attempt, MAX_SEARCH_ATTEMPTS, exc,
                )
            else:
                if r.status_code < 400:
                    return _parse_response(r.json(), include_unavailable)
                if r.status_code < 500 and r.status_code != 429:
                    log.warning("TCDD search %s -> %s", r.status_code, r.text[:200])
                    raise RuntimeError(f"TCDD HTTP {r.status_code}")
                last_exc = RuntimeError(f"TCDD HTTP {r.status_code}")
                log.warning(
                    "TCDD search attempt %d/%d -> HTTP %s",
                    attempt, MAX_SEARCH_ATTEMPTS, r.status_code,
                )
            if attempt < MAX_SEARCH_ATTEMPTS:
                await asyncio.sleep(min(8.0, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.5))
        raise last_exc or RuntimeError("TCDD search failed")


def _parse_response(data: dict, include_unavailable: bool = False) -> list[TrainResult]:
    """Map raw response into TrainResult list. Wheelchair cabin excluded.

    By default only trains with available seats are returned. Pass
    include_unavailable=True to also return sold-out trains (available_seats=0,
    empty cabin_breakdown) — used by the alarm's train picker, where the user
    wants to watch a currently-full train.
    """
    out: list[TrainResult] = []
    for leg in data.get("trainLegs") or []:
        for av in leg.get("trainAvailabilities") or []:
            for train in av.get("trains") or []:
                cabins: dict[str, int] = {}
                for fare in train.get("availableFareInfo") or []:
                    for cabin in fare.get("cabinClasses") or []:
                        name = (cabin.get("cabinClass") or {}).get("name") or ""
                        name_u = name.upper()
                        count = int(cabin.get("availabilityCount") or 0)
                        if name_u in WHEELCHAIR_CABIN_NAMES:
                            continue
                        if name_u not in ALLOWED_CABIN_NAMES:
                            continue
                        if count <= 0:
                            continue
                        cabins[name] = cabins.get(name, 0) + count
                if not cabins and not include_unavailable:
                    continue
                segments = train.get("segments") or []
                if not segments:
                    continue
                dep_ms = segments[0].get("departureTime")
                arr_ms = segments[-1].get("arrivalTime")
                out.append(
                    TrainResult(
                        train_no=str(train.get("number") or train.get("id") or "?"),
                        departure_time=_epoch_ms_to_dt(dep_ms),
                        arrival_time=_epoch_ms_to_dt(arr_ms),
                        available_seats=sum(cabins.values()),
                        cabin_breakdown=cabins,
                    )
                )
    out.sort(key=lambda t: t.departure_time)
    return out


def _epoch_ms_to_dt(ms: int | None) -> datetime:
    if not ms:
        return datetime.min
    # TCDD returns true UTC epoch ms; render in Europe/Istanbul wall-clock.
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        .astimezone(ISTANBUL_TZ)
        .replace(tzinfo=None)
    )


def build_backend(mode: str) -> TcddBackend:
    if mode == "live":
        try:
            return LiveBackend()
        except ImportError:
            log.warning("curl_cffi missing — falling back to stub")
            return StubBackend()
    return StubBackend()
