from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, DateTime, Text, UniqueConstraint, select
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
engine = create_async_engine(database_url, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)
class Base(DeclarativeBase): pass

class Content(Base):
    __tablename__ = 'content'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(10), nullable=False)
    catalog: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    sort_priority: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
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
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint('tmdb_id', 'media_type', name='uq_content_tmdb_type'),)

class Unmatched(Base):
    __tablename__ = 'unmatched'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_name: Mapped[str] = mapped_column(String(1000))
    cleaned_title: Mapped[str] = mapped_column(String(500))
    year: Mapped[int | None] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

async def init_db():
    import os
    if settings.database_url.startswith('sqlite'):
        os.makedirs('data', exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
