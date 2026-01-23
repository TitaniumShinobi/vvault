"""
Memory Router - STM/LTM Routing with Integration Hooks
Routes memory entries between short-term and long-term memory.
Provides hooks for memup (frame™) and chatgpt-retrieval-plugin integration.
"""

import logging
from typing import Optional, Dict, Any, List, Callable
from pathlib import Path
from datetime import datetime
from collections import deque

logger = logging.getLogger(__name__)


class MemoryRouter:
    """
    Routes memory between STM (Short-Term Memory) and LTM (Long-Term Memory).
    
    STM: Recent conversation window (in-memory, fast)
    LTM: Persistent memory store (capsules, semantic search)
    
    Integration hooks:
    - memup (frame™): bank.py, context.py, mem_check.py, stm.py, ltm.py
    - chatgpt-retrieval-plugin: Semantic search for LTM queries
    """
    
    def __init__(
        self, 
        construct_id: str, 
        vvault_root: Optional[str] = None,
        stm_max_size: int = 30,
        ltm_threshold: int = 20
    ):
        self.construct_id = construct_id
        self.vvault_root = Path(vvault_root) if vvault_root else Path(__file__).parent.parent.parent
        
        self.stm_max_size = stm_max_size
        self.ltm_threshold = ltm_threshold
        
        self._stm: deque = deque(maxlen=stm_max_size)
        self._ltm_buffer: List[Dict] = []
        
        self._memup_hook: Optional[Callable] = None
        self._semantic_search_hook: Optional[Callable] = None
        
        self._stats = {
            "stm_adds": 0,
            "ltm_promotions": 0,
            "ltm_queries": 0
        }
    
    def register_memup_hook(self, hook: Callable[[Dict], None]):
        """
        Register hook for memup integration (frame™).
        Called when entries are promoted to LTM.
        
        Hook signature: def hook(entry: Dict) -> None
        """
        self._memup_hook = hook
        logger.info(f"[{self.construct_id}] memup hook registered")
    
    def register_semantic_search_hook(self, hook: Callable[[str, int], List[Dict]]):
        """
        Register hook for semantic search (chatgpt-retrieval-plugin).
        Called when querying LTM for relevant context.
        
        Hook signature: def hook(query: str, top_k: int) -> List[Dict]
        """
        self._semantic_search_hook = hook
        logger.info(f"[{self.construct_id}] Semantic search hook registered")
    
    def add_to_stm(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Add an entry to short-term memory."""
        entry["_added_at"] = datetime.now().isoformat()
        entry["_construct_id"] = self.construct_id
        
        self._stm.append(entry)
        self._stats["stm_adds"] += 1
        
        if len(self._stm) >= self.ltm_threshold:
            self._check_ltm_promotion()
        
        return {"added": True, "stm_size": len(self._stm)}
    
    def get_stm_window(self) -> List[Dict[str, Any]]:
        """Get the current STM window."""
        return list(self._stm)
    
    def get_relevant_ltm(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Query LTM for entries relevant to the query.
        Uses semantic search if available, otherwise falls back to keyword matching.
        """
        self._stats["ltm_queries"] += 1
        
        if self._semantic_search_hook:
            try:
                results = self._semantic_search_hook(query, top_k)
                return results
            except Exception as e:
                logger.error(f"Semantic search failed: {e}")
        
        return self._keyword_ltm_search(query, top_k)
    
    def route_new_entries(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Route new entries from transcript ingestion.
        Adds to STM and checks for LTM promotion.
        """
        result = {
            "stm_count": 0,
            "ltm_count": 0,
            "should_update_capsule": False
        }
        
        for entry in entries:
            self.add_to_stm(entry)
            result["stm_count"] += 1
        
        if len(entries) > 10:
            result["should_update_capsule"] = True
        
        promoted = self._check_ltm_promotion()
        result["ltm_count"] = promoted
        
        return result
    
    def _check_ltm_promotion(self) -> int:
        """Check if old STM entries should be promoted to LTM."""
        promoted = 0
        
        while len(self._stm) > self.stm_max_size - 5:
            if len(self._stm) <= self.ltm_threshold // 2:
                break
                
            old_entry = self._stm.popleft()
            self._promote_to_ltm(old_entry)
            promoted += 1
        
        return promoted
    
    def _promote_to_ltm(self, entry: Dict[str, Any]):
        """Promote an entry from STM to LTM."""
        entry["_promoted_at"] = datetime.now().isoformat()
        
        self._ltm_buffer.append(entry)
        self._stats["ltm_promotions"] += 1
        
        if self._memup_hook:
            try:
                self._memup_hook(entry)
            except Exception as e:
                logger.error(f"memup hook failed: {e}")
        
        if len(self._ltm_buffer) > 100:
            self._ltm_buffer = self._ltm_buffer[-100:]
    
    def _keyword_ltm_search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        """Fallback keyword-based LTM search."""
        query_words = set(query.lower().split())
        
        scored = []
        for entry in self._ltm_buffer:
            content = entry.get("content", "").lower()
            content_words = set(content.split())
            
            overlap = len(query_words & content_words)
            if overlap > 0:
                scored.append((overlap, entry))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """Get memory routing statistics."""
        return {
            "construct_id": self.construct_id,
            "stm_size": len(self._stm),
            "stm_max": self.stm_max_size,
            "ltm_buffer_size": len(self._ltm_buffer),
            "stats": self._stats,
            "hooks": {
                "memup": self._memup_hook is not None,
                "semantic_search": self._semantic_search_hook is not None
            }
        }
    
    def clear_stm(self):
        """Clear short-term memory (e.g., for new session)."""
        self._stm.clear()
        logger.info(f"[{self.construct_id}] STM cleared")
    
    def export_for_frame(self) -> Dict[str, Any]:
        """
        Export memory state in format compatible with frame™/memup.
        
        Returns data structured for:
        - bank.py: Memory bank operations
        - context.py: Context building
        - stm.py / ltm.py: Memory tier management
        """
        return {
            "construct_id": self.construct_id,
            "timestamp": datetime.now().isoformat(),
            "stm": {
                "entries": list(self._stm),
                "size": len(self._stm),
                "max_size": self.stm_max_size
            },
            "ltm": {
                "buffer": self._ltm_buffer,
                "size": len(self._ltm_buffer)
            },
            "context": {
                "recent_speakers": self._get_recent_speakers(),
                "topic_summary": self._get_topic_summary()
            }
        }
    
    def _get_recent_speakers(self) -> List[str]:
        """Get list of recent speakers from STM."""
        speakers = []
        for entry in self._stm:
            speaker = entry.get("speaker")
            if speaker and speaker not in speakers:
                speakers.append(speaker)
        return speakers
    
    def _get_topic_summary(self) -> str:
        """Generate brief topic summary from STM."""
        if not self._stm:
            return "No recent conversation"
        
        messages = [e.get("content", "")[:100] for e in list(self._stm)[-5:]]
        return " | ".join(messages)
