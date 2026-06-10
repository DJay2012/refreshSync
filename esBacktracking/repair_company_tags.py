#!/usr/bin/env python3
"""
Repair script: Update companyTag arrays in article and socialFeed collections
for companies that were already backtracked but missed the companyTag update.

Usage: python repair_company_tags.py INVENGE330 RIVER167

Field name note:
  - article collection uses 'articleId' (ETL pipeline) OR 'sourceArticleId' (backtracking-created)
  - socialFeed collection uses 'socialFeedId' (ETL pipeline) OR 'sourceArticleId' (backtracking-created)
"""

import sys
import os
from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError


def get_mongo():
    tls_ca = '/etc/ssl/mongo/ca.crt'
    uri = os.environ.get('PG_MONGO_URI')
    db_name = os.environ.get('PG_MONGO_DB', 'pnq_etl')
    client = MongoClient(uri, tls=True, tlsCAFile=tls_ca)
    return client[db_name]


def bulk_write_safe(collection, ops, label):
    if not ops:
        print(f"  {label}: no operations to perform")
        return 0
    try:
        result = collection.bulk_write(ops, ordered=False)
        print(f"  {label}: {result.modified_count} documents updated")
        return result.modified_count
    except BulkWriteError as e:
        modified = e.details.get('nModified', 0)
        errors = len(e.details.get('writeErrors', []))
        print(f"  {label}: {modified} modified, {errors} write errors (likely already present)")
        return modified


def repair_company(db, company_id):
    print(f"\n{'='*60}")
    print(f"Repairing companyTag arrays for: {company_id}")
    print(f"{'='*60}")

    # ---- Articles ----
    article_tags = list(db['articleTag'].find(
        {'company.id': company_id},
        {'_id': 1, 'articleId': 1, 'company': 1}
    ))
    print(f"Found {len(article_tags)} articleTags for {company_id}")

    # Two passes: one for ETL-style 'articleId' field, one for backtracking-style 'sourceArticleId' field
    ops_by_article_id = []       # for ETL-created articles
    ops_by_source_article_id = []  # for backtracking-created articles

    for tag in article_tags:
        pg_id = tag.get('articleId')
        company_name = tag.get('company', {}).get('name', '')
        if pg_id is None:
            continue
        entry = {"id": company_id, "name": company_name}

        # Update where articleId matches (ETL pipeline field)
        ops_by_article_id.append(UpdateOne(
            {"articleId": pg_id, "companyTag.id": {"$ne": company_id}},
            {"$push": {"companyTag": entry}}
        ))
        # Update where sourceArticleId matches (backtracking-created field)
        ops_by_source_article_id.append(UpdateOne(
            {"sourceArticleId": pg_id, "companyTag.id": {"$ne": company_id}},
            {"$push": {"companyTag": entry}}
        ))

    col = db['article']
    bulk_write_safe(col, ops_by_article_id, "article (articleId field)")
    bulk_write_safe(col, ops_by_source_article_id, "article (sourceArticleId field)")

    # ---- Social Feeds ----
    social_tags = list(db['socialFeedTag'].find(
        {'company.id': company_id},
        {'_id': 1, 'socialFeedId': 1, 'company': 1}
    ))
    print(f"Found {len(social_tags)} socialFeedTags for {company_id}")

    ops_by_social_feed_id = []
    ops_by_source_social_id = []

    for tag in social_tags:
        pg_id = tag.get('socialFeedId')
        company_name = tag.get('company', {}).get('name', '')
        if pg_id is None:
            continue
        entry = {"id": company_id, "name": company_name}

        # Update where socialFeedId matches (ETL pipeline field)
        ops_by_social_feed_id.append(UpdateOne(
            {"socialFeedId": pg_id, "companyTag.id": {"$ne": company_id}},
            {"$addToSet": {"companyTag": entry}}
        ))
        # Update where sourceArticleId matches (backtracking-created field)
        ops_by_source_social_id.append(UpdateOne(
            {"sourceArticleId": pg_id, "companyTag.id": {"$ne": company_id}},
            {"$addToSet": {"companyTag": entry}}
        ))

    col = db['socialFeed']
    bulk_write_safe(col, ops_by_social_feed_id, "socialFeed (socialFeedId field)")
    bulk_write_safe(col, ops_by_source_social_id, "socialFeed (sourceArticleId field)")

    print(f"Done repairing {company_id}")


def main():
    company_ids = sys.argv[1:] if len(sys.argv) > 1 else ['INVENGE330', 'RIVER167']
    print(f"Repair target companies: {company_ids}")

    db = get_mongo()
    print("Connected to MongoDB")

    for cid in company_ids:
        repair_company(db, cid)

    print("\nAll repairs complete.")


if __name__ == '__main__':
    main()
