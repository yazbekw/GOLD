"""FastAPI entrypoint + internal APScheduler."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from config import settings
from notify import telegram
from scheduler import jobs

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")

scheduler = BackgroundScheduler(timezone="UTC")


def _require_admin(token: str) -> None:
    """Raise 401 if token doesn't match ADMIN_TOKEN."""
    if not settings.admin_token:
        # If ADMIN_TOKEN not set, allow (dev mode)
        return
    if token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(
        jobs.run_all, "interval", minutes=15,
        id="run_all", replace_existing=True,
    )
    scheduler.add_job(
        jobs.refresh_events, "interval", hours=6,
        id="refresh", replace_existing=True,
    )
    scheduler.start()
    log.info("Scheduler started (every 15 min)")

    try:
        jobs.refresh_events()
    except Exception as exc:
        log.warning("Initial refresh failed: %s", exc)

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="News & Macro Bot", version="0.2.0", lifespan=lifespan)


# ── Health / Root ─────────────────────────────────────
@app.get("/")
def root():
    return {"ok": True, "service": "news-macro-bot", "tz": settings.tz_name}


@app.head("/")
def root_head():
    return Response(status_code=200)


@app.get("/health")
def health():
    return {"ok": True}


@app.head("/health")
def health_head():
    return Response(status_code=200)


# ── Admin endpoints (require token) ───────────────────
@app.post("/run")
def run_now(token: str = Query("")):
    _require_admin(token)
    return JSONResponse(jobs.run_all())


@app.get("/run")
def run_now_get(token: str = Query("")):
    _require_admin(token)
    return JSONResponse(jobs.run_all())


@app.post("/daily")
def daily_now(token: str = Query("")):
    _require_admin(token)
    return JSONResponse(jobs.run_daily())


# ── Public test endpoints ─────────────────────────────
@app.get("/test-telegram")
def test_telegram():
    ok = telegram.send_health("اختبار الاتصال ✅")
    return {"ok": ok}


@app.get("/test-pre-event/{event_id}")
def test_pre_event(event_id: int, token: str = Query("")):
    """Generate a pre-event notification for the given event ID (for testing)."""
    _require_admin(token)

    res = (
        db_client().table("events").select("*").eq("id", event_id).limit(1).execute()
    )
    if not res.data:
        return {"error": f"Event {event_id} not found"}

    from data import macro_fetcher, price_fetcher
    from engine import decision_engine

    event = res.data[0]
    macro = macro_fetcher.fetch_macro()
    gold = price_fetcher.fetch_gold()
    brief = decision_engine.build_pre_event_brief(event, macro, gold)
    ok = telegram.send_pre_event_group([event], brief)

    return {
        "ok": ok,
        "event_id": event_id,
        "event": event.get("title"),
        "bias": brief.get("bias"),
        "confidence": brief.get("confidence"),
        "macro_keys": list(macro.keys()),
        "gold_price": gold.get("price"),
    }


def db_client():
    from storage import db
    return db.client()


# ── Debug endpoints ───────────────────────────────────
@app.get("/debug/config")
def debug_config():
    return {
        "tz": settings.tz_name,
        "has_telegram": bool(settings.telegram_bot_token and settings.telegram_chat_id),
        "has_supabase": bool(settings.supabase_url and settings.supabase_key),
        "has_fmp": bool(settings.fmp_api_key),
        "has_finnhub": bool(settings.finnhub_api_key),
        "has_ff_github": bool(settings.ff_github_url),
        "has_ff_proxy": bool(settings.ff_proxy_url),
        "has_admin_token": bool(settings.admin_token),
        "allowed_countries": list(settings.allowed_countries),
        "min_importance": settings.min_importance,
    }


@app.get("/debug/news")
def debug_news():
    from data import news_fetcher
    out = {"allowed_countries": list(settings.allowed_countries)}
    try:
        events, diag = news_fetcher.fetch_all()
        out["count"] = len(events)
        out["diag"] = diag
        out["sample"] = events[:2]
    except Exception as exc:
        out["error"] = str(exc)
    return out


@app.get("/debug/macro")
def debug_macro():
    from data import macro_fetcher
    return {"macro": macro_fetcher.fetch_macro()}


@app.get("/debug/supabase")
def debug_supabase():
    from storage import db
    out = {"url_set": bool(settings.supabase_url)}
    try:
        client = db.client()
        res = client.table("events").select("id").limit(1).execute()
        out["select_ok"] = True
        out["existing_rows"] = len(res.data) if res.data else 0
    except Exception as exc:
        out["error"] = str(exc)
    return out
