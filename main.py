"""FastAPI entrypoint + internal APScheduler."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
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

    # تشغيل أولي فوري عند الإقلاع
    try:
        jobs.refresh_events()
    except Exception as exc:
        log.warning("Initial refresh failed: %s", exc)

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="News & Macro Bot", version="0.1.0", lifespan=lifespan)


@app.get("/")
def root():
    return {"ok": True, "service": "news-macro-bot", "tz": settings.tz_name}


@app.head("/")
def root_head():
    """Render health check uses HEAD / — respond 200 without body."""
    return Response(status_code=200)


@app.get("/health")
def health():
    return {"ok": True}


@app.head("/health")
def health_head():
    return Response(status_code=200)


@app.post("/run")
def run_now():
    return JSONResponse(jobs.run_all())


@app.post("/test-telegram")
def test_telegram():
    ok = telegram.send_health("اختبار الاتصال ✅")
    return {"ok": ok}


@app.get("/test-telegram")
def test_telegram_get():
    """Convenience — allows testing from a browser."""
    ok = telegram.send_health("اختبار الاتصال ✅")
    return {"ok": ok}


@app.get("/debug/config")
def debug_config():
    """Check env vars are loaded (does NOT expose secrets)."""
    return {
        "tz": settings.tz_name,
        "has_telegram": bool(settings.telegram_bot_token and settings.telegram_chat_id),
        "has_supabase": bool(settings.supabase_url and settings.supabase_key),
        "has_fmp": bool(settings.fmp_api_key),
        "gold_symbol": settings.gold_binance_symbol,
        "min_importance": settings.min_importance,
    }
