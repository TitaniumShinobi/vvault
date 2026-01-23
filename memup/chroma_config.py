#!/usr/bin/env python3
"""
ChromaDB Configuration Module
Unified configuration for ChromaDB collections with proper embedding function setup
"""

import os
import logging
from chromadb import Client, Settings
from chromadb.utils import embedding_functions

# Configure logging
logger = logging.getLogger(__name__)

# Centralized ChromaDB path - Updated to point to VVAULT
frame_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CHROMA_PATH = os.path.join(frame_ROOT, '..', 'VVAULT (macos)', 'nova-001', 'Memories', 'chroma_db')

# Ensure directory exists
os.makedirs(CHROMA_PATH, exist_ok=True)

# Embedding model configuration
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

# Global client instance
_client = None
_embedder = None

def get_chroma_client():
    """Get or create the global ChromaDB client"""
    global _client
    if _client is None:
        _client = Client(Settings(
            persist_directory=CHROMA_PATH,
            anonymized_telemetry=False
        ))
        logger.info(f"✅ ChromaDB client initialized at {CHROMA_PATH}")
    return _client

def get_embedding_function():
    """Get or create the global embedding function"""
    global _embedder
    if _embedder is None:
        _embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL
        )
        logger.info(f"✅ Embedding function initialized with model: {EMBEDDING_MODEL}")
    return _embedder

def get_or_create_collection(name: str, metadata: dict = None):
    """
    Get or create a ChromaDB collection with proper embedding function
    
    Args:
        name: Collection name
        metadata: Optional metadata for the collection
        
    Returns:
        ChromaDB collection with embedding function configured
    """
    client = get_chroma_client()
    embedder = get_embedding_function()
    
    # Check if collection exists
    existing_collections = [c.name for c in client.list_collections()]
    
    if name not in existing_collections:
        # Create new collection with embedding function
        collection = client.create_collection(
            name=name,
            embedding_function=embedder,
            metadata=metadata or {}
        )
        logger.info(f"✅ Created collection '{name}' with embedding function")
    else:
        # Get existing collection and ensure it has embedding function
        collection = client.get_collection(name=name)
        
        # Check if collection has embedding function
        try:
            # Try a simple query to test embedding function
            collection.query(query_texts=["test"], n_results=1)
            logger.debug(f"✅ Collection '{name}' has embedding function")
        except Exception as e:
            logger.warning(f"⚠️ Collection '{name}' missing embedding function, recreating...")
            # Delete and recreate collection with embedding function
            client.delete_collection(name=name)
            collection = client.create_collection(
                name=name,
                embedding_function=embedder,
                metadata=metadata or {}
            )
            logger.info(f"✅ Recreated collection '{name}' with embedding function")
    
    return collection

def get_collection(name: str):
    """
    Get an existing ChromaDB collection
    
    Args:
        name: Collection name
        
    Returns:
        ChromaDB collection
    """
    client = get_chroma_client()
    return client.get_collection(name=name)

def list_collections():
    """List all ChromaDB collections"""
    client = get_chroma_client()
    return [c.name for c in client.list_collections()]

def delete_collection(name: str):
    """Delete a ChromaDB collection"""
    client = get_chroma_client()
    client.delete_collection(name=name)
    logger.info(f"🗑️ Deleted collection '{name}'")

def health_check():
    """Perform health check on ChromaDB"""
    try:
        client = get_chroma_client()
        collections = list_collections()
        
        # Test each collection
        for collection_name in collections:
            collection = get_collection(collection_name)
            # Test query
            collection.query(query_texts=["health_check"], n_results=1)
        
        logger.info(f"✅ ChromaDB health check passed. Collections: {collections}")
        return True
    except Exception as e:
        logger.error(f"❌ ChromaDB health check failed: {e}")
        return False

# Get profile from brain.py startup
try:
    from ..vvault_profile import load_active_profile
    PROFILE = load_active_profile()
    LT = f"{PROFILE.chroma_prefix}long_term_memory"
    ST = f"{PROFILE.chroma_prefix}short_term_memory"
    logger.info(f"🧠 Chroma collections: {LT}, {ST}")
except Exception as e:
    logger.warning(f"⚠️ Could not load profile, using default collections: {e}")
    LT = "long_term_memory"
    ST = "short_term_memory"

# Convenience functions for common collections
def get_long_term_collection(collection_name: str = None):
    """Get the long-term memory collection"""
    if collection_name is None:
        collection_name = LT
    return get_or_create_collection(collection_name, {
        "module": "Memory",
        "authority": "High",
        "type": "long_term"
    })

def get_short_term_collection(collection_name: str = None):
    """Get the short-term memory collection"""
    if collection_name is None:
        collection_name = ST
    return get_or_create_collection(collection_name, {
        "module": "Memory",
        "authority": "High",
        "type": "short_term"
    })

def get_core_memory_collection():
    """Get the core memory collection"""
    return get_or_create_collection("core_memory", {
        "module": "Memory",
        "authority": "High"
    })

def get_terminal_context_collection():
    """Get the terminal context collection"""
    return get_or_create_collection("terminal_context", {
        "module": "Terminal",
        "authority": "Medium"
    })

def get_web_interactions_collection():
    """Get the web interactions collection"""
    return get_or_create_collection("web_interactions", {
        "module": "Web",
        "authority": "Medium"
    })

def get_persona_dialogue_collection():
    """Get the persona dialogue collection"""
    return get_or_create_collection("persona_dialogue", {
        "module": "Persona",
        "authority": "Medium"
    }) 