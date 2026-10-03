"""Telegram notifier — grouped messages + follow-ups."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import requests

from config import settings

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"


# ── Core ──────────────────────────────────────────────
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


def _macro_block(macro: dict) -> str:
    lines: list[str] = []
    for name in ("DXY", "US10Y", "VIX"):
        d = macro.get(name)
        if not d:
            continue
        arrow = {"up": "↗️", "down": "↘️", "flat": "➡️"}.get(d.get("trend", "flat"), "➡️")
        lines.append(
            f"• {name}: {d['value']} {arrow} ({d['change_24h_pct']:+.2f}%)"
        )
    return "\n".join(lines) if lines else "• لا بيانات"


def _bias_text(bias: str) -> str:
    return {
        "bullish": "صعود مرجّح",
        "bearish": "هبوط مرجّح",
        "unclear": "غير واضح",
    }.get(bias, "غير واضح")


def _levels_block(levels: dict) -> str:
    lines: list[str] = []
    if levels.get("resistance"):
        lines.append(f"• مقاومة: {levels['resistance']}")
    if levels.get("support_1"):
        lines.append(f"• دعم 1: {levels['support_1']}")
    if levels.get("support_2"):
        lines.append(f"• دعم 2: {levels['support_2']}")
    return "\n".join(lines) if lines else "• لا مستويات"


# ── Pre-event (Grouped) ───────────────────────────────
def send_pre_event_group(events: list[dict], brief: dict) -> bool:
    """Send a single grouped message for one or more events at the same time."""
    if not events:
        return False

    # Time
    first_time = datetime.fromisoformat(events[0]["event_time"]).astimezone(settings.tz)
    time_str = first_time.strftime("%Y-%m-%d %H:%M")

    # Header
    count = len(events)
    if count == 1:
        header = f"📊 <b>تنبيه خبر مهم</b>\n\n🕐 الوقت: {time_str} ({settings.tz_name})"
    else:
        header = f"📊 <b>{count} أخبار مهمة في نفس التوقيت</b>\n\n🕐 الوقت: {time_str} ({settings.tz_name})"

    # Events list
    events_lines: list[str] = []
    for i, ev in enumerate(events, 1):
        prefix = f"{i}️⃣ " if count > 1 else ""
        title = ev.get("title") or "—"
        forecast = ev.get("forecast") or "—"
        previous = ev.get("previous") or "—"
        events_lines.append(
            f"{prefix}<b>{title}</b>\n"
            f"   📈 التوقع: {forecast} | السابق: {previous}"
        )
    events_block = "\n\n".join(events_lines)

    # Scenarios
    scenarios_block = (
        "🔮 <b>السيناريوهات:</b>\n"
        "1️⃣ أقل من المتوقع → DXY ↓ / الذهب ↑\n"
        "2️⃣ مطابق للمتوقع → حركة محدودة\n"
        "3️⃣ أعلى من المتوقع → DXY ↑ / الذهب ↓"
    )

    # Context
    context_block = (
        "📊 <b>السياق الحالي:</b>\n"
        f"{_macro_block(brief.get('macro') or {})}\n"
        f"• الذهب: {(brief.get('gold') or {}).get('price', '—')}"
    )

    # Reasons
    reasons_list = brief.get("reasons") or []
    reasons_block = (
        "🧠 <b>لماذا هذا التحيز؟</b>\n"
        + "\n".join(f"• {r}" for r in reasons_list)
        if reasons_list else
        "🧠 <b>لماذا هذا التحيز؟</b>\n• لا سياق كافٍ"
    )

    # Decision
    bias = _bias_text(brief.get("bias", "unclear"))
    confidence = brief.get("confidence", 0)
    decision_block = f"🎯 الاتجاه الأرجح: <b>{bias}</b> (ثقة {confidence}%)"

    # Levels
    levels_block = "📍 <b>مستويات المراقبة:</b>\n" + _levels_block(brief.get("levels") or {})

    # Advice
    advice_block = (
        "💡 <b>نصائح:</b>\n"
        "• لا تدخل قبل الخبر\n"
        "• انتظر 5–15 دقيقة بعد الصدور\n"
        "• راقب DXY و US10Y للتأكيد\n\n"
        "⚠️ المخاطرة عالية — قلّل الحجم"
    )

    sep = "━━━━━━━━━━━━━━━━━━━"
    text = (
        f"{header}\n\n{sep}\n{events_block}\n\n{sep}\n"
        f"{scenarios_block}\n\n{sep}\n"
        f"{context_block}\n\n{sep}\n"
        f"{reasons_block}\n\n{sep}\n"
        f"{decision_block}\n\n"
        f"{levels_block}\n\n{sep}\n"
        f"{advice_block}"
    )
    return _send(text)


# ── Reminder (Grouped) ────────────────────────────────
def send_reminder_group(events: list[dict], minutes: int) -> bool:
    if not events:
        return False

    first_time = datetime.fromisoformat(events[0]["event_time"]).astimezone(settings.tz)
    time_str = first_time.strftime("%H:%M")

    if len(events) == 1:
        head = f"⏰ <b>تذكير</b>\nخبر <b>{events[0]['title']}</b> بعد {minutes} دقيقة ({time_str})."
    else:
        titles = "\n".join(f"• {e['title']}" for e in events)
        head = (
            f"⏰ <b>تذكير</b>\n"
            f"{len(events)} أخبار مهمة بعد {minutes} دقيقة ({time_str}):\n{titles}"
        )

    text = head + "\n\nلا تدخل الآن — استعد فقط."
    return _send(text)


# ── Post-event (Grouped) ──────────────────────────────
def send_post_event_group(events: list[dict], briefs: list[dict]) -> bool:
    """Send results for one or more events."""
    if not events or not briefs:
        return False

    surprise_map = {
        "higher": "أعلى من المتوقع ⚠️",
        "lower": "أقل من المتوقع ✅",
        "inline": "مطابق",
        "unknown": "غير معروف",
    }
    dir_map = {"bullish": "صعود", "bearish": "هبوط", "neutral": "عرضي", "unclear": "غير واضح"}

    results: list[str] = []
    for i, (ev, br) in enumerate(zip(events, briefs), 1):
        prefix = f"{i}️⃣ " if len(events) > 1 else ""
        results.append(
            f"{prefix}<b>{ev.get('title')}</b>\n"
            f"   • الفعلي: <b>{br.get('actual', '—')}</b>\n"
            f"   • المتوقع: {br.get('forecast', '—')}\n"
            f"   • التقييم: {surprise_map.get(br.get('surprise'), '—')}"
        )

    # Confirmation
    first = briefs[0]
    confirmed = first.get("confirmed") or []
    confirmed_block = (
        "📊 <b>رد فعل السوق:</b>\n"
        + "\n".join(f"• {c}" for c in confirmed)
        if confirmed else
        "📊 <b>رد فعل السوق:</b>\n• لا بيانات"
    )

    direction = dir_map.get(first.get("direction", "unclear"), "—")

    # Levels
    lv = first.get("levels") or {}
    lv_lines: list[str] = []
    if lv.get("resistance"):
        lv_lines.append(f"• مقاومة: {lv['resistance']}")
    if lv.get("support_1"):
        lv_lines.append(f"• دعم 1: {lv['support_1']}")
    if lv.get("support_2"):
        lv_lines.append(f"• دعم 2: {lv['support_2']}")
    levels_block = "📍 <b>مستويات:</b>\n" + "\n".join(lv_lines) if lv_lines else ""

    sep = "━━━━━━━━━━━━━━━━━━━"
    text = (
        "✅ <b>نتيجة الخبر</b>\n\n"
        + "\n\n".join(results)
        + f"\n\n{sep}\n{confirmed_block}\n\n"
        + f"🎯 الاتجاه المرجّح: <b>{direction}</b>\n"
    )
    if levels_block:
        text += f"\n{levels_block}\n"
    text += "\n💡 انتظر التأكيد قبل الدخول — لا تطارد السعر."
    return _send(text)


# ── Follow-up (T+15m, T+60m, T+4h) ────────────────────
def send_follow_up(events: list[dict], brief: dict, elapsed_label: str) -> bool:
    """Send a follow-up message at T+15m, T+60m, T+4h."""
    if not events:
        return False

    gold = brief.get("gold") or {}
    macro = brief.get("macro") or {}

    titles = "\n".join(f"• {e['title']}" for e in events)
    title = f"📊 <b>متابعة بعد {elapsed_label}</b>\n\n{titles}"

    context = (
        "📊 <b>السياق الحالي:</b>\n"
        f"{_macro_block(macro)}\n"
        f"• الذهب: {gold.get('price', '—')}"
    )

    gold_trend = gold.get("trend", "flat")
    trend_map = {"up": "صاعد ↗️", "down": "هابط ↘️", "flat": "عرضي ➡️"}
    trend_text = trend_map.get(gold_trend, "—")

    note_map = {
        "T+15m": "هل ثبت الاتجاه الأولي؟",
        "T+60m": "هل استمر الاتجاه بعد الساعة الأولى؟",
        "T+4h": "الحصيلة النهائية بعد 4 ساعات.",
    }
    note = note_map.get(elapsed_label, "")

    sep = "━━━━━━━━━━━━━━━━━━━"
    text = (
        f"{title}\n\n{sep}\n{context}\n\n"
        f"📈 اتجاه الذهب الآن: <b>{trend_text}</b>\n\n"
        f"💡 {note}"
    )
    return _send(text)


# ── Daily Report ──────────────────────────────────────
def send_daily_report(events_today: list[dict], events_past: list[dict]) -> bool:
    """08:00 Damascus — what's coming today + what happened yesterday."""
    sep = "━━━━━━━━━━━━━━━━━━━"

    if events_today:
        today_lines = []
        for ev in events_today:
            when = datetime.fromisoformat(ev["event_time"]).astimezone(settings.tz)
            today_lines.append(
                f"• {when.strftime('%H:%M')} — <b>{ev['title']}</b>\n"
                f"   توقع: {ev.get('forecast') or '—'}"
            )
        today_block = "📅 <b>أخبار اليوم:</b>\n" + "\n\n".join(today_lines)
    else:
        today_block = "📅 <b>أخبار اليوم:</b>\n• لا أخبار مهمة اليوم"

    text = f"🌅 <b>تقرير صباحي</b>\n\n{today_block}"

    if events_past:
        text += f"\n\n{sep}\n📊 <b>أخبار الأمس:</b>\n"
        for ev in events_past[:5]:
            text += f"• {ev.get('title')}: {ev.get('actual') or '—'}\n"

    text += f"\n\n{sep}\n💡 راقب DXY و US10Y قبل الأخبار المهمة."
    return _send(text)


def send_health(text: str) -> bool:
    return _send(f"🩺 <b>حالة البوت</b>\n{text}")
