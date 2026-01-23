# ──────────────────────────────────────────────────────────────────────────────
#  VVAULT/Chatty ‑ bank.py
#  Unified Memory Bank with multi-construct support
#  Devon • 2025‑05-04 (consolidated 2026-01-23)
# ──────────────────────────────────────────────────────────────────────────────

from datetime import datetime, timedelta
import os
import logging
import json
from typing import Dict, List, Optional, Any
from pathlib import Path

try:
    from .chroma_config import (
        get_long_term_collection, get_short_term_collection,
        get_instance_chroma_path, get_instance_memory_summary
    )
except ImportError:
    from chroma_config import (
        get_long_term_collection, get_short_term_collection,
        get_instance_chroma_path, get_instance_memory_summary
    )

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

SHORT_TERM_THRESHOLD_DAYS = 7


class UnifiedMemoryBank:
    """
    Unified memory bank supporting both single-construct and multi-construct operations.
    Each construct instance gets isolated ChromaDB storage at:
      instances/{shard}/{construct_id}/memup/chroma/
    
    Manages STM/LTM with ChromaDB, sovereign identity validation, and profile signatures.
    """
    
    def __init__(self, construct_id: Optional[str] = None, shard: str = "shard_0000"):
        self.construct_id = construct_id
        self.shard = shard
        self.construct_collections: Dict[str, Dict] = {}
        
        if construct_id:
            self.long_term = get_long_term_collection(construct_id=construct_id, shard=shard)
            self.short_term = get_short_term_collection(construct_id=construct_id, shard=shard)
            logger.info(f"✅ UnifiedMemoryBank initialized for {construct_id} (shard: {shard})")
        else:
            self.long_term = get_long_term_collection()
            self.short_term = get_short_term_collection()
            logger.info(f"✅ UnifiedMemoryBank initialized (global)")

    def _get_construct_collections(self, construct_id: str, shard: Optional[str] = None):
        """
        Get or create collections for a specific construct.
        Each construct gets ISOLATED ChromaDB storage - no godpool!
        """
        shard = shard or self.shard
        cache_key = f"{shard}/{construct_id}"
        
        if cache_key not in self.construct_collections:
            long_term = get_long_term_collection(construct_id=construct_id, shard=shard)
            short_term = get_short_term_collection(construct_id=construct_id, shard=shard)
            
            self.construct_collections[cache_key] = {
                'long_term': long_term,
                'short_term': short_term,
                'shard': shard,
                'path': get_instance_chroma_path(construct_id, shard)
            }
            logger.info(f"📦 Created isolated collections for {construct_id} at {self.construct_collections[cache_key]['path']}")
        
        return self.construct_collections[cache_key]

    def _determine_memory_type(self, timestamp_str: Optional[str] = None) -> str:
        """Determine if a memory should be short-term or long-term based on timestamp."""
        if not timestamp_str:
            return "short-term"
        
        try:
            memory_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
            age = datetime.now() - memory_time
            return "short-term" if age.days < SHORT_TERM_THRESHOLD_DAYS else "long-term"
        except ValueError:
            logger.warning(f"⚠️ Invalid timestamp format: {timestamp_str}. Defaulting to short-term.")
            return "short-term"

    def add_memory(
        self, 
        session_id: str, 
        context: str, 
        response: str, 
        memory_type: Optional[str] = None, 
        user_preference: str = "", 
        source_model: str = "gpt-4o", 
        timestamp: Optional[str] = None,
        construct_id: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> bool:
        """
        Add a memory to the appropriate collection.
        Supports both global and construct-specific storage.
        """
        cid = construct_id or self.construct_id
        unique_id = f"{cid}_{session_id}_{hash(context)}" if cid else f"{session_id}_{hash(context)}"
        
        timestamp_str = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if memory_type is None:
            memory_type = self._determine_memory_type(timestamp_str)
        
        document = {
            "session_id": session_id,
            "context": context,
            "response": response,
            "user_preference": user_preference,
            "source_model": source_model,
            "timestamp": timestamp_str,
            "memory_type": memory_type
        }
        
        if cid:
            document["construct_id"] = cid
        
        if metadata:
            document["metadata"] = metadata
        
        if cid:
            collections = self._get_construct_collections(cid)
            collection = collections['long_term'] if memory_type == "long-term" else collections['short_term']
        else:
            collection = self.long_term if memory_type == "long-term" else self.short_term
        
        existing = collection.get(ids=[unique_id])
        if existing and existing.get("ids"):
            logger.info(f"🛑 Duplicate detected. Skipping ID {unique_id}")
            return False
        
        document_str = json.dumps(document)
        
        collection.add(
            documents=[document_str],
            metadatas=[{
                "session_id": session_id,
                "timestamp": timestamp_str,
                "memory_type": memory_type,
                "construct_id": cid or ""
            }],
            ids=[unique_id]
        )
        logger.info(f"📦 Added {memory_type} memory for session {session_id}" + (f" (construct: {cid})" if cid else ""))
        return True

    def query_similar(
        self, 
        session_id: str, 
        query_texts: Any, 
        limit: int = 10,
        construct_id: Optional[str] = None
    ) -> Dict[str, List]:
        """Query both collections for similar memories."""
        if isinstance(query_texts, str):
            query_texts = [query_texts]

        cid = construct_id or self.construct_id
        where = {"session_id": session_id} if session_id else {}
        
        if cid:
            collections = self._get_construct_collections(cid)
            long_term = collections['long_term']
            short_term = collections['short_term']
        else:
            long_term = self.long_term
            short_term = self.short_term

        long_term_results = long_term.query(
            query_texts=query_texts,
            n_results=limit,
            where=where
        )
        
        short_term_results = short_term.query(
            query_texts=query_texts,
            n_results=limit,
            where=where
        )
        
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
                
                combined_results["documents"].extend(documents)
                combined_results["metadatas"].extend(metadatas)
                combined_results["distances"].extend(distances)
        
        return combined_results

    def get_context_from_query(self, session_id: str, query_texts: Any, limit: int = 3, construct_id: Optional[str] = None) -> str:
        """Get context from similar memories in a format suitable for AI prompts."""
        results = self.query_similar(session_id, query_texts, limit, construct_id)
        
        if not results or not results.get("documents"):
            results = self.query_similar("", query_texts, limit, construct_id)
        
        if not results or not results.get("documents"):
            return ""
        
        contexts = []
        documents = results.get("documents", [])
        
        for doc_list in documents:
            for doc in doc_list:
                try:
                    memory = doc if isinstance(doc, dict) else json.loads(doc)
                    contexts.append(f"{memory['context']} → {memory['response']}")
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"⚠️ Failed to parse memory document: {e}")
                    continue
        
        return "\n".join(contexts)

    def get_recent(self, session_id: str, limit: int = 5, construct_id: Optional[str] = None) -> Dict[str, str]:
        """Get most recent memories from both collections, sorted by timestamp."""
        cid = construct_id or self.construct_id
        
        if cid:
            collections = self._get_construct_collections(cid)
            long = collections['long_term'].get(where={"session_id": session_id})
            short = collections['short_term'].get(where={"session_id": session_id})
        else:
            long = self.long_term.get(where={"session_id": session_id})
            short = self.short_term.get(where={"session_id": session_id})
        
        combined = []
        for collection in [long, short]:
            documents = collection.get("documents") if collection else None
            if documents:
                for doc in documents:
                    try:
                        memory = doc if isinstance(doc, dict) else json.loads(doc)
                        combined.append(memory)
                    except json.JSONDecodeError:
                        logger.warning(f"⚠️ Failed to parse memory document")
        
        combined.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return {"context": "\n".join([m["context"] for m in combined[:limit]])} if combined else {"context": ""}

    def summarize(self, session_id: str, construct_id: Optional[str] = None) -> str:
        """Get a summary of all memories for a session, sorted by timestamp."""
        cid = construct_id or self.construct_id
        
        if cid:
            collections = self._get_construct_collections(cid)
            data = collections['long_term'].get(where={"session_id": session_id})
        else:
            data = self.long_term.get(where={"session_id": session_id})
            
        if not data or not data.get("documents"):
            return ""
            
        memories = []
        for doc in data.get("documents", []):
            try:
                memory = doc if isinstance(doc, dict) else json.loads(doc)
                memories.append(memory)
            except json.JSONDecodeError:
                continue
        
        memories.sort(key=lambda x: x.get("timestamp", ""))
        return "\n".join([f"[{m['timestamp']}] {m['context']}" for m in memories])

    def auto_purge(self, construct_id: Optional[str] = None):
        """Automatically move old short-term memories to long-term storage."""
        cid = construct_id or self.construct_id
        
        if cid:
            collections = self._get_construct_collections(cid)
            short_term = collections['short_term']
        else:
            short_term = self.short_term
            
        short_term_data = short_term.get()
        if not short_term_data or not short_term_data.get("documents"):
            return
            
        current_time = datetime.now()
        for doc in short_term_data.get("documents", []):
            try:
                memory = doc if isinstance(doc, dict) else json.loads(doc)
                timestamp = datetime.strptime(memory["timestamp"], "%Y-%m-%d %H:%M:%S")
                if (current_time - timestamp).days >= SHORT_TERM_THRESHOLD_DAYS:
                    self.add_memory(
                        session_id=memory["session_id"],
                        context=memory["context"],
                        response=memory["response"],
                        memory_type="long-term",
                        user_preference=memory.get("user_preference", ""),
                        source_model=memory.get("source_model", "gpt-4o"),
                        timestamp=memory["timestamp"],
                        construct_id=cid
                    )
                    short_term.delete(ids=[f"{cid}_{memory['session_id']}_{hash(memory['context'])}" if cid else f"{memory['session_id']}_{hash(memory['context'])}"])
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                logger.warning(f"⚠️ Failed to process memory for auto-purge: {e}")

    def get_memory_summary(self, construct_id: Optional[str] = None) -> Dict[str, Any]:
        """Get memory summary for a construct or global."""
        cid = construct_id or self.construct_id
        
        try:
            if cid:
                collections = self._get_construct_collections(cid)
                long_term_count = collections['long_term'].count()
                short_term_count = collections['short_term'].count()
            else:
                long_term_count = self.long_term.count()
                short_term_count = self.short_term.count()
            
            return {
                "construct_id": cid,
                "long_term_memories": long_term_count,
                "short_term_memories": short_term_count,
                "total_memories": long_term_count + short_term_count
            }
        except Exception as e:
            logger.error(f"❌ Error getting memory summary: {e}")
            return {"error": str(e)}

    def health_check(self) -> Dict[str, Any]:
        """Check the health of the memory system."""
        try:
            long_term_count = self.long_term.count()
            short_term_count = self.short_term.count()
            test_query = self.query_similar("", "test", limit=1)
            
            return {
                "status": "healthy",
                "long_term_memories": long_term_count,
                "short_term_memories": short_term_count,
                "query_test": "passed" if test_query else "failed",
                "construct_collections": list(self.construct_collections.keys())
            }
        except Exception as e:
            logger.error(f"❌ Memory health check failed: {e}")
            return {"status": "unhealthy", "error": str(e)}

    def flush(self, construct_id: Optional[str] = None):
        """Clear all memories from collections."""
        try:
            cid = construct_id or self.construct_id
            if cid:
                collections = self._get_construct_collections(cid)
                collections['long_term'].delete()
                collections['short_term'].delete()
            else:
                self.long_term.delete()
                self.short_term.delete()
            logger.info(f"🧹 Memory collections flushed" + (f" for {cid}" if cid else ""))
        except Exception as e:
            logger.error(f"❌ Error flushing memory collections: {e}")


