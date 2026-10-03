"""Telegram notifier via plain requests (no heavy deps)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import requests

from config import settings

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"


def _send(text: str) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.warning("Telegram not configured")
        return False
    try:
        r = requests.post(
            API.format(token=settings.telegram_bot_token),
            json={
                "chat_id": settings.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if r.status_code >= 400:
            log.error("Telegram %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:
        log.exception("Telegram send failed: %s", exc)
        return False


def _macro_lines(macro: dict) -> str:
    lines: list[str] = []
    for name in ("DXY", "US10Y", "VIX"):
        d = macro.get(name)
        if not d:
            continue
        arrow = {"up": "↗️", "down": "↘️", "flat": "➡️"}.get(d.get("trend", "flat"), "➡️")
        lines.append(f"• {name}: {d['value']} {arrow} ({d['change_24h_pct']:+.2f}%)")
    return "\n".join(lines) if lines else "• لا بيانات"


def send_pre_event(event: dict, brief: dict) -> bool:
    when = datetime.fromisoformat(event["event_time"]).astimezone(settings.tz)
    time_str = when.strftime("%Y-%m-%d %H:%M")

    bias_map = {"bullish": "صعود مرجّح", "bearish": "هبوط مرجّح", "unclear": "غير واضح"}
    bias_text = bias_map.get(brief["bias"], "غير واضح")

    scenarios = "\n".join(
        f"{i}️⃣ {s['label']}\n    → {s['outcome']}"
        for i, s in enumerate(brief["scenarios"], 1)
    )
    reasons = "\n".join(f"• {r}" for r in brief["reasons"]) or "• لا سياق كافٍ"

    lv = brief.get("levels") or {}
    level_lines: list[str] = []
    if lv.get("resistance"):
        level_lines.append(f"• مقاومة: {lv['resistance']}")
    if lv.get("support_1"):
        level_lines.append(f"• دعم 1: {lv['support_1']}")
    if lv.get("support_2"):
        level_lines.append(f"• دعم 2: {lv['support_2']}")

    gold = brief.get("gold") or {}
    gold_price = gold.get("price", "—")

    text = (
        "📊 <b>تنبيه خبر مهم</b>\n\n"
        f"🕐 الوقت: {time_str} ({settings.tz_name})\n"
        f"📰 الحدث: <b>{brief['title']}</b>\n"
        "🎯 الأصل: الذهب (XAU/USD)\n\n"
        "━━━━━━━━━━━━━━━\n"
        f"📈 التوقع: {brief['forecast']} | السابق: {brief['previous']}\n\n"
        "🔮 <b>السيناريوهات:</b>\n"
        f"{scenarios}\n\n"
        "━━━━━━━━━━━━━━━\n"
        "📊 <b>السياق الحالي:</b>\n"
        f"{_macro_lines(brief['macro'])}\n"
        f"• الذهب: {gold_price}\n\n"
        "🧠 <b>لماذا هذا التحيز؟</b>\n"
        f"{reasons}\n\n"
        "━━━━━━━━━━━━━━━\n"
        f"🎯 الاتجاه الأرجح: <b>{bias_text}</b> (ثقة {brief['confidence']}%)\n\n"
    )
    if level_lines:
        text += "📍 <b>مستويات المراقبة:</b>\n" + "\n".join(level_lines) + "\n\n"

    text += (
        "💡 <b>نصائح:</b>\n"
        "• لا تدخل قبل الخبر\n"
        "• انتظر 5–15 دقيقة بعد الصدور\n"
        "• راقب DXY و US10Y للتأكيد\n\n"
        "⚠️ المخاطرة عالية — قلّل الحجم"
    )
    return _send(text)


def send_reminder(event: dict, minutes: int) -> bool:
    when = datetime.fromisoformat(event["event_time"]).astimezone(settings.tz)
    text = (
        "⏰ <b>تذكير</b>\n"
        f"خبر <b>{event['title']}</b> بعد {minutes} دقيقة ({when.strftime('%H:%M')}).\n"
        "لا تدخل الآن — استعد فقط."
    )
    return _send(text)


def send_post_event(event: dict, brief: dict) -> bool:
    dir_map = {"bullish": "صعود", "bearish": "هبوط", "neutral": "عرضي", "unclear": "غير واضح"}
    surprise_map = {
        "higher": "أعلى من المتوقع",
        "lower": "أقل من المتوقع",
        "inline": "مطابق",
        "unknown": "غير معروف",
    }

    confirmed = "\n".join(f"• {c}" for c in brief["confirmed"]) or "• لا بيانات"
    lv = brief.get("levels") or {}
    lvl_lines = []
    if lv.get("resistance"):
        lvl_lines.append(f"مقاومة: {lv['resistance']}")
    if lv.get("support_1"):
        lvl_lines.append(f"دعم 1: {lv['support_1']}")
    if lv.get("support_2"):
        lvl_lines.append(f"دعم 2: {lv['support_2']}")

    text = (
        "✅ <b>نتيجة الخبر</b>\n\n"
        f"📰 {brief['title']}\n"
        f"• الفعلي: <b>{brief['actual']}</b>\n"
        f"• المتوقع: {brief['forecast']}\n"
        f"• السابق: {brief['previous']}\n"
        f"• التقييم: <b>{surprise_map.get(brief['surprise'], '—')}</b>\n\n"
        "━━━━━━━━━━━━━━━\n"
        "📊 <b>رد فعل السوق:</b>\n"
        f"{confirmed}\n\n"
        f"🎯 الاتجاه المرجّح: <b>{dir_map.get(brief['direction'], '—')}</b>\n\n"
    )
    if lvl_lines:
        text += "📍 <b>مستويات:</b>\n" + "\n".join(f"• {l}" for l in lvl_lines) + "\n\n"
    text += "💡 انتظر التأكيد قبل الدخول — لا تطارد السعر."
    return _send(text)


def send_health(text: str) -> bool:
    return _send(f"🩺 <b>حالة البوت</b>\n{text}")
