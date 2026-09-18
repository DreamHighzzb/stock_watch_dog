# -*- coding: utf-8 -*-
"""
stock-radar · 认证工具（零依赖，纯标准库）

- 密码：pbkdf2_hmac(sha256) + 随机盐，不存明文。
- token：secrets 生成的随机串，存于 stock_sessions（由 db 管理）。
"""
import hashlib
import hmac
import os
import secrets


PBKDF2_ITERATIONS = 100_000


def hash_password(password: str):
    """返回 (salt_hex, hash_hex)"""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return salt.hex(), dk.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
    except Exception:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk.hex(), hash_hex)


def generate_token() -> str:
    return secrets.token_urlsafe(32)
