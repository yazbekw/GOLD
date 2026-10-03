"""Fetch High-impact economic events.

Source priority (in order):
1. GitHub-cached Forex Factory JSON (most reliable, no rate limit)
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

# ── Endpoints ─────────────────────────────────────────
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


# ── Helpers ───────────────────────────────────────────
def _hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:24]


def _is_high_impact(title: str, importance: str = "") -> bool:
    """High impact ONLY if the 'impact' field says so.

    No keyword fallback — FF marks impacts explicitly.
    Finnhub uses numeric: 3 = high.
    """
    imp = str(importance or "").strip().lower()
    if imp in ("high", "3"):
        return True
    # For Finnhub where impact could be missing, use keywords
    # but ONLY if impact is empty (not explicit low/medium)
    if not imp:
        t = title.upper()
        return any(k in t for k in HIGH_KEYWORDS)
    return False

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
    # ISO format
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    # Fallback formats
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None


def _parse_ff_list(raw: list) -> list[dict[str, Any]]:
    """Common parser for FF JSON list (used by both GitHub and direct)."""
    if not isinstance(raw, list):
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


# ── Source 1: GitHub-cached FF (primary) ──────────────
def fetch_forexfactory_from_github() -> list[dict[str, Any]]:
    """Fetch pre-cached FF data from GitHub raw CDN.

    Requires FF_GITHUB_URL env var pointing to the raw JSON file
    (updated daily by .github/workflows/fetch-ff.yml).
    """
    url = getattr(settings, "ff_github_url", "") or ""
    if not url:
        log.debug("FF_GITHUB_URL not configured — skipping GitHub source")
        return []

    try:
        r = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "news-macro-bot/1.0",
                "Accept": "application/json",
            },
        )
        if r.status_code != 200:
            log.warning("GitHub FF cache returned %s", r.status_code)
            return []
        raw = r.json()
    except Exception as exc:
        log.warning("GitHub FF cache fetch failed: %s", exc)
        return []

    return _parse_ff_list(raw)


# ── Source 2: Forex Factory direct (fallback) ─────────
def fetch_forexfactory() -> list[dict[str, Any]]:
    """Fetch directly from Forex Factory. May be blocked on cloud IPs.

    If FF_PROXY_URL is set (Cloudflare Worker), uses it as intermediary.
    """
    proxy = getattr(settings, "ff_proxy_url", "") or ""
    if proxy:
        url = f"{proxy.rstrip('/')}/?url={quote(FF_URL, safe='')}"
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

    return _parse_ff_list(raw)


# ── Source 3: Finnhub (paid fallback) ─────────────────
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
            log.info("Finnhub status=%s body=%s", r.status_code, r.text[:150])
            return []
        data = r.json()
    except Exception as exc:
        log.warning("Finnhub fetch failed: %s", exc)
        return []

    raw = data.get("economicCalendar") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        log.warning("Finnhub unexpected format: %s", type(data))
        return []

    allowed = set(settings.allowed_countries)
    out: list[dict[str, Any]] = []

    for e in raw:
        country = str(e.get("country") or "").upper()
        if allowed:
            # Finnhub uses "US", "EU", "GB" — also accept currency codes
            currency = str(e.get("currency") or "").upper()
            if country not in allowed and currency not in allowed:
                continue

        title = e.get("event") or ""
        importance = str(e.get("impact") or "").lower()
        # Finnhub numeric impacts: 1=low, 2=medium, 3=high
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


# ── Source 4: FMP (paid fallback) ─────────────────────
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

            allowed = set(settings.allowed_countries)
            out: list[dict[str, Any]] = []
            for e in raw:
                country = str(e.get("country") or "").upper()
                if allowed and country not in allowed:
                    currency = str(e.get("currency") or "").upper()
                    if currency not in allowed:
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


# ── Orchestrator ──────────────────────────────────────
def fetch_all() -> tuple[list[dict[str, Any]], dict]:
    """Try sources in order, return first non-empty + diagnostics."""
    diag = {
        "github": 0,
        "ff": 0,
        "finnhub": 0,
        "fmp": 0,
        "used": None,
        "has_proxy": bool(getattr(settings, "ff_proxy_url", "")),
        "has_github_url": bool(getattr(settings, "ff_github_url", "")),
    }

    # 1) GitHub cache (fastest, most reliable)
    gh = fetch_forexfactory_from_github()
    diag["github"] = len(gh)
    if gh:
        diag["used"] = "github"
        return gh, diag

    # 2) Forex Factory direct (or via proxy)
    ff = fetch_forexfactory()
    diag["ff"] = len(ff)
    if ff:
        diag["used"] = "forexfactory"
        return ff, diag

    # 3) Finnhub (only if paid)
    fh = fetch_finnhub()
    diag["finnhub"] = len(fh)
    if fh:
        diag["used"] = "finnhub"
        return fh, diag

    # 4) FMP (only if subscribed)
    fmp = fetch_fmp()
    diag["fmp"] = len(fmp)
    if fmp:
        diag["used"] = "fmp"
        return fmp, diag

    log.warning("All news sources failed: %s", diag)
    return [], diag
