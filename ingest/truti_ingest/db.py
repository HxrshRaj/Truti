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
    webhook_url: Mapped[str] = mapped_column(Text)
    last_fired: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AlertLog(Base):
    __tablename__ = "alert_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(Integer)
    issue_id: Mapped[int] = mapped_column(Integer)
    fired_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered: Mapped[bool] = mapped_column(default=False)
    detail: Mapped[str] = mapped_column(Text, default="")


def make_session_factory(url: str):
    kw = {}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url == "sqlite://":
            from sqlalchemy.pool import StaticPool
            kw["poolclass"] = StaticPool
    else:
        kw["pool_pre_ping"] = True
    engine = create_engine(url, **kw)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
