"""Fetch macro indicators.

Source priority:
1. Yahoo Finance via Cloudflare Worker proxy (recommended, no blocking)
2. Yahoo Finance direct (may be blocked on cloud IPs)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from config import settings

log = logging.getLogger(__name__)

# Yahoo Finance chart endpoint (public, no key needed)
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

TICKERS = {
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
    "VIX": "^VIX",
}


def _build_yahoo_url(symbol: str, interval: str = "1h", range_: str = "5d") -> str:
    """Build a Yahoo Finance chart URL for the given symbol."""
    return (
        f"{YAHOO_CHART.format(symbol=symbol)}"
        f"?interval={interval}&range={range_}"
    )


def _fetch_via_proxy(url: str) -> dict[str, Any] | None:
    """Fetch URL via Cloudflare Worker proxy."""
    proxy = getattr(settings, "ff_proxy_url", "") or ""
    if not proxy:
        return None
    encoded = quote(url, safe="")
    proxy_url = f"{proxy.rstrip('/')}/?url={encoded}"
    try:
        r = requests.get(proxy_url, timeout=20)
        if r.status_code != 200:
            log.warning("Proxy returned %s for %s", r.status_code, url)
            return None
        return r.json()
    except Exception as exc:
        log.warning("Proxy fetch failed: %s", exc)
        return None


def _fetch_direct(url: str) -> dict[str, Any] | None:
    """Fetch URL directly (fallback)."""
    try:
        r = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Referer": "https://finance.yahoo.com/",
            },
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as exc:
        log.warning("Direct fetch failed: %s", exc)
        return None


def _parse_yahoo(data: dict) -> dict[str, Any] | None:
    """Parse Yahoo chart response into a snapshot dict."""
    try:
        result = data.get("chart", {}).get("result")
        if not result:
            return None
        res = result[0]
        timestamps = res.get("timestamp") or []
        indicators = res.get("indicators", {}).get("quote") or []
        if not indicators:
            return None
        closes = indicators[0].get("close") or []
        # Pair timestamps with closes, dropping None values
        pairs = [
            (t, c) for t, c in zip(timestamps, closes) if c is not None
        ]
        if not pairs:
            return None

        last = float(pairs[-1][1])
        prev = float(pairs[-2][1]) if len(pairs) > 1 else last
        # ~24 hours back for hourly data (24 candles)
        ref_idx = max(0, len(pairs) - 25)
        ref = float(pairs[ref_idx][1])

        c1h = (last - prev) / prev * 100 if prev else 0.0
        c24h = (last - ref) / ref * 100 if ref else 0.0

        if c24h > 0.1:
            trend = "up"
        elif c24h < -0.1:
            trend = "down"
        else:
            trend = "flat"

        return {
            "value": round(last, 4),
            "change_1h_pct": round(c1h, 3),
            "change_24h_pct": round(c24h, 3),
            "trend": trend,
            "source": "yahoo",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        log.warning("Yahoo parse failed: %s", exc)
        return None


def _snapshot(name: str, symbol: str) -> dict[str, Any]:
    """Get a single macro snapshot, trying proxy first then direct."""
    url = _build_yahoo_url(symbol)

    # Try proxy
    data = _fetch_via_proxy(url)
    if data:
        parsed = _parse_yahoo(data)
        if parsed:
            log.info("%s (proxy): %s", name, parsed.get("value"))
            return parsed

    # Try direct
    data = _fetch_direct(url)
    if data:
        parsed = _parse_yahoo(data)
        if parsed:
            log.info("%s (direct): %s", name, parsed.get("value"))
            return parsed

    log.warning("%s: all sources failed", name)
    return {}


def fetch_macro() -> dict[str, dict[str, Any]]:
    """Fetch DXY, US10Y, VIX."""
    out: dict[str, dict[str, Any]] = {}
    for name, symbol in TICKERS.items():
        snap = _snapshot(name, symbol)
        if snap:
            out[name] = snap
    log.info("Macro fetched: %s", list(out.keys()))
    return out


def fetch_us10y_yield() -> float | None:
    """Convenience for decision engine: return current US10Y value."""
    us10y = fetch_macro().get("US10Y")
    return us10y.get("value") if us10y else None
