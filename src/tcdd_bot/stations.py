"""Station list + fuzzy search.

Stations come from TCDD's public CDN (no auth):
  https://cdn-api-prod-ytp.tcddtasimacilik.gov.tr/datas/stations.json

We download once at process startup and keep a slim {id, name} list in memory.
Fuzzy match via rapidfuzz so users can type partial Turkish names without
diacritics.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass

import httpx
from rapidfuzz import fuzz, process

log = logging.getLogger(__name__)

STATIONS_URL = (
    "https://cdn-api-prod-ytp.tcddtasimacilik.gov.tr/datas/stations.json"
)


@dataclass(frozen=True)
class Station:
    id: int
    name: str


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.upper().replace("İ", "I").replace("I", "I")


class StationCatalog:
    def __init__(self, stations: list[Station]):
        self._stations = stations
        self._by_id = {s.id: s for s in stations}
        self._norm_names = {_normalize(s.name): s for s in stations}

    @classmethod
    async def load(cls) -> "StationCatalog":
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(STATIONS_URL)
            r.raise_for_status()
            raw = r.json()
        seen: dict[int, Station] = {}
        for entry in raw:
            sid = entry.get("id")
            name = entry.get("name")
            if not sid or not name or not entry.get("active"):
                continue
            if not (entry.get("ticketSaleActive") or entry.get("showOnQuery")):
                continue
            if sid not in seen:
                seen[sid] = Station(id=sid, name=name.strip())
        log.info("Loaded %d stations", len(seen))
        return cls(sorted(seen.values(), key=lambda s: s.name))

    def get(self, station_id: int) -> Station | None:
        return self._by_id.get(station_id)

    def search(self, query: str, limit: int = 5) -> list[Station]:
        if not query.strip():
            return []
        q = _normalize(query)
        # Score each station with a mix of contains-bonus and fuzzy ratio.
        scored: list[tuple[int, Station]] = []
        for norm, st in self._norm_names.items():
            if norm == q:
                score = 1000
            elif norm.startswith(q):
                score = 900 - len(norm)
            elif q in norm:
                score = 700 - norm.index(q) - len(norm)
            else:
                score = fuzz.token_set_ratio(q, norm)
                if score < 70:
                    continue
            scored.append((score, st))
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:limit]]
