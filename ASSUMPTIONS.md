# ASSUMPTIONS.md — الافتراضات والنقاط غير الموثقة

> هذا الملف سجل حي. كل افتراض أو نقطة غير موثقة تُضاف هنا مع تاريخ التدوين (UTC).
> قاعدة المشروع: لا نفترض وجود نقطة نهاية لأن منصة أخرى لديها نقطة مشابهة — كل موصل يتحقق ويسجل النتائج الفعلية.

## A1. الموصلات — نقاط النهاية والمحددات (توثيق مبدئي؛ يحدّثه "فحص الصحة" تلقائياً في `/health/sources`)

| المنصة | الأسواق | نقاط REST عامة المفترضة | WS عامة | محدد المعدل المطبق لدينا | حالة التحقق |
|---|---|---|---|---|---|
| Binance | Spot | /api/v3/exchangeInfo, /api/v3/klines, /api/v3/depth, /api/v3/trades, /api/v3/ticker/24hr | wss://stream.binance.com:9443 | 10 req/s (token bucket) | ✅ موثق رسمياً؛ الفحص الحي يؤكد |
| Binance | Futures | /fapi/v1/exchangeInfo, /fapi/v1/klines, /fapi/v1/premiumIndex, /fapi/v1/openInterest, /fapi/v1/depth | wss://fstream.binance.com | 10 req/s | ✅ موثق رسمياً |
| Bybit | Spot+Derivatives | /v5/market/instruments-info, /v5/market/kline, /v5/market/orderbook, /v5/market/tickers, /v5/market/funding/history, /v5/market/open-interest | wss://stream.bybit.com/v5/public/{spot\|linear} | 8 req/s | ✅ موثق رسمياً (v5) |
| OKX | Spot+Derivatives | /api/v5/public/instruments, /api/v5/market/candles, /api/v5/market/books, /api/v5/market/tickers, /api/v5/public/funding-rate, /api/v5/public/open-interest | wss://ws.okx.com:8443/ws/v5/public | 6 req/s (حذر؛ غير موثق بدقة) | ⚠️ معدل IP الفعلي غير موثق بدقة — الموصل يتراجع أسياً عند 429 |
| MEXC | Spot | /api/v3/exchangeInfo, /api/v3/klines, /api/v3/depth, /api/v3/trades | wss://wbs.mexc.com/ws (v3) | 5 req/s (حذر) | ⚠️ تاريخياً قيّدت MEXC بعض نقاط v3؛ الموصل يفحص ويرمي المصدر "غير متوفر" عند 403/451 |
| MEXC | Futures | — (لا يوجد مسار عام مؤكد) | — | — | ❌ **غير مفترض**: واجهة futures العامة مقيدة/متغيرة؛ `connector` يعرض "غير متوفر: مصدر MEXC Futures غير متاح بدون مفاتيح" — خيار graceful-degradation مُفعّل |
| CoinGecko | إثراء اختياري فقط | /api/v3/simple/price (keyless) | لا يوجد | 5-10 req/min حسب الحالة الحالية | ⚠️ الحدود keyless تتغير؛ إضافة اختيارية، الافتراضي OFF |

## A2. نقاط غير موثقة / قيود مفتوحة (تاريخ 2026-11-08 UTC)

1. **XAUUSD الفوري:** لا يوجد مصدر عام مجاني موثوق ومستقر بلا مفتاح. القرار: يظهر دائماً "غير متوفر — لا يوجد مصدر عام موثوق" ولا يُقدَّر.
2. **حدود OKX وMEXC الفعلية للـ IP:** غير منشورة بدقة؛ الموصلات تحافظ على حدود متحفظة وتسجل 429/403 في ملف الصحة.
3. **رسوم taker لكل منصة:** تؤخذ من جدول منشور أو من إعداد المستخدم `fees.json`؛ عند غياب الاثنين يُرفض تصنيف "قابل للتنفيذ" في M1.
4. **الذهب المُرمَّز PAXG/XAUT:** يُدرج فقط إذا ظهر في exchangeInfo للحساب "trading" فعلياً في كل منصة.
5. **بيانات on-chain (M2):** لا يوجد مصدر رسمي مجاني يسمح الاستخدام بلا مفاتيح حالياً؛ ON-CHAIN = غير متوفر افتراضياً مع السبب. لا ننسب محافظ لأشخاص أبداً.
6. **زمن الرحلة (latency):** يقاس فعلياً بين جهاز المستخدم والمنصة (ping/REST)؛ يختلف حسب الموقع ولا يُفترض رقم ثابت.
7. **المزامنة الزمنية:** كل الطوابع UTC من أطقم المنصات؛ انحراف الساعة المحلي يُراقَب (قيمة مطلقة > 500ms يعلّم البيانات "منحرفة الساعة").

## A3. افتراضات تصميم

1. **PWA بدل Flutter:** أخف (بدون toolchain، يعمل من نفس خادم FastAPI) — قرار نهائي لهذا الإصدار.
2. **SQLite WAL افتراضياً؛** PostgreSQL اختياري عبر `DB_BACKEND=postgres` (نفس المخطط عبر الهجرات). RLS على Supabase عند تشغيلها.
3. **"AI" محلي فقط:** مؤشرات + إحصاء + كشف نظام + تسجيل حتمي. نموذج scikit-learn/LightGBM اختياري OFF افتراضياً، يدرَّب على البيانات المجمعة محلياً ويُختبر walk-forward.
4. **الطابور المتين:** جدول jobs بحالات (queued/leased/done/failed/dead)، عقود إيجار، backoff أسّي، مفاتيح idempotency، وأولوية لـ kill_switch وclose_all.
5. **الحساسية للاختبارات:** كل بيانات الاختبارات في `tests/fixtures/` وموسومة `fixture`؛ ممنوع في مسارات الإنتاج.
6. **عدم مزج الفئات:** مقارنات العبر منصات تتم فقط لفئة أداة مطابقة (نفس الأصل، الفئة، عملة التسعير)؛ خلاف ذلك تُعلَّم "تحليل أساس/سبريد".
