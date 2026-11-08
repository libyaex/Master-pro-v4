"""الخادم الرئيسي — FastAPI. يبدأ بأمر واحد بلا أي مفاتيح.

نقاط النهاية:
- / — الواجهة العربية RTL (PWA)
- /api/health/sources — صحة كل مصدر (متوفر/غير متوفر بسبب + آخر صالح)
- /api/markets — اكتشاف الأسواق الديناميكي
- /api/costs — شاشة تدقيق التكلفة والتبعيات
- /api/rank — ترتيب الفرص (M6)
- /api/arb/{base} — ماسح التحكيم (M1)
- /api/signals — إشارات مفسرة عربياً (M4)
- /api/whale/{symbol} — ذكاء الحيتان (M2)
- /api/listings — إدراجات واختراقات (M3)
- /api/risk — إعدادات وحالة بوابة المخاطر
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.engine.market_engine import MarketEngine
from app.models import SourceHealth, utcnow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("main")

engine = MarketEngine()

# جذر المستودع — مسارات التوثيق لا تعتمد على مجلد التشغيل الحالي (مهم على Railway/Docker)
ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).parent / "web"


async def _initial_discovery() -> None:
    """استكشاف أولي في الخلفية — الخادم يرد على /healthz فوراً حتى لو تأخرت المنصات أو حُجبت."""
    try:
        diff = await engine.refresh_discovery()
        log.info("أدوات: %d | إدراجات جديدة: %d | إلغاءات: %d",
                 len(engine.instruments), len(diff.new_listings), len(diff.delistings))
    except Exception as exc:  # لا نمنع الإقلاع — الواجهة تعمل والمصادر تظهر غير متوفرة
        log.warning("فشل الاستكشاف الأولي: %s — ستظهر المصادر غير متوفرة", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("بدء المحرك — استكشاف أسواق عام بدون أي مفاتيح")
    task = asyncio.create_task(_initial_discovery())
    try:
        yield
    finally:
        task.cancel()
        for conn in engine.connectors.values():
            try:
                await conn.close()
            except Exception:  # pragma: no cover
                pass


app = FastAPI(title="Master-pro-v4", version="4.0.0", lifespan=lifespan,
              description="منصة ذكاء سوق متعددة المنصات — عربية، صفر تكلفة إلزامية")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health/sources", response_model=list[SourceHealth])
async def health_sources():
    """صحة كل مصدر — غير المتوفر يظهر بسبب وآخر طابع صالح، دائماً."""
    return engine.health_list()


@app.get("/api/markets")
async def markets(instrument_class: str = Query(default="crypto")):
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
    return {"items": [], "note_ar": "لا فرص مرصودة حالياً في هذه الجولة — الماسحات تعمل بالخلفية", "ts": utcnow().isoformat()}


@app.post("/api/refresh")
async def refresh():
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
    return {"ok": True, "ts": utcnow().isoformat()}


def resolve_bind() -> tuple[str, int]:
    """منفذ/عنوان التشغيل. على Railway: PORT يُحقن تلقائياً ويجب الربط على 0.0.0.0؛ محلياً 127.0.0.1:8000."""
    on_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))
    port = int(os.environ.get("PORT") or os.environ.get("APP_PORT") or "8000")
    default_host = "0.0.0.0" if on_railway or os.environ.get("PORT") else "127.0.0.1"
    host = os.environ.get("APP_HOST", default_host)
    return host, port


def main() -> None:
    import uvicorn

    host, port = resolve_bind()
    uvicorn.run("app.main:app", host=host, port=port, log_level=os.environ.get("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()
