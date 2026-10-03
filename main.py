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

@app.get("/run")
def run_now_get():
    """Convenience — allows manual trigger from browser."""
    return JSONResponse(jobs.run_all())
    
@app.head("/")
def root_head():
    """Render health check uses HEAD / — respond 200 without body."""
    return Response(status_code=200)

@app.get("/debug/news")
def debug_news():
    """Test news fetching directly from all sources."""
    from data import news_fetcher
    from config import settings

    out = {
        "has_fmp_key": bool(settings.fmp_api_key),
        "has_finnhub_key": bool(getattr(settings, "finnhub_api_key", "")),
        "finnhub_key_prefix": (
            settings.finnhub_api_key[:6]
            if getattr(settings, "finnhub_api_key", "") else ""
        ),
        "allowed_countries": list(settings.allowed_countries),
        "min_importance": settings.min_importance,
    }

    # Finnhub
    try:
        fh = news_fetcher.fetch_finnhub()
        out["finnhub_count"] = len(fh)
        out["finnhub_sample"] = fh[:2]
    except Exception as exc:
        out["finnhub_error"] = str(exc)

    # ForexFactory
    try:
        ff = news_fetcher.fetch_forexfactory()
        out["ff_count"] = len(ff)
        out["ff_sample"] = ff[:2]
    except Exception as exc:
        out["ff_error"] = str(exc)

    # FMP
    try:
        fmp = news_fetcher.fetch_fmp()
        out["fmp_count"] = len(fmp)
        out["fmp_sample"] = fmp[:2]
    except Exception as exc:
        out["fmp_error"] = str(exc)

    # Combined
    try:
        events, diag = news_fetcher.fetch_all()
        out["final_diag"] = diag
        out["final_count"] = len(events)
    except Exception as exc:
        out["final_error"] = str(exc)

    return out


@app.get("/debug/fmp-raw")
def debug_fmp_raw():
    """Call FMP API directly and return raw response."""
    import requests
    from datetime import datetime, timedelta, timezone
    from config import settings

    if not settings.fmp_api_key:
        return {"error": "no FMP key"}

    start = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")

    urls = [
        f"https://financialmodelingprep.com/stable/economic-calendar?from={start}&to={end}&apikey={settings.fmp_api_key}",
        f"https://financialmodelingprep.com/api/v3/economic_calendar?from={start}&to={end}&apikey={settings.fmp_api_key}",
    ]

    results = []
    for url in urls:
        try:
            r = requests.get(url, timeout=20)
            body = r.text[:500]
            try:
                j = r.json()
                count = len(j) if isinstance(j, list) else "not-list"
                sample = j[:1] if isinstance(j, list) and j else None
            except Exception:
                count = "not-json"
                sample = None
            results.append({
                "url": url.split("apikey=")[0],
                "status": r.status_code,
                "count": count,
                "sample": sample,
                "body_start": body if count != "not-json" else body,
            })
        except Exception as exc:
            results.append({"url": url.split("apikey=")[0], "error": str(exc)})

    return {"start": start, "end": end, "results": results}
    
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
@app.get("/debug/ff-raw")
def debug_ff_raw():
    """Call Forex Factory directly and return raw diagnostics."""
    import requests
    from datetime import datetime, timezone

    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    out = {
        "url": url,
        "now_utc": datetime.now(timezone.utc).isoformat(),
    }
    try:
        r = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)",
                "Accept": "application/json",
            },
        )
        out["status"] = r.status_code
        out["content_type"] = r.headers.get("Content-Type", "")
        out["content_length"] = len(r.text)
        out["first_200_chars"] = r.text[:200]
        try:
            data = r.json()
            out["is_list"] = isinstance(data, list)
            out["total_items"] = len(data) if isinstance(data, list) else 0
            if isinstance(data, list) and data:
                out["first_item"] = data[0]
                # عدّ حسب impact
                from collections import Counter
                impacts = Counter(str(x.get("impact", "")).lower() for x in data)
                countries = Counter(str(x.get("country", "")).upper() for x in data)
                out["impacts_count"] = dict(impacts)
                out["countries_count"] = dict(countries)
                # عدّ ما يمر من الفلاتر
                high_usd = [
                    x for x in data
                    if str(x.get("impact", "")).lower() == "high"
                    and str(x.get("country", "")).upper() == "USD"
                ]
                out["high_usd_count"] = len(high_usd)
                out["high_usd_sample"] = high_usd[:3]
        except Exception as e:
            out["json_error"] = str(e)
    except Exception as exc:
        out["request_error"] = str(exc)

    return out

@app.get("/debug/config")
def debug_config():
    """Check env vars are loaded (does NOT expose secrets)."""
    return {
        "tz": settings.tz_name,
        "has_telegram": bool(settings.telegram_bot_token and settings.telegram_chat_id),
        "has_supabase": bool(settings.supabase_url and settings.supabase_key),
        "has_fmp": bool(settings.fmp_api_key),
        "has_finnhub": bool(getattr(settings, "finnhub_api_key", "")),
        "gold_symbol": settings.gold_binance_symbol,
        "min_importance": settings.min_importance,
        "allowed_countries": list(settings.allowed_countries),
    }
