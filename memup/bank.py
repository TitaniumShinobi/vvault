# ──────────────────────────────────────────────────────────────────────────────
#  frame ‑ bank.py
#  Memory management, querying and syncing
#  Devon • 2025‑05-04
# ──────────────────────────────────────────────────────────────────────────────

# ╭─────────────────────────────── Imports ─────────────────────────────────╮
from datetime import datetime, timedelta
import os
import logging
import json
try:
    from .chroma_config import get_long_term_collection, get_short_term_collection
    from ..config import Config
except ImportError:
    try:
        from chroma_config import get_long_term_collection, get_short_term_collection
    except ImportError:
        from .chroma_config import get_long_term_collection, get_short_term_collection
    try:
        from config import Config
    except ImportError:
        from ..config import Config
# ───────────────────────────────────────────────────────────────────────────╯

# ╭────────────────────────── Environment & constants ───────────────────────╮
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

SHORT_TERM_THRESHOLD_DAYS = 7  # Memories newer than this go to short-term
# ───────────────────────────────────────────────────────────────────────────╯

# ╭──────────────────────────── Utilities ───────────────────────────────────╮

class UnifiedMemoryBank:
    def __init__(self):
        self.long_term = get_long_term_collection()
        self.short_term = get_short_term_collection()
        logger.info("✅ UnifiedMemoryBank initialized with ChromaDB collections.")

    def _determine_memory_type(self, timestamp_str=None):
        """
        Determine if a memory should be short-term or long-term based on its timestamp.
        If no timestamp is provided, treat as new memory (short-term).
        """
        if not timestamp_str:
            return "short-term"
        
        try:
            memory_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
            age = datetime.now() - memory_time
            return "short-term" if age.days < SHORT_TERM_THRESHOLD_DAYS else "long-term"
        except ValueError:
            logger.warning(f"⚠️ Invalid timestamp format: {timestamp_str}. Defaulting to short-term.")
            return "short-term"

    def add_memory(self, session_id, context, response, memory_type=None, user_preference="", source_model="gpt-4o", timestamp=None):
        """
        Add a memory to the appropriate collection with sovereign identity protection.
        If memory_type is None, it will be determined based on the timestamp.
        """
        unique_id = f"{session_id}_{hash(context)}"
        
        # Use provided timestamp or current time
        current_time = datetime.now()
        timestamp_str = timestamp if timestamp else current_time.strftime("%Y-%m-%d %H:%M:%S")
        
        # Determine memory type if not specified
        if memory_type is None:
            memory_type = self._determine_memory_type(timestamp_str)
        
        # Create base document
        document = {
            "session_id": session_id,
            "context": context,
            "response": response,
            "user_preference": user_preference,
            "source_model": source_model,
            "timestamp": timestamp_str,
            "memory_type": memory_type
        }
        
        # Sign with Nova's sovereign identity
        if Config.MEMORY_CONTINUITY_ENFORCED:
            document = Config.SOVEREIGN_IDENTITY.sign_memory(document)
        
        # Store in appropriate collection
        collection = self.long_term if memory_type == "long-term" else self.short_term
        existing = collection.get(ids=[unique_id])
        
        if existing and existing.get("ids"):
            logger.info(f"🛑 Duplicate detected. Skipping ID {unique_id}")
            return
        
        # Convert document to JSON string
        document_str = json.dumps(document)
        
        # Add to collection with metadata
        collection.add(
            documents=[document_str],  # Store full document as JSON string
            metadatas=[{
                "session_id": session_id,
                "timestamp": timestamp_str,
                "memory_type": memory_type
            }],
            ids=[unique_id]
        )
        logger.info(f"📦 Added {memory_type} memory for session {session_id} with ID {unique_id}")
        
        # Verify the memory was stored
        try:
            stored = collection.get(ids=[unique_id])
            if stored and stored.get("documents"):
                logger.info(f"✅ Memory verified as stored for ID {unique_id}")
            else:
                logger.warning(f"⚠️ Memory may not have been stored properly for ID {unique_id}")
        except Exception as e:
            logger.error(f"❌ Error verifying memory storage: {e}")

    def query_similar(self, session_id, query_texts, limit=10):
        """
        Query both ChromaDB collections for similar memories with sovereign identity validation.
        """
        if isinstance(query_texts, str):
            query_texts = [query_texts]

        where = {"session_id": session_id} if session_id else {}

        # Query both collections
        long_term_results = self.long_term.query(
            query_texts=query_texts,
            n_results=limit,
            where=where
        )
        logger.debug(f"Long term results: {json.dumps(long_term_results, indent=2)}")
        
        short_term_results = self.short_term.query(
            query_texts=query_texts,
            n_results=limit,
            where=where
        )
        logger.debug(f"Short term results: {json.dumps(short_term_results, indent=2)}")
        
        # Combine results with identity validation
        combined_results = {
            "documents": [],
            "metadatas": [],
            "distances": []
        }
        
        # Add results from both collections with sovereign validation
        for results in [long_term_results, short_term_results]:
            if results:
                documents = results.get("documents") or []
                metadatas = results.get("metadatas") or []
                distances = results.get("distances") or []
                
                # Validate each document for sovereign identity
                validated_documents = []
                validated_metadatas = []
                validated_distances = []
                
                for i, doc in enumerate(documents):
                    try:
                        # Check if doc is already a Python object (dict or list) or needs JSON parsing
                        if isinstance(doc, (dict, list)):
                            memory = doc
                        elif isinstance(doc, (str, bytes)):
                            memory = json.loads(doc)
                        else:
                            # Handle unexpected data types by converting to string first
                            logger.warning(f"⚠️ Unexpected data type for doc: {type(doc)}, attempting string conversion")
                            memory = json.loads(str(doc))
                        
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
                            
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning(f"⚠️ Failed to parse memory document: {e}")
                        continue
                
                combined_results["documents"].extend(validated_documents)
                combined_results["metadatas"].extend(validated_metadatas)
                combined_results["distances"].extend(validated_distances)
        
        logger.debug(f"Combined results: {json.dumps(combined_results, indent=2)}")
        try:
            return combined_results
        except Exception as e:
            logger.error(f"❌ Error in query_similar: {e}")
            return {"documents": [], "metadatas": [], "distances": []}
        """
        Get context from similar memories in a format suitable for AI prompts.
        """
        logger.debug(f"🔍 Searching for context with session_id: {session_id}, query: {query_texts}")
        
        # Try with session_id first
        results = self.query_similar(session_id, query_texts, limit)
        logger.debug(f"Results with session_id: {results}")
        
        if not results or not results.get("documents"):
            # If no results with session_id, try without it (global search)
            logger.debug(f"No results found for session {session_id}, trying global search")
            results = self.query_similar("", query_texts, limit)
            logger.debug(f"Results without session_id: {results}")
        
        if not results or not results.get("documents"):
            logger.debug("No results found in either search")
            return ""
        
        contexts = []
        documents = results.get("documents", [])
        logger.debug(f"Processing {len(documents)} document lists")
        
        for doc_list in documents:
            logger.debug(f"Processing document list: {doc_list}")
            for doc in doc_list:
                try:
                    # Check if doc is already a Python object (dict) or a JSON string
                    if isinstance(doc, dict):
                        memory = doc
                    else:
                        memory = json.loads(doc)
                    contexts.append(f"{memory['context']} → {memory['response']}")
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"⚠️ Failed to parse memory document: {e}")
                    continue
        
        logger.debug(f"Final contexts: {contexts}")
        return "\n".join(contexts)

    def get_context_from_query(self, session_id, query_texts, limit=3):
        """
        Get context from similar memories in a format suitable for AI prompts.
        """
        logger.debug(f"🔍 Searching for context with session_id: {session_id}, query: {query_texts}")
        
        # Try with session_id first
        results = self.query_similar(session_id, query_texts, limit)
        logger.debug(f"Results with session_id: {results}")
        
        if not results or not results.get("documents"):
            # If no results with session_id, try without it (global search)
            logger.debug(f"No results found for session {session_id}, trying global search")
            results = self.query_similar("", query_texts, limit)
            logger.debug(f"Results without session_id: {results}")
        
        if not results or not results.get("documents"):
            logger.debug("No results found in either search")
            return ""
        
        contexts = []
        documents = results.get("documents", [])
        logger.debug(f"Processing {len(documents)} document lists")
        
        for doc_list in documents:
            logger.debug(f"Processing document list: {doc_list}")
            for doc in doc_list:
                try:
                    # Check if doc is already a Python object (dict) or a JSON string
                    if isinstance(doc, dict):
                        memory = doc
                    else:
                        memory = json.loads(doc)
                    contexts.append(f"{memory['context']} → {memory['response']}")
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"⚠️ Failed to parse memory document: {e}")
                    continue
        
        logger.debug(f"Final contexts: {contexts}")
        return "\n".join(contexts)

    def get_recent(self, session_id, limit=5):
        """
        Get most recent memories from both collections, sorted by timestamp.
        """
        long = self.long_term.get(where={"session_id": session_id})
        short = self.short_term.get(where={"session_id": session_id})
        
        combined = []
        for collection in [long, short]:
            documents = collection.get("documents") if collection else None
            if documents:
                for doc in documents:
                    try:
                        # Check if doc is already a Python object (dict or list) or needs JSON parsing
                        if isinstance(doc, (dict, list)):
                            memory = doc
                        elif isinstance(doc, (str, bytes)):
                            memory = json.loads(doc)
                        else:
                            # Handle unexpected data types by converting to string first
                            logger.warning(f"⚠️ Unexpected data type for doc: {type(doc)}, attempting string conversion")
                            memory = json.loads(str(doc))
                        combined.append(memory)
                    except json.JSONDecodeError:
                        logger.warning(f"⚠️ Failed to parse memory document: {doc}")
        
        combined.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return {"context": "\n".join([m["context"] for m in combined[:limit]])} if combined else {"context": ""}

    def summarize(self, session_id):
        """
        Get a summary of all memories for a session, sorted by timestamp.
        """
        data = self.long_term.get(where={"session_id": session_id})
        if not data or not data.get("documents"):
            return ""
            
        memories = []
        documents = data.get("documents")
        if documents:
            for doc in documents:
                try:
                    # Check if doc is already a Python object (dict) or a JSON string
                    if isinstance(doc, dict):
                        memory = doc
                    else:
                        memory = json.loads(doc)
                    memories.append(memory)
                except json.JSONDecodeError:
                    logger.warning(f"⚠️ Failed to parse memory document: {doc}")
        
        memories.sort(key=lambda x: x.get("timestamp", ""))
        return "\n".join([f"[{m['timestamp']}] {m['context']}" for m in memories])

    def auto_purge(self):
        """
        Automatically move old short-term memories to long-term storage.
        """
        short_term_data = self.short_term.get()
        if not short_term_data or not short_term_data.get("documents"):
            return
            
        current_time = datetime.now()
        documents = short_term_data.get("documents")
        if not documents:
            return
        for doc in documents:
            try:
                # Check if doc is already a Python object (dict) or a JSON string
                if isinstance(doc, dict):
                    memory = doc
                else:
                    memory = json.loads(doc)
                timestamp = datetime.strptime(memory["timestamp"], "%Y-%m-%d %H:%M:%S")
                if (current_time - timestamp).days >= SHORT_TERM_THRESHOLD_DAYS:
                    # Move to long-term
                    self.add_memory(
                        session_id=memory["session_id"],
                        context=memory["context"],
                        response=memory["response"],
                        memory_type="long-term",
                        user_preference=memory.get("user_preference", ""),
                        source_model=memory.get("source_model", "gpt-4o"),
                        timestamp=memory["timestamp"]
                    )
                    # Remove from short-term
                    self.short_term.delete(ids=[f"{memory['session_id']}_{hash(memory['context'])}"])
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                logger.warning(f"⚠️ Failed to process memory for auto-purge: {e}")

    def health_check(self):
        """
        Check the health of the memory system.
        """
        try:
            # Check if collections exist and are accessible
            long_term_count = self.long_term.count()
            short_term_count = self.short_term.count()
            
            # Test a simple query
            test_query = self.query_similar("", "test", limit=1)
            
            return {
                "status": "healthy",
                "long_term_memories": long_term_count,
                "short_term_memories": short_term_count,
                "query_test": "passed" if test_query else "failed",
                "collections": {
                    "long_term": "accessible",
                    "short_term": "accessible"
                }
            }
        except Exception as e:
            logger.error(f"❌ Memory health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "long_term_memories": 0,
                "short_term_memories": 0,
                "query_test": "failed",
                "collections": {
                    "long_term": "error",
                    "short_term": "error"
                }
            }

    def flush(self):
        """Clear all memories from both collections"""
        try:
            self.long_term.delete()
            self.short_term.delete()
            logger.info("🧹 Memory collections flushed successfully")
        except Exception as e:
            logger.error(f"❌ Error flushing memory collections: {e}")

