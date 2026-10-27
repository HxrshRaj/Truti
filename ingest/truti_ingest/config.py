import os
from dataclasses import dataclass, field


def _int(name, default):
    return int(os.environ.get(name, default))


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: _normalise_db(os.environ.get("DATABASE_URL", "sqlite:///truti.db")))
    redis_url: str = field(default_factory=lambda: os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    admin_token: str = field(default_factory=lambda: os.environ.get("ADMIN_TOKEN", "dev-admin-token"))
    max_body_bytes: int = field(default_factory=lambda: _int("MAX_BODY_BYTES", 200_000))
    max_events_per_envelope: int = 50
    default_rate_limit_eps: int = field(default_factory=lambda: _int("RATE_LIMIT_EPS", 200))
    stream_maxlen: int = field(default_factory=lambda: _int("STREAM_MAXLEN", 100_000))
    dedup_ttl_seconds: int = 3600
    stream: str = "truti:events"
    dead_stream: str = "truti:dead"
    group: str = "workers"
    retention_days: int = field(default_factory=lambda: _int("RETENTION_DAYS", 30))  # 0 disables pruning
    allow_registration: bool = field(default_factory=lambda: os.environ.get("ALLOW_REGISTRATION", "0") == "1")
    cors_origins: str = field(default_factory=lambda: os.environ.get("CORS_ORIGINS", "*"))
    metrics_port: int = field(default_factory=lambda: _int("WORKER_METRICS_PORT", 9100))


def _normalise_db(url: str) -> str:
    # Render hands out postgres:// ; SQLAlchemy + psycopg3 wants postgresql+psycopg://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url
