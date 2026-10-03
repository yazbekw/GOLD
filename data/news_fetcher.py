"""Fetch High-impact economic events.

Source priority:
1. Finnhub (official API, no rate limit for our usage)
2. Forex Factory (fallback, may get 429)
3. FMP (only if subscribed — currently restricted)
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from config import settings

log = logging.getLogger(__name__)

FINNHUB_URL = "https://finnhub.io/api/v1/calendar/economic"
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
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
    imp = str(importance or "").lower()
    if imp in ("high", "3"):
        return True
    t = title.upper()
    return any(k in t for k in HIGH_KEYWORDS)


def _parse_dt(when: Any) -> datetime | None:
    if not when:
        return None
    # Unix timestamp (Finnhub)
    if isinstance(when, (int, float)):
        try:
            return datetime.fromtimestamp(float(when), tz=timezone.utc)
        except Exception:
            return None
    s = str(when).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None


# ── Source 1: Finnhub ─────────────────────────────────
def fetch_finnhub() -> list[dict[str, Any]]:
    key = getattr(settings, "finnhub_api_key", "") or ""
    if not key:
        return []

    start = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")

    try:
        r = requests.get(
            FINNHUB_URL,
            params={"from": start, "to": end, "token": key},
            timeout=20,
        )
        if r.status_code != 200:
            log.warning("Finnhub status=%s body=%s", r.status_code, r.text[:200])
            return []
        data = r.json()
    except Exception as exc:
        log.warning("Finnhub fetch failed: %s", exc)
        return []

    raw = data.get("economicCalendar") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        log.warning("Finnhub unexpected format: %s", type(data))
        return []

    log.info("Finnhub returned %d items", len(raw))
    out: list[dict[str, Any]] = []
    for e in raw:
        country = str(e.get("country") or "").upper()
        # Finnhub uses "US", "EU", "GB", "JP", etc.
        # Map to our allowed list (which now uses currency-style codes for FF compat)
        # Match either US or USD
        if settings.allowed_countries:
            allowed = set(settings.allowed_countries)
            if country not in allowed and f"{country}D" not in allowed:
                # Special case: US -> USD, GB -> GBP, etc.
                currency = str(e.get("currency") or "").upper()
                if currency not in allowed:
                    continue
        title = e.get("event") or ""
        importance = str(e.get("impact") or "").lower()
        # Finnhub sometimes: 1=low, 2=medium, 3=high
        if importance in ("1", "low"):
            importance = "low"
        elif importance in ("2", "medium"):
            importance = "medium"
        elif importance in ("3", "high"):
            importance = "high"

        if not _is_high_impact(title, importance):
            continue
        dt = _parse_dt(e.get("time") or e.get("date"))
        if not dt:
            continue
        out.append({
            "external_id": _hash("fh", title, dt.isoformat(), country),
            "event_time": dt.isoformat(),
            "country": country or "US",
            "currency": e.get("currency") or "",
            "title": title,
            "importance": "high",
            "forecast": str(e.get("estimate") or ""),
            "previous": str(e.get("prev") or ""),
            "actual": str(e.get("actual") or ""),
            "source": "finnhub",
        })
    log.info("Finnhub parsed %d high-impact events", len(out))
    return out


# ── Source 2: Forex Factory ───────────────────────────
def fetch_forexfactory() -> list[dict[str, Any]]:
    try:
        r = requests.get(
            FF_URL,
            timeout=20,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.forexfactory.com/",
            },
        )
        log.info("FF status=%s len=%s", r.status_code, len(r.text))
        if r.status_code != 200:
            return []
        raw = r.json()
    except Exception as exc:
        log.warning("ForexFactory fetch failed: %s", exc)
        return []

    if not isinstance(raw, list):
        return []

    out: list[dict[str, Any]] = []
    for e in raw:
        country = str(e.get("country") or "").upper()
        if settings.allowed_countries and country not in set(settings.allowed_countries):
            continue
        title = e.get("title") or ""
        impact = str(e.get("impact") or "").lower()
        if not _is_high_impact(title, impact):
            continue
        dt = _parse_dt(e.get("date"))
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


# ── Source 3: FMP (may not work on free plan) ─────────
def fetch_fmp(days_ahead: int = 7) -> list[dict[str, Any]]:
    if not settings.fmp_api_key:
        return []
    start = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    for url in FMP_URLS:
        try:
            r = requests.get(
                url,
                params={"from": start, "to": end, "apikey": settings.fmp_api_key},
                timeout=20,
            )
            if r.status_code != 200:
                continue
            raw = r.json()
            if not isinstance(raw, list):
                continue
            out: list[dict[str, Any]] = []
            for e in raw:
                country = str(e.get("country") or "").upper()
                if settings.allowed_countries and country not in set(settings.allowed_countries):
                    if f"{country}D" not in set(settings.allowed_countries):
                        continue
                title = e.get("event") or e.get("title") or ""
                imp = str(e.get("impact") or e.get("importance") or "").lower()
                if not _is_high_impact(title, imp):
                    continue
                dt = _parse_dt(e.get("date"))
                if not dt:
                    continue
                out.append({
                    "external_id": _hash("fmp", title, dt.isoformat(), country),
                    "event_time": dt.isoformat(),
                    "country": country,
                    "currency": e.get("currency") or "",
                    "title": title,
                    "importance": "high",
                    "forecast": str(e.get("estimate") or ""),
                    "previous": str(e.get("previous") or ""),
                    "actual": str(e.get("actual") or ""),
                    "source": "fmp",
                })
            return out
        except Exception as exc:
            log.warning("FMP %s failed: %s", url, exc)
    return []


def fetch_all() -> tuple[list[dict[str, Any]], dict]:
    """Try sources in order, return first non-empty + diagnostic."""
    diag = {"finnhub": 0, "ff": 0, "fmp": 0, "used": None}

    fh = fetch_finnhub()
    diag["finnhub"] = len(fh)
    if fh:
        diag["used"] = "finnhub"
        return fh, diag

    time.sleep(0.5)
    ff = fetch_forexfactory()
    diag["ff"] = len(ff)
    if ff:
        diag["used"] = "forexfactory"
        return ff, diag

    fmp = fetch_fmp()
    diag["fmp"] = len(fmp)
    if fmp:
        diag["used"] = "fmp"
        return fmp, diag

    return [], diag
