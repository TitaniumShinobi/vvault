"""
Script Runner - Central Controller for Identity Scripts
Orchestrates all identity module operations for a construct.
"""

import logging
import asyncio
from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class ScriptRunner:
    """
    Central controller for all identity scripts.
    Manages lifecycle of identity operations for a construct.
    """
    
    def __init__(self, construct_id: str, vvault_root: Optional[str] = None):
        self.construct_id = construct_id
        self.vvault_root = Path(vvault_root) if vvault_root else Path(__file__).parent.parent.parent
        
        self._transcript_ingester = None
        self._memory_router = None
        self._state_manager = None
        self._identity_guard = None
        
        self._running = False
        self._last_run = None
        
    @property
    def transcript_ingester(self):
        if self._transcript_ingester is None:
            from .transcript_ingester import TranscriptIngester
            self._transcript_ingester = TranscriptIngester(self.construct_id, str(self.vvault_root))
        return self._transcript_ingester
    
    @property
    def memory_router(self):
        if self._memory_router is None:
            from .memory_router import MemoryRouter
            self._memory_router = MemoryRouter(self.construct_id, str(self.vvault_root))
        return self._memory_router
    
    @property
    def state_manager(self):
        if self._state_manager is None:
            from .state_manager import StateManager
            self._state_manager = StateManager(self.construct_id, str(self.vvault_root))
        return self._state_manager
    
    @property
    def identity_guard(self):
        if self._identity_guard is None:
            from .identity_guard import IdentityGuard
            self._identity_guard = IdentityGuard(self.construct_id, str(self.vvault_root))
        return self._identity_guard
    
    def run_ingestion_cycle(self) -> Dict[str, Any]:
        """
        Run a full transcript ingestion cycle.
        1. Detect new transcript content
        2. Parse into memory entries
        3. Route to STM/LTM
        4. Create/update capsule if needed
        """
        logger.info(f"[{self.construct_id}] Starting ingestion cycle")
        
        result = {
            "construct_id": self.construct_id,
            "timestamp": datetime.now().isoformat(),
            "ingested": 0,
            "routed_stm": 0,
            "routed_ltm": 0,
            "capsule_updated": False,
            "errors": []
        }
        
        try:
            ingestion_result = self.transcript_ingester.ingest_new_messages()
            result["ingested"] = ingestion_result.get("count", 0)
            
            if result["ingested"] > 0:
                routing_result = self.memory_router.route_new_entries(
                    ingestion_result.get("entries", [])
                )
                result["routed_stm"] = routing_result.get("stm_count", 0)
                result["routed_ltm"] = routing_result.get("ltm_count", 0)
                
                if routing_result.get("should_update_capsule", False):
                    capsule_result = self.transcript_ingester.update_capsule()
                    result["capsule_updated"] = capsule_result.get("success", False)
            
            self.state_manager.record_ingestion(result)
            
        except Exception as e:
            logger.error(f"[{self.construct_id}] Ingestion cycle error: {e}")
            result["errors"].append(str(e))
        
        self._last_run = datetime.now()
        logger.info(f"[{self.construct_id}] Ingestion cycle complete: {result['ingested']} messages")
        return result
    
    def run_identity_check(self) -> Dict[str, Any]:
        """
        Run identity verification and drift detection.
        """
        logger.info(f"[{self.construct_id}] Running identity check")
        return self.identity_guard.check_identity()
    
    def get_context_for_inference(self, user_message: str) -> Dict[str, Any]:
        """
        Build full context for LLM inference.
        Combines STM, LTM, and identity context.
        """
        stm = self.memory_router.get_stm_window()
        ltm_relevant = self.memory_router.get_relevant_ltm(user_message)
        identity_context = self.identity_guard.get_identity_context()
        state = self.state_manager.get_current_state()
        
        return {
            "construct_id": self.construct_id,
            "stm_messages": stm,
            "ltm_context": ltm_relevant,
            "identity": identity_context,
            "state": state,
            "timestamp": datetime.now().isoformat()
        }
    
    def process_response(self, response: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Process an LLM response - update memory and check for drift.
        """
        self.memory_router.add_to_stm({
            "role": "assistant",
            "content": response,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {}
        })
        
        drift_check = self.identity_guard.check_response_drift(response)
        self.state_manager.update_state(response_generated=True)
        
        return {
            "processed": True,
            "drift_detected": drift_check.get("drift_detected", False),
            "drift_score": drift_check.get("score", 0.0)
        }
    
    def get_status(self) -> Dict[str, Any]:
        """Get current status of all identity components."""
        return {
            "construct_id": self.construct_id,
            "running": self._running,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "state": self.state_manager.get_current_state(),
            "stm_size": len(self.memory_router.get_stm_window()),
            "identity_locked": self.identity_guard.is_locked()
        }


_runners: Dict[str, ScriptRunner] = {}

def get_script_runner(construct_id: str, vvault_root: Optional[str] = None) -> ScriptRunner:
    """Get or create a ScriptRunner for a construct."""
    if construct_id not in _runners:
        _runners[construct_id] = ScriptRunner(construct_id, vvault_root)
    return _runners[construct_id]
