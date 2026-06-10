#!/usr/bin/env python3
"""
Export script for articleResearch collection from smFeeds MongoDB database.
Exports all documents with entities extracted as comma-separated values.
"""

import sys
import csv
import json
import os
from datetime import datetime
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def connect_to_research_db():
    """Connect to the smFeeds database and articleResearch collection."""
    try:
        mongo_uri = os.getenv("PG_MONGO_URI", "mongodb://localhost:27017/")
        client = MongoClient(mongo_uri)
        db = client["smFeeds"]
        collection = db["articleResearch"]
        return collection
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}")
        return None

def extract_entities_from_document(doc):
    """Extract all entities from a document and return as comma-separated values."""
    entities = []
    
    research_data = doc.get("researchData", {})
    entity_categories = research_data.get("entities", [])
    
    for category in entity_categories:
        category_name = category.get("category", "")
        category_entities = category.get("entities", [])
        
        for entity_data in category_entities:
            entity_name = entity_data.get("entity", "")
            if entity_name:
                entities.append(entity_name)
    
    return ", ".join(entities)

def extract_company_ids_from_document(doc):
    """Extract all company IDs and names from a document."""
    companies = []
    
    research_data = doc.get("researchData", {})
    entity_categories = research_data.get("entities", [])
    
    for category in entity_categories:
        category_entities = category.get("entities", [])
        
        for entity_data in category_entities:
            company_ids = entity_data.get("companyIds", [])
            
            for company in company_ids:
                company_id = company.get("companyId", "")
                company_name = company.get("companyName", "")
                if company_id and company_name:
                    companies.append(f"{company_name} ({company_id})")
    
    return ", ".join(companies)

def export_research_collection_to_csv(output_file=None):
    """Export all documents from articleResearch collection to CSV."""
    collection = connect_to_research_db()
    
    if collection is None:
        print("Failed to connect to database")
        return
    
    # Generate filename if not provided
    if not output_file:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f"articleResearch_export_{timestamp}.csv"
    
    print("Connecting to smFeeds.articleResearch collection...")
    
    try:
        # Get total count for progress tracking
        total_count = collection.count_documents({})
        print(f"Found {total_count} documents to export")
        
        if total_count == 0:
            print("No documents found in collection")
            return
        
        # Open CSV file for writing
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = [
                'article_id',
                'article_date', 
                'article_type',
                'headline',
                'researched',
                'research_upload_date',
                'all_entities',
                'company_mappings',
                'total_entity_categories',
                'total_entities'
            ]
            
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            # Process documents in batches
            batch_size = 1000
            processed = 0
            
            cursor = collection.find({})
            
            for doc in cursor:
                try:
                    # Extract basic fields
                    article_id = doc.get("articleId", "")
                    article_date = doc.get("articleDate", {})
                    if isinstance(article_date, dict) and "$date" in article_date:
                        article_date = article_date["$date"]
                    
                    article_type = doc.get("articleType", "")
                    headline = doc.get("headline", "")
                    researched = doc.get("researched", False)
                    
                    research_upload_date = doc.get("researchUploadDate", {})
                    if isinstance(research_upload_date, dict) and "$date" in research_upload_date:
                        research_upload_date = research_upload_date["$date"]
                    
                    # Extract entities
                    all_entities = extract_entities_from_document(doc)
                    company_mappings = extract_company_ids_from_document(doc)
                    
                    # Count entities
                    research_data = doc.get("researchData", {})
                    entity_categories = research_data.get("entities", [])
                    total_entity_categories = len(entity_categories)
                    
                    total_entities = 0
                    for category in entity_categories:
                        total_entities += len(category.get("entities", []))
                    
                    # Write row to CSV
                    writer.writerow({
                        'article_id': article_id,
                        'article_date': article_date,
                        'article_type': article_type,
                        'headline': headline,
                        'researched': researched,
                        'research_upload_date': research_upload_date,
                        'all_entities': all_entities,
                        'company_mappings': company_mappings,
                        'total_entity_categories': total_entity_categories,
                        'total_entities': total_entities
                    })
                    
                    processed += 1
                    
                    # Progress update
                    if processed % batch_size == 0:
                        print(f"Processed {processed}/{total_count} documents ({processed/total_count*100:.1f}%)")
                
                except Exception as e:
                    print(f"Error processing document {doc.get('_id', 'unknown')}: {e}")
                    continue
            
            print(f"Export completed! Processed {processed} documents")
            print(f"Results saved to: {output_file}")
            
    except Exception as e:
        print(f"Error during export: {e}")

