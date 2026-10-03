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

@app.get("/debug/github-ff")
def debug_github_ff():
    """Test GitHub FF URL directly."""
    import requests
    from config import settings

    url = getattr(settings, "ff_github_url", "")
    out = {
        "url": url,
        "url_set": bool(url),
    }

    if not url:
        return out

    try:
        r = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "news-macro-bot/1.0",
                "Accept": "application/json",
            },
        )
        out["status"] = r.status_code
        out["content_type"] = r.headers.get("Content-Type", "")
        out["content_length"] = len(r.text)
        out["first_200"] = r.text[:200]

        if r.status_code == 200:
            try:
                data = r.json()
                out["is_list"] = isinstance(data, list)
                out["total_items"] = len(data) if isinstance(data, list) else 0

                if isinstance(data, list) and data:
                    from collections import Counter
                    countries = Counter(str(x.get("country", "")).upper() for x in data)
                    impacts = Counter(str(x.get("impact", "")).lower() for x in data)
                    out["countries"] = dict(countries)
                    out["impacts"] = dict(impacts)

                    allowed = set(settings.allowed_countries)
                    high_usd = [
                        x for x in data
                        if str(x.get("impact", "")).lower() == "high"
                        and str(x.get("country", "")).upper() in allowed
                    ]
                    out["filtered_count"] = len(high_usd)
                    out["filtered_sample"] = high_usd[:2]
            except Exception as e:
                out["json_error"] = str(e)

        # Also test via news_fetcher
        from data import news_fetcher
        events = news_fetcher.fetch_forexfactory_from_github()
        out["fetcher_count"] = len(events)
        out["fetcher_sample"] = events[:2]

    except Exception as exc:
        out["error"] = str(exc)

    return out
    
@app.get("/debug/finnhub-raw")
def debug_finnhub_raw():
    """Call Finnhub directly and return raw response (truncated)."""
    import requests
    from datetime import datetime, timedelta, timezone
    from config import settings

    key = getattr(settings, "finnhub_api_key", "")
    if not key:
        return {"error": "no finnhub key"}

    start = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")

    out = {"start": start, "end": end, "url": "https://finnhub.io/api/v1/calendar/economic"}

    try:
        r = requests.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"from": start, "to": end, "token": key},
            timeout=20,
        )
        out["status"] = r.status_code
        out["content_type"] = r.headers.get("Content-Type", "")
        out["content_length"] = len(r.text)
        out["first_500_chars"] = r.text[:500]

        try:
            data = r.json()
            out["keys"] = list(data.keys()) if isinstance(data, dict) else "not-dict"

            # Finnhub returns {"economicCalendar": [...]}
            cal = data.get("economicCalendar") if isinstance(data, dict) else None
            if isinstance(cal, list):
                out["total_items"] = len(cal)
                out["first_item"] = cal[0] if cal else None

                # Count by country
                from collections import Counter
                countries = Counter(str(x.get("country", "")).upper() for x in cal)
                out["countries"] = dict(countries)

                # Count by impact
                impacts = Counter(str(x.get("impact", "")).lower() for x in cal)
                out["impacts"] = dict(impacts)

                # Count events matching our filters
                allowed = set(settings.allowed_countries)
                high = [
                    x for x in cal
                    if str(x.get("impact", "")).lower() in ("high", "3")
                    and str(x.get("country", "")).upper() in allowed
                ]
                out["high_filtered_count"] = len(high)
                out["high_filtered_sample"] = high[:3]
            else:
                out["economicCalendar_type"] = type(cal).__name__
        except Exception as e:
            out["json_error"] = str(e)
    except Exception as exc:
        out["request_error"] = str(exc)

    return out
