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


def refresh_events() -> dict:
    """Fetch events and store them. Returns diagnostic dict."""
    result = {
        "fetched": 0,
        "stored": 0,
        "failed": 0,
        "fmp_raw": 0,
        "ff_raw": 0,
        "errors": [],
    }

    # --- FMP ---
    try:
        fmp_events = news_fetcher.fetch_fmp()
        result["fmp_raw"] = len(fmp_events)
    except Exception as exc:
        result["errors"].append(f"fmp_fetch: {exc}")
        fmp_events = []

    # --- ForexFactory fallback ---
    ff_events = []
    if not fmp_events:
        try:
            ff_events = news_fetcher.fetch_forexfactory()
            result["ff_raw"] = len(ff_events)
        except Exception as exc:
            result["errors"].append(f"ff_fetch: {exc}")

    events = fmp_events or ff_events
    result["fetched"] = len(events)

    # --- Store ---
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

    log.info(
        "refresh_events: fmp=%d ff=%d fetched=%d stored=%d failed=%d",
        result["fmp_raw"], result["ff_raw"],
        result["fetched"], result["stored"], result["failed"],
    )
    if result["errors"]:
        log.warning("refresh_events errors: %s", result["errors"])

    return result


def _window_around(minutes_offset: int, tolerance: int = 2) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = now + timedelta(minutes=minutes_offset - tolerance)
    end = now + timedelta(minutes=minutes_offset + tolerance)
    return start.isoformat(), end.isoformat()


def _send_pre_event_if_due() -> dict:
    start, end = _window_around(settings.pre_event_minutes)
    events = db.events_between(start, end)
    sent = 0
    for ev in events:
        eid = ev["id"]
        if db.already_notified(eid, "pre_60"):
            continue
        macro = macro_fetcher.fetch_macro()
        gold = price_fetcher.fetch_gold()
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
            sent += 1
    return {"candidates": len(events), "sent": sent}


def _send_reminder_if_due() -> dict:
    start, end = _window_around(settings.reminder_minutes, tolerance=1)
    events = db.events_between(start, end)
    sent = 0
    for ev in events:
        if db.already_notified(ev["id"], "reminder"):
            continue
        if telegram.send_reminder(ev, settings.reminder_minutes):
            db.log_notification(ev["id"], "reminder")
            sent += 1
    return {"candidates": len(events), "sent": sent}


def _send_post_event_if_due() -> dict:
    start, end = _window_around(-settings.post_event_minutes, tolerance=1)
    events = db.events_between(start, end)
    sent = 0
    for ev in events:
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
            sent += 1
    return {"candidates": len(events), "sent": sent}


def run_all() -> dict:
    out = {
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat(),
        "refresh": refresh_events(),
        "pre": _send_pre_event_if_due(),
        "reminder": _send_reminder_if_due(),
        "post": _send_post_event_if_due(),
    }
    return out