def export_simple_entities_csv(output_file=None):
    """Export simplified format: article_id, headline, entity_name (comma-separated)."""
    collection = connect_to_research_db()
    
    if collection is None:
        print("Failed to connect to database")
        return
    
    # Generate filename if not provided
    if not output_file:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f"simple_entities_{timestamp}.csv"
    
    print("Exporting simple entities format from smFeeds.articleResearch collection...")
    
    try:
        total_count = collection.count_documents({})
        print(f"Found {total_count} documents to process")
        
        if total_count == 0:
            print("No documents found in collection")
            return
        
        # Open CSV file for writing
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['article_id', 'headline', 'entity_name']
            
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            processed = 0
            
            for doc in collection.find({}):
                try:
                    article_id = doc.get("articleId", "")
                    headline = doc.get("headline", "")
                    
                    # Collect all entity names from all categories
                    all_entities = []
                    research_data = doc.get("researchData", {})
                    entity_categories = research_data.get("entities", [])
                    
                    for category in entity_categories:
                        category_entities = category.get("entities", [])
                        
                        for entity_data in category_entities:
                            entity_name = entity_data.get("entity", "")
                            if entity_name and entity_name not in all_entities:
                                all_entities.append(entity_name)
                    
                    # Join all entities with commas
                    entities_string = ", ".join(all_entities)
                    
                    # Write one row per article
                    writer.writerow({
                        'article_id': article_id,
                        'headline': headline,
                        'entity_name': entities_string
                    })
                    
                    processed += 1
                    
                    if processed % 1000 == 0:
                        print(f"Processed {processed}/{total_count} documents")
                
                except Exception as e:
                    print(f"Error processing document {doc.get('_id', 'unknown')}: {e}")
                    continue
            
            print(f"Simple entities export completed! Processed {processed} documents")
            print(f"Results saved to: {output_file}")
            
    except Exception as e:
        print(f"Error during simple entities export: {e}")

def export_entities_only_csv(output_file=None):
    """Export only entities data in a simplified format."""
    collection = connect_to_research_db()
    
    if collection is None:
        print("Failed to connect to database")
        return
    
    # Generate filename if not provided
    if not output_file:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f"research_entities_only_{timestamp}.csv"
    
    print("Exporting entities only from smFeeds.articleResearch collection...")
    
    try:
        total_count = collection.count_documents({})
        print(f"Found {total_count} documents to process")
        
        if total_count == 0:
            print("No documents found in collection")
            return
        
        # Open CSV file for writing
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = [
                'article_id',
                'headline',
                'entity_category',
                'entity_name',
                'sentiment',
                'prominence_score',
                'reporting_subject',
                'justification',
                'company_id',
                'company_name',
                'qc3_status'
            ]
            
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            processed = 0
            
            for doc in collection.find({}):
                try:
                    article_id = doc.get("articleId", "")
                    headline = doc.get("headline", "")
                    
                    research_data = doc.get("researchData", {})
                    entity_categories = research_data.get("entities", [])
                    
                    for category in entity_categories:
                        category_name = category.get("category", "")
                        category_entities = category.get("entities", [])
                        
                        for entity_data in category_entities:
                            entity_name = entity_data.get("entity", "")
                            sentiment = entity_data.get("sentiment", "")
                            prominence_score = entity_data.get("prominence_score", "")
                            reporting_subject = entity_data.get("reportingSubject", "")
                            justification = entity_data.get("justification", "")
                            
                            company_ids = entity_data.get("companyIds", [])
                            
                            if company_ids:
                                # Write one row per company mapping
                                for company in company_ids:
                                    writer.writerow({
                                        'article_id': article_id,
                                        'headline': headline,
                                        'entity_category': category_name,
                                        'entity_name': entity_name,
                                        'sentiment': sentiment,
                                        'prominence_score': prominence_score,
                                        'reporting_subject': reporting_subject,
                                        'justification': justification,
                                        'company_id': company.get("companyId", ""),
                                        'company_name': company.get("companyName", ""),
                                        'qc3_status': company.get("qc3Status", "")
                                    })
                            else:
                                # Write row without company mapping
                                writer.writerow({
                                    'article_id': article_id,
                                    'headline': headline,
                                    'entity_category': category_name,
                                    'entity_name': entity_name,
                                    'sentiment': sentiment,
                                    'prominence_score': prominence_score,
                                    'reporting_subject': reporting_subject,
                                    'justification': justification,
                                    'company_id': '',
                                    'company_name': '',
                                    'qc3_status': ''
                                })
                    
                    processed += 1
                    
                    if processed % 1000 == 0:
                        print(f"Processed {processed}/{total_count} documents")
                
                except Exception as e:
                    print(f"Error processing document {doc.get('_id', 'unknown')}: {e}")
                    continue
            
            print(f"Entities export completed! Processed {processed} documents")
            print(f"Results saved to: {output_file}")
            
    except Exception as e:
        print(f"Error during entities export: {e}")

