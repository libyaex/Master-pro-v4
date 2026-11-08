"""تشفير الأسرار AES-256-GCM — للمفاتيح الاختيارية للتداول فقط.

قواعد: الأسرار من متغيرات البيئة فقط، تُشفَّر سكوناً، لا تُسجل أبداً،
لا تُعاد للواجهة (write-only، مقنّعة بعد الحفظ)، وتحذير إن كانت بمفتاح
صلاحية سحب Withdrawals (نرفضها). لا تطلب أبداً مفاتيح لتحليل البيانات العامة.
"""
from __future__ import annotations

import base64
import hashlib
import os
import warnings


class WithdrawalScopeError(ValueError):
    """مفتاح بعمليات سحب — مرفوض دائماً."""


class SecretVault:
    def __init__(self, master_key_env: str = "SECRET_KEY") -> None:
        raw = os.environ.get(master_key_env, "").strip()
        if not raw or raw == "change-me-generate-random-64-hex":
            warnings.warn("SECRET_KEY غير مضبوط — تشفير المفاتيح معطّل حتى ضبطه (لا يؤثر على تحليل البيانات العامة)")
            self._key: bytes | None = None
        else:
            self._key = hashlib.sha256(raw.encode()).digest()  # 32 بايت لـ AES-256

    @property
    def ready(self) -> bool:
        return self._key is not None

    def _cipher(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(self._key)

    def encrypt(self, plaintext: str) -> str:
        if self._key is None:
            raise RuntimeError("SECRET_KEY غير مضبوط — لا يمكن حفظ أسرار")
        nonce = os.urandom(12)
        ct = self._cipher().encrypt(nonce, plaintext.encode(), b"master-pro-v4")
        return base64.b64encode(nonce + ct).decode()

    def decrypt(self, blob: str) -> str:
        if self._key is None:
            raise RuntimeError("SECRET_KEY غير مضبوط")
        raw = base64.b64decode(blob)
        return self._cipher().decrypt(raw[:12], raw[12:], b"master-pro-v4").decode()

    @staticmethod
    def mask(value: str) -> str:
        return "••••••••" + value[-4:] if len(value) > 4 else "••••"

    @staticmethod
    def check_permissions(permissions: list[str]) -> None:
        """نرفض أي مفتاح بصلاحية سحب — تحذير صريح ورفض حفظ."""
        bad = [p for p in permissions if "withdraw" in p.lower()]
        if bad:
            raise WithdrawalScopeError(
                f"مرفوض: المفتاح يحمل صلاحية سحب {bad} — لا نقبل مفاتيح سحب أبداً. أنشئ مفتاحاً بلا سحب."
            )


def redact_log(obj: dict) -> dict:
    """إزالة أي سر من السجلات — أسماء شائعة تُقص دائماً."""
    SECRET_HINTS = ("key", "secret", "passphrase", "token", "password")
    out = {}
    for k, v in obj.items():
        if any(h in k.lower() for h in SECRET_HINTS):
            out[k] = "[REDACTED]"
        elif isinstance(v, dict):
            out[k] = redact_log(v)
        else:
            out[k] = v
    return out