from datetime import datetime, timezone
from sqlalchemy import String, Integer, BigInteger, Boolean, Float, DateTime, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON
from .config import settings

# Railway may provide DATABASE_URL as postgres:// or postgresql://.
# SQLAlchemy's default PostgreSQL driver is psycopg2, but this service uses asyncpg.
database_url = settings.database_url
if database_url.startswith('postgres://'):
    database_url = 'postgresql+asyncpg://' + database_url[len('postgres://'):]
elif database_url.startswith('postgresql://'):
    database_url = 'postgresql+asyncpg://' + database_url[len('postgresql://'):]
elif database_url.startswith('postgresql+psycopg2://'):
    database_url = 'postgresql+asyncpg://' + database_url[len('postgresql+psycopg2://'):]
engine = create_async_engine(
    database_url,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_timeout=30,
    connect_args={'command_timeout': 60} if database_url.startswith('postgresql+asyncpg://') else {},
)
Session = async_sessionmaker(engine, expire_on_commit=False)
class Base(DeclarativeBase): pass

class Content(Base):
    __tablename__ = 'content'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(10), nullable=False)
    catalog: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    collection_id: Mapped[int | None] = mapped_column(Integer, index=True)
    collection_name: Mapped[str | None] = mapped_column(String(500), index=True)
    collection_order: Mapped[int | None] = mapped_column(Integer)
    collection_popularity: Mapped[float | None] = mapped_column(Float, index=True)
    sort_priority: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    original_language: Mapped[str | None] = mapped_column(String(10), index=True)
    imdb_id: Mapped[str | None] = mapped_column(String(20), index=True)
    english_title: Mapped[str | None] = mapped_column(String(500))
    tamil_title: Mapped[str | None] = mapped_column(String(500))
    overview: Mapped[str | None] = mapped_column(Text)
    poster: Mapped[str | None] = mapped_column(String(500))
    backdrop: Mapped[str | None] = mapped_column(String(500))
    genres: Mapped[list] = mapped_column(JSON, default=list)
    cast: Mapped[list] = mapped_column(JSON, default=list)
    director: Mapped[str | None] = mapped_column(String(500))
    rating: Mapped[float | None] = mapped_column(Float)
    runtime: Mapped[int | None] = mapped_column(Integer)
    release_date: Mapped[str | None] = mapped_column(String(20))
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    seasons: Mapped[list] = mapped_column(JSON, default=list)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint('tmdb_id', 'media_type', name='uq_content_tmdb_type'),)

class ScanTracker(Base):
    __tablename__ = 'scan_tracker'
    channel_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_message_id: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    historical_scan_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime)

class Unmatched(Base):
    __tablename__ = 'unmatched'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_name: Mapped[str] = mapped_column(String(1000))
    cleaned_title: Mapped[str] = mapped_column(String(500))
    year: Mapped[int | None] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

async def init_db():
    import os
    if settings.database_url.startswith('sqlite'):
        os.makedirs('data', exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if database_url.startswith('postgresql+asyncpg://'):
            await conn.exec_driver_sql("ALTER TABLE content ADD COLUMN IF NOT EXISTS original_language VARCHAR(10)")
            await conn.exec_driver_sql("ALTER TABLE content ADD COLUMN IF NOT EXISTS imdb_id VARCHAR(20)")
            await conn.exec_driver_sql("ALTER TABLE content ADD COLUMN IF NOT EXISTS discovered_at TIMESTAMP WITHOUT TIME ZONE")
            await conn.exec_driver_sql("ALTER TABLE content ADD COLUMN IF NOT EXISTS collection_id INTEGER")
            await conn.exec_driver_sql("ALTER TABLE content ADD COLUMN IF NOT EXISTS collection_name VARCHAR(500)")
            await conn.exec_driver_sql("ALTER TABLE content ADD COLUMN IF NOT EXISTS collection_order INTEGER")
            await conn.exec_driver_sql("ALTER TABLE content ADD COLUMN IF NOT EXISTS collection_popularity DOUBLE PRECISION")
            await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_content_original_language ON content (original_language)")
            await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_content_imdb_id ON content (imdb_id)")
            await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_content_discovered_at ON content (discovered_at)")
            await conn.exec_driver_sql("UPDATE content SET discovered_at = updated_at WHERE discovered_at IS NULL")
            await conn.exec_driver_sql("UPDATE content SET catalog = CASE WHEN media_type = 'series' THEN 'anime_series' ELSE 'anime_movies' END WHERE catalog = 'anime'")
            # Dubbed Movies is strictly movie-only; move legacy series out of it.
            await conn.exec_driver_sql("UPDATE content SET catalog = CASE WHEN (original_language = 'ja' OR genres::text ILIKE '%Animation%') THEN 'anime_series' ELSE 'other_series' END WHERE catalog = 'dubbed_movies' AND media_type = 'series'")
