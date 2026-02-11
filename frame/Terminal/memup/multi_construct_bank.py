# ──────────────────────────────────────────────────────────────────────────────
#  Frame ‑ multi_construct_bank.py
#  Multi-construct memory management with VVAULT profile support
#  Devon • 2025‑08-08
# ──────────────────────────────────────────────────────────────────────────────

import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from pathlib import Path

try:
    from .chroma_config import get_long_term_collection, get_short_term_collection
    from ..config import Config
    from ..profile_manager import get_profile_manager, VVAULTProfile
except ImportError:
    try:
        from chroma_config import get_long_term_collection, get_short_term_collection
    except ImportError:
        from .chroma_config import get_long_term_collection, get_short_term_collection
    try:
        from config import Config
    except ImportError:
        from ..config import Config
    try:
        from profile_manager import get_profile_manager, VVAULTProfile
    except ImportError:
        from ..profile_manager import get_profile_manager, VVAULTProfile

logger = logging.getLogger(__name__)

class MultiConstructMemoryBank:
    """Memory bank that supports multiple VVAULT profiles with signature validation"""
    
    def __init__(self):
        self.profile_manager = get_profile_manager()
        self.active_profile_id = Config.ACTIVE_PROFILE_ID
        self.profile_collections = {}  # Cache for profile-specific collections
        logger.info("✅ MultiConstructMemoryBank initialized with profile support.")
    
    def _get_profile_collections(self, profile_id: str):
        """Get or create collections for a specific profile"""
        if profile_id not in self.profile_collections:
            # Create profile-specific collection names
            long_term_name = f"long_term_memory_{profile_id}"
            short_term_name = f"short_term_memory_{profile_id}"
            
            # Get collections (they will be created if they don't exist)
            long_term = get_long_term_collection(collection_name=long_term_name)
            short_term = get_short_term_collection(collection_name=short_term_name)
            
            self.profile_collections[profile_id] = {
                'long_term': long_term,
                'short_term': short_term
            }
        
        return self.profile_collections[profile_id]
    
    def add_memory_with_profile(self, profile_id: str, session_id: str, context: str, 
                               response: str, memory_type: str = None, 
                               user_preference: str = "", source_model: str = "gpt-4o", 
                               timestamp: str = None) -> bool:
        """Add memory with profile-specific signature validation"""
        try:
            # Get profile
            profile = self.profile_manager.profiles.get(profile_id)
            if not profile:
                logger.warning(f"⚠️ Profile {profile_id} not found")
                return False
            
            # Create signature data
            signature_data = self.profile_manager.create_profile_signature(profile_id, context)
            
            # Create memory document with profile signature
            memory_doc = {
                "session_id": session_id,
                "context": context,
                "response": response,
                "user_preference": user_preference,
                "source_model": source_model,
                "timestamp": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "memory_type": memory_type or "short-term",
                "profile_id": profile_id,
                "profile_signature": signature_data,
                "construct_name": profile.profile_data.get('construct_name', 'Unknown')
            }
            
            # Sign with Frame's sovereign identity
            if Config.MEMORY_CONTINUITY_ENFORCED:
                memory_doc = Config.SOVEREIGN_IDENTITY.sign_memory(memory_doc)
            
            # Get profile-specific collections
            collections = self._get_profile_collections(profile_id)
            
            # Determine which collection to use
            collection = collections['long_term'] if memory_type == "long-term" else collections['short_term']
            
            # Create unique ID
            unique_id = f"{profile_id}_{session_id}_{hash(context)}"
            
            # Convert to JSON string
            document_str = json.dumps(memory_doc)
            
            # Add to collection
            collection.add(
                documents=[document_str],
                ids=[unique_id],
                metadatas=[{
                    "profile_id": profile_id,
                    "session_id": session_id,
                    "memory_type": memory_doc["memory_type"],
                    "timestamp": memory_doc["timestamp"]
                }]
            )
            
            logger.info(f"✅ Memory added for profile {profile_id} with signature validation")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error adding memory for profile {profile_id}: {e}")
            return False
    
    def query_similar_with_profile(self, profile_id: str, session_id: str, 
                                  query_texts: List[str], limit: int = 10) -> Dict[str, Any]:
        """Query memories for a specific profile with signature validation"""
        try:
            # Get profile
            profile = self.profile_manager.profiles.get(profile_id)
            if not profile:
                logger.warning(f"⚠️ Profile {profile_id} not found")
                return {"documents": [], "metadatas": [], "distances": []}
            
            # Get profile-specific collections
            collections = self._get_profile_collections(profile_id)
            
            # Query both collections
            long_term_results = collections['long_term'].query(
                query_texts=query_texts,
                n_results=limit,
                where={"session_id": session_id} if session_id else {}
            )
            
            short_term_results = collections['short_term'].query(
                query_texts=query_texts,
                n_results=limit,
                where={"session_id": session_id} if session_id else {}
            )
            
            # Combine and validate results
            combined_results = {
                "documents": [],
                "metadatas": [],
                "distances": []
            }
            
            for results in [long_term_results, short_term_results]:
                if results:
                    documents = results.get("documents") or []
                    metadatas = results.get("metadatas") or []
                    distances = results.get("distances") or []
                    
                    # Validate each document for profile signature
                    validated_documents = []
                    validated_metadatas = []
                    validated_distances = []
                    
                    for i, doc in enumerate(documents):
                        try:
                            # Check if doc is already a Python object (dict) or a JSON string
                            if isinstance(doc, dict):
                                memory = doc
                            else:
                                memory = json.loads(doc)
                            
                            # Validate profile signature
                            profile_signature = memory.get('profile_signature', {})
                            if not profile.validate_signature(profile_signature):
                                logger.warning(f"⚠️ Memory failed profile signature validation: {memory.get('session_id', 'unknown')}")
                                continue
                            
                            # Validate sovereign identity if protection is enabled
                            if Config.MEMORY_CONTINUITY_ENFORCED:
                                if not Config.SOVEREIGN_IDENTITY.validate_identity(memory):
                                    logger.warning(f"⚠️ Memory failed identity validation: {memory.get('session_id', 'unknown')}")
                                    continue
                            
                            validated_documents.append(doc)
                            if i < len(metadatas):
                                validated_metadatas.append(metadatas[i])
                            if i < len(distances):
                                validated_distances.append(distances[i])
                                
                        except json.JSONDecodeError as e:
                            logger.warning(f"⚠️ Failed to parse memory document: {e}")
                            continue
                    
                    combined_results["documents"].extend(validated_documents)
                    combined_results["metadatas"].extend(validated_metadatas)
                    combined_results["distances"].extend(validated_distances)
            
            return combined_results
            
        except Exception as e:
            logger.error(f"❌ Error querying memories for profile {profile_id}: {e}")
            return {"documents": [], "metadatas": [], "distances": []}
    
    def get_profile_memory_summary(self, profile_id: str) -> Dict[str, Any]:
        """Get memory summary for a specific profile"""
        try:
            profile = self.profile_manager.profiles.get(profile_id)
            if not profile:
                return {"error": f"Profile {profile_id} not found"}
            
            collections = self._get_profile_collections(profile_id)
            
            # Get counts from both collections
            long_term_count = collections['long_term'].count()
            short_term_count = collections['short_term'].count()
            
            return {
                "profile_id": profile_id,
                "construct_name": profile.profile_data.get('construct_name', 'Unknown'),
                "long_term_memories": long_term_count,
                "short_term_memories": short_term_count,
                "total_memories": long_term_count + short_term_count,
                "last_accessed": profile.last_accessed.isoformat(),
                "signature_hash": profile.signature_hash
            }
            
        except Exception as e:
            logger.error(f"❌ Error getting memory summary for profile {profile_id}: {e}")
            return {"error": str(e)}
    
    def switch_active_profile(self, profile_id: str) -> bool:
        """Switch to a different active profile"""
        if profile_id in self.profile_manager.profiles:
            self.active_profile_id = profile_id
            self.profile_manager.set_active_profile(profile_id)
            logger.info(f"✅ Switched to profile: {profile_id}")
            return True
        else:
            logger.warning(f"⚠️ Profile {profile_id} not found")
            return False
    
    def list_all_profiles_with_memory_counts(self) -> List[Dict[str, Any]]:
        """List all profiles with their memory counts"""
        profiles = []
        
        for profile_id in self.profile_manager.profiles:
            summary = self.get_profile_memory_summary(profile_id)
            profiles.append(summary)
        
        return profiles

# Global multi-construct memory bank instance
multi_construct_bank = MultiConstructMemoryBank()

def get_multi_construct_bank() -> MultiConstructMemoryBank:
    """Get the global multi-construct memory bank instance"""
    return multi_construct_bank
