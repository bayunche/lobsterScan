"""管台 SQLite 持久化（独立于业务后端）

存储：
- token_usage  · 各 Agent / Provider / Model 的 token 消耗流水
- secrets      · API Key（落本地，使用 Fernet 对称加密；KMS 后续接入）
- avatars      · 数字人形象元数据（HeyGen AVATAR-*.md + 自托管头像）
- audit_log    · 平台写操作审计
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from sqlalchemy import (
    Column, DateTime, Integer, String, Text, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


class TokenUsage(Base):
    __tablename__ = "token_usage"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=datetime.utcnow, index=True)
    agent_id = Column(String(64), index=True)
    task_id = Column(String(64), index=True, nullable=True)
    provider = Column(String(32))
    model = Column(String(64))
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    cost_usd_micros = Column(Integer, default=0)  # 1e-6 USD，避免浮点


class Secret(Base):
    __tablename__ = "secrets"
    key = Column(String(64), primary_key=True)
    value_enc = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Avatar(Base):
    __tablename__ = "avatars"
    id = Column(String(64), primary_key=True)
    source = Column(String(32))      # heygen | self-hosted
    name = Column(String(128))
    preview_url = Column(String(512), nullable=True)
    voice_id = Column(String(128), nullable=True)
    meta_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=datetime.utcnow, index=True)
    actor = Column(String(64), default="admin")
    action = Column(String(64))                  # e.g. secret.set / skill.install / config.update
    target = Column(String(128), nullable=True)
    detail_json = Column(Text, default="{}")


# ---- engine ----
DB_PATH = settings.project_root / "data" / "admin.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{DB_PATH}", future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


@contextmanager
def db_session() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ---- 对称加密（用环境变量 ADMIN_SECRET_KEY 派生密钥）----
def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:  # pragma: no cover
        return None
    seed = os.environ.get("ADMIN_SECRET_KEY", "lobster-default-key-change-me")
    key = base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest())
    return Fernet(key)


def encrypt(plain: str) -> str:
    f = _fernet()
    if not f:
        return "PLAIN:" + plain
    return f.encrypt(plain.encode()).decode()


def decrypt(enc: str) -> str:
    if enc.startswith("PLAIN:"):
        return enc[6:]
    f = _fernet()
    if not f:
        return enc
    return f.decrypt(enc.encode()).decode()


def audit(action: str, target: str | None = None, detail: dict | None = None) -> None:
    with db_session() as s:
        s.add(AuditLog(action=action, target=target, detail_json=json.dumps(detail or {}, ensure_ascii=False)))
