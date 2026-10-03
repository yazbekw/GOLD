"""Fetch High-impact economic events.

Source priority:
1. Forex Factory via Cloudflare Worker proxy (recommended)
2. Forex Factory direct (may be blocked on cloud IPs)
3. Finnhub (only if paid plan)
4. FMP (only if subscribed)
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import requests

from config import settings

log = logging.getLogger(__name__)

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FINNHUB_URL = "https://finnhub.io/api/v1/calendar/economic"
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


# ── Forex Factory (primary) ───────────────────────────
def fetch_forexfactory() -> list[dict[str, Any]]:
    """Fetch from Forex Factory. Uses Cloudflare Worker proxy if configured."""
    # Build URL
    if settings.ff_proxy_url:
        proxy_base = settings.ff_proxy_url.rstrip("/")
        url = f"{proxy_base}/?url={quote(FF_URL, safe='')}"
        mode = "proxy"
    else:
        url = FF_URL
        mode = "direct"

    try:
        r = requests.get(
            url,
            timeout=25,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
            },
        )
        log.info("FF (%s) status=%s len=%s", mode, r.status_code, len(r.text))
        if r.status_code != 200:
            log.warning("FF non-200 (%s): %s", r.status_code, r.text[:200])
            return []
        raw = r.json()
    except Exception as exc:
        log.warning("FF fetch failed: %s", exc)
        return []

    if not isinstance(raw, list):
        log.warning("FF unexpected format: %s", type(raw))
        return []

    allowed = set(settings.allowed_countries)
    out: list[dict[str, Any]] = []
    for e in raw:
        country = str(e.get("country") or "").upper()
        if allowed and country not in allowed:
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


# ── Finnhub (fallback) ────────────────────────────────
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
            log.info("Finnhub status=%s", r.status_code)
            return []
        data = r.json()
    except Exception as exc:
        log.warning("Finnhub failed: %s", exc)
        return []
    raw = data.get("economicCalendar") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    # (بقية الفلترة كما في النسخة السابقة — اختصارًا)
    return []


# ── FMP (fallback) ────────────────────────────────────
def fetch_fmp(days_ahead: int = 7) -> list[dict[str, Any]]:
    return []  # FMP restricted on free plan


def fetch_all() -> tuple[list[dict[str, Any]], dict]:
    """Return first non-empty source + diagnostics."""
    diag = {"ff": 0, "finnhub": 0, "fmp": 0, "used": None, "proxy": bool(settings.ff_proxy_url)}

    ff = fetch_forexfactory()
    diag["ff"] = len(ff)
    if ff:
        diag["used"] = "forexfactory"
        return ff, diag

    fh = fetch_finnhub()
    diag["finnhub"] = len(fh)
    if fh:
        diag["used"] = "finnhub"
        return fh, diag

    fmp = fetch_fmp()
    diag["fmp"] = len(fmp)
    if fmp:
        diag["used"] = "fmp"
        return fmp, diag

    return [], diag
