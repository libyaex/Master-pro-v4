"""نقطة دخول Vercel serverless — يعاد تصدير تطبيق FastAPI كما هو.

Vercel (@vercel/python) يتوقع متغير `app` من نوع ASGI في api/index.py.
المسارات الأصلية (/api/...) تصل كما هي عبر إعادة الكتابة في vercel.json.
"""
from app.main import app  # noqa: F401