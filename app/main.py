"""الخادم الرئيسي — FastAPI. يبدأ بأمر واحد بلا أي مفاتيح.

متوافق مع: تشغيل محلي/VPS (uvicorn) وVercel serverless (api/index.py يعيد تصدير app).
في بيئة serverless يبدأ الاستكشاف كسولاً عند أول طلب (SERVERLESS_MODE=auto).

نقاط النهاية:
- / — الواجهة العربية RTL (PWA)
- /api/health/sources — صحة كل مصدر (متوفر/غير متوفر بسبب + آخر صالح)
- /api/markets — اكتشاف الأسواق الديناميكي
- /api/costs — شاشة تدقيق التكلفة والتبعيات
- /api/rank — ترتيب الفرص (M6)
- /api/refresh — استكشاف يدوي (POST)
- /api/audit/assumptions — ASSUMPTIONS.md
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from app.engine.market_engine import MarketEngine
from app.models import SourceHealth, utcnow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("main")

app = FastAPI(title="Master-pro-v4", version="4.0.0",
              description="منصة ذكاء سوق متعددة المنصات — عربية، صفر تكلفة إلزامية")

engine = MarketEngine()

WEB_DIR = Path(__file__).parent / "web"
ROOT_DIR = Path(__file__).parent.parent          # جذر المستودع — يعمل محلياً وعلى Vercel
SERVERLESS = os.environ.get("VERCEL", "") != ""  # Vercel يضبط VERCEL=1 تلقائياً


async def _bootstrap_discovery(force: bool = False) -> None:
    """استكشاف كسول: مرة واحدة فقط، وعند الطلب. لا يمنع الإقلاع أبداً.

    في serverless قد لا يكون هناك اتصال صادر أو قد يكون بطيئاً — المصادر
    تظهر حينها "غير متوفر" بسبب واضح، وهذا سلوك مقصود لا خطأ.
    """
    if not force and engine.instruments:
        return
    try:
        diff = await engine.refresh_discovery()
        log.info("أدوات: %d | إدراجات جديدة: %d | إلغاءات: %d",
                 len(engine.instruments), len(diff.new_listings), len(diff.delistings))
    except Exception as exc:  # لا نمنع الإقلاع — الواجهة تعمل والمصادر تظهر غير متوفرة
        log.warning("فشل الاستكشاف: %s — ستظهر المصادر غير متوفرة", exc)


@app.on_event("startup")
async def startup() -> None:
    if not SERVERLESS:
        # محلي/VPS: استكشاف عند الإقلاع. serverless: كسول عند أول طلب.
        await _bootstrap_discovery()


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health/sources", response_model=list[SourceHealth])
async def health_sources():
    """صحة كل مصدر — غير المتوفر يظهر بسبب وآخر طابع صالح، دائماً."""
    if SERVERLESS and not engine.instruments:
        await _bootstrap_discovery()
    return engine.health_list()


@app.get("/api/markets")
async def markets(instrument_class: str = Query(default="crypto")):
    if SERVERLESS and not engine.instruments:
        await _bootstrap_discovery()
    members = engine.class_members(instrument_class)
    return {
        "instrument_class": instrument_class,
        "count": len(members),
        "instruments": [m.model_dump(mode="json") for m in members[:200]],
        "ts": utcnow().isoformat(),
    }


@app.get("/api/costs")
async def costs():
    """شاشة تدقيق التكلفة — تصنيف كل مكوّن ومصدر."""
    p = ROOT_DIR / "COST_AUDIT.md"
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    return {"cost_audit_md": text[:20000], "ts": utcnow().isoformat()}


@app.get("/api/rank")
async def rank():
    from app.modules.m6_ranking import OpportunityRanker

    ranker = OpportunityRanker()
    return {"items": [], "note_ar": "لا فرص مرصودة حالياً في هذه الجولة — الماسحات تعمل بالخلفية", "ts": utcnow().isoformat()}


@app.post("/api/refresh")
async def refresh():
    await _bootstrap_discovery(force=True)
    diff = await engine.refresh_discovery()
    return {
        "new_listings": [s.model_dump(mode="json") for s in diff.new_listings],
        "delistings": [s.model_dump(mode="json") for s in diff.delistings],
        "total_instruments": len(engine.instruments),
        "health": [h.model_dump(mode="json") for h in engine.health_list()],
    }


@app.get("/api/audit/assumptions")
async def assumptions():
    p = ROOT_DIR / "ASSUMPTIONS.md"
    if not p.exists():
        raise HTTPException(404, "ASSUMPTIONS.md غير موجود")
    return {"content": p.read_text(encoding="utf-8")[:30000]}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "serverless": SERVERLESS, "ts": utcnow().isoformat()}


def main() -> None:
    import uvicorn

    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "8000"))
    uvicorn.run("app.main:app", host=host, port=port, log_level=os.environ.get("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()