import datetime as dt
import secrets

from sqlalchemy import (JSON, BigInteger, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    public_key: Mapped[str] = mapped_column(String(64), unique=True, default=lambda: secrets.token_hex(16))
    rate_limit_eps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Release(Base):
    __tablename__ = "releases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    version: Mapped[str] = mapped_column(String(200))
    first_seen: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event_count: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (UniqueConstraint("project_id", "version"),)


class Issue(Base):
    __tablename__ = "issues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    fingerprint: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    culprit: Mapped[str | None] = mapped_column(Text, nullable=True)
    level: Mapped[str] = mapped_column(String(10), default="error")
    status: Mapped[str] = mapped_column(String(20), default="unresolved")
    first_seen: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    count: Mapped[int] = mapped_column(BigInteger, default=0)
    first_release: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_release: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_environment: Mapped[str | None] = mapped_column(String(100), nullable=True)
    __table_args__ = (UniqueConstraint("project_id", "fingerprint"),
                      Index("ix_issue_project_last_seen", "project_id", "last_seen"))


class FingerprintAlias(Base):
    """After issues are merged, the merged-away fingerprint keeps resolving to the surviving issue."""
    __tablename__ = "fingerprint_aliases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    fingerprint: Mapped[str] = mapped_column(String(40))
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"), index=True)
    __table_args__ = (UniqueConstraint("project_id", "fingerprint"),)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(32))
    project_id: Mapped[int] = mapped_column(Integer)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"), index=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    release: Mapped[str | None] = mapped_column(String(200), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(100), nullable=True)
    data: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("project_id", "event_id"),)


class SourceMap(Base):
    __tablename__ = "sourcemaps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    release: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(500))  # the *minified* file this map belongs to
    content: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("project_id", "release", "filename"),)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(20))  # new_issue | spike
    threshold: Mapped[int] = mapped_column(Integer, default=10)
    window_seconds: Mapped[int] = mapped_column(Integer, default=60)
    channel: Mapped[str] = mapped_column(String(10), default="webhook")  # webhook | email
    webhook_url: Mapped[str] = mapped_column(Text, default="")
    email_to: Mapped[str | None] = mapped_column(String(320), nullable=True)
    last_fired: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AlertLog(Base):
    __tablename__ = "alert_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(Integer)
    issue_id: Mapped[int] = mapped_column(Integer)
    fired_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered: Mapped[bool] = mapped_column(default=False)
    detail: Mapped[str] = mapped_column(Text, default="")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserSession(Base):
    __tablename__ = "user_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the bearer token
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class Membership(Base):
    __tablename__ = "memberships"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    role: Mapped[str] = mapped_column(String(10), default="member")  # owner | member
    __table_args__ = (UniqueConstraint("user_id", "project_id"),)


def migrate(engine):
    """Bring the schema to the latest Alembic revision. A Postgres advisory lock serialises
    concurrent starts (api replicas + worker all call this on boot)."""
    import os

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    cfg = Config()
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "migrations"))
    with engine.connect() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_lock(727001)"))
        try:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            conn.commit()
        finally:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_unlock(727001)"))
                conn.commit()


def make_session_factory(url: str, use_migrations: bool = False):
    kw = {}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url == "sqlite://":
            from sqlalchemy.pool import StaticPool
            kw["poolclass"] = StaticPool
    else:
        kw["pool_pre_ping"] = True
    engine = create_engine(url, **kw)
    if use_migrations:
        migrate(engine)
    else:
        Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
