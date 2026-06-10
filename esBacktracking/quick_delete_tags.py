#!/usr/bin/env python3
"""
Quick script to delete specific test tags by ID
"""

import sys
import os
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient

# Load environment variables from .env file (local directory)
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

def quick_delete_tags():
    """Quickly delete specific test tags by ID."""
    
    print("Quick Delete Test Tags")
    print("=" * 30)
    
    # Connect to MongoDB
    mongo_uri = os.getenv("PG_MONGO_URI")
    mongo_db_name = os.getenv("PG_MONGO_DB")
    
    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        db = client[mongo_db_name]
        
        # Test connection
        client.admin.command('ping')
        print("Connected to MongoDB")
        
        # Delete tags with specific patterns
        print("\nDeleting tags with CYBERPE865 and INDIA124...")
        
        # Delete article tags
        article_result = db["articleTag"].delete_many({
            "_id": {"$regex": "(CYBERPE865|INDIA124)$"}
        })
        print(f"Deleted {article_result.deleted_count} article tags")
        
        # Delete social feed tags
        social_result = db["socialFeedTag"].delete_many({
            "_id": {"$regex": "(CYBERPE865|INDIA124)$"}
        })
        print(f"Deleted {social_result.deleted_count} social feed tags")
        
        # Remove companyTag entries
        print("\nRemoving companyTag entries...")
        
        article_update = db["article"].update_many(
            {"companyTag.id": {"$in": ["CYBERPE865", "INDIA124"]}},
            {"$pull": {"companyTag": {"id": {"$in": ["CYBERPE865", "INDIA124"]}}}}
        )
        print(f"Updated {article_update.modified_count} articles")
        
        social_update = db["socialFeed"].update_many(
            {"companyTag.id": {"$in": ["CYBERPE865", "INDIA124"]}},
            {"$pull": {"companyTag": {"id": {"$in": ["CYBERPE865", "INDIA124"]}}}}
        )
        print(f"Updated {social_update.modified_count} social feeds")
        
        client.close()
        print("\nQuick cleanup completed!")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    quick_delete_tags()
















