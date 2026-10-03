"""FastAPI entrypoint + internal APScheduler."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.responses import JSONResponse

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
    scheduler.add_job(jobs.run_all, "interval", minutes=15, id="run_all", replace_existing=True)
    scheduler.add_job(jobs.refresh_events, "interval", hours=6, id="refresh", replace_existing=True)
    scheduler.start()
    log.info("Scheduler started (every 15 min)")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="News & Macro Bot", version="0.1.0", lifespan=lifespan)


@app.get("/")
def root():
    return {"ok": True, "service": "news-macro-bot", "tz": settings.tz_name}


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/run")
def run_now():
    return JSONResponse(jobs.run_all())


@app.post("/test-telegram")
def test_telegram():
    ok = telegram.send_health("اختبار الاتصال ✅")
    return {"ok": ok}