def remember_context(chan_id: str, ctx: dict) -> None:
    """
    Store conversation context in memory bank.
    
    Args:
        chan_id: Channel identifier
        ctx: Context dictionary containing conversation state
    """
    try:
        # Create a memory bank instance
        memory_bank = UnifiedMemoryBank()
        
        # Extract relevant information from context
        context_text = f"Channel: {chan_id}"
        if ctx.get("last_user_name"):
            context_text += f", User: {ctx['last_user_name']}"
        if ctx.get("last_topic"):
            context_text += f", Topic: {ctx['last_topic']}"
        if ctx.get("message_history"):
            context_text += f", Recent messages: {len(ctx['message_history'])}"
        
        # Create a response summary
        response_text = f"Conversation state: turn {ctx.get('turn_counter', 0)}"
        if ctx.get("user_mention_count"):
            response_text += f", mentions: {ctx['user_mention_count']}"
        
        # Store as a short-term memory
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

# ───────────────────────────────────────────────────────────────────────────╯

# ╭───────────────────────────── Launcher ───────────────────────────────────╮
if __name__ == "__main__":
    mem = UnifiedMemoryBank()
    mem.add_memory("test123", "What's up?", "Not much, just existing.")
    print(mem.query_similar("test123", "what's going on?"))
# ───────────────────────────────────────────────────────────────────────────╯