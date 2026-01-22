"""
Conversation Engine
Main orchestration class that ties together persona, memory, and LLM inference
"""

import os
import json
import logging
import requests
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from .construct_registry import ConstructRegistry, get_registry
from ..persona.persona_loader import PersonaLoader, get_persona_loader
from ..memory.memory_context import MemoryContextBuilder, get_memory_builder, MemoryContext

logger = logging.getLogger(__name__)

@dataclass
class ConversationResponse:
    success: bool
    response: str = ""
    construct_id: str = ""
    display_name: str = ""
    timestamp: str = ""
    error: Optional[str] = None
    memory_context: Optional[MemoryContext] = None
    traits_applied: Dict[str, float] = field(default_factory=dict)


class ConversationEngine:
    """
    Main conversation engine for VVAULT constructs.
    Orchestrates persona loading, memory context, and LLM inference.
    """
    
    def __init__(
        self,
        vvault_root: Optional[str] = None,
        ollama_host: str = "http://localhost:11434",
        model: str = "qwen2.5:0.5b",
        max_stm: int = 20,
        max_ltm: int = 10
    ):
        self.vvault_root = Path(vvault_root) if vvault_root else Path(__file__).parent.parent.parent
        self.ollama_host = ollama_host
        self.model = model
        
        self.registry = get_registry(str(self.vvault_root))
        self.persona_loader = get_persona_loader(str(self.vvault_root))
        self.memory_builder = MemoryContextBuilder(str(self.vvault_root), max_stm, max_ltm)
    
    def process_message(
        self,
        construct_id: str,
        user_message: str,
        user_name: str = "User",
        timezone_str: str = "EST",
        include_history: bool = True
    ) -> ConversationResponse:
        """
        Process a user message and generate a construct response.
        
        Args:
            construct_id: The construct to converse with
            user_message: The user's message
            user_name: Display name for the user
            timezone_str: User's timezone for display
            include_history: Whether to include conversation history
            
        Returns:
            ConversationResponse with the construct's reply
        """
        persona = self.persona_loader.load(construct_id)
        if not persona:
            return ConversationResponse(
                success=False,
                error=f"Could not load persona for construct: {construct_id}"
            )
        
        system_prompt = self.persona_loader.build_full_prompt(construct_id)
        
        messages = []
        memory_context = None
        
        if include_history:
            memory_context = self.memory_builder.build_context(construct_id)
            history = self.memory_builder.format_for_llm(memory_context)
            messages.extend(history)
        
        messages.append({
            "role": "user",
            "content": user_message
        })
        
        try:
            response_text = self._call_ollama(system_prompt, messages)
        except Exception as e:
            logger.error(f"LLM inference failed: {e}")
            return ConversationResponse(
                success=False,
                error=f"LLM inference failed: {str(e)}"
            )
        
        now_utc = datetime.now(timezone.utc)
        timestamp = now_utc.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now_utc.microsecond // 1000:03d}Z'
        
        return ConversationResponse(
            success=True,
            response=response_text,
            construct_id=construct_id,
            display_name=persona.display_name,
            timestamp=timestamp,
            memory_context=memory_context,
            traits_applied=persona.traits
        )
    
    def _call_ollama(self, system_prompt: str, messages: List[Dict[str, str]]) -> str:
        """Call Ollama for LLM inference"""
        prompt = ""
        for msg in messages:
            if msg["role"] == "user":
                prompt += f"User: {msg['content']}\n"
            else:
                prompt += f"Assistant: {msg['content']}\n"
        
        if messages and messages[-1]["role"] == "user":
            pass
        
        response = requests.post(
            f"{self.ollama_host}/api/generate",
            json={
                "model": self.model,
                "prompt": messages[-1]["content"] if messages else "",
                "system": system_prompt,
                "stream": False
            },
            timeout=60
        )
        
        if not response.ok:
            raise Exception(f"Ollama returned {response.status_code}: {response.text[:200]}")
        
        data = response.json()
        return data.get("response", "")
    
    def list_constructs(self) -> List[Dict[str, Any]]:
        """List all available constructs"""
        self.registry.load_all()
        results = []
        for c in self.registry.list_all():
            info = self.registry.to_dict(c.construct_id)
            if info:
                results.append(info)
        return results
    
    def get_construct_info(self, construct_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed info about a construct"""
        manifest = self.registry.to_dict(construct_id)
        if not manifest:
            return None
        
        persona = self.persona_loader.load(construct_id)
        if persona:
            manifest['traits'] = persona.traits
            manifest['personality'] = persona.personality
        
        return manifest
    
    def get_conversation_history(self, construct_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get conversation history for a construct"""
        entries = self.memory_builder.get_recent_messages(construct_id, limit)
        return [
            {
                "role": e.role,
                "content": e.content,
                "speaker": e.speaker,
                "timestamp": e.timestamp
            }
            for e in entries
        ]


_default_engine: Optional[ConversationEngine] = None

def get_conversation_engine(vvault_root: Optional[str] = None) -> ConversationEngine:
    """Get the default conversation engine singleton"""
    global _default_engine
    if _default_engine is None:
        _default_engine = ConversationEngine(vvault_root)
    return _default_engine
