"""Gold price: Binance PAXGUSDT primary, Yahoo GC=F fallback."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import requests
import yfinance as yf

from config import settings

log = logging.getLogger(__name__)

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"

COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "qav", "trades", "tbb", "tbq", "ignore",
]


def _binance_candles(symbol: str, interval: str = "1h", limit: int = 250) -> list:
    r = requests.get(
        BINANCE_KLINES,
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _from_binance() -> tuple[pd.DataFrame | None, str]:
    try:
        candles = _binance_candles(settings.gold_binance_symbol, "1h", 250)
        df = pd.DataFrame(candles, columns=COLUMNS)
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["close"])
        if df.empty:
            return None, ""
        return df, f"binance:{settings.gold_binance_symbol}"
    except Exception as exc:
        log.warning("Binance gold failed: %s", exc)
        return None, ""


def _from_yahoo() -> tuple[pd.DataFrame | None, str]:
    try:
        hist = yf.Ticker(settings.gold_yahoo_symbol).history(period="60d", interval="1h")
        if hist is None or hist.empty:
            return None, ""
        df = hist.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
        if df.empty:
            return None, ""
        return df, f"yahoo:{settings.gold_yahoo_symbol}"
    except Exception as exc:
        log.warning("Yahoo gold failed: %s", exc)
        return None, ""


def fetch_gold() -> dict[str, Any]:
    df, source = _from_binance()
    if df is None:
        df, source = _from_yahoo()
    if df is None:
        return {}
    from analysis.technical import compute_indicators
    return compute_indicators(df, source=source)
