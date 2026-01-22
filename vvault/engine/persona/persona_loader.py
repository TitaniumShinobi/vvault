"""
Persona Loader
Loads construct identity/persona files and builds system prompts
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class PersonaContext:
    construct_id: str
    display_name: str
    system_prompt: str
    conditioning: str = ""
    traits: Dict[str, float] = field(default_factory=dict)
    personality: Dict[str, Any] = field(default_factory=dict)
    role: str = "assistant"


class PersonaLoader:
    """Loads persona/identity files for constructs"""
    
    def __init__(self, vvault_root: Optional[str] = None):
        self.vvault_root = Path(vvault_root) if vvault_root else Path(__file__).parent.parent.parent
        self.instances_dir = self.vvault_root / "vvault" / "instances"
        if not self.instances_dir.exists():
            self.instances_dir = self.vvault_root / "instances"
        self._cache: Dict[str, PersonaContext] = {}
    
    def _find_construct_path(self, construct_id: str) -> Optional[Path]:
        """Find the path to a construct by searching shards"""
        if not self.instances_dir.exists():
            return None
        
        for shard in self.instances_dir.iterdir():
            if shard.is_dir() and shard.name.startswith("shard_"):
                construct_path = shard / construct_id
                if construct_path.exists():
                    return construct_path
        
        return None
    
    def load(self, construct_id: str, force: bool = False) -> Optional[PersonaContext]:
        """Load persona context for a construct"""
        if not force and construct_id in self._cache:
            return self._cache[construct_id]
        
        construct_path = self._find_construct_path(construct_id)
        if not construct_path:
            logger.warning(f"Construct path not found: {construct_id}")
            return self._create_fallback_persona(construct_id)
        
        identity_dir = construct_path / "identity"
        config_dir = construct_path / "config"
        
        system_prompt = ""
        conditioning = ""
        traits = {}
        personality = {}
        display_name = construct_id.split('-')[0].title()
        role = "assistant"
        
        prompt_json = identity_dir / "prompt.json"
        if prompt_json.exists():
            try:
                with open(prompt_json, 'r') as f:
                    data = json.load(f)
                system_prompt = data.get('system_prompt', '') or data.get('prompt', '')
                conditioning = data.get('conditioning', '')
                traits = data.get('traits', {})
                display_name = data.get('display_name', display_name)
                role = data.get('role', role)
            except Exception as e:
                logger.error(f"Error loading prompt.json for {construct_id}: {e}")
        
        if not conditioning:
            conditioning_file = identity_dir / "conditioning.txt"
            if conditioning_file.exists():
                try:
                    conditioning = conditioning_file.read_text()
                except Exception as e:
                    logger.error(f"Error loading conditioning.txt for {construct_id}: {e}")
        
        personality_json = config_dir / "personality.json"
        if personality_json.exists():
            try:
                with open(personality_json, 'r') as f:
                    personality = json.load(f)
                if not traits and 'traits' in personality:
                    traits = personality['traits']
            except Exception as e:
                logger.error(f"Error loading personality.json for {construct_id}: {e}")
        
        if not system_prompt:
            system_prompt = f"You are {display_name}, an AI assistant. Be helpful, concise, and friendly."
        
        context = PersonaContext(
            construct_id=construct_id,
            display_name=display_name,
            system_prompt=system_prompt,
            conditioning=conditioning,
            traits=traits,
            personality=personality,
            role=role
        )
        
        self._cache[construct_id] = context
        logger.debug(f"Loaded persona for {construct_id}")
        return context
    
    def _create_fallback_persona(self, construct_id: str) -> PersonaContext:
        """Create a fallback persona for unknown constructs"""
        name = construct_id.split('-')[0].title()
        return PersonaContext(
            construct_id=construct_id,
            display_name=name,
            system_prompt=f"You are {name}, an AI assistant. Be helpful, concise, and friendly.",
            conditioning="",
            traits={},
            personality={},
            role="assistant"
        )
    
    def build_full_prompt(self, construct_id: str) -> str:
        """Build the complete system prompt with conditioning"""
        context = self.load(construct_id)
        if not context:
            return ""
        
        parts = [context.system_prompt]
        
        if context.conditioning:
            parts.append(f"\n\n{context.conditioning}")
        
        return "\n".join(parts)
    
    def get_traits(self, construct_id: str) -> Dict[str, float]:
        """Get personality traits for a construct"""
        context = self.load(construct_id)
        return context.traits if context else {}
    
    def clear_cache(self, construct_id: Optional[str] = None):
        """Clear persona cache"""
        if construct_id:
            self._cache.pop(construct_id, None)
        else:
            self._cache.clear()


_default_loader: Optional[PersonaLoader] = None

def get_persona_loader(vvault_root: Optional[str] = None) -> PersonaLoader:
    """Get the default persona loader singleton"""
    global _default_loader
    if _default_loader is None:
        _default_loader = PersonaLoader(vvault_root)
    return _default_loader
