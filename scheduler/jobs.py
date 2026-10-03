"""Scheduled jobs — the heart of the bot."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from config import settings
from data import macro_fetcher, news_fetcher, price_fetcher
from engine import decision_engine
from notify import telegram
from storage import db

log = logging.getLogger(__name__)


def refresh_events() -> int:
    events = news_fetcher.fetch_all()
    count = 0
    for ev in events:
        if db.upsert_event(ev):
            count += 1
    log.info("Refreshed %d events", count)
    return count


def _window_around(minutes_offset: int, tolerance: int = 2) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = now + timedelta(minutes=minutes_offset - tolerance)
    end = now + timedelta(minutes=minutes_offset + tolerance)
    return start.isoformat(), end.isoformat()


def _send_pre_event_if_due() -> None:
    start, end = _window_around(settings.pre_event_minutes)
    events = db.events_between(start, end)
    if not events:
        return
    macro = macro_fetcher.fetch_macro()
    gold = price_fetcher.fetch_gold()
    for ev in events:
        eid = ev["id"]
        if db.already_notified(eid, "pre_60"):
            continue
        brief = decision_engine.build_pre_event_brief(ev, macro, gold)
        if telegram.send_pre_event(ev, brief):
            db.log_notification(eid, "pre_60")
            db.save_prediction({
                "event_id": eid,
                "scenario": brief["bias"],
                "confidence": brief["confidence"],
                "context": {"macro": macro, "gold": gold},
                "levels": brief["levels"],
                "message_sent_at": datetime.now(timezone.utc).isoformat(),
            })


def _send_reminder_if_due() -> None:
    start, end = _window_around(settings.reminder_minutes, tolerance=1)
    for ev in db.events_between(start, end):
        if db.already_notified(ev["id"], "reminder"):
            continue
        if telegram.send_reminder(ev, settings.reminder_minutes):
            db.log_notification(ev["id"], "reminder")


def _send_post_event_if_due() -> None:
    start, end = _window_around(-settings.post_event_minutes, tolerance=1)
    for ev in db.events_between(start, end):
        if db.already_notified(ev["id"], "post_5"):
            continue
        if not ev.get("actual"):
            continue
        macro = macro_fetcher.fetch_macro()
        gold = price_fetcher.fetch_gold()
        brief = decision_engine.build_post_event_brief(ev, macro, gold, ev["actual"])
        if telegram.send_post_event(ev, brief):
            db.log_notification(ev["id"], "post_5")
            db.save_outcome({
                "event_id": ev["id"],
                "actual_released": datetime.now(timezone.utc).isoformat(),
                "direction": brief["direction"],
                "notes": f"surprise={brief['surprise']}",
            })


def run_all() -> dict:
    refresh_events()
    _send_pre_event_if_due()
    _send_reminder_if_due()
    _send_post_event_if_due()
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}
