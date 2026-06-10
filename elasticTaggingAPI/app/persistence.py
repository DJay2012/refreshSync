from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from legacy.core.Config import pgDatabaseConnection, return_pg_connection
from legacy.helpers.mongo_helpers import (
    ensure_article_exists_in_mongo,
    save_tags_to_mongo,
)


class PersistenceError(RuntimeError):
    """Raised when persisting tagging results fails."""


@dataclass
class PersistenceResult:
    persisted: bool
    postgres_update_type: Optional[int]
    mongo_upserted: bool


def persist_tagging_results(
    article_id: Optional[str],
    headline: str,
    summary: Optional[str],
    content: str,
    language: Optional[str],
    raw_tags: List[dict],
) -> PersistenceResult:
    """
    Persist tagging results to PostgreSQL and MongoDB.

    Args:
        article_id: Primary key of the article in PostgreSQL.
        headline: Article headline.
        summary: Article summary (optional).
        content: Article body content.
        language: Article language code.
        raw_tags: Tagging payload returned by the legacy tagger.
    """

    if not raw_tags:
        return PersistenceResult(persisted=False, postgres_update_type=None, mongo_upserted=False)

    if article_id is None:
        raise PersistenceError("articleId is required when writeToDb is enabled.")

    try:
        pg_article_id = int(article_id)
    except (TypeError, ValueError) as exc:
        raise PersistenceError("articleId must be an integer value.") from exc

    conn = pgDatabaseConnection()
    if not conn or isinstance(conn, Exception):
        raise PersistenceError("Failed to obtain PostgreSQL connection for persistence.")

    inserted = False
    updated = False
    update_type = 0

    cursor = None
    try:
        cursor = conn.cursor()
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        for tag_data in raw_tags:
            company_id = str(tag_data.get("COMPANYID") or "").strip()
            if not company_id:
                continue

            cursor.execute(
                'SELECT COUNT(*) FROM "ARTICLEDETAIL_LOCAL" WHERE "articleid" = %s AND "companyid" = %s',
                (pg_article_id, company_id),
            )
            exists = cursor.fetchone()[0] > 0

            keyword_value = tag_data.get("KEYWORDS", "")

            if exists:
                cursor.execute(
                    """
                    UPDATE "ARTICLEDETAIL_LOCAL"
                    SET "keyword" = "keyword" || ' ' || %s,
                        "HEADLINE" = %s,
                        "CONTENT" = %s,
                        "SUMMARY" = %s,
                        "SOURCE" = %s,
                        "createdby" = 'elastictagger',
                        "modifiedby" = 'elastictagger',
                        "modifiedon" = %s
                    WHERE "articleid" = %s AND "companyid" = %s
                    """,
                    (
                        keyword_value,
                        True,
                        True,
                        False,
                        "{}",
                        timestamp,
                        pg_article_id,
                        company_id,
                    ),
                )
                updated = True
            else:
                cursor.execute(
                    """
                    INSERT INTO "ARTICLEDETAIL_LOCAL"
                        ("articleid", "companyid", "keyword", "HEADLINE", "CONTENT", "SUMMARY",
                         "SOURCE", "createdon", "modifiedon", "createdby", "modifiedby")
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        pg_article_id,
                        company_id,
                        keyword_value,
                        True,
                        True,
                        False,
                        "{}",
                        timestamp,
                        timestamp,
                        "elastictagger",
                        "elastictagger",
                    ),
                )
                inserted = True

        if inserted and not updated:
            update_type = 1
        elif updated:
            update_type = 2

        cursor.execute(
            """
            UPDATE "ARTICLEHEADER_LOCAL"
            SET "elasticTagger" = TRUE,
                "ELASTICTAGUPDATED" = %s,
                "COUNTER_UPDATE" = NULL
            WHERE "articleid" = %s
            """,
            (update_type, pg_article_id),
        )

        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        raise PersistenceError("Failed to persist tags to PostgreSQL.") from exc
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        return_pg_connection(conn)

    mongo_upserted = False
    if update_type:
        try:
            mongo_article_id = ensure_article_exists_in_mongo(
                pg_article_id,
                headline,
                summary or "",
                content,
                language or "en",
            )

            if mongo_article_id:
                mongo_upserted = bool(
                    save_tags_to_mongo(
                        mongo_article_id,
                        pg_article_id,
                        raw_tags,
                        update_type,
                        content,
                    )
                )
            else:
                raise PersistenceError("Failed to ensure MongoDB article exists.")
        except PersistenceError:
            raise
        except Exception as exc:
            raise PersistenceError("Failed to persist tags to MongoDB.") from exc

    return PersistenceResult(
        persisted=bool(update_type or mongo_upserted),
        postgres_update_type=update_type if update_type else None,
        mongo_upserted=mongo_upserted,
    )

