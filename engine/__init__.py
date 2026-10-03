"""Build pre-event scenario + post-event verdict."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def _macro_score(macro: dict, gold: dict) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    dxy = macro.get("DXY", {})
    if dxy:
        if dxy.get("trend") == "up":
            score -= 1
            reasons.append(f"DXY صاعد ({_pct(dxy['change_24h_pct'])}) → ضغط هبوطي على الذهب")
        elif dxy.get("trend") == "down":
            score += 1
            reasons.append(f"DXY هابط ({_pct(dxy['change_24h_pct'])}) → دعم للذهب")

    us10 = macro.get("US10Y", {})
    if us10:
        if us10.get("trend") == "up":
            score -= 1
            reasons.append(f"US10Y صاعد ({_pct(us10['change_24h_pct'])}) → ضغط هبوطي")
        elif us10.get("trend") == "down":
            score += 1
            reasons.append(f"US10Y هابط ({_pct(us10['change_24h_pct'])}) → دعم")

    vix = macro.get("VIX", {})
    if vix:
        v = vix.get("value", 0) or 0
        if v >= 20:
            score += 1
            reasons.append(f"VIX مرتفع ({v}) → Risk-Off → دعم للذهب")
        elif v <= 14:
            reasons.append(f"VIX هادئ ({v}) → لا خطر حالي")

    if gold.get("trend") == "up":
        score += 1
        reasons.append("الذهب فوق EMA20 → اتجاه فني صاعد")
    elif gold.get("trend") == "down":
        score -= 1
        reasons.append("الذهب تحت EMA20 → اتجاه فني هابط")

    return score, reasons


def build_pre_event_brief(event: dict, macro: dict, gold: dict) -> dict[str, Any]:
    title = event.get("title") or ""
    forecast = event.get("forecast") or "—"
    previous = event.get("previous") or "—"

    score, reasons = _macro_score(macro, gold)

    if score >= 2:
        bias, confidence = "bullish", 55 + min(score * 5, 20)
    elif score <= -2:
        bias, confidence = "bearish", 55 + min(abs(score) * 5, 20)
    else:
        bias, confidence = "unclear", 40

    scenarios = [
        {"label": "أقل من المتوقع (Dovish)", "outcome": "DXY ↓ / الذهب ↑"},
        {"label": "مطابق للمتوقع", "outcome": "حركة محدودة / عرضي"},
        {"label": "أعلى من المتوقع (Hawkish)", "outcome": "DXY ↑ / الذهب ↓"},
    ]

    levels: dict[str, Any] = {}
    if gold:
        if "recent_high" in gold:
            levels["resistance"] = gold["recent_high"]
        if "ema50" in gold:
            levels["support_1"] = gold["ema50"]
        if "recent_low" in gold:
            levels["support_2"] = gold["recent_low"]

    return {
        "title": title,
        "forecast": forecast,
        "previous": previous,
        "bias": bias,
        "confidence": confidence,
        "reasons": reasons,
        "scenarios": scenarios,
        "levels": levels,
        "macro": macro,
        "gold": gold,
    }


def build_post_event_brief(
    event: dict, macro: dict, gold: dict, actual: str
) -> dict[str, Any]:
    forecast = str(event.get("forecast") or "")
    prev = str(event.get("previous") or "")
    actual_s = (actual or "").strip()

    surprise = "unknown"
    try:
        a = float(actual_s.replace("%", "").strip())
        f = float(forecast.replace("%", "").strip())
        surprise = "higher" if a > f else "lower" if a < f else "inline"
    except Exception:
        pass

    confirmed: list[str] = []
    dxy = macro.get("DXY", {})
    if dxy:
        confirmed.append(f"DXY: {_pct(dxy['change_1h_pct'])} ({dxy['trend']})")
    us10 = macro.get("US10Y", {})
    if us10:
        confirmed.append(f"US10Y: {_pct(us10['change_1h_pct'])} ({us10['trend']})")
    if gold:
        confirmed.append(f"الذهب: {gold.get('price')} ({gold.get('trend')})")

    direction = {
        "higher": "bearish",
        "lower": "bullish",
        "inline": "neutral",
    }.get(surprise, "unclear")

    return {
        "title": event.get("title"),
        "actual": actual_s or "—",
        "forecast": forecast or "—",
        "previous": prev or "—",
        "surprise": surprise,
        "direction": direction,
        "confirmed": confirmed,
        "gold_price": gold.get("price"),
        "levels": {
            "support_1": gold.get("ema50"),
            "support_2": gold.get("recent_low"),
            "resistance": gold.get("recent_high"),
        },
    }
