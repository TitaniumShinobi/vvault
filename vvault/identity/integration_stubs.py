"""
Integration Stubs - Hooks for External Systems
Provides stub implementations for chatgpt-retrieval-plugin and memup (frame™).
Replace with actual integrations when available.
"""

import logging
from typing import Dict, Any, List, Callable, Optional

logger = logging.getLogger(__name__)


class SemanticSearchStub:
    """
    Stub for chatgpt-retrieval-plugin integration.
    Replace with actual plugin when available.
    
    Expected interface from chatgpt-retrieval-plugin:
    - query(text: str, top_k: int) -> List[Dict]
    - upsert(documents: List[Dict]) -> bool
    - delete(ids: List[str]) -> bool
    """
    
    def __init__(self, construct_id: str):
        self.construct_id = construct_id
        self._documents: List[Dict] = []
        self._connected = False
    
    def connect(self, endpoint: Optional[str] = None, api_key: Optional[str] = None) -> bool:
        """
        Connect to chatgpt-retrieval-plugin.
        Stub: Always returns False until real plugin is configured.
        """
        logger.info(f"[{self.construct_id}] SemanticSearch stub - no real connection")
        return False
    
    def query(self, text: str, top_k: int = 5) -> List[Dict]:
        """
        Query for semantically similar documents.
        Stub: Returns empty list.
        """
        if not self._connected:
            return []
        return []
    
    def upsert(self, documents: List[Dict]) -> bool:
        """
        Upsert documents to the index.
        Stub: Stores locally for now.
        """
        self._documents.extend(documents)
        self._documents = self._documents[-1000:]
        return True
    
    def delete(self, ids: List[str]) -> bool:
        """Delete documents by ID."""
        self._documents = [d for d in self._documents if d.get("id") not in ids]
        return True
    
    def get_hook(self) -> Callable[[str, int], List[Dict]]:
        """Get the query function as a hook for MemoryRouter."""
        return self.query


class MemupStub:
    """
    Stub for memup (frame™) integration.
    Replace with actual frame™ connection when available.
    
    Expected modules from frame™:
    - bank.py: Memory bank operations
    - context.py: Context building
    - mem_check.py: Memory verification
    - stm.py: Short-term memory
    - ltm.py: Long-term memory
    """
    
    def __init__(self, construct_id: str):
        self.construct_id = construct_id
        self._bank: List[Dict] = []
        self._connected = False
    
    def connect(self, frame_endpoint: Optional[str] = None) -> bool:
        """
        Connect to frame™ memup system.
        Stub: Always returns False until frame™ is configured.
        """
        logger.info(f"[{self.construct_id}] Memup stub - no real connection to frame™")
        return False
    
    def bank_deposit(self, entry: Dict) -> bool:
        """
        Deposit an entry into the memory bank.
        Maps to bank.py operations.
        """
        entry["_deposited_at"] = __import__("datetime").datetime.now().isoformat()
        self._bank.append(entry)
        self._bank = self._bank[-500:]
        return True
    
    def bank_withdraw(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Withdraw entries from the memory bank.
        Maps to bank.py operations.
        """
        return self._bank[-limit:]
    
    def context_build(self, thread_id: str) -> Dict[str, Any]:
        """
        Build context for a thread.
        Maps to context.py operations.
        """
        return {
            "thread_id": thread_id,
            "construct_id": self.construct_id,
            "entries": self._bank[-20:],
            "built_at": __import__("datetime").datetime.now().isoformat()
        }
    
    def mem_check(self, entry_id: str) -> Dict[str, Any]:
        """
        Check if a memory entry exists and is valid.
        Maps to mem_check.py operations.
        """
        for entry in self._bank:
            if entry.get("id") == entry_id:
                return {"exists": True, "valid": True, "entry": entry}
        return {"exists": False, "valid": False}
    
    def stm_add(self, entry: Dict) -> bool:
        """
        Add to short-term memory.
        Maps to stm.py operations.
        """
        return self.bank_deposit(entry)
    
    def ltm_promote(self, entry: Dict) -> bool:
        """
        Promote entry to long-term memory.
        Maps to ltm.py operations.
        """
        entry["_promoted_to_ltm"] = True
        return self.bank_deposit(entry)
    
    def get_hook(self) -> Callable[[Dict], None]:
        """Get the deposit function as a hook for MemoryRouter."""
        def hook(entry: Dict):
            self.ltm_promote(entry)
        return hook


def create_semantic_search(construct_id: str) -> SemanticSearchStub:
    """Factory for semantic search integration."""
    return SemanticSearchStub(construct_id)


def create_memup(construct_id: str) -> MemupStub:
    """Factory for memup integration."""
    return MemupStub(construct_id)


def register_integrations(memory_router, construct_id: str) -> Dict[str, bool]:
    """
    Register available integrations with a MemoryRouter.
    Returns status of each integration.
    """
    status = {
        "semantic_search": False,
        "memup": False
    }
    
    semantic = create_semantic_search(construct_id)
    if semantic.connect():
        memory_router.register_semantic_search_hook(semantic.get_hook())
        status["semantic_search"] = True
    
    memup = create_memup(construct_id)
    if memup.connect():
        memory_router.register_memup_hook(memup.get_hook())
        status["memup"] = True
    
    return status
