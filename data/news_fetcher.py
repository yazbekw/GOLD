"""Fetch High-impact economic events.

Sources (in order of preference):
1. TradingEconomics (needs free API key) — best quality
2. Forex Factory JSON (no key needed)     — reliable
3. FMP (only if subscribed)               — currently restricted
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from config import settings

log = logging.getLogger(__name__)

# ── Endpoints ─────────────────────────────────────────
FF_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
]
FF_JSON_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

TE_URL = "https://api.tradingeconomics.com/calendar"

FMP_URLS = [
    "https://financialmodelingprep.com/stable/economic-calendar",
    "https://financialmodelingprep.com/api/v3/economic_calendar",
]

HIGH_KEYWORDS = (
    "CPI", "NFP", "NONFARM", "NON-FARM", "FOMC",
    "FED ", "FEDERAL", "GDP", "PCE", "PPI",
    "UNEMPLOYMENT", "INTEREST RATE", "PAYROLLS",
    "RETAIL SALES", "ISM", "PMI",
)


def _hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:24]


def _is_high_impact(title: str, importance: str = "") -> bool:
    imp = (importance or "").lower()
    if imp in ("high", "3"):
        return True
    t = title.upper()
    return any(k in t for k in HIGH_KEYWORDS)


def _parse_dt(when: str) -> datetime | None:
    if not when:
        return None
    s = str(when).strip()
    # ISO
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    # FF format: "2026-10-03T08:30:00-04:00"
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None


# ── Source 1: Forex Factory ───────────────────────────
def fetch_forexfactory() -> list[dict[str, Any]]:
    try:
        r = requests.get(
            FF_JSON_URL,
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)",
                "Accept": "application/json",
            },
        )
        log.info("FF status=%s len=%s", r.status_code, len(r.text))
        r.raise_for_status()
        raw = r.json()
    except Exception as exc:
        log.warning("ForexFactory fetch failed: %s", exc)
        return []

    if not isinstance(raw, list):
        log.warning("FF unexpected format: %s", type(raw))
        return []

    out: list[dict[str, Any]] = []
    for e in raw:
        country = (e.get("country") or "").upper()
        if settings.allowed_countries and country not in settings.allowed_countries:
            continue
        title = e.get("title") or ""
        impact = (e.get("impact") or "").lower()
        if not _is_high_impact(title, impact):
            continue
        dt = _parse_dt(e.get("date") or "")
        if not dt:
            continue
        out.append({
            "external_id": _hash("ff", title, dt.isoformat(), country),
            "event_time": dt.isoformat(),
            "country": country,
            "currency": e.get("currency") or "",
            "title": title,
            "importance": "high",
            "forecast": str(e.get("forecast") or ""),
            "previous": str(e.get("previous") or ""),
            "actual": str(e.get("actual") or ""),
            "source": "forexfactory",
        })
    log.info("FF parsed %d high-impact events", len(out))
    return out


# ── Source 2: TradingEconomics ────────────────────────
def fetch_tradingeconomics() -> list[dict[str, Any]]:
    key = getattr(settings, "te_api_key", "") or ""
    if not key:
        return []
    start = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        r = requests.get(
            f"{TE_URL}/country/united states/{start}/{end}",
            params={"c": key, "f": "json"},
            timeout=20,
        )
        r.raise_for_status()
        raw = r.json()
    except Exception as exc:
        log.warning("TradingEconomics failed: %s", exc)
        return []

    out: list[dict[str, Any]] = []
    for e in raw:
        title = e.get("Event") or e.get("Category") or ""
        importance = str(e.get("Importance") or "").lower()
        if not _is_high_impact(title, importance):
            continue
        dt = _parse_dt(e.get("Date") or "")
        if not dt:
            continue
        out.append({
            "external_id": _hash("te", title, dt.isoformat(), "US"),
            "event_time": dt.isoformat(),
            "country": "US",
            "currency": e.get("Currency") or "USD",
            "title": title,
            "importance": "high",
            "forecast": str(e.get("Forecast") or ""),
            "previous": str(e.get("Previous") or ""),
            "actual": str(e.get("Actual") or ""),
            "source": "tradingeconomics",
        })
    return out


# ── Source 3: FMP (may fail on free plan) ─────────────
def fetch_fmp(days_ahead: int = 7) -> list[dict[str, Any]]:
    if not settings.fmp_api_key:
        return []
    start = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    raw = None
    for url in FMP_URLS:
        try:
            r = requests.get(
                url,
                params={"from": start, "to": end, "apikey": settings.fmp_api_key},
                timeout=20,
            )
            if r.status_code == 200:
                raw = r.json()
                log.info("FMP %s OK (%d items)", url.split("?")[0], len(raw) if isinstance(raw, list) else 0)
                break
            else:
                log.info("FMP %s -> %s", url.split("?")[0], r.status_code)
        except Exception as exc:
            log.warning("FMP %s failed: %s", url, exc)
    if not isinstance(raw, list):
        return []

    out: list[dict[str, Any]] = []
    for e in raw:
        country = (e.get("country") or "").upper()
        if settings.allowed_countries and country not in settings.allowed_countries:
            continue
        title = e.get("event") or e.get("title") or ""
        importance = (e.get("impact") or e.get("importance") or "").lower()
        if not _is_high_impact(title, importance):
            continue
        dt = _parse_dt(e.get("date") or "")
        if not dt:
            continue
        out.append({
            "external_id": _hash("fmp", title, dt.isoformat(), country),
            "event_time": dt.isoformat(),
            "country": country,
            "currency": e.get("currency") or ("USD" if country == "US" else ""),
            "title": title,
            "importance": "high",
            "forecast": str(e.get("estimate") or e.get("forecast") or ""),
            "previous": str(e.get("previous") or ""),
            "actual": str(e.get("actual") or ""),
            "source": "fmp",
        })
    return out


def fetch_all() -> tuple[list[dict[str, Any]], dict]:
    """Try sources in order, return first non-empty + diagnostic."""
    diag = {"fmp": 0, "ff": 0, "te": 0, "used": None}

    fmp = fetch_fmp()
    diag["fmp"] = len(fmp)
    if fmp:
        diag["used"] = "fmp"
        return fmp, diag

    ff = fetch_forexfactory()
    diag["ff"] = len(ff)
    if ff:
        diag["used"] = "forexfactory"
        return ff, diag

    te = fetch_tradingeconomics()
    diag["te"] = len(te)
    if te:
        diag["used"] = "tradingeconomics"
        return te, diag

    return [], diag