def show_collection_stats():
    """Show statistics about the articleResearch collection."""
    collection = connect_to_research_db()
    
    if collection is None:
        print("Failed to connect to database")
        return
    
    try:
        print("=" * 80)
        print("ARTICLE RESEARCH COLLECTION STATISTICS")
        print("=" * 80)
        
        total_count = collection.count_documents({})
        researched_count = collection.count_documents({"researched": True})
        
        print(f"Total Documents: {total_count}")
        print(f"Researched Documents: {researched_count}")
        print(f"Research Percentage: {(researched_count/total_count*100):.1f}%" if total_count > 0 else "N/A")
        
        # Article type distribution
        print("\nArticle Type Distribution:")
        pipeline = [
            {"$group": {"_id": "$articleType", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]
        
        for result in collection.aggregate(pipeline):
            article_type = result["_id"] or "Unknown"
            count = result["count"]
            print(f"  {article_type}: {count}")
        
        # Sample recent documents
        print("\nRecent Documents (last 5):")
        recent_docs = collection.find({}).sort("researchUploadDate", -1).limit(5)
        
        print(f"{'ID':<12} {'Type':<8} {'Headline':<50} {'Researched':<10}")
        print("-" * 80)
        
        for doc in recent_docs:
            article_id = doc.get("articleId", "N/A")
            article_type = doc.get("articleType", "N/A")
            headline = doc.get("headline", "No headline")
            headline_short = (headline[:47] + "...") if len(headline) > 50 else headline
            researched = "Yes" if doc.get("researched", False) else "No"
            
            print(f"{article_id:<12} {article_type:<8} {headline_short:<50} {researched:<10}")
        
    except Exception as e:
        print(f"Error getting collection stats: {e}")

def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Article Research Collection Export Tool")
        print("Usage:")
        print("  python export_research_entities.py --full [output.csv]     - Export full data")
        print("  python export_research_entities.py --entities [output.csv] - Export detailed entities")
        print("  python export_research_entities.py --simple [output.csv]   - Export simple format (article_id, headline, entity_name)")
        print("  python export_research_entities.py --stats                 - Show collection statistics")
        print("\nExamples:")
        print("  python export_research_entities.py --full")
        print("  python export_research_entities.py --entities entities.csv")
        print("  python export_research_entities.py --simple simple.csv")
        print("  python export_research_entities.py --stats")
        sys.exit(1)
    
    command = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    if command == "--full":
        export_research_collection_to_csv(output_file)
    elif command == "--entities":
        export_entities_only_csv(output_file)
    elif command == "--simple":
        export_simple_entities_csv(output_file)
    elif command == "--stats":
        show_collection_stats()
    else:
        print(f"Unknown command: {command}")
        print("Use --full, --entities, --simple, or --stats")
        sys.exit(1)

if __name__ == "__main__":
    main()