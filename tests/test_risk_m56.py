# شاهد: استيراد m2_whales فقط — بلا اختبارات
from app.modules.m2_whales import WhaleWatcher


def test_m2_import_ok():
    w = WhaleWatcher()
    assert w.min_notional_usd == 50_000.0
