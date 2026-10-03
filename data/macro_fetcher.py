"""Fetch macro indicators via yfinance: DXY, US10Y, VIX."""
from __future__ import annotations

import logging
from typing import Any

import yfinance as yf

log = logging.getLogger(__name__)

TICKERS = {
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
    "VIX": "^VIX",
}


def _snapshot(symbol: str) -> dict[str, Any]:
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="5d", interval="1h")
        if hist is None or hist.empty:
            hist = t.history(period="10d", interval="1d")
        if hist is None or hist.empty:
            return {}
        close = hist["Close"].dropna()
        if close.empty:
            return {}
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) > 1 else last
        ref = float(close.iloc[-24]) if len(close) > 24 else prev
        c1 = (last - prev) / prev * 100 if prev else 0.0
        c24 = (last - ref) / ref * 100 if ref else 0.0
        if c24 > 0.1:
            trend = "up"
        elif c24 < -0.1:
            trend = "down"
        else:
            trend = "flat"
        return {
            "value": round(last, 4),
            "change_1h_pct": round(c1, 3),
            "change_24h_pct": round(c24, 3),
            "trend": trend,
        }
    except Exception as exc:
        log.warning("yfinance %s failed: %s", symbol, exc)
        return {}


def fetch_macro() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, symbol in TICKERS.items():
        snap = _snapshot(symbol)
        if snap:
            out[name] = snap
    return out
