# شاهد تشخيصي: الطبقة 1 — models فقط
from app.models import BookLevel, BookSnapshot, InstrumentClass, InstrumentSpec, Trade, utcnow


def test_models_import_ok():
    assert InstrumentClass.CRYPTO.value == "crypto"
