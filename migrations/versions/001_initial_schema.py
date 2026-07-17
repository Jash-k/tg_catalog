"""initial schema: catalog_items, scan_tracker, unmatched_items + FTS trigger

Revision ID: 001_initial
Revises:
Create Date: 2024-01-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Trigram extension powers the search endpoint's typo-tolerant fallback.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ------------------------------------------------------------------
    # catalog_items
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE catalog_items (
            id                   SERIAL PRIMARY KEY,
            tmdb_id              INTEGER NOT NULL,
            catalog_type         VARCHAR(20) NOT NULL,
            content_type         VARCHAR(10) NOT NULL,
            title_english        VARCHAR(500) NOT NULL,
            title_tamil          VARCHAR(500),
            title_original       VARCHAR(500),
            overview             TEXT,
            tagline              VARCHAR(500),
            year                 INTEGER,
            release_date         DATE,
            poster_url           VARCHAR(1000),
            backdrop_url         VARCHAR(1000),
            genres               JSONB DEFAULT '[]'::jsonb,
            cast_list            JSONB DEFAULT '[]'::jsonb,
            director             VARCHAR(300),
            director_profile_url VARCHAR(1000),
            rating               DECIMAL(3,1),
            vote_count           INTEGER,
            runtime              INTEGER,
            original_language    VARCHAR(10),
            is_dubbed            BOOLEAN DEFAULT false,
            is_tamil_original    BOOLEAN DEFAULT false,
            is_anime             BOOLEAN DEFAULT false,
            available_seasons    JSONB DEFAULT '[]'::jsonb,
            total_seasons        INTEGER,
            search_vector        TSVECTOR,
            added_at             TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at           TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            tmdb_synced_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """
    )

    # Indexes
    op.execute("CREATE UNIQUE INDEX idx_catalog_tmdb_id ON catalog_items(tmdb_id)")
    op.execute("CREATE INDEX idx_catalog_type ON catalog_items(catalog_type)")
    op.execute("CREATE INDEX idx_content_type ON catalog_items(content_type)")
    op.execute("CREATE INDEX idx_year ON catalog_items(year)")
    op.execute("CREATE INDEX idx_rating ON catalog_items(rating)")
    op.execute("CREATE INDEX idx_genres ON catalog_items USING GIN(genres)")
    op.execute("CREATE INDEX idx_cast ON catalog_items USING GIN(cast_list)")
    op.execute("CREATE INDEX idx_search ON catalog_items USING GIN(search_vector)")
    op.execute("CREATE INDEX idx_added_at ON catalog_items(added_at DESC)")
    op.execute("CREATE INDEX idx_is_dubbed ON catalog_items(is_dubbed)")
    op.execute("CREATE INDEX idx_original_language ON catalog_items(original_language)")
    op.execute("CREATE INDEX idx_is_tamil_original ON catalog_items(is_tamil_original)")

    # ------------------------------------------------------------------
    # search_vector maintenance trigger
    #   title_english (A), title_tamil (A), title_original (B),
    #   director (B), cast names from cast_list JSON (C), overview (D)
    # The 'simple' config is used so Tamil text survives unstemmed.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION catalog_items_search_vector_update()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                  setweight(to_tsvector('simple', coalesce(NEW.title_english, '')), 'A')
                || setweight(to_tsvector('simple', coalesce(NEW.title_tamil, '')), 'A')
                || setweight(to_tsvector('simple', coalesce(NEW.title_original, '')), 'B')
                || setweight(to_tsvector('simple', coalesce(NEW.director, '')), 'B')
                || setweight(
                       to_tsvector('simple', coalesce(
                           (SELECT string_agg(elem ->> 'name', ' ')
                              FROM jsonb_array_elements(NEW.cast_list) AS elem),
                           ''
                       )),
                       'C'
                   )
                || setweight(to_tsvector('simple', coalesce(NEW.overview, '')), 'D');
            NEW.updated_at := NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("DROP TRIGGER IF EXISTS catalog_items_search_vector_trigger ON catalog_items")
    op.execute(
        """
        CREATE TRIGGER catalog_items_search_vector_trigger
        BEFORE INSERT OR UPDATE ON catalog_items
        FOR EACH ROW EXECUTE FUNCTION catalog_items_search_vector_update()
        """
    )

    # ------------------------------------------------------------------
    # scan_tracker (internal only)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE scan_tracker (
            id               SERIAL PRIMARY KEY,
            channel_username VARCHAR(200) NOT NULL UNIQUE,
            last_message_id  BIGINT DEFAULT 0,
            total_scanned    INTEGER DEFAULT 0,
            total_matched    INTEGER DEFAULT 0,
            total_unmatched  INTEGER DEFAULT 0,
            last_scanned_at  TIMESTAMP WITH TIME ZONE,
            created_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """
    )

    # ------------------------------------------------------------------
    # unmatched_items (review/debugging)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE unmatched_items (
            id                SERIAL PRIMARY KEY,
            original_filename TEXT NOT NULL,
            cleaned_title     VARCHAR(500),
            detected_year     INTEGER,
            detected_type     VARCHAR(10),
            channel_username  VARCHAR(200),
            reason            VARCHAR(200),
            created_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_unmatched_created_at ON unmatched_items(created_at DESC)")
    op.execute("CREATE INDEX idx_unmatched_channel ON unmatched_items(channel_username)")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS catalog_items_search_vector_trigger ON catalog_items")
    op.execute("DROP FUNCTION IF EXISTS catalog_items_search_vector_update()")
    op.execute("DROP TABLE IF EXISTS unmatched_items")
    op.execute("DROP TABLE IF EXISTS scan_tracker")
    op.execute("DROP TABLE IF EXISTS catalog_items")
