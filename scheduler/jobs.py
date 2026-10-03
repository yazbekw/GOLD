"""Scheduled jobs — grouped notifications + follow-ups."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from config import settings
from data import macro_fetcher, news_fetcher, price_fetcher
from engine import decision_engine
from notify import telegram
from storage import db

log = logging.getLogger(__name__)


# ── Fetch events ──────────────────────────────────────
def refresh_events() -> dict:
    result = {"fetched": 0, "stored": 0, "failed": 0, "diag": {}, "errors": []}
    try:
        events, diag = news_fetcher.fetch_all()
        result["diag"] = diag
        result["fetched"] = len(events)
    except Exception as exc:
        result["errors"].append(f"fetch: {exc}")
        return result

    for ev in events:
        try:
            row = db.upsert_event(ev)
            if row:
                result["stored"] += 1
            else:
                result["failed"] += 1
        except Exception as exc:
            result["failed"] += 1
            if len(result["errors"]) < 5:
                result["errors"].append(f"upsert: {exc}")

    log.info("refresh_events: %s", result)
    return result


# ── Window helpers ────────────────────────────────────
def _window(minutes_offset: int, tolerance: int = 3) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = now + timedelta(minutes=minutes_offset - tolerance)
    end = now + timedelta(minutes=minutes_offset + tolerance)
    return start.isoformat(), end.isoformat()


def _group_by_time(events: list[dict], tolerance_seconds: int = 120) -> list[list[dict]]:
    """Group events by event_time within tolerance."""
    if not events:
        return []
    sorted_events = sorted(events, key=lambda e: e["event_time"])
    groups: list[list[dict]] = []
    current: list[dict] = []
    last_ts: datetime | None = None

    for ev in sorted_events:
        ts = datetime.fromisoformat(ev["event_time"])
        if last_ts is None or (ts - last_ts).total_seconds() <= tolerance_seconds:
            current.append(ev)
        else:
            if current:
                groups.append(current)
            current = [ev]
        last_ts = ts

    if current:
        groups.append(current)
    return groups


# ── Pre-event (T-60m) ─────────────────────────────────
def _send_pre_event_if_due() -> dict:
    start, end = _window(settings.pre_event_minutes, tolerance=3)
    events = db.events_between(start, end)
    if not events:
        return {"candidates": 0, "sent_groups": 0}

    macro = macro_fetcher.fetch_macro()
    gold = price_fetcher.fetch_gold()
    sent = 0

    for group in _group_by_time(events):
        # Skip if all already notified
        pending = [e for e in group if not db.already_notified(e["id"], "pre_60")]
        if not pending:
            continue

        brief = decision_engine.build_pre_event_brief(pending[0], macro, gold)
        if telegram.send_pre_event_group(pending, brief):
            for ev in pending:
                db.log_notification(ev["id"], "pre_60")
            # Save one prediction per event
            for ev in pending:
                db.save_prediction({
                    "event_id": ev["id"],
                    "scenario": brief["bias"],
                    "confidence": brief["confidence"],
                    "context": {"macro": macro, "gold": gold},
                    "levels": brief["levels"],
                    "message_sent_at": datetime.now(timezone.utc).isoformat(),
                })
            sent += 1

    return {"candidates": len(events), "sent_groups": sent}


# ── Reminder (T-15m) ──────────────────────────────────
def _send_reminder_if_due() -> dict:
    start, end = _window(settings.reminder_minutes, tolerance=2)
    events = db.events_between(start, end)
    if not events:
        return {"candidates": 0, "sent_groups": 0}

    sent = 0
    for group in _group_by_time(events):
        pending = [e for e in group if not db.already_notified(e["id"], "reminder")]
        if not pending:
            continue
        if telegram.send_reminder_group(pending, settings.reminder_minutes):
            for ev in pending:
                db.log_notification(ev["id"], "reminder")
            sent += 1

    return {"candidates": len(events), "sent_groups": sent}


# ── Post-event (T+5m) ─────────────────────────────────
def _send_post_event_if_due() -> dict:
    start, end = _window(-settings.post_event_minutes, tolerance=2)
    events = db.events_between(start, end)
    if not events:
        return {"candidates": 0, "sent_groups": 0}

    macro = macro_fetcher.fetch_macro()
    gold = price_fetcher.fetch_gold()
    sent = 0

    for group in _group_by_time(events):
        pending = [
            e for e in group
            if not db.already_notified(e["id"], "post_5") and e.get("actual")
        ]
        if not pending:
            continue

        briefs = [
            decision_engine.build_post_event_brief(e, macro, gold, e["actual"])
            for e in pending
        ]
        if telegram.send_post_event_group(pending, briefs):
            for ev, br in zip(pending, briefs):
                db.log_notification(ev["id"], "post_5")
                db.save_outcome({
                    "event_id": ev["id"],
                    "actual_released": datetime.now(timezone.utc).isoformat(),
                    "direction": br["direction"],
                    "notes": f"surprise={br['surprise']}",
                })
            sent += 1

    return {"candidates": len(events), "sent_groups": sent}


# ── Follow-ups (T+15m, T+60m, T+4h) ───────────────────
def _follow_up_stage(offset_minutes: int, label: str, key: str) -> dict:
    start, end = _window(-offset_minutes, tolerance=3)
    events = db.events_between(start, end)
    if not events:
        return {"candidates": 0, "sent_groups": 0}

    macro = macro_fetcher.fetch_macro()
    gold = price_fetcher.fetch_gold()
    sent = 0

    for group in _group_by_time(events):
        pending = [e for e in group if not db.already_notified(e["id"], key)]
        if not pending:
            continue
        brief = {"macro": macro, "gold": gold}
        if telegram.send_follow_up(pending, brief, label):
            for ev in pending:
                db.log_notification(ev["id"], key)
            sent += 1

    return {"candidates": len(events), "sent_groups": sent}


def _send_t15_if_due() -> dict:
    return _follow_up_stage(15, "T+15m", "followup_15")


def _send_t60_if_due() -> dict:
    return _follow_up_stage(60, "T+60m", "followup_60")


def _send_t4h_if_due() -> dict:
    return _follow_up_stage(240, "T+4h", "followup_4h")


# ── Daily report (08:00 Damascus) ─────────────────────
def send_daily_report_if_due() -> dict:
    now = datetime.now(settings.tz)
    # Send only between 08:00 and 08:15 Damascus
    if now.hour != 8 or now.minute > 15:
        return {"skipped": True, "reason": "outside 08:00-08:15 window"}

    # Check if already sent today
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    key = f"daily_{today_start.strftime('%Y%m%d')}"
    # Use a fake event_id = 0 in notifications_log for daily
    if db.already_notified(0, key):
        return {"skipped": True, "reason": "already sent"}

    today_end = today_start + timedelta(days=1)
    events_today = db.events_between(
        today_start.astimezone(timezone.utc).isoformat(),
        today_end.astimezone(timezone.utc).isoformat(),
    )

    yesterday_start = today_start - timedelta(days=1)
    events_past = db.events_between(
        yesterday_start.astimezone(timezone.utc).isoformat(),
        today_start.astimezone(timezone.utc).isoformat(),
    )

    ok = telegram.send_daily_report(events_today, events_past)
    if ok:
        db.log_notification(0, key)
    return {"sent": ok, "today": len(events_today), "yesterday": len(events_past)}


# ── Orchestrator ──────────────────────────────────────
def run_all() -> dict:
    return {
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat(),
        "refresh": refresh_events(),
        "pre": _send_pre_event_if_due(),
        "reminder": _send_reminder_if_due(),
        "post": _send_post_event_if_due(),
        "t15": _send_t15_if_due(),
        "t60": _send_t60_if_due(),
        "t4h": _send_t4h_if_due(),
        "daily": send_daily_report_if_due(),
    }


def run_daily() -> dict:
    """Run only the daily report (for cron)."""
    return send_daily_report_if_due()
