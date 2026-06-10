"""
Client sync service for migrating client-specific data from MongoDB to Elasticsearch.
Handles both CBCP (Client Basket City Publication Group) and CPOnline (Client Publication Online) data.
"""

import asyncio
import math
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor

import pytz
from elasticsearch.helpers import bulk
from pymongo import MongoClient

from app.config import settings
from app.utils.database import get_mongo_client, get_elasticsearch_client
from app.utils.logger import get_logger
from app.models.schemas import ClientSyncResult as ClientSyncResultSchema

logger = get_logger(__name__)


class ClientSyncService:
    """Service for syncing client-specific data from MongoDB to Elasticsearch."""
    
    def __init__(self):
        self.mongo_client: Optional[MongoClient] = None
        self.es_client = None
        self._initialized = False
    
    async def initialize(self):
        """Initialize the service with database connections."""
        if self._initialized:
            return
        
        try:
            self.mongo_client = await get_mongo_client()
            self.es_client = await get_elasticsearch_client()
            self._initialized = True
            logger.info("ClientSyncService initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize ClientSyncService: {e}")
            raise
    
    async def sync_cbcp_data(self, client_id: str) -> ClientSyncResultSchema:
        """Sync Client Basket City Publication Group data for a client."""
        start_time = time.time()
        
        try:
            await self.initialize()
            
            # Clear existing data
            deleted_count = await self._clear_cbcp_elasticsearch_data(client_id)
            
            # Get total document count
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection = db.clientBasketCityPubGroup
            total_docs = collection.count_documents({"clientId": client_id})
            
            if total_docs == 0:
                return ClientSyncResultSchema(
                    client_id=client_id,
                    sync_type="cbcp",
                    success=True,
                    message=f"No CBCP documents found for client {client_id}",
                    documents_processed=0,
                    documents_indexed=0,
                    documents_deleted=deleted_count,
                    processing_time=time.time() - start_time,
                    timestamp=datetime.utcnow()
                )
            
            # Process in batches with progress tracking
            # Use smaller batch size for Amazon entities to prevent timeout
            batch_size = settings.AMAZON_BATCH_SIZE if client_id in ["AMPARENT", "AMZIN", "QC2AMZ", "AMZCOMP", "AMZHR", "AMZNPO"] else 100
            batches = math.ceil(total_docs / batch_size)
            total_indexed = 0
            
            logger.info(f"Starting CBCP sync for {client_id}: {total_docs} documents in {batches} batches (batch_size={batch_size})")
            
            for batch_no in range(batches):
                skip = batch_no * batch_size
                batch_indexed = await self._process_cbcp_batch(client_id, skip, batch_size, batch_no)
                total_indexed += batch_indexed
                
                # Log progress
                progress = (batch_no + 1) / batches * 100
                logger.info(f"CBCP {client_id} sync progress: {progress:.1f}% (batch {batch_no + 1}/{batches}) - indexed {total_indexed} docs")
                
                # Small delay to prevent overwhelming Elasticsearch
                await asyncio.sleep(0.1)
            
            logger.info(f"Completed CBCP sync for {client_id}: {total_indexed} documents indexed")
            
            return ClientSyncResultSchema(
                client_id=client_id,
                sync_type="cbcp",
                success=True,
                message=f"Successfully synced CBCP data for client {client_id}",
                documents_processed=total_docs,
                documents_indexed=total_indexed,
                documents_deleted=deleted_count,
                processing_time=time.time() - start_time,
                timestamp=datetime.utcnow()
            )
            
        except Exception as e:
            logger.error(f"Error syncing CBCP data for client {client_id}: {e}")
            return ClientSyncResultSchema(
                client_id=client_id,
                sync_type="cbcp",
                success=False,
                message=f"Failed to sync CBCP data for client {client_id}: {str(e)}",
                processing_time=time.time() - start_time,
                timestamp=datetime.utcnow()
            )
    
    async def sync_cponline_data(self, client_id: str) -> ClientSyncResultSchema:
        """Sync Client Publication Online data for a client."""
        start_time = time.time()
        
        try:
            await self.initialize()
            
            # Fetch documents from MongoDB
            mongo_docs = await self._fetch_cponline_from_mongo(client_id)
            
            if not mongo_docs:
                return ClientSyncResultSchema(
                    client_id=client_id,
                    sync_type="cponline",
                    success=True,
                    message=f"No CPOnline documents found for client {client_id}",
                    documents_processed=0,
                    documents_indexed=0,
                    documents_deleted=0,
                    processing_time=time.time() - start_time,
                    timestamp=datetime.utcnow()
                )
            
            # Clear existing data
            deleted_count = await self._clear_cponline_elasticsearch_data(client_id)
            
            # Insert new data in chunks to prevent worker timeout
            indexed_count = await self._insert_cponline_into_elastic_chunked(mongo_docs, client_id)
            
            return ClientSyncResultSchema(
                client_id=client_id,
                sync_type="cponline",
                success=True,
                message=f"Successfully synced CPOnline data for client {client_id}",
                documents_processed=len(mongo_docs),
                documents_indexed=indexed_count,
                documents_deleted=deleted_count,
                processing_time=time.time() - start_time,
                timestamp=datetime.utcnow()
            )
            
        except Exception as e:
            logger.error(f"Error syncing CPOnline data for client {client_id}: {e}")
            return ClientSyncResultSchema(
                client_id=client_id,
                sync_type="cponline",
                success=False,
                message=f"Failed to sync CPOnline data for client {client_id}: {str(e)}",
                processing_time=time.time() - start_time,
                timestamp=datetime.utcnow()
            )
    
    async def sync_all_client_data(self, client_id: str) -> List[ClientSyncResultSchema]:
        """Sync both CBCP and CPOnline data for a client."""
        results = []
        
        # Sync CBCP data
        cbcp_result = await self.sync_cbcp_data(client_id)
        results.append(cbcp_result)
        
        # Sync CPOnline data
        cponline_result = await self.sync_cponline_data(client_id)
        results.append(cponline_result)
        
        return results
    
    async def _clear_cbcp_elasticsearch_data(self, client_id: str) -> int:
        """Clear existing CBCP documents in Elasticsearch for the given clientId."""
        try:
            response = self.es_client.delete_by_query(
                index="cbcpindex",
                body={"query": {"term": {"clientId.keyword": client_id}}},
                refresh=True,
                conflicts="proceed"
            )
            deleted_count = response.get("deleted", 0)
            logger.info(f"Cleared {deleted_count} CBCP documents for client {client_id}")
            return deleted_count
        except Exception as e:
            logger.error(f"Error clearing CBCP data for client {client_id}: {e}")
            return 0
    
    async def _clear_cponline_elasticsearch_data(self, client_id: str) -> int:
        """Clear existing CPOnline documents in Elasticsearch for the given clientId."""
        try:
            response = self.es_client.delete_by_query(
                index="cponline",
                body={"query": {"term": {"clientId.keyword": str(client_id)}}},
                refresh=True,
                conflicts="proceed"
            )
            deleted_count = response.get("deleted", 0)
            logger.info(f"Cleared {deleted_count} CPOnline documents for client {client_id}")
            return deleted_count
        except Exception as e:
            logger.error(f"Error clearing CPOnline data for client {client_id}: {e}")
            return 0
    
    async def _process_cbcp_batch(self, client_id: str, skip: int, limit: int, batch_no: int) -> int:
        """Process a batch of CBCP documents."""
        try:
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection = db.clientBasketCityPubGroup
            cursor = collection.find({"clientId": client_id}).skip(skip).limit(limit)
            
            actions = []
            for doc in cursor:
                actions.append({
                    "_op_type": "index",
                    "_index": "cbcpindex",
                    "_id": str(doc["_id"]),
                    "_source": {
                        "clientId": doc["clientId"],
                        "companyId": doc["companyId"],
                        "cityId": doc["cityId"],
                        "pubGroupId": doc.get("pubGroupId", [])
                    }
                })
            
            if actions:
                success_count, errors = bulk(self.es_client, actions, refresh=True)
                logger.info(f"CBCP batch {batch_no} for client {client_id}: indexed {success_count} docs, errors {len(errors)}")
                return success_count
            
            return 0
            
        except Exception as e:
            logger.error(f"Error processing CBCP batch {batch_no} for client {client_id}: {e}")
            return 0
    
    async def _fetch_cponline_from_mongo(self, client_id: str) -> List[Dict[str, Any]]:
        """Fetch CPOnline documents from MongoDB."""
        try:
            db = self.mongo_client[settings.MONGODB_DATABASE]
            collection = db.clientPublicationOnline
            docs = list(collection.find({"clientPublicationInfo.clientId": str(client_id)}))
            logger.info(f"Found {len(docs)} CPOnline records for client {client_id}")
            return docs
        except Exception as e:
            logger.error(f"Error fetching CPOnline data for client {client_id}: {e}")
            return []
    
    async def _insert_cponline_into_elastic_chunked(self, mongo_docs: List[Dict[str, Any]], client_id: str) -> int:
        """Insert CPOnline documents into Elasticsearch in chunks to prevent worker timeout."""
        try:
            # Use smaller chunk size for Amazon entities to prevent timeout
            chunk_size = settings.AMAZON_BATCH_SIZE if client_id in ["AMPARENT", "AMZIN", "QC2AMZ", "AMZCOMP", "AMZHR", "AMZNPO"] else 100
            total_indexed = 0
            total_docs = len(mongo_docs)
            
            logger.info(f"Starting chunked CPOnline sync for {client_id}: {total_docs} documents in chunks of {chunk_size}")
            
            for i in range(0, total_docs, chunk_size):
                chunk = mongo_docs[i:i + chunk_size]
                chunk_indexed = await self._insert_cponline_chunk(chunk, i // chunk_size)
                total_indexed += chunk_indexed
                
                # Log progress
                progress = (i + len(chunk)) / total_docs * 100
                logger.info(f"CPOnline {client_id} sync progress: {progress:.1f}% ({i + len(chunk)}/{total_docs}) - indexed {total_indexed} docs")
                
                # Small delay to prevent overwhelming Elasticsearch
                await asyncio.sleep(0.1)
            
            logger.info(f"Completed CPOnline sync for {client_id}: {total_indexed} documents indexed")
            return total_indexed
            
        except Exception as e:
            logger.error(f"Error in chunked CPOnline insertion for {client_id}: {e}")
            return 0
    
    async def _insert_cponline_chunk(self, chunk_docs: List[Dict[str, Any]], chunk_no: int) -> int:
        """Insert a chunk of CPOnline documents into Elasticsearch."""
        try:
            actions = []
            id_counts = {}  # Track duplicate IDs
            
            for doc in chunk_docs:
                info = doc["clientPublicationInfo"]
                
                pub_name = str(info["publicationName"]).strip()
                es_id = f"{info['clientId']}_{self._safe_id(pub_name)}"
                
                # Track duplicate IDs
                if es_id in id_counts:
                    id_counts[es_id] += 1
                    es_id = f"{es_id}_{id_counts[es_id]}"
                else:
                    id_counts[es_id] = 1
                
                # Convert createdOn to ISO string
                created_on = info.get("createdOn", datetime.now(pytz.utc))
                if isinstance(created_on, datetime):
                    created_on = created_on.isoformat()
                
                # Convert createdBy to numeric
                created_by = info.get("createdBy", 1)
                if isinstance(created_by, str):
                    try:
                        created_by = int(created_by) if created_by.isdigit() else 1
                    except (ValueError, TypeError):
                        created_by = 1
                
                actions.append({
                    "_index": "cponline",
                    "_id": es_id,
                    "_source": {
                        "clientId": str(info["clientId"]),
                        "publicationName": pub_name,
                        "createdBy": created_by,
                        "createdOn": created_on
                    }
                })
            
            # Report duplicate IDs for this chunk
            duplicates = {k: v for k, v in id_counts.items() if v > 1}
            if duplicates:
                logger.warning(f"Chunk {chunk_no}: Found {len(duplicates)} duplicate publication names")
            
            if actions:
                from elasticsearch.helpers import bulk
                success_count, errors = bulk(self.es_client, actions, raise_on_error=False)
                if errors:
                    logger.warning(f"CPOnline chunk {chunk_no}: {len(errors)} errors occurred")
                return success_count
            
            return 0
            
        except Exception as e:
            logger.error(f"Error inserting CPOnline chunk {chunk_no}: {e}")
            return 0

    async def _insert_cponline_into_elastic(self, mongo_docs: List[Dict[str, Any]]) -> int:
        """Insert CPOnline documents into Elasticsearch (legacy method for backward compatibility)."""
        try:
            actions = []
            id_counts = {}  # Track duplicate IDs
            
            for doc in mongo_docs:
                info = doc["clientPublicationInfo"]
                
                pub_name = str(info["publicationName"]).strip()
                es_id = f"{info['clientId']}_{self._safe_id(pub_name)}"
                
                # Track duplicate IDs
                if es_id in id_counts:
                    id_counts[es_id] += 1
                    es_id = f"{es_id}_{id_counts[es_id]}"
                else:
                    id_counts[es_id] = 1
                
                # Convert createdOn to ISO string
                created_on = info.get("createdOn", datetime.now(pytz.utc))
                if isinstance(created_on, datetime):
                    created_on = created_on.isoformat()
                
                # Convert createdBy to numeric
                created_by = info.get("createdBy", 1)
                if isinstance(created_by, str):
                    try:
                        created_by = int(created_by) if created_by.isdigit() else 1
                    except (ValueError, TypeError):
                        created_by = 1
                
                actions.append({
                    "_index": "cponline",
                    "_id": es_id,
                    "_source": {
                        "clientId": str(info["clientId"]),
                        "publicationName": pub_name,
                        "createdBy": created_by,
                        "createdOn": created_on
                    }
                })
            
            # Report duplicate IDs
            duplicates = {k: v for k, v in id_counts.items() if v > 1}
            if duplicates:
                logger.warning(f"Found {len(duplicates)} duplicate publication names, created unique IDs")
            
            if actions:
                from elasticsearch.helpers import bulk
                success_count, errors = bulk(self.es_client, actions, raise_on_error=False)
                logger.info(f"Indexed {success_count} CPOnline records to Elasticsearch")
                return success_count
            
            return 0
            
        except Exception as e:
            logger.error(f"Error inserting CPOnline data into Elasticsearch: {e}")
            return 0
    
    def _safe_id(self, text: str) -> str:
        """Convert text to safe ID (alphanumeric + underscore)."""
        return re.sub(r'[^A-Za-z0-9_-]+', '_', str(text))
    
    async def cleanup(self):
        """Cleanup resources."""
        try:
            if self.mongo_client:
                self.mongo_client.close()
            
            if self.es_client:
                self.es_client.close()
            
            self._initialized = False
            logger.info("ClientSyncService cleanup completed")
            
        except Exception as e:
            logger.error(f"Error during ClientSyncService cleanup: {e}")


# Global service instance
_client_sync_service = None


async def get_client_sync_service() -> ClientSyncService:
    """Get the global client sync service instance."""
    global _client_sync_service
    
    if _client_sync_service is None:
        _client_sync_service = ClientSyncService()
        await _client_sync_service.initialize()
    
    return _client_sync_service
