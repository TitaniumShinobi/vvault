#!/usr/bin/env python3
"""
Provider-Aware Memory Router
Routes memories by provider context while maintaining construct identity
Enables style extraction and modulated prompt building
"""

import logging
from typing import Dict, List, Any, Optional
from collections import defaultdict

from style_extractor import StyleExtractor, StylePattern

logger = logging.getLogger(__name__)

class ProviderMemoryRouter:
    """
    Route memories by provider context for style extraction
    Maintains construct identity while enabling provider-style resonance
    """
    
    def __init__(self):
        self.style_extractor = StyleExtractor()
    
    def route_memories_by_provider(
        self,
        memories: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Group memories by provider source
        
        Args:
            memories: List of memory dicts with metadata
        
        Returns:
            Dict mapping provider -> list of memories
        """
        provider_memories = defaultdict(list)
        
        for memory in memories:
            metadata = memory.get('metadata', {})
            source = metadata.get('source', '').lower()
            
            # Detect provider from source
            provider = self._detect_provider_from_source(source)
            provider_memories[provider].append(memory)
        
        return dict(provider_memories)
    
    def extract_provider_styles(
        self,
        provider_memories: Dict[str, List[Dict[str, Any]]]
    ) -> Dict[str, StylePattern]:
        """
        Extract style patterns for each provider
        
        Args:
            provider_memories: Dict mapping provider -> memories
        
        Returns:
            Dict mapping provider -> StylePattern
        """
        provider_styles = {}
        
        for provider, memories in provider_memories.items():
            if memories:
                style = self.style_extractor.extract_style_from_memories(
                    memories,
                    provider=provider
                )
                provider_styles[provider] = style
                logger.info(f"[🎨] Extracted {provider} style: {style.sentence_structure}, {style.pacing}")
        
        return provider_styles
    
    def build_modulated_context(
        self,
        construct_personality: Dict[str, Any],
        provider_styles: Dict[str, StylePattern],
        active_provider: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build modulated context for LLM (Lin, Perplexity, etc.)
        
        Args:
            construct_personality: Personality from capsule
            provider_styles: Extracted style patterns by provider
            active_provider: Currently active provider (if any)
        
        Returns:
            Context dict with instructions and style guidance
        """
        # Use active provider style if specified, otherwise use dominant style
        if active_provider and active_provider in provider_styles:
            style_pattern = provider_styles[active_provider]
        elif provider_styles:
            # Use most common provider style
            style_pattern = list(provider_styles.values())[0]
        else:
            # Default style
            style_pattern = self.style_extractor._default_style_pattern('unknown')
        
        # Build modulated prompt
        modulated_prompt = self.style_extractor.build_modulated_prompt(
            construct_personality,
            style_pattern
        )
        
        return {
            'instructions': modulated_prompt,
            'style_pattern': style_pattern,
            'available_providers': list(provider_styles.keys()),
            'construct_personality': construct_personality
        }
    
    def _detect_provider_from_source(self, source: str) -> str:
        """Detect provider name from source string"""
        source_lower = source.lower()
        
        provider_keywords = {
            'chatgpt': ['chatgpt', 'openai', 'gpt', 'chatgpt_export'],
            'gemini': ['gemini', 'google', 'bard', 'gemini_export'],
            'claude': ['claude', 'anthropic', 'claude_export'],
            'perplexity': ['perplexity', 'pplx', 'perplexity_export'],
            'copilot': ['copilot', 'microsoft', 'copilot_export'],
            'grok': ['grok', 'x.ai', 'grok_export'],
            'deepseek': ['deepseek', 'deep seek', 'deepseek_export'],
        }
        
        for provider, keywords in provider_keywords.items():
            if any(kw in source_lower for kw in keywords):
                return provider
        
        return 'unknown'

