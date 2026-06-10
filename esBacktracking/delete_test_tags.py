#!/usr/bin/env python3
"""
Script to delete test tags created by backtracking system
"""

import sys
import os
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient
from datetime import datetime, timedelta

# Load environment variables from .env file (local directory)
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

def delete_test_tags():
    """Delete test tags created by backtracking system."""
    
    print("Deleting Test Tags from MongoDB")
    print("=" * 50)
    
    # Connect to MongoDB
    mongo_uri = os.getenv("PG_MONGO_URI")
    mongo_db_name = os.getenv("PG_MONGO_DB")
    
    print(f"MongoDB URI: {mongo_uri}")
    print(f"Database: {mongo_db_name}")
    print()
    
    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        db = client[mongo_db_name]
        
        # Test connection
        client.admin.command('ping')
        print("Success: Connected to MongoDB")
        print()
        
        # Delete article tags created by backtracking engine in last 2 hours
        cutoff_time = datetime.now() - timedelta(hours=2)
        
        print("1. DELETING ARTICLE TAGS")
        print("-" * 30)
        
        article_tag_query = {
            "auditInfo.created.name": "backtracking_engine",
            "auditInfo.created.on": {"$gte": cutoff_time}
        }
        
        article_tags_deleted = db["articleTag"].delete_many(article_tag_query)
        print(f"Deleted {article_tags_deleted.deleted_count} article tags")
        
        # Delete social feed tags created by backtracking engine in last 2 hours
        print("\n2. DELETING SOCIAL FEED TAGS")
        print("-" * 30)
        
        social_tag_query = {
            "auditInfo.created.name": "backtracking_engine",
            "auditInfo.created.on": {"$gte": cutoff_time}
        }
        
        social_tags_deleted = db["socialFeedTag"].delete_many(social_tag_query)
        print(f"Deleted {social_tags_deleted.deleted_count} social feed tags")
        
        # Remove companyTag entries from articles (created by backtracking)
        print("\n3. REMOVING COMPANY TAG ENTRIES FROM ARTICLES")
        print("-" * 30)
        
        article_update_query = {
            "companyTag.id": {"$in": ["CYBERPE865", "INDIA124"]},
            "auditInfo.modified": {"$elemMatch": {"on": {"$gte": cutoff_time}}}
        }
        
        article_updates = db["article"].update_many(
            article_update_query,
            {"$pull": {"companyTag": {"id": {"$in": ["CYBERPE865", "INDIA124"]}}}}
        )
        print(f"Updated {article_updates.modified_count} articles (removed companyTag entries)")
        
        # Remove companyTag entries from social feeds (created by backtracking)
        print("\n4. REMOVING COMPANY TAG ENTRIES FROM SOCIAL FEEDS")
        print("-" * 30)
        
        social_feed_update_query = {
            "companyTag.id": {"$in": ["CYBERPE865", "INDIA124"]},
            "auditInfo.modified": {"$elemMatch": {"on": {"$gte": cutoff_time}}}
        }
        
        social_feed_updates = db["socialFeed"].update_many(
            social_feed_update_query,
            {"$pull": {"companyTag": {"id": {"$in": ["CYBERPE865", "INDIA124"]}}}}
        )
        print(f"Updated {social_feed_updates.modified_count} social feeds (removed companyTag entries)")
        
        # Summary
        print("\n" + "=" * 50)
        print("CLEANUP SUMMARY")
        print("=" * 50)
        print(f"Article tags deleted: {article_tags_deleted.deleted_count}")
        print(f"Social feed tags deleted: {social_tags_deleted.deleted_count}")
        print(f"Articles updated: {article_updates.modified_count}")
        print(f"Social feeds updated: {social_feed_updates.modified_count}")
        print(f"Total cleanup operations: {article_tags_deleted.deleted_count + social_tags_deleted.deleted_count + article_updates.modified_count + social_feed_updates.modified_count}")
        
        client.close()
        print("\nCleanup completed successfully!")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    delete_test_tags()
