@app.get("/debug/supabase")
def debug_supabase():
    """Test Supabase connection and insert."""
    from storage import db
    from config import settings

    out = {
        "url_set": bool(settings.supabase_url),
        "key_set": bool(settings.supabase_key),
        "key_prefix": settings.supabase_key[:20] if settings.supabase_key else "",
    }

    try:
        client = db.client()
        out["client_ok"] = True
    except Exception as exc:
        out["client_error"] = str(exc)
        return out

    # Test SELECT
    try:
        res = client.table("events").select("id").limit(1).execute()
        out["select_ok"] = True
        out["existing_rows"] = len(res.data) if res.data else 0
    except Exception as exc:
        out["select_error"] = str(exc)

    # Test INSERT
    try:
        test_event = {
            "external_id": f"test_{int(__import__('time').time())}",
            "event_time": "2026-12-31T23:59:59+00:00",
            "country": "USD",
            "currency": "USD",
            "title": "DEBUG TEST EVENT",
            "importance": "high",
            "forecast": "",
            "previous": "",
            "actual": "",
            "source": "debug",
        }
        res = client.table("events").insert(test_event).execute()
        out["insert_ok"] = True
        out["inserted_id"] = res.data[0]["id"] if res.data else None

        # Cleanup
        if res.data:
            client.table("events").delete().eq("id", res.data[0]["id"]).execute()
            out["cleanup"] = "done"
    except Exception as exc:
        out["insert_ok"] = False
        out["insert_error"] = str(exc)

    # Test UPSERT (الذي نستخدمه فعليًا)
    try:
        upsert_event = {
            "external_id": f"upsert_test_{int(__import__('time').time())}",
            "event_time": "2026-12-31T23:59:59+00:00",
            "country": "USD",
            "currency": "USD",
            "title": "UPSERT TEST",
            "importance": "high",
            "forecast": "",
            "previous": "",
            "actual": "",
            "source": "debug",
        }
        res = client.table("events").upsert(
            upsert_event, on_conflict="external_id"
        ).execute()
        out["upsert_ok"] = True

        # Cleanup
        if res.data:
            client.table("events").delete().eq(
                "external_id", upsert_event["external_id"]
            ).execute()
    except Exception as exc:
        out["upsert_ok"] = False
        out["upsert_error"] = str(exc)

    return out
@app.get("/debug/macro")
def debug_macro():
    """Test macro fetch from yfinance."""
    from data import macro_fetcher
    import yfinance as yf

    out = {}

    # Test yfinance directly
    for name, symbol in [
        ("DXY", "DX-Y.NYB"),
        ("US10Y", "^TNX"),
        ("VIX", "^VIX"),
    ]:
        try:
            t = yf.Ticker(symbol)
            hist = t.history(period="5d", interval="1h")
            if hist is None or hist.empty:
                out[name] = {"error": "empty history"}
            else:
                last = float(hist["Close"].iloc[-1])
                out[name] = {
                    "ok": True,
                    "value": round(last, 4),
                    "rows": len(hist),
                }
        except Exception as exc:
            out[name] = {"error": str(exc)}

    # Test our fetcher
    try:
        result = macro_fetcher.fetch_macro()
        out["fetcher_result"] = result
        out["fetcher_keys"] = list(result.keys())
    except Exception as exc:
        out["fetcher_error"] = str(exc)

    return out
    
@app.get("/test-pre-event/{event_id}")
def test_pre_event(event_id: int):
    """Generate a pre-event notification for the given event ID (for testing)."""
    from storage import db
    from data import macro_fetcher, price_fetcher
    from engine import decision_engine
    from notify import telegram

    # Get event
    res = db.client().table("events").select("*").eq("id", event_id).limit(1).execute()
    if not res.data:
        return {"error": f"Event {event_id} not found"}

    event = res.data[0]

    # Fetch context
    macro = macro_fetcher.fetch_macro()
    gold = price_fetcher.fetch_gold()

    # Build brief
    brief = decision_engine.build_pre_event_brief(event, macro, gold)

    # Send
    ok = telegram.send_pre_event(event, brief)

    return {
        "ok": ok,
        "event": event.get("title"),
        "bias": brief.get("bias"),
        "confidence": brief.get("confidence"),
        "macro_keys": list(macro.keys()),
        "gold_price": gold.get("price"),
    }
    
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
