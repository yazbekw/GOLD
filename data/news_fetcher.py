"""Fetch High-impact economic events: FMP primary, ForexFactory fallback."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from config import settings

log = logging.getLogger(__name__)

FMP_URL = "https://financialmodelingprep.com/stable/economic-calendar"
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

HIGH_KEYWORDS = (
    "CPI", "NFP", "NONFARM", "NON-FARM", "FOMC",
    "FED ", "FEDERAL", "GDP", "PCE", "PPI", "UNEMPLOYMENT",
)


def _hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:24]


def _is_high_impact(title: str, importance: str) -> bool:
    if (importance or "").lower() == "high":
        return True
    t = title.upper()
    return any(k in t for k in HIGH_KEYWORDS)


def _parse_dt(when: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def fetch_fmp(days_ahead: int = 7) -> list[dict[str, Any]]:
    if not settings.fmp_api_key:
        return []
    start = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    try:
        r = requests.get(
            FMP_URL,
            params={"from": start, "to": end, "apikey": settings.fmp_api_key},
            timeout=20,
        )
        r.raise_for_status()
        raw = r.json()
    except Exception as exc:
        log.warning("FMP fetch failed: %s", exc)
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


def fetch_forexfactory() -> list[dict[str, Any]]:
    try:
        r = requests.get(FF_URL, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        raw = r.json()
    except Exception as exc:
        log.warning("ForexFactory fetch failed: %s", exc)
        return []

    out: list[dict[str, Any]] = []
    for e in raw:
        country = (e.get("country") or "").upper()
        if settings.allowed_countries and country not in settings.allowed_countries:
            continue
        title = e.get("title") or ""
        importance = (e.get("impact") or "").lower()
        if not _is_high_impact(title, importance):
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
            "forecast": e.get("forecast") or "",
            "previous": e.get("previous") or "",
            "actual": e.get("actual") or "",
            "source": "forexfactory",
        })
    return out


def fetch_all() -> list[dict[str, Any]]:
    events = fetch_fmp()
    if not events:
        log.info("FMP returned no events, trying ForexFactory")
        events = fetch_forexfactory()
    return events
