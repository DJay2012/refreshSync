#!/usr/bin/env python3
"""
Delete specific test tags by their exact _id values
"""

import sys
import os
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient

# Load environment variables from .env file (local directory)
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

def delete_specific_tags():
    """Delete specific test tags by their exact _id values."""
    
    print("Deleting Specific Test Tags by _id")
    print("=" * 40)
    
    # Connect to MongoDB
    mongo_uri = os.getenv("PG_MONGO_URI")
    mongo_db_name = os.getenv("PG_MONGO_DB")
    
    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        db = client[mongo_db_name]
        
        # Test connection
        client.admin.command('ping')
        print("Connected to MongoDB")
        
        # Specific tag IDs from the logs (INDIA124 tags)
        india124_tag_ids = [
            "18223112055INDIA124",
            "18223112048INDIA124", 
            "18223112051INDIA124",
            "18223112027INDIA124",
            "18223112043INDIA124",
            "18223112028INDIA124",
            "18223112008INDIA124",
            "18223111849INDIA124",
            "18223108541INDIA124",
            "18223111017INDIA124",
            "18223112443INDIA124",
            "18223112424INDIA124",
            "18223112436INDIA124",
            "18223112478INDIA124",
            "18223112496INDIA124",
            "18223112125INDIA124",
            "18223112184INDIA124",
            "18223112191INDIA124",
            "18223111789INDIA124",
            "18223111944INDIA124",
            "18223111940INDIA124",
            "18223111931INDIA124",
            "18223111606INDIA124",
            "18223111630INDIA124",
            "18223111653INDIA124",
            "18223111640INDIA124",
            "18223111666INDIA124",
            "18223111718INDIA124",
            "18223111735INDIA124",
            "18223111770INDIA124",
            "18223111797INDIA124",
            "18223111780INDIA124",
            "18223111774INDIA124",
            "18223111801INDIA124"
        ]
        
        # CYBERPE865 tag IDs (from previous run)
        cyberpe_tag_ids = [
            # Add some example CYBERPE865 tag IDs if needed
        ]
        
        all_tag_ids = india124_tag_ids + cyberpe_tag_ids
        
        print(f"Deleting {len(all_tag_ids)} specific tag IDs...")
        
        # Delete article tags
        article_result = db["articleTag"].delete_many({
            "_id": {"$in": all_tag_ids}
        })
        print(f"Deleted {article_result.deleted_count} article tags")
        
        # Delete social feed tags  
        social_result = db["socialFeedTag"].delete_many({
            "_id": {"$in": all_tag_ids}
        })
        print(f"Deleted {social_result.deleted_count} social feed tags")
        
        # Also delete any remaining tags with these company IDs
        print("\nDeleting any remaining tags with CYBERPE865/INDIA124...")
        
        remaining_article = db["articleTag"].delete_many({
            "_id": {"$regex": "(CYBERPE865|INDIA124)$"}
        })
        print(f"Deleted {remaining_article.deleted_count} remaining article tags")
        
        remaining_social = db["socialFeedTag"].delete_many({
            "_id": {"$regex": "(CYBERPE865|INDIA124)$"}
        })
        print(f"Deleted {remaining_social.deleted_count} remaining social feed tags")
        
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
        
        total_deleted = article_result.deleted_count + social_result.deleted_count + remaining_article.deleted_count + remaining_social.deleted_count
        print(f"\nTotal tags deleted: {total_deleted}")
        print("Cleanup completed!")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    delete_specific_tags()
















