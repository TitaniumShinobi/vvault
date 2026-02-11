#!/usr/bin/env python3
"""
ChromaDB Configuration Module
Per-instance ChromaDB storage - each construct gets isolated memory collections
"""

import os
import logging
from pathlib import Path
from typing import Optional, Dict

try:
    from chromadb import Client, Settings
    from chromadb.utils import embedding_functions
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    Client = None
    Settings = None

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

INSTANCES_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'instances'))

_instance_clients: Dict[str, 'Client'] = {}
_embedder = None


def get_instance_chroma_path(construct_id: str, shard: str = "shard_0000") -> str:
    """
    Get the ChromaDB path for a specific construct instance.
    Each instance gets isolated storage at: instances/{shard}/{construct_id}/memup/chroma/
    """
    instance_path = os.path.join(INSTANCES_ROOT, shard, construct_id, "memup", "chroma")
    os.makedirs(instance_path, exist_ok=True)
    return instance_path


def get_instance_client(construct_id: str, shard: str = "shard_0000") -> Optional['Client']:
    """
    Get or create a ChromaDB client for a specific construct instance.
    Each instance has its own isolated ChromaDB storage.
    """
    if not CHROMADB_AVAILABLE:
        logger.warning("⚠️ ChromaDB not installed, memory features disabled")
        return None
    
    cache_key = f"{shard}/{construct_id}"
    
    if cache_key not in _instance_clients:
        chroma_path = get_instance_chroma_path(construct_id, shard)
        _instance_clients[cache_key] = Client(Settings(
            persist_directory=chroma_path,
            anonymized_telemetry=False
        ))
        logger.info(f"✅ ChromaDB client initialized for {construct_id} at {chroma_path}")
    
    return _instance_clients[cache_key]


def get_global_chroma_path() -> str:
    """Get the global ChromaDB path for shared collections (fallback)"""
    global_path = os.path.join(INSTANCES_ROOT, "..", "data", "chroma_global")
    os.makedirs(global_path, exist_ok=True)
    return global_path


def get_global_client() -> Optional['Client']:
    """Get or create a global ChromaDB client (for shared/cross-instance queries)"""
    if not CHROMADB_AVAILABLE:
        logger.warning("⚠️ ChromaDB not installed, memory features disabled")
        return None
    
    if "_global" not in _instance_clients:
        global_path = get_global_chroma_path()
        _instance_clients["_global"] = Client(Settings(
            persist_directory=global_path,
            anonymized_telemetry=False
        ))
        logger.info(f"✅ Global ChromaDB client initialized at {global_path}")
    
    return _instance_clients["_global"]


def get_embedding_function():
    """Get or create the global embedding function"""
    global _embedder
    if not CHROMADB_AVAILABLE:
        return None
    
    if _embedder is None:
        _embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL
        )
        logger.info(f"✅ Embedding function initialized with model: {EMBEDDING_MODEL}")
    return _embedder


def get_or_create_collection(name: str, construct_id: Optional[str] = None, 
                              shard: str = "shard_0000", metadata: dict = None):
    """
    Get or create a ChromaDB collection.
    
    Args:
        name: Collection name
        construct_id: If provided, uses instance-specific storage; otherwise global
        shard: Shard directory for the instance
        metadata: Optional metadata for the collection
        
    Returns:
        ChromaDB collection with embedding function configured
    """
    if not CHROMADB_AVAILABLE:
        logger.warning("⚠️ ChromaDB not available")
        return None
    
    if construct_id:
        client = get_instance_client(construct_id, shard)
    else:
        client = get_global_client()
    
    if client is None:
        return None
    
    embedder = get_embedding_function()
    
    existing_collections = [c.name for c in client.list_collections()]
    
    if name not in existing_collections:
        collection = client.create_collection(
            name=name,
            embedding_function=embedder,
            metadata=metadata or {}
        )
        logger.info(f"✅ Created collection '{name}'" + (f" for {construct_id}" if construct_id else " (global)"))
    else:
        collection = client.get_collection(name=name)
        logger.debug(f"✅ Retrieved existing collection '{name}'")
    
    return collection


def get_long_term_collection(construct_id: Optional[str] = None, 
                              collection_name: Optional[str] = None,
                              shard: str = "shard_0000"):
    """Get the long-term memory collection for an instance or global"""
    name = collection_name or "long_term_memory"
    return get_or_create_collection(name, construct_id, shard, {
        "module": "Memory",
        "authority": "High",
        "type": "long_term"
    })


def get_short_term_collection(construct_id: Optional[str] = None,
                               collection_name: Optional[str] = None,
                               shard: str = "shard_0000"):
    """Get the short-term memory collection for an instance or global"""
    name = collection_name or "short_term_memory"
    return get_or_create_collection(name, construct_id, shard, {
        "module": "Memory",
        "authority": "High",
        "type": "short_term"
    })


def list_instance_collections(construct_id: str, shard: str = "shard_0000"):
    """List all ChromaDB collections for a specific instance"""
    client = get_instance_client(construct_id, shard)
    if client:
        return [c.name for c in client.list_collections()]
    return []


def get_instance_memory_summary(construct_id: str, shard: str = "shard_0000") -> dict:
    """Get memory summary for a specific instance"""
    client = get_instance_client(construct_id, shard)
    if not client:
        return {"error": "ChromaDB not available"}
    
    try:
        collections = list_instance_collections(construct_id, shard)
        summary = {
            "construct_id": construct_id,
            "shard": shard,
            "chroma_path": get_instance_chroma_path(construct_id, shard),
            "collections": {}
        }
        
        for coll_name in collections:
            coll = client.get_collection(coll_name)
            summary["collections"][coll_name] = coll.count()
        
        return summary
    except Exception as e:
        logger.error(f"❌ Error getting memory summary: {e}")
        return {"error": str(e)}


def health_check(construct_id: Optional[str] = None, shard: str = "shard_0000"):
    """Perform health check on ChromaDB for an instance or global"""
    if not CHROMADB_AVAILABLE:
        return {"status": "unavailable", "reason": "ChromaDB not installed"}
    
    try:
        if construct_id:
            client = get_instance_client(construct_id, shard)
            path = get_instance_chroma_path(construct_id, shard)
        else:
            client = get_global_client()
            path = get_global_chroma_path()
        
        if not client:
            return {"status": "error", "reason": "Could not create client"}
        
        collections = [c.name for c in client.list_collections()]
        
        return {
            "status": "healthy",
            "construct_id": construct_id or "global",
            "path": path,
            "collections": collections,
            "collection_count": len(collections)
        }
    except Exception as e:
        logger.error(f"❌ ChromaDB health check failed: {e}")
        return {"status": "unhealthy", "error": str(e)}