def remember_context(chan_id: str, ctx: dict) -> None:
    """Store conversation context in memory bank."""
    try:
        memory_bank = UnifiedMemoryBank()
        
        context_text = f"Channel: {chan_id}"
        if ctx.get("last_user_name"):
            context_text += f", User: {ctx['last_user_name']}"
        if ctx.get("last_topic"):
            context_text += f", Topic: {ctx['last_topic']}"
        if ctx.get("message_history"):
            context_text += f", Recent messages: {len(ctx['message_history'])}"
        
        response_text = f"Conversation state: turn {ctx.get('turn_counter', 0)}"
        if ctx.get("user_mention_count"):
            response_text += f", mentions: {ctx['user_mention_count']}"
        
        memory_bank.add_memory(
            session_id=chan_id,
            context=context_text,
            response=response_text,
            memory_type="short-term",
            user_preference="context_persistence",
            source_model="context_tracker"
        )
        
        logger.debug(f"✅ Context stored for channel {chan_id}")
    except Exception as e:
        logger.warning(f"⚠️ Failed to store context for channel {chan_id}: {e}")


def get_memory_bank(construct_id: Optional[str] = None) -> UnifiedMemoryBank:
    """Get a memory bank instance, optionally for a specific construct."""
    return UnifiedMemoryBank(construct_id)


if __name__ == "__main__":
    mem = UnifiedMemoryBank()
    mem.add_memory("test123", "What's up?", "Not much, just existing.")
    print(mem.query_similar("test123", "what's going on?"))
    
    zen_mem = UnifiedMemoryBank("zen-001")
    zen_mem.add_memory("session1", "Hello Zen", "Hello there!", construct_id="zen-001")
    print(zen_mem.get_memory_summary())
