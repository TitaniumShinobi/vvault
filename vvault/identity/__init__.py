"""
VVAULT Identity Module
Master identity scripts for construct memory routing, transcript ingestion,
and continuity management.

Architecture:
- script_runner: Central controller for all identity operations
- transcript_ingester: Transcript → Capsule ingestion pipeline  
- memory_router: STM/LTM routing with hooks for memup/frame integration
- state_manager: Construct state and continuity tracking
- identity_guard: Identity drift detection and context lock

Integration Points:
- chatgpt-retrieval-plugin: Semantic search (external)
- memup (frame™): bank.py, context.py, mem_check.py, stm.py, ltm.py (external)
- vvault/engine/memory: MemoryContextBuilder
- vvault/continuity: ProviderMemoryRouter, StyleExtractor
"""

from .script_runner import ScriptRunner, get_script_runner
from .transcript_ingester import TranscriptIngester
from .memory_router import MemoryRouter
from .state_manager import StateManager
from .identity_guard import IdentityGuard

__all__ = [
    'ScriptRunner',
    'get_script_runner',
    'TranscriptIngester', 
    'MemoryRouter',
    'StateManager',
    'IdentityGuard'
]
