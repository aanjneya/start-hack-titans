"""Pluggable external risk signal providers for Agent 6 (Risk Rater).

Feeds supported (all zero-API-key public endpoints with strict timeouts, in-memory TTL caching,
and deterministic MOCK_MODE=true snapshots for offline/stage demo reliability):
1. PolymarketProvider       — Live prediction-market odds (Gamma API) on tariffs, China/US/EU trade,
                              Taiwan/Red Sea shipping, and rare-earth/export controls.
2. OpenMeteoWeatherProvider — Live weather & wind/storm conditions at exact port & shipment lat/lon
                              read from `soony/master/ports.csv` and `open_shipments.csv`.
3. PoliticalNewsProvider    — Portal news drops (`soony/news/*.json` in `documents`) + live GDELT
                              country-level geopolitical/trade news API.
4. TelemetryProvider        — Pluggable satellite/AIS telemetry interface grounded in live shipment
                              GPS coordinates (`last_lat`, `last_lon`), port dwell, and feed silence.
"""
from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

import httpx

from ..core import llm
from ..core.config import get_settings
from ..core.models import Document, Evidence, Shipment

HTTP_TIMEOUT_SEC = 3.0
CACHE_TTL_SEC = 300.0  # 5-minute in-memory cache so reruns stay fast & polite

POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com/markets"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


@dataclass
class ExternalSignal:
    """One verifiable external risk observation linked to a country, port, shipment, or tag."""

    provider: str  # polymarket | weather | news | gdelt | telemetry
    category: str  # external_politics | weather | maritime_telemetry | trade_news
    title: str
    score_0_100: float  # normalized severity 0..100 for this single signal
    summary: str
    countries: list[str] = field(default_factory=list)  # ISO-2 codes (CN, KR, US, IN, PL, DE)
    ports: list[str] = field(default_factory=list)  # LOCODEs (CNYTN, KRPUS, DEHAM, ...)
    shipment_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_url: str = ""
    quote: str = ""
    live: bool = False  # True if fetched live over HTTP, False if portal/mock fallback

    def as_evidence(self) -> Evidence:
        return Evidence(
            path=self.source_url or f"external://{self.provider}",
            locator=f"{self.provider}:{self.category}",
            quote=self.quote or self.summary[:180],
        )


class SignalProvider(Protocol):
    name: str

    def fetch(self, *, countries: set[str], ports: dict[str, dict[str, Any]],
              shipments: list[Shipment], documents: list[Document],
              reference_now: datetime) -> list[ExternalSignal]: ...


# ---------------------------------------------------------------------------
# Simple TTL cache for live HTTP responses
# ---------------------------------------------------------------------------
_CACHE: dict[str, tuple[float, Any]] = {}


def clear_cache() -> None:
    _CACHE.clear()


