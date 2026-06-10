#!/usr/bin/env python3
"""
Script to check MongoDB tags created by backtracking and show the IDs
"""

import sys
import os
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient
from datetime import datetime, timedelta

# Load environment variables from .env file (local directory)
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

def check_mongo_tags():
    """Check MongoDB tags created by backtracking."""
    
    print("Checking MongoDB Tags Created by Backtracking")
    print("=" * 60)
    
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
        
        # Check article tags created in last 30 minutes
        cutoff_time = datetime.now() - timedelta(minutes=30)
        
        print("1. ARTICLE TAGS CREATED (last 30 minutes)")
        print("-" * 50)
        
        article_tags = db["articleTag"].find({
            "auditInfo.created.on": {"$gte": cutoff_time}
        }).sort("auditInfo.created.on", -1).limit(20)
        
        article_tag_count = 0
        for tag in article_tags:
            article_tag_count += 1
            print(f"Tag ID: {tag['_id']}")
            print(f"  Article ID: {tag['articleId']}")
            print(f"  Source Article ID: {tag['sourceArticleId']}")
            print(f"  Company: {tag['company']['id']} - {tag['company']['name']}")
            print(f"  Created: {tag['auditInfo']['created']['on']}")
            print(f"  Keyword: {tag['tagInfo']['keyword']}")
            print()
        
        print(f"Total article tags found: {article_tag_count}")
        print()
        
        # Check social feed tags created in last 30 minutes
        print("2. SOCIAL FEED TAGS CREATED (last 30 minutes)")
        print("-" * 50)
        
        social_tags = db["socialFeedTag"].find({
            "auditInfo.created.on": {"$gte": cutoff_time}
        }).sort("auditInfo.created.on", -1).limit(20)
        
        social_tag_count = 0
        for tag in social_tags:
            social_tag_count += 1
            print(f"Tag ID: {tag['_id']}")
            print(f"  Social Feed ID: {tag['socialFeedId']}")
            print(f"  Source Social Feed ID: {tag['sourceSocialFeedId']}")
            print(f"  Company: {tag['company']['id']} - {tag['company']['name']}")
            print(f"  Created: {tag['auditInfo']['created']['on']}")
            print(f"  Keyword: {tag['tagInfo']['keyword']}")
            print()
        
        print(f"Total social feed tags found: {social_tag_count}")
        print()
        
        # Check if companyTag arrays were updated in articles
        print("3. ARTICLES WITH COMPANY TAG UPDATES (last 30 minutes)")
        print("-" * 50)
        
        # Find articles that have companyTag array with recent companies
        articles_with_tags = db["article"].find({
            "companyTag.id": {"$in": ["CYBERPE865", "INDIA124"]},
            "auditInfo.modified": {"$elemMatch": {"on": {"$gte": cutoff_time}}}
        }).limit(10)
        
        article_count = 0
        for article in articles_with_tags:
            article_count += 1
            print(f"Article ID: {article['_id']}")
            print(f"  Company Tags: {len(article.get('companyTag', []))}")
            for company_tag in article.get('companyTag', []):
                if company_tag['id'] in ['CYBERPE865', 'INDIA124']:
                    print(f"    - {company_tag['id']}: {company_tag['name']}")
            print()
        
        print(f"Total articles with company tags: {article_count}")
        print()
        
        # Check if companyTag arrays were updated in social feeds
        print("4. SOCIAL FEEDS WITH COMPANY TAG UPDATES (last 30 minutes)")
        print("-" * 50)
        
        social_feeds_with_tags = db["socialFeed"].find({
            "companyTag.id": {"$in": ["CYBERPE865", "INDIA124"]},
            "auditInfo.modified": {"$elemMatch": {"on": {"$gte": cutoff_time}}}
        }).limit(10)
        
        social_feed_count = 0
        for social_feed in social_feeds_with_tags:
            social_feed_count += 1
            print(f"Social Feed ID: {social_feed['_id']}")
            print(f"  Company Tags: {len(social_feed.get('companyTag', []))}")
            for company_tag in social_feed.get('companyTag', []):
                if company_tag['id'] in ['CYBERPE865', 'INDIA124']:
                    print(f"    - {company_tag['id']}: {company_tag['name']}")
            print()
        
        print(f"Total social feeds with company tags: {social_feed_count}")
        print()
        
        # Summary
        print("5. SUMMARY")
        print("-" * 50)
        print(f"Article tags created: {article_tag_count}")
        print(f"Social feed tags created: {social_tag_count}")
        print(f"Articles with company tag updates: {article_count}")
        print(f"Social feeds with company tag updates: {social_feed_count}")
        print(f"Total tags created: {article_tag_count + social_tag_count}")
        
        client.close()
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_mongo_tags()
















