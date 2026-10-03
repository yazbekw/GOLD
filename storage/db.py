"""Supabase wrapper — thin layer over supabase-py."""
from __future__ import annotations

import logging
from typing import Any, Optional

from supabase import Client, create_client

from config import settings

log = logging.getLogger(__name__)

_client: Optional[Client] = None


def client() -> Client:
    global _client
    if _client is None:
        if not settings.supabase_url or not settings.supabase_key:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
        _client = create_client(settings.supabase_url, settings.supabase_key)
    return _client


# ── Events ─────────────────────────────────────────────
def upsert_event(ev: dict[str, Any]) -> Optional[dict]:
    try:
        res = client().table("events").upsert(ev, on_conflict="external_id").execute()
        return res.data[0] if res.data else None
    except Exception as exc:
        log.exception("upsert_event failed: %s", exc)
        return None


def events_between(start_iso: str, end_iso: str) -> list[dict]:
    try:
        res = (
            client()
            .table("events")
            .select("*")
            .gte("event_time", start_iso)
            .lte("event_time", end_iso)
            .eq("importance", settings.min_importance)
            .order("event_time")
            .execute()
        )
        return res.data or []
    except Exception as exc:
        log.exception("events_between failed: %s", exc)
        return []


# ── Notifications log ──────────────────────────────────
def already_notified(event_id: int, notification_type: str) -> bool:
    try:
        res = (
            client()
            .table("notifications_log")
            .select("id")
            .eq("event_id", event_id)
            .eq("notification_type", notification_type)
            .limit(1)
            .execute()
        )
        return bool(res.data)
    except Exception:
        return False


def log_notification(event_id: int, notification_type: str) -> None:
    try:
        client().table("notifications_log").insert(
            {"event_id": event_id, "notification_type": notification_type}
        ).execute()
    except Exception as exc:
        log.warning("log_notification failed: %s", exc)


# ── Predictions / Outcomes ─────────────────────────────
def save_prediction(payload: dict) -> None:
    try:
        client().table("predictions").insert(payload).execute()
    except Exception as exc:
        log.warning("save_prediction failed: %s", exc)


def save_outcome(payload: dict) -> None:
    try:
        client().table("outcomes").insert(payload).execute()
    except Exception as exc:
        log.warning("save_outcome failed: %s", exc)