def _cached_get_json(url: str, params: dict[str, Any] | None = None) -> Optional[Any]:
    key = url + "?" + json.dumps(params or {}, sort_keys=True)
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and (now - hit[0]) < CACHE_TTL_SEC:
        return hit[1]
    try:
        resp = httpx.get(url, params=params, timeout=HTTP_TIMEOUT_SEC, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        _CACHE[key] = (now, data)
        return data
    except Exception:
        return None


def load_ports_from_root(data_root: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """Read port coordinates (locode -> {name, country, lat, lon, mode}) from master/ports.csv if present."""
    root = Path(data_root or get_settings().data_root)
    ports: dict[str, dict[str, Any]] = {}
    for candidate in (root / "soony/master/ports.csv", *root.rglob("ports.csv")):
        if not candidate.is_file():
            continue
        try:
            with candidate.open(encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    code = (row.get("locode") or "").strip().upper()
                    if not code:
                        continue
                    try:
                        lat = float(row.get("lat") or 0)
                        lon = float(row.get("lon") or 0)
                    except ValueError:
                        continue
                    ports[code] = {
                        "locode": code,
                        "name": (row.get("name") or code).strip(),
                        "country": (row.get("country") or "").strip().upper(),
                        "lat": lat,
                        "lon": lon,
                        "mode": (row.get("mode") or "").strip().lower(),
                        "source": str(candidate.relative_to(root)),
                    }
            break
        except Exception:
            continue
    return ports


# ---------------------------------------------------------------------------
# 1. Polymarket Provider (External Politics & Geopolitical Odds)
# ---------------------------------------------------------------------------
# Keyword-to-country mapping for matching live Polymarket questions to supplier countries/routes
_POLYMARKET_RULES: list[tuple[tuple[str, ...], list[str], list[str], str]] = [
    (("china", "tariff", "trade", "export", "rare earth", "semiconductor", "chip"), ["CN"], ["tariffs", "export-control"],
     "US/EU-China trade & export-control market odds"),
    (("taiwan", "south china sea", "strait"), ["CN", "KR"], ["geopolitics", "maritime"],
     "East Asia maritime / Taiwan Strait disruption odds"),
    (("red sea", "houthi", "suez", "shipping"), ["CN", "KR", "IN"], ["red-sea", "maritime"],
     "Red Sea / Suez sea-freight disruption odds"),
    (("korea", "north korea"), ["KR"], ["geopolitics"],
     "Korean Peninsula geopolitical risk odds"),
    (("india", "tariff", "export"), ["IN"], ["tariffs", "geopolitics"],
     "India trade / tariff risk odds"),
    (("poland", "nato", "ukraine", "border", "eu tariff"), ["PL", "DE"], ["geopolitics", "eu-border"],
     "Central/Eastern Europe border & freight corridor odds"),
    (("us tariff", "united states", "port strike", "recession"), ["US"], ["tariffs", "us-logistics"],
     "US tariff / port disruption odds"),
]

# Deterministic offline fallback snapshots (used when MOCK_MODE=true or offline)
_MOCK_POLYMARKET_MARKETS: list[dict[str, Any]] = [
    {
        "slug": "china-tightens-rare-earth-or-tech-export-controls-q4",
        "question": "Will China enforce additional rare-earth or critical tech export controls?",
        "prob": 0.72,
        "countries": ["CN"],
        "tags": ["export-control", "rare-earth", "tariffs"],
    },
    {
        "slug": "red-sea-container-rerouting-continues-through-q4",
        "question": "Will major Asia-Europe container lines keep rerouting away from Red Sea / Suez?",
        "prob": 0.68,
        "countries": ["CN", "KR", "IN"],
        "tags": ["red-sea", "maritime", "sea-freight"],
    },
    {
        "slug": "us-eu-additional-tariffs-on-china-electronics-goods",
        "question": "Will new US/EU tariffs hit Chinese manufactured components this quarter?",
        "prob": 0.54,
        "countries": ["CN", "US"],
        "tags": ["tariffs", "trade-war"],
    },
    {
        "slug": "india-export-customs-clearance-disruption",
        "question": "Will India port/customs bottlenecks delay manufacturing exports in Q4?",
        "prob": 0.31,
        "countries": ["IN"],
        "tags": ["customs", "port-delay"],
    },
    {
        "slug": "polish-german-border-freight-controls-delays",
        "question": "Will Polish-German border checks cause >12h road freight queues?",
        "prob": 0.24,
        "countries": ["PL", "DE"],
        "tags": ["eu-border", "road-freight"],
    },
]


def _extract_yes_prob(market: dict[str, Any]) -> Optional[float]:
    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except Exception:
            prices = None
    if isinstance(prices, list) and prices:
        try:
            p = float(prices[0])
            if 0.0 <= p <= 1.0:
                return p
        except (TypeError, ValueError):
            pass
    for key in ("lastTradePrice", "bestBid", "prob"):
        if market.get(key) is not None:
            try:
                p = float(market[key])
                if 0.0 <= p <= 1.0:
                    return p
            except (TypeError, ValueError):
                pass
    return None


class PolymarketProvider:
    name = "polymarket"

    def fetch(self, *, countries: set[str], ports: dict[str, dict[str, Any]],
              shipments: list[Shipment], documents: list[Document],
              reference_now: datetime) -> list[ExternalSignal]:
        if not llm.is_mock():
            live_signals = self._fetch_live(countries)
            if live_signals:
                return live_signals
        return self._mock_signals(countries)

    def _fetch_live(self, countries: set[str]) -> list[ExternalSignal]:
        data = _cached_get_json(
            POLYMARKET_GAMMA_URL,
            {"active": "true", "closed": "false", "limit": 80, "order": "volume24hr", "ascending": "false"},
        )
        if not isinstance(data, list):
            return []
        signals: list[ExternalSignal] = []
        seen_slugs: set[str] = set()
        for m in data:
            if not isinstance(m, dict):
                continue
            question = str(m.get("question") or "").strip()
            slug = str(m.get("slug") or m.get("id") or "")
            if not question or slug in seen_slugs:
                continue
            q_low = question.lower()
            prob = _extract_yes_prob(m)
            if prob is None or prob < 0.15:
                continue
            for keywords, rule_countries, tags, _desc in _POLYMARKET_RULES:
                hit_countries = [c for c in rule_countries if not countries or c in countries]
                if not hit_countries:
                    continue
                if any(kw in q_low for kw in keywords):
                    seen_slugs.add(slug)
                    signals.append(ExternalSignal(
                        provider=self.name,
                        category="external_politics",
                        title=question,
                        score_0_100=round(prob * 100.0, 1),
                        summary=f"Polymarket odds {prob * 100:.0f}%: {question}",
                        countries=hit_countries,
                        tags=list(tags),
                        source_url=f"https://polymarket.com/event/{slug}" if slug else POLYMARKET_GAMMA_URL,
                        quote=f"Yes={prob * 100:.0f}% (live Polymarket Gamma API)",
                        live=True,
                    ))
                    break
            if len(signals) >= 12:
                break
        return signals

    def _mock_signals(self, countries: set[str]) -> list[ExternalSignal]:
        signals: list[ExternalSignal] = []
        for m in _MOCK_POLYMARKET_MARKETS:
            hit = [c for c in m["countries"] if not countries or c in countries]
            if not hit:
                continue
            prob = float(m["prob"])
            signals.append(ExternalSignal(
                provider=self.name,
                category="external_politics",
                title=m["question"],
                score_0_100=round(prob * 100.0, 1),
                summary=f"Polymarket odds {prob * 100:.0f}%: {m['question']}",
                countries=hit,
                tags=list(m["tags"]),
                source_url=f"https://polymarket.com/event/{m['slug']}",
                quote=f"Yes={prob * 100:.0f}% (offline Polymarket snapshot)",
                live=False,
            ))
        return signals


# ---------------------------------------------------------------------------
# 2. Open-Meteo Weather Provider (Exact Port & Shipment Coordinates)
# ---------------------------------------------------------------------------
# Severe WMO weather codes: 65 heavy rain, 75 heavy snow, 82 violent showers, 95-99 thunderstorm/hail
_SEVERE_WMO = {65, 67, 75, 82, 86, 95, 96, 99}
_MODERATE_WMO = {55, 63, 73, 81, 85}

# Deterministic offline fallback weather for key ports (used when MOCK_MODE=true or offline)
_MOCK_PORT_WEATHER: dict[str, dict[str, Any]] = {
    "DEHAM": {"wind_kmh": 48.0, "gust_kmh": 66.0, "precip_mm": 6.4, "wmo": 63,
              "desc": "North Sea autumn squalls & 66 km/h gusts near Hamburg container terminals"},
    "CNYTN": {"wind_kmh": 24.0, "gust_kmh": 34.0, "precip_mm": 1.2, "wmo": 2,
              "desc": "Moderate coastal breeze at Yantian (Shenzhen)"},
    "KRPUS": {"wind_kmh": 31.0, "gust_kmh": 44.0, "precip_mm": 2.0, "wmo": 3,
              "desc": "Brisk sea winds at Busan port"},
    "INNSA": {"wind_kmh": 18.0, "gust_kmh": 25.0, "precip_mm": 0.0, "wmo": 1,
              "desc": "Clear conditions at Nhava Sheva (Mumbai)"},
    "ORD": {"wind_kmh": 38.0, "gust_kmh": 54.0, "precip_mm": 4.5, "wmo": 73,
            "desc": "November snow/gusts at Chicago O'Hare air cargo hub"},
    "PLPOZ": {"wind_kmh": 22.0, "gust_kmh": 30.0, "precip_mm": 1.5, "wmo": 61,
              "desc": "Light rain along Poznań–Hamburg A2 road corridor"},
}


def _weather_severity(wind_kmh: float, gust_kmh: float, precip_mm: float, wmo: int) -> float:
    score = 0.0
    if gust_kmh >= 75 or wind_kmh >= 55:
        score += 55.0
    elif gust_kmh >= 55 or wind_kmh >= 40:
        score += 32.0
    elif gust_kmh >= 40 or wind_kmh >= 28:
        score += 15.0
    if precip_mm >= 15:
        score += 25.0
    elif precip_mm >= 5:
        score += 12.0
    if wmo in _SEVERE_WMO:
        score += 30.0
    elif wmo in _MODERATE_WMO:
        score += 12.0
    return min(100.0, round(score, 1))


class OpenMeteoWeatherProvider:
    name = "weather"

    def fetch(self, *, countries: set[str], ports: dict[str, dict[str, Any]],
              shipments: list[Shipment], documents: list[Document],
              reference_now: datetime) -> list[ExternalSignal]:
        # Target only ports actually referenced by active shipments or supplier countries
        active_ports: dict[str, dict[str, Any]] = {}
        for sh in shipments:
            for code in (sh.origin, sh.destination):
                c = (code or "").strip().upper()
                if c in ports:
                    active_ports[c] = ports[c]
        if not active_ports:
            active_ports = {k: v for k, v in ports.items() if not countries or v["country"] in countries}

        if not llm.is_mock() and active_ports:
            live_signals = self._fetch_live(active_ports)
            if live_signals:
                return live_signals
        return self._mock_signals(active_ports, countries)

    def _fetch_live(self, active_ports: dict[str, dict[str, Any]]) -> list[ExternalSignal]:
        signals: list[ExternalSignal] = []
        for code, info in list(active_ports.items())[:8]:
            params = {
                "latitude": info["lat"],
                "longitude": info["lon"],
                "current": "wind_speed_10m,wind_gusts_10m,precipitation,weather_code",
            }
            data = _cached_get_json(OPEN_METEO_URL, params)
            cur = (data or {}).get("current") if isinstance(data, dict) else None
            if not isinstance(cur, dict):
                continue
            wind = float(cur.get("wind_speed_10m") or 0)
            gust = float(cur.get("wind_gusts_10m") or 0)
            precip = float(cur.get("precipitation") or 0)
            wmo = int(cur.get("weather_code") or 0)
            sev = _weather_severity(wind, gust, precip, wmo)
            if sev < 20.0:
                continue  # Ignore calm/clear weather so reasons stay high-signal
            signals.append(ExternalSignal(
                provider=self.name,
                category="weather",
                title=f"Adverse weather at {info['name']} ({code})",
                score_0_100=sev,
                summary=f"{info['name']} ({code}): wind {wind:.0f} km/h (gusts {gust:.0f} km/h), precip {precip:.1f} mm",
                countries=[info["country"]] if info.get("country") else [],
                ports=[code],
                tags=["weather", info.get("mode", "port")],
                source_url=f"{OPEN_METEO_URL}?latitude={info['lat']}&longitude={info['lon']}",
                quote=f"wind={wind}km/h, gusts={gust}km/h, precip={precip}mm, WMO={wmo} (live Open-Meteo)",
                live=True,
            ))
        return signals

    def _mock_signals(self, active_ports: dict[str, dict[str, Any]], countries: set[str]) -> list[ExternalSignal]:
        signals: list[ExternalSignal] = []
        for code, wx in _MOCK_PORT_WEATHER.items():
            info = active_ports.get(code)
            country = info["country"] if info else code[:2]
            if countries and country not in countries and code != "DEHAM":
                continue
            sev = _weather_severity(wx["wind_kmh"], wx["gust_kmh"], wx["precip_mm"], wx["wmo"])
            if sev < 20.0:
                continue
            name = info["name"] if info else code
            lat = info["lat"] if info else 0.0
            lon = info["lon"] if info else 0.0
            signals.append(ExternalSignal(
                provider=self.name,
                category="weather",
                title=f"Weather alert at {name} ({code})",
                score_0_100=sev,
                summary=f"{name} ({code}): {wx['desc']}",
                countries=[country] if country else [],
                ports=[code],
                tags=["weather", (info or {}).get("mode", "port")],
                source_url=f"{OPEN_METEO_URL}?latitude={lat}&longitude={lon}",
                quote=f"wind={wx['wind_kmh']}km/h, gusts={wx['gust_kmh']}km/h, precip={wx['precip_mm']}mm (offline Open-Meteo snapshot)",
                live=False,
            ))
        return signals


# ---------------------------------------------------------------------------
# 3. Political & Trade News Provider (Portal news/*.json + Live GDELT API)
# ---------------------------------------------------------------------------
_COUNTRY_NAMES = {
    "CN": "China",
    "KR": "South Korea",
    "US": "United States",
    "IN": "India",
    "PL": "Poland",
    "DE": "Germany",
}

_HIGH_SEVERITY_NEWS_WORDS = ("export", "ban", "licence", "license", "sanction", "pause", "suspend", "blockade", "strike")
_MED_SEVERITY_NEWS_WORDS = ("tariff", "delay", "shortage", "disruption", "congestion", "restriction", "protest")


class PoliticalNewsProvider:
    name = "news"

    def fetch(self, *, countries: set[str], ports: dict[str, dict[str, Any]],
              shipments: list[Shipment], documents: list[Document],
              reference_now: datetime) -> list[ExternalSignal]:
        signals: list[ExternalSignal] = []

        # 3a. Portal news drops (always processed first — authoritative for the challenge!)
        for doc in documents:
            if doc.kind != "news":
                continue
            item = (doc.extracted or {}).get("item") or {}
            headline = str(item.get("headline") or item.get("title") or doc.title or doc.path)
            summary = str(item.get("summary") or doc.text or headline)
            regions = [str(r).upper() for r in (item.get("regions") or [])]
            tags = [str(t).lower() for t in (item.get("tags") or [])]
            txt_low = f"{headline} {summary}".lower()
            sev = 45.0
            if any(w in txt_low for w in _HIGH_SEVERITY_NEWS_WORDS):
                sev = 90.0
            elif any(w in txt_low for w in _MED_SEVERITY_NEWS_WORDS):
                sev = 65.0
            signals.append(ExternalSignal(
                provider="portal_news",
                category="trade_news",
                title=headline,
                score_0_100=sev,
                summary=headline,
                countries=regions,
                tags=tags,
                source_url=doc.source or doc.path,
                quote=summary[:180],
                live=True,  # real data from portal drop
            ))

        # 3b. Live GDELT geopolitical/trade headlines (only when live mode is active)
        if not llm.is_mock() and countries:
            signals.extend(self._fetch_gdelt(countries))

        return signals

    def _fetch_gdelt(self, countries: set[str]) -> list[ExternalSignal]:
        signals: list[ExternalSignal] = []
        for code in sorted(countries):
            cname = _COUNTRY_NAMES.get(code)
            if not cname:
                continue
            query = f'"{cname}" (tariff OR "export control" OR "port strike" OR sanctions OR "supply chain")'
            data = _cached_get_json(
                GDELT_DOC_URL,
                {"query": query, "mode": "ArtList", "maxrecords": 3, "format": "json", "timespan": "3d"},
            )
            articles = (data or {}).get("articles") if isinstance(data, dict) else None
            if not isinstance(articles, list) or not articles:
                continue
            art = articles[0]
            title = str(art.get("title") or "").strip()
            url = str(art.get("url") or GDELT_DOC_URL)
            if not title:
                continue
            t_low = title.lower()
            sev = 65.0 if any(w in t_low for w in _HIGH_SEVERITY_NEWS_WORDS) else 40.0
            signals.append(ExternalSignal(
                provider="gdelt",
                category="external_politics",
                title=title,
                score_0_100=sev,
                summary=f"GDELT political/trade news ({code}): {title}",
                countries=[code],
                tags=["gdelt", "politics"],
                source_url=url,
                quote=f"{art.get('domain', 'GDELT')}: {title[:160]}",
                live=True,
            ))
        return signals


# ---------------------------------------------------------------------------
# 4. Satellite / AIS Telemetry Provider (GPS Coordinates + Dwell + Feed Silence)
# ---------------------------------------------------------------------------
def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


class TelemetryProvider:
    """Grounded maritime/road telemetry provider using real shipment GPS fixes (`last_lat`, `last_lon`).

    Acts as the pluggable satellite/AIS telemetry interface: analyzes vessel/truck transponder
    freshness, port discharge dwell (`arrived_port`), and sea-route chokepoint transit (`via=SGSIN`).
    """

    name = "telemetry"

    def fetch(self, *, countries: set[str], ports: dict[str, dict[str, Any]],
              shipments: list[Shipment], documents: list[Document],
              reference_now: datetime) -> list[ExternalSignal]:
        signals: list[ExternalSignal] = []
        erp_shipments = [s for s in shipments if s.updated_at is not None]
        if not erp_shipments:
            return signals
        feed_now = max(_as_utc(s.updated_at) for s in erp_shipments)  # type: ignore[type-var]

        for sh in erp_shipments:
            st = (sh.status or "").lower()
            if st in {"delivered", "cancelled"}:
                continue
            upd = _as_utc(sh.updated_at)
            age_h = ((feed_now - upd).total_seconds() / 3600.0) if (feed_now and upd) else 0.0
            coord = f"({sh.last_lat:.2f}, {sh.last_lon:.2f})" if (sh.last_lat is not None and sh.last_lon is not None) else ""
            origin_country = ports.get((sh.origin or "").upper(), {}).get("country", "")

            if st == "delayed":
                signals.append(ExternalSignal(
                    provider=self.name,
                    category="maritime_telemetry",
                    title=f"Delayed vessel/shipment {sh.shipment_id}",
                    score_0_100=72.0,
                    summary=f"AIS/GPS fix {coord} shows {sh.shipment_id} ({sh.mode}) delayed (ETA {sh.eta})",
                    countries=[origin_country] if origin_country else [],
                    ports=[p for p in (sh.origin, sh.destination) if p],
                    shipment_ids=[sh.shipment_id],
                    tags=["telemetry", "delayed", sh.mode],
                    source_url=sh.source,
                    quote=f"{sh.shipment_id} lat/lon={coord}, status={sh.status}, eta={sh.eta}",
                    live=True,
                ))
            elif age_h >= 18.0:
                signals.append(ExternalSignal(
                    provider=self.name,
                    category="maritime_telemetry",
                    title=f"Silent AIS/GPS transponder on {sh.shipment_id}",
                    score_0_100=min(85.0, 40.0 + age_h),
                    summary=f"Transponder silent {age_h:.0f}h at {coord} on {sh.shipment_id} ({sh.mode}, {sh.status})",
                    countries=[origin_country] if origin_country else [],
                    ports=[p for p in (sh.origin, sh.destination) if p],
                    shipment_ids=[sh.shipment_id],
                    tags=["telemetry", "silent_transponder", sh.mode],
                    source_url=sh.source,
                    quote=f"{sh.shipment_id} last fix {coord} at {sh.updated_at} ({age_h:.0f}h old)",
                    live=True,
                ))
            elif st == "arrived_port":
                signals.append(ExternalSignal(
                    provider=self.name,
                    category="maritime_telemetry",
                    title=f"Port dwell at {sh.destination} for {sh.shipment_id}",
                    score_0_100=58.0,
                    summary=f"GPS fix {coord} confirms {sh.shipment_id} sitting at {sh.destination} awaiting pickup",
                    countries=[origin_country] if origin_country else [],
                    ports=[sh.destination] if sh.destination else [],
                    shipment_ids=[sh.shipment_id],
                    tags=["telemetry", "port_dwell"],
                    source_url=sh.source,
                    quote=f"{sh.shipment_id} at {coord}, note={sh.note}",
                    live=True,
                ))

        return signals


DEFAULT_PROVIDERS: tuple[SignalProvider, ...] = (
    PolymarketProvider(),
    OpenMeteoWeatherProvider(),
    PoliticalNewsProvider(),
    TelemetryProvider(),
)


def collect_external_signals(
    *,
    countries: set[str],
    shipments: list[Shipment],
    documents: list[Document],
    reference_now: datetime,
    data_root: Optional[Path] = None,
    providers: tuple[SignalProvider, ...] = DEFAULT_PROVIDERS,
) -> tuple[list[ExternalSignal], dict[str, int]]:
    """Run all configured external providers safely (one provider failure never stops scoring)."""
    ports = load_ports_from_root(data_root)
    all_signals: list[ExternalSignal] = []
    counts: dict[str, int] = {}
    for provider in providers:
        try:
            sigs = provider.fetch(
                countries=countries,
                ports=ports,
                shipments=shipments,
                documents=documents,
                reference_now=reference_now,
            )
            all_signals.extend(sigs)
            counts[provider.name] = len(sigs)
        except Exception:
            counts[provider.name] = 0
    return all_signals, counts
