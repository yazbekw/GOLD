"""Technical indicators: EMA / RSI / ATR + simple S/R."""
from __future__ import annotations

from typing import Any

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period).mean()


def compute_indicators(df: pd.DataFrame, source: str = "") -> dict[str, Any]:
    close = df["close"].astype(float)
    if len(close) < 5:
        return {}

    out: dict[str, Any] = {
        "source": source,
        "price": round(float(close.iloc[-1]), 2),
    }

    for p in (20, 50, 200):
        if len(close) >= p:
            out[f"ema{p}"] = round(float(ema(close, p).iloc[-1]), 2)

    if len(close) >= 20:
        rv = float(rsi(close, 14).iloc[-1])
        out["rsi14"] = round(rv, 2)
        out["rsi_state"] = (
            "overbought" if rv >= 70 else "oversold" if rv <= 30 else "neutral"
        )

    if len(df) >= 20:
        out["atr14"] = round(float(atr(df, 14).iloc[-1]), 2)

    window = df.tail(50)
    if not window.empty:
        out["recent_high"] = round(float(window["high"].max()), 2)
        out["recent_low"] = round(float(window["low"].min()), 2)

    if "ema20" in out and "ema50" in out:
        if out["ema20"] > out["ema50"] and out["price"] > out["ema20"]:
            out["trend"] = "up"
        elif out["ema20"] < out["ema50"] and out["price"] < out["ema20"]:
            out["trend"] = "down"
        else:
            out["trend"] = "flat"
    return out
