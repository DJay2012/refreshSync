import json
from datetime import datetime
from collections import OrderedDict
from ..core.Config import mongoConnection

def to_midnight(dt_like):
    """Normalize date/datetime/None -> datetime at 00:00:00"""
    if not dt_like:
        return datetime.combine(datetime.utcnow().date(), datetime.min.time())
    if isinstance(dt_like, datetime):
        dt_like = dt_like.date()
    return datetime.combine(dt_like, datetime.min.time())

def clean_source_field(source_str):
    """Clean up source field by removing escaped Unicode characters and formatting properly"""
    if not source_str:
        return "N/A"
    
    try:
        # If it's a JSON string, parse and re-format it cleanly
        parsed = json.loads(source_str)
        # Re-format as clean JSON
        return json.dumps(parsed, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        # If not valid JSON, return as-is
        return source_str

def get_next_article_id(mongo_db):
    """Get next article ID from MongoDB sequence"""
    try:
        # First, find the highest existing article ID to avoid collisions
        article_col = mongo_db["article"]
        highest_article = article_col.find_one(
            {},
            sort=[("_id", -1)]
        )
        
        if highest_article:
            max_id = highest_article["_id"]
            # Set counter to be higher than existing max
            mongo_db["counters"].update_one(
                {"_id": "articleId"},
                {"$max": {"seq": max_id}},
                upsert=True
            )
        
        # Now get next ID safely
        counter = mongo_db["counters"].find_one_and_update(
            {"_id": "articleId"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=True
        )
        return counter["seq"]
    except Exception as e:
        print(f"Error getting next article ID: {e}")
        return None

def create_mongo_tag_document(article_id, pg_articleid, company_id, company_name, tag_data, sources, is_new=True, content=None):
    """Create MongoDB tag document matching the existing structure"""
    tag_id = f"{article_id}{company_id}"
    current_timestamp = datetime.now()
    
    # Extract source flags
    headline_flag = any(field == 'headline' for source_locations in sources.values() for field in source_locations)
    content_flag = any(field == 'content' for source_locations in sources.values() for field in source_locations)
    summary_flag = any(field == 'summary' for source_locations in sources.values() for field in source_locations)
    sources_json = clean_source_field(json.dumps(sources))
    
    # Generate detailSummary if content is provided
    detail_summary = None
    if content and tag_data.get('KEYWORDS'):
        try:
            from ..utils.sentence_extractor import create_detail_summary_for_tag
            detail_summary = create_detail_summary_for_tag(tag_data, content)
        except Exception as e:
            print(f"Error creating detailSummary for tag {tag_id}: {e}")
    
    tag_doc = OrderedDict({
        "_id": tag_id,
        "articleId": article_id,
        "articleDate": to_midnight(current_timestamp),
        "sortOrder": 1,  # Default sort order
        "sourceArticleId": pg_articleid,
        "company": OrderedDict({"id": company_id, "name": company_name}),
        "tagInfo": OrderedDict({
            "keyword": tag_data.get('KEYWORDS', ''),
            "reportingTone": None,
            "reportingSubject": None,
            "subcategory": None,
            "prominence": None,
            "detailSummary": detail_summary,
            "adArticleDate": to_midnight(current_timestamp)
        }),
        "qc": OrderedDict({
            "qc1Status": False,
            "qc2Status": False,
            "qc3Status": False,
            "qc1": [{"name": None, "on": None}],
            "qc2": [{"name": None, "on": None}],
            "qc3": [{"name": None, "on": None}]
        }),
        "uploadInfo": OrderedDict({"ipadress": None, "macaddress": None}),
        "auditInfo": OrderedDict({
            "created": {"name": "elastictagger", "on": current_timestamp},
            "modified": [] if is_new else [{"name": "elastictagger", "on": current_timestamp}]
        }),
        "sourceTagInfo": OrderedDict({
            "elasticTagger": True,
            "isHeadlineTagged": headline_flag,
            "isContentTagged": content_flag,
            "isSummaryTagged": summary_flag,
            "source": sources_json
        })
    })
    
    return tag_doc

def save_tags_to_mongo(article_id, pg_articleid, tag_results, update_type, content=None):
    """Save tag results to MongoDB collections with optimized bulk operations"""
    try:
        from pymongo import UpdateOne, InsertOne
        from ..core.Config import get_mongodb_with_retry
        
        mongo_db = get_mongodb_with_retry(max_retries=2, retry_delay=3)
        if mongo_db is None:
            print(f"MongoDB connection failed after retries, skipping MongoDB operations")
            return False
        
        tag_col = mongo_db["articleTag"]
        article_col = mongo_db["article"]
        company_col = mongo_db["companyMaster"]
        
        # Get company names in bulk
        company_ids = [str(tag_data.get('COMPANYID')) for tag_data in tag_results if tag_data.get('COMPANYID')]
        companies_map = {}
        if company_ids:
            companies_cursor = company_col.find(
                {"_id": {"$in": company_ids}},
                {"_id": 1, "companyInfo.companyName": 1}
            )
            companies_map = {
                str(doc["_id"]): doc.get("companyInfo", {}).get("companyName", "")
                for doc in companies_cursor
            }
        
        # Prepare bulk operations
        tag_operations = []
        company_operations = []
        
        # Check existing tags in bulk (for both new tags and updates)
        existing_tag_ids = set()
        tag_ids_to_check = [f"{article_id}{str(tag_data.get('COMPANYID'))}" for tag_data in tag_results if tag_data.get('COMPANYID')]
        if tag_ids_to_check:
            existing_tags = tag_col.find({"_id": {"$in": tag_ids_to_check}}, {"_id": 1})
            existing_tag_ids = {doc["_id"] for doc in existing_tags}
        
        for tag_data in tag_results:
            company_id = str(tag_data.get('COMPANYID'))
            if not company_id:
                continue
                
            company_name = companies_map.get(company_id, tag_data.get('COMPANYNAME', ''))
            sources = tag_data.get('SOURCES', {})
            tag_id = f"{article_id}{company_id}"
            
            # Extract source flags once
            headline_flag = any(field == 'headline' for source_locations in sources.values() for field in source_locations)
            content_flag = any(field == 'content' for source_locations in sources.values() for field in source_locations)
            summary_flag = any(field == 'summary' for source_locations in sources.values() for field in source_locations)
            sources_json = clean_source_field(json.dumps(sources))
            
            if update_type == 1:  # New tag from elasticTagger - insert if not exists
                if tag_id not in existing_tag_ids:
                    # Create new tag (only for new tags created by elasticTagger)
                    tag_doc = create_mongo_tag_document(
                        article_id, pg_articleid, company_id, company_name, tag_data, sources, is_new=True, content=content
                    )
                    tag_operations.append(InsertOne(tag_doc))
                    
                    # Add to company tags list
                    company_operations.append(UpdateOne(
                        {"_id": article_id, "companyTag.id": {"$ne": company_id}},
                        {"$push": {"companyTag": {"id": company_id, "name": company_name}}}
                    ))
                else:
                    # Update existing tag's sourceTagInfo
                    tag_operations.append(UpdateOne(
                        {"_id": tag_id},
                        {"$set": {
                            "sourceTagInfo.elasticTagger": True,
                            "sourceTagInfo.isHeadlineTagged": headline_flag,
                            "sourceTagInfo.isContentTagged": content_flag,
                            "sourceTagInfo.isSummaryTagged": summary_flag,
                            "sourceTagInfo.source": sources_json
                        }}
                    ))
                    
            elif update_type == 2:  # Update existing tag
                if tag_id in existing_tag_ids:
                    # Tag exists in MongoDB - update it
                    tag_operations.append(UpdateOne(
                        {"_id": tag_id},
                        {"$set": {
                            "sourceTagInfo.elasticTagger": True,
                            "sourceTagInfo.isHeadlineTagged": headline_flag,
                            "sourceTagInfo.isContentTagged": content_flag,
                            "sourceTagInfo.isSummaryTagged": summary_flag,
                            "sourceTagInfo.source": sources_json,
                            "auditInfo.modified": [{"name": "elastictagger", "on": datetime.now()}]
                        }}
                    ))
                else:
                    # Tag doesn't exist in MongoDB but exists in PostgreSQL
                    # Since we're ONLY processing tags that elastictagger is currently processing:
                    # - If elastictagger is processing it NOW, it means Elasticsearch just found a match
                    # - If it doesn't exist in MongoDB, it's a sync failure (never synced), not a deletion
                    # - If it was deleted, it would have been synced first, so it won't be re-processed
                    #   unless elastictagger finds it again (which means it should exist)
                    # Therefore: Insert it to fix sync failures
                    print(f"Tag {tag_id} not in MongoDB but exists in PG - inserting (sync failure fix)")
                    tag_doc = create_mongo_tag_document(
                        article_id, pg_articleid, company_id, company_name, tag_data, sources, is_new=False, content=content
                    )
                    tag_operations.append(InsertOne(tag_doc))
                    
                    # Add to company tags list
                    company_operations.append(UpdateOne(
                        {"_id": article_id, "companyTag.id": {"$ne": company_id}},
                        {"$push": {"companyTag": {"id": company_id, "name": company_name}}}
                    ))
        
        # Execute bulk operations
        if tag_operations:
            try:
                result = tag_col.bulk_write(tag_operations, ordered=False)
                print(f"MongoDB tag operations: {result.inserted_count} inserts, {result.modified_count} updates")
            except Exception as e:
                print(f"Tag bulk write error: {e}")
                return False
        
        if company_operations:
            try:
                result = article_col.bulk_write(company_operations, ordered=False)
                print(f"MongoDB company tag operations: {result.modified_count} updates")
            except Exception as e:
                print(f"Company tag bulk write error: {e}")
        
        return True
        
    except Exception as e:
        print(f"Error saving tags to MongoDB: {e}")
        return False

def ensure_article_exists_in_mongo(pg_articleid, headline, summary, content, language, article_date=None):
    """Ensure article exists in MongoDB, create if missing"""
    try:
        from ..core.Config import get_mongodb_with_retry
        
        mongo_db = get_mongodb_with_retry(max_retries=2, retry_delay=3)
        if mongo_db is None:
            print(f"MongoDB connection failed after retries, skipping article creation")
            return None
        
        article_col = mongo_db["article"]
        
        # Check if article already exists
        existing_article = article_col.find_one({"sourceArticleId": pg_articleid})
        if existing_article:
            return existing_article["_id"]
        
        # Create new article
        article_id = get_next_article_id(mongo_db)
        if not article_id:
            return None
        
        article_doc = OrderedDict({
            "_id": article_id,
            "sourceArticleId": pg_articleid,
            "articleData": OrderedDict({
                "headlines": headline or "",
                "summary": summary or "",
                "content": content or "",
                "language": language or "en"
            }),
            "articleInfo": OrderedDict({
                "articleDate": to_midnight(article_date or datetime.now())
            }),
            "companyTag": []
        })
        
        article_col.insert_one(article_doc)
        print(f"Created new MongoDB article: {article_id} (PG: {pg_articleid})")
        return article_id
        
    except Exception as e:
        print(f"Error ensuring article exists in MongoDB: {e}")
        return None

def get_next_social_feed_id(mongo_db):
    """Get next social feed ID from MongoDB sequence"""
    try:
        # First, find the highest existing social feed ID to avoid collisions
        social_feed_col = mongo_db["socialFeed"]
        highest_feed = social_feed_col.find_one(
            {},
            sort=[("_id", -1)]
        )
        
        if highest_feed:
            max_id = highest_feed["_id"]
            # Set counter to be higher than existing max
            mongo_db["counters"].update_one(
                {"_id": "socialFeedId"},
                {"$max": {"seq": max_id}},
                upsert=True
            )
        
        # Now get next ID safely
        counter = mongo_db["counters"].find_one_and_update(
            {"_id": "socialFeedId"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=True
        )
        return counter["seq"]
    except Exception as e:
        print(f"Error getting next social feed ID: {e}")
        return None

def create_mongo_social_tag_document(social_feed_id, pg_socialfeedid, company_id, company_name, tag_data, sources, is_new=True, content=None):
    """Create MongoDB social feed tag document matching the existing structure"""
    from bson.int64 import Int64
    
    tag_id = f"{social_feed_id}{company_id}"
    current_timestamp = datetime.now()
    
    # Extract source flags
    headline_flag = any(field == 'headline' for source_locations in sources.values() for field in source_locations)
    content_flag = any(field == 'content' for source_locations in sources.values() for field in source_locations)
    summary_flag = any(field == 'summary' for source_locations in sources.values() for field in source_locations)
    sources_json = clean_source_field(json.dumps(sources))
    
    # Generate detailSummary if content is provided
    detail_summary = None
    if content and tag_data.get('KEYWORDS'):
        try:
            from ..utils.sentence_extractor import create_detail_summary_for_tag
            detail_summary = create_detail_summary_for_tag(tag_data, content)
        except Exception as e:
            print(f"Error creating detailSummary for tag {tag_id}: {e}")
    
    tag_doc = {
        "_id": tag_id,
        "socialFeedId": social_feed_id,
        "feedDate": to_midnight(current_timestamp),
        "company": {
            "id": str(company_id),
            "name": company_name
        },
        "tagInfo": {
            "keyword": tag_data.get('KEYWORDS', ''),
            "reportingTone": None,
            "reportingSubject": None,
            "subcategory": None,
            "prominence": None,
            "detailSummary": detail_summary,
            "mailerReportingSubject": None,
            "adFeedDate": to_midnight(current_timestamp),
            "detailId": None
        },
        "qc": {
            "qc1Status": False,
            "qc2Status": False,
            "qc3Status": False,
            "qc1": [{"name": None, "on": None}],
            "qc2": [{"name": None, "on": None}],
            "qc3": [{"name": None, "on": None}]
        },
        "auditInfo": {
            "created": {"name": "elastictagger", "on": current_timestamp}
        },
        "sourceTagInfo": {
            "elasticTagger": True,
            "isHeadlineTagged": headline_flag,
            "isSummaryTagged": summary_flag,
            "isContentTagged": content_flag,
            "source": sources_json
        },
        "sourceArticleId": Int64(pg_socialfeedid)
    }
    
    return tag_doc

def save_social_tags_to_mongo(social_feed_id, pg_socialfeedid, tag_results, update_type, content=None):
    """Save social feed tag results to MongoDB collections with optimized bulk operations"""
    try:
        from pymongo import UpdateOne, InsertOne
        from ..core.Config import get_mongodb_with_retry
        
        mongo_db = get_mongodb_with_retry(max_retries=2, retry_delay=3)
        if mongo_db is None:
            print(f"MongoDB connection failed after retries, skipping MongoDB operations")
            return False
        
        social_tag_col = mongo_db["socialFeedTag"]
        social_feed_col = mongo_db["socialFeed"]
        company_col = mongo_db["companyMaster"]
        
        # Get company names in bulk
        company_ids = [str(tag_data.get('COMPANYID')) for tag_data in tag_results if tag_data.get('COMPANYID')]
        companies_map = {}
        if company_ids:
            companies_cursor = company_col.find(
                {"_id": {"$in": company_ids}},
                {"_id": 1, "companyInfo.companyName": 1}
            )
            companies_map = {
                str(doc["_id"]): doc.get("companyInfo", {}).get("companyName", "")
                for doc in companies_cursor
            }
        
        # Prepare bulk operations
        tag_operations = []
        company_operations = []
        
        # Check existing tags in bulk (for both new tags and updates)
        existing_tag_ids = set()
        tag_ids_to_check = [f"{social_feed_id}{str(tag_data.get('COMPANYID'))}" for tag_data in tag_results if tag_data.get('COMPANYID')]
        if tag_ids_to_check:
            existing_tags = social_tag_col.find({"_id": {"$in": tag_ids_to_check}}, {"_id": 1})
            existing_tag_ids = {doc["_id"] for doc in existing_tags}
        
        for tag_data in tag_results:
            company_id = str(tag_data.get('COMPANYID'))
            if not company_id:
                continue
                
            company_name = companies_map.get(company_id, tag_data.get('COMPANYNAME', ''))
            sources = tag_data.get('SOURCES', {})
            tag_id = f"{social_feed_id}{company_id}"
            
            # Extract source flags once
            headline_flag = any(field == 'headline' for source_locations in sources.values() for field in source_locations)
            content_flag = any(field == 'content' for source_locations in sources.values() for field in source_locations)
            summary_flag = any(field == 'summary' for source_locations in sources.values() for field in source_locations)
            sources_json = clean_source_field(json.dumps(sources))
            
            if update_type == 1:  # New tag from elasticTagger - insert if not exists
                if tag_id not in existing_tag_ids:
                    # Create new tag (only for new tags created by elasticTagger)
                    tag_doc = create_mongo_social_tag_document(
                        social_feed_id, pg_socialfeedid, company_id, company_name, tag_data, sources, is_new=True, content=content
                    )
                    tag_operations.append(InsertOne(tag_doc))
                    
                    # Add to company tags list
                    company_operations.append(UpdateOne(
                        {"_id": social_feed_id, "companyTag.id": {"$ne": company_id}},
                        {"$addToSet": {"companyTag": {"id": company_id, "name": company_name}}}
                    ))
                else:
                    # Update existing tag's sourceTagInfo
                    tag_operations.append(UpdateOne(
                        {"_id": tag_id},
                        {"$set": {
                            "sourceTagInfo.elasticTagger": True,
                            "sourceTagInfo.isHeadlineTagged": headline_flag,
                            "sourceTagInfo.isSummaryTagged": summary_flag,
                            "sourceTagInfo.isContentTagged": content_flag,
                            "sourceTagInfo.source": sources_json
                        }}
                    ))
                    
            elif update_type == 2:  # Update existing tag
                if tag_id in existing_tag_ids:
                    # Tag exists in MongoDB - update it
                    tag_operations.append(UpdateOne(
                        {"_id": tag_id},
                        {"$set": {
                            "sourceTagInfo.elasticTagger": True,
                            "sourceTagInfo.isHeadlineTagged": headline_flag,
                            "sourceTagInfo.isSummaryTagged": summary_flag,
                            "sourceTagInfo.isContentTagged": content_flag,
                            "sourceTagInfo.source": sources_json
                        }}
                    ))
                else:
                    # Tag doesn't exist in MongoDB but exists in PostgreSQL
                    # Since we're ONLY processing tags that elastictagger is currently processing:
                    # - If elastictagger is processing it NOW, it means Elasticsearch just found a match
                    # - If it doesn't exist in MongoDB, it's a sync failure (never synced), not a deletion
                    # - If it was deleted, it would have been synced first, so it won't be re-processed
                    #   unless elastictagger finds it again (which means it should exist)
                    # Therefore: Insert it to fix sync failures
                    print(f"Tag {tag_id} not in MongoDB but exists in PG - inserting (sync failure fix)")
                    tag_doc = create_mongo_social_tag_document(
                        social_feed_id, pg_socialfeedid, company_id, company_name, tag_data, sources, is_new=False, content=content
                    )
                    tag_operations.append(InsertOne(tag_doc))
                    
                    # Add to company tags list
                    company_operations.append(UpdateOne(
                        {"_id": social_feed_id, "companyTag.id": {"$ne": company_id}},
                        {"$addToSet": {"companyTag": {"id": company_id, "name": company_name}}}
                    ))
        
        # Execute bulk operations
        if tag_operations:
            try:
                result = social_tag_col.bulk_write(tag_operations, ordered=False)
                print(f"MongoDB social tag operations: {result.inserted_count} inserts, {result.modified_count} updates")
            except Exception as e:
                print(f"Social tag bulk write error: {e}")
                return False
        
        if company_operations:
            try:
                result = social_feed_col.bulk_write(company_operations, ordered=False)
                print(f"MongoDB social company tag operations: {result.modified_count} updates")
            except Exception as e:
                print(f"Social company tag bulk write error: {e}")
        
        return True
        
    except Exception as e:
        print(f"Error saving social tags to MongoDB: {e}")
        return False

def ensure_social_feed_exists_in_mongo(pg_socialfeedid, headline, summary, content, language, feed_date=None):
    """Ensure social feed exists in MongoDB, create if missing"""
    try:
        from bson.int64 import Int64
        from ..core.Config import get_mongodb_with_retry
        
        mongo_db = get_mongodb_with_retry(max_retries=2, retry_delay=3)
        if mongo_db is None:
            print(f"MongoDB connection failed after retries, skipping social feed creation")
            return None
        
        social_feed_col = mongo_db["socialFeed"]
        
        # Check if social feed already exists using sourceArticleId (matching the patch file)
        existing_feed = social_feed_col.find_one({"sourceArticleId": Int64(pg_socialfeedid)})
        if existing_feed:
            return existing_feed["_id"]
        
        # Create new social feed
        social_feed_id = get_next_social_feed_id(mongo_db)
        if not social_feed_id:
            return None
        
        feed_doc = {
            "_id": social_feed_id,
            "sourceArticleId": Int64(pg_socialfeedid),
            "feedData": {
                "headline": headline or "",
                "summary": summary or "",
                "content": content or "",
                "language": language or "en",
                "feedDate": to_midnight(feed_date or datetime.now())
            },
            "companyTag": []
        }
        
        social_feed_col.insert_one(feed_doc)
        print(f"Created new MongoDB social feed: {social_feed_id} (PG: {pg_socialfeedid})")
        return social_feed_id
        
    except Exception as e:
        print(f"Error ensuring social feed exists in MongoDB: {e}")
        return None
