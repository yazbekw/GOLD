# News & Macro Bot — Gold Edition

بوت تلغرام يرسل تنبيهات قبل الأخبار الأمريكية عالية الأهمية (CPI / NFP / FOMC / GDP)، مع سياق ماكرو (DXY / US10Y / VIX) وتحليل فني للذهب (EMA / RSI / ATR).

## المزايا

- ⏰ إشعار قبل 60 دقيقة (سياق كامل + سيناريوهات)
- ⏰ تذكير قبل 15 دقيقة
- ✅ نتيجة أولية بعد 5 دقائق من الصدور
- 🌍 توقيت سوريا (`Asia/Damascus`)
- 💾 تخزين كل شيء في Supabase للمراجعة لاحقًا

## الإعداد

### 1) Telegram

1. افتح [@BotFather](https://t.me/BotFather) → `/newbot` → خذ **token**.
2. أرسل رسالة لبوتك.
3. افتح: `https://api.telegram.org/bot<TOKEN>/getUpdates` وخذ `chat.id`.

### 2) Supabase

1. أنشئ مشروعًا جديدًا.
2. SQL Editor → شغّل محتوى `storage/schema.sql`.
3. Settings → API → انسخ:
   - `Project URL` → `SUPABASE_URL`
   - `service_role` key → `SUPABASE_SERVICE_KEY`

### 3) FMP

1. سجّل في [financialmodelingprep.com](https://financialmodelingprep.com/developer/docs) (مجاني).
2. انسخ API key → `FMP_API_KEY`.

### 4) محليًا

```bash
cp .env.example .env
# املأ المتغيرات
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

اختبر:
```bash
curl -X POST http://localhost:8000/test-telegram
curl -X POST http://localhost:8000/run
```

### 5) النشر على Render

1. ارفع المشروع إلى GitHub.
2. Render → New Web Service → اربط الـrepo → اختر **Docker**.
3. أضف المتغيرات من `.env`.
4. Deploy.

> **ملاحظة:** خطة Render المجانية تُنيم الخدمة بعد 15 دقيقة من عدم النشاط. الحل: أضف ping خارجي (مثل [cron-job.org](https://cron-job.org)) على `https://<your-app>.onrender.com/health` كل 10 دقائق.

## المسارات

| المسار | الوصف |
|---|---|
| `GET /health` | فحص صحة |
| `POST /run` | تشغيل يدوي |
| `POST /test-telegram` | اختبار تلغرام |

## ملاحظات

- الذهب المصدر الأساسي: Binance `PAXGUSDT` (يتتبع XAU/USD بفارق صغير). الاحتياطي: Yahoo `GC=F`.
- الأخبار: FMP أساسي، Forex Factory احتياطي.
- البوت **لا ينفّذ صفقات** — مساعد قرار فقط.
