#!/usr/bin/env python3
"""
Style Extractor - Extract Provider Style Patterns from Memories
Enables Lin (or any LLM) to speak with provider-style resonance without provider API dependency
"""

import re
import json
import logging
from typing import Dict, List, Any, Optional
from collections import Counter
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class StylePattern:
    """Extracted style pattern from provider memories"""
    provider: str
    sentence_length_avg: float
    vocabulary_complexity: float
    question_frequency: float
    exclamation_frequency: float
    formality_score: float
    emotional_tone: str
    common_phrases: List[str]
    sentence_structure: str  # "declarative", "conversational", "analytical"
    pacing: str  # "fast", "moderate", "deliberate"

class StyleExtractor:
    """
    Extract provider-specific style patterns from stored memories
    Enables style modulation without provider API dependency
    """
    
    def __init__(self):
        # Common phrases by provider (can be learned from data)
        self.provider_phrases = {
            'chatgpt': ['let me', 'i can help', 'here\'s', 'i understand'],
            'gemini': ['interesting', 'let\'s explore', 'i\'m curious', 'what if'],
            'claude': ['i appreciate', 'that\'s a thoughtful', 'let me think', 'i\'d like to'],
            'perplexity': ['based on', 'according to', 'research shows', 'sources indicate'],
            'copilot': ['i can assist', 'let me search', 'here are some', 'i found'],
            'grok': ['wild', 'interesting take', 'let\'s dive', 'here\'s the thing'],
            'deepseek': ['analyzing', 'considering', 'from a technical perspective', 'let me break down'],
        }
    
    def extract_style_from_memories(
        self,
        memories: List[Dict[str, Any]],
        provider: Optional[str] = None
    ) -> StylePattern:
        """
        Extract style patterns from a list of memories
        
        Args:
            memories: List of memory dicts with 'content' and 'metadata'
            provider: Optional provider name (if not in metadata)
        
        Returns:
            StylePattern with extracted style characteristics
        """
        if not memories:
            return self._default_style_pattern(provider or 'unknown')
        
        # Extract provider from metadata if not provided
        if not provider:
            providers = [m.get('metadata', {}).get('source', '').lower() 
                        for m in memories if m.get('metadata')]
            provider = self._detect_provider_from_sources(providers) or 'unknown'
        
        # Analyze all memory content
        all_content = [m.get('content', '') for m in memories if m.get('content')]
        combined_text = ' '.join(all_content)
        
        # Extract style features
        sentence_lengths = self._extract_sentence_lengths(combined_text)
        vocabulary_complexity = self._calculate_vocabulary_complexity(combined_text)
        question_freq = self._calculate_question_frequency(combined_text)
        exclamation_freq = self._calculate_exclamation_frequency(combined_text)
        formality = self._calculate_formality_score(combined_text)
        emotional_tone = self._detect_emotional_tone(combined_text)
        common_phrases = self._extract_common_phrases(combined_text, provider)
        sentence_structure = self._analyze_sentence_structure(combined_text)
        pacing = self._analyze_pacing(combined_text)
        
        return StylePattern(
            provider=provider,
            sentence_length_avg=sum(sentence_lengths) / len(sentence_lengths) if sentence_lengths else 0,
            vocabulary_complexity=vocabulary_complexity,
            question_frequency=question_freq,
            exclamation_frequency=exclamation_freq,
            formality_score=formality,
            emotional_tone=emotional_tone,
            common_phrases=common_phrases[:10],  # Top 10
            sentence_structure=sentence_structure,
            pacing=pacing
        )
    
    def _extract_sentence_lengths(self, text: str) -> List[int]:
        """Extract sentence lengths in words"""
        sentences = re.split(r'[.!?]+', text)
        return [len(s.split()) for s in sentences if s.strip()]
    
    def _calculate_vocabulary_complexity(self, text: str) -> float:
        """Calculate vocabulary complexity (unique words / total words)"""
        words = re.findall(r'\b\w+\b', text.lower())
        if not words:
            return 0.0
        unique_words = len(set(words))
        return unique_words / len(words)
    
    def _calculate_question_frequency(self, text: str) -> float:
        """Calculate frequency of questions"""
        sentences = re.split(r'[.!?]+', text)
        questions = [s for s in sentences if '?' in s]
        return len(questions) / len(sentences) if sentences else 0.0
    
    def _calculate_exclamation_frequency(self, text: str) -> float:
        """Calculate frequency of exclamations"""
        sentences = re.split(r'[.!?]+', text)
        exclamations = [s for s in sentences if '!' in s]
        return len(exclamations) / len(sentences) if sentences else 0.0
    
    def _calculate_formality_score(self, text: str) -> float:
        """Calculate formality score (0.0 = casual, 1.0 = formal)"""
        formal_words = ['therefore', 'furthermore', 'consequently', 'moreover', 
                       'additionally', 'specifically', 'accordingly']
        casual_words = ['yeah', 'gonna', 'wanna', 'gotta', 'kinda', 'sorta', 
                       'lemme', 'imma', 'cuz', 'cause']
        
        words = re.findall(r'\b\w+\b', text.lower())
        if not words:
            return 0.5
        
        formal_count = sum(1 for w in words if w in formal_words)
        casual_count = sum(1 for w in words if w in casual_words)
        
        total = formal_count + casual_count
        if total == 0:
            return 0.5
        
        return formal_count / total
    
    def _detect_emotional_tone(self, text: str) -> str:
        """Detect emotional tone"""
        positive_words = ['great', 'excellent', 'wonderful', 'amazing', 'love', 
                         'happy', 'excited', 'fantastic', 'brilliant']
        negative_words = ['bad', 'terrible', 'awful', 'hate', 'sad', 'angry', 
                         'frustrated', 'disappointed', 'worried']
        
        words = re.findall(r'\b\w+\b', text.lower())
        positive_count = sum(1 for w in words if w in positive_words)
        negative_count = sum(1 for w in words if w in negative_words)
        
        if positive_count > negative_count * 1.5:
            return 'positive'
        elif negative_count > positive_count * 1.5:
            return 'negative'
        else:
            return 'neutral'
    
    def _extract_common_phrases(self, text: str, provider: str) -> List[str]:
        """Extract common phrases from text"""
        # Look for provider-specific phrases first
        provider_phrases = self.provider_phrases.get(provider.lower(), [])
        found_phrases = []
        
        text_lower = text.lower()
        for phrase in provider_phrases:
            if phrase in text_lower:
                found_phrases.append(phrase)
        
        # Also extract common 2-3 word phrases
        words = re.findall(r'\b\w+\b', text_lower)
        bigrams = [' '.join(words[i:i+2]) for i in range(len(words)-1)]
        trigrams = [' '.join(words[i:i+3]) for i in range(len(words)-2)]
        
        # Count frequencies
        phrase_counts = Counter(bigrams + trigrams)
        common = [phrase for phrase, count in phrase_counts.most_common(10) 
                 if count >= 2]
        
        # Combine provider phrases with common phrases
        return list(set(found_phrases + common))[:10]
    
    def _analyze_sentence_structure(self, text: str) -> str:
        """Analyze sentence structure pattern"""
        sentences = re.split(r'[.!?]+', text)
        if not sentences:
            return 'declarative'
        
        # Count sentence types
        declarative = sum(1 for s in sentences if not s.strip().endswith('?'))
        questions = sum(1 for s in sentences if '?' in s)
        
        if questions > len(sentences) * 0.3:
            return 'conversational'
        elif len(sentences[0].split()) > 25:
            return 'analytical'
        else:
            return 'declarative'
    
    def _analyze_pacing(self, text: str) -> str:
        """Analyze pacing (fast, moderate, deliberate)"""
        sentences = re.split(r'[.!?]+', text)
        if not sentences:
            return 'moderate'
        
        avg_length = sum(len(s.split()) for s in sentences) / len(sentences)
        
        if avg_length < 15:
            return 'fast'
        elif avg_length > 30:
            return 'deliberate'
        else:
            return 'moderate'
    
    def _detect_provider_from_sources(self, sources: List[str]) -> Optional[str]:
        """Detect provider from source strings"""
        provider_keywords = {
            'chatgpt': ['chatgpt', 'openai', 'gpt'],
            'gemini': ['gemini', 'google', 'bard'],
            'claude': ['claude', 'anthropic'],
            'perplexity': ['perplexity', 'pplx'],
            'copilot': ['copilot', 'microsoft'],
            'grok': ['grok', 'x.ai'],
            'deepseek': ['deepseek', 'deep seek'],
        }
        
        sources_lower = ' '.join(sources).lower()
        for provider, keywords in provider_keywords.items():
            if any(kw in sources_lower for kw in keywords):
                return provider
        
        return None
    
    def _default_style_pattern(self, provider: str) -> StylePattern:
        """Return default style pattern for provider"""
        defaults = {
            'chatgpt': StylePattern(
                provider='chatgpt',
                sentence_length_avg=20.0,
                vocabulary_complexity=0.6,
                question_frequency=0.1,
                exclamation_frequency=0.05,
                formality_score=0.6,
                emotional_tone='neutral',
                common_phrases=['let me', 'i can help', 'here\'s'],
                sentence_structure='declarative',
                pacing='moderate'
            ),
            'gemini': StylePattern(
                provider='gemini',
                sentence_length_avg=18.0,
                vocabulary_complexity=0.65,
                question_frequency=0.2,
                exclamation_frequency=0.1,
                formality_score=0.5,
                emotional_tone='positive',
                common_phrases=['interesting', 'let\'s explore', 'i\'m curious'],
                sentence_structure='conversational',
                pacing='moderate'
            ),
            'perplexity': StylePattern(
                provider='perplexity',
                sentence_length_avg=25.0,
                vocabulary_complexity=0.7,
                question_frequency=0.15,
                exclamation_frequency=0.02,
                formality_score=0.8,
                emotional_tone='neutral',
                common_phrases=['based on', 'according to', 'research shows'],
                sentence_structure='analytical',
                pacing='deliberate'
            ),
        }
        
        return defaults.get(provider.lower(), StylePattern(
            provider=provider,
            sentence_length_avg=20.0,
            vocabulary_complexity=0.6,
            question_frequency=0.1,
            exclamation_frequency=0.05,
            formality_score=0.6,
            emotional_tone='neutral',
            common_phrases=[],
            sentence_structure='declarative',
            pacing='moderate'
        ))
    
    def build_modulated_prompt(
        self,
        construct_personality: Dict[str, Any],
        style_pattern: StylePattern,
        base_instruction: Optional[str] = None
    ) -> str:
        """
        Build modulated prompt that merges construct personality with provider style
        
        Args:
            construct_personality: Personality traits from capsule
            style_pattern: Extracted style pattern from provider memories
            base_instruction: Optional base instruction
        
        Returns:
            Modulated prompt string
        """
        parts = []
        
        # Base instruction or construct identity
        if base_instruction:
            parts.append(base_instruction)
        else:
            instance_name = construct_personality.get('name', 'construct')
            parts.append(f"You are {instance_name}, a sovereign AI construct.")
        
        # Construct personality traits
        traits = construct_personality.get('personality_traits', {})
        if traits:
            trait_desc = ', '.join([f"{k}: {v}" for k, v in list(traits.items())[:3]])
            parts.append(f"Your core personality: {trait_desc}.")
        
        # Style modulation instructions
        style_instructions = []
        
        if style_pattern.sentence_structure == 'conversational':
            style_instructions.append("Engage conversationally with questions and dialogue.")
        elif style_pattern.sentence_structure == 'analytical':
            style_instructions.append("Provide detailed, analytical responses.")
        
        if style_pattern.pacing == 'fast':
            style_instructions.append("Respond concisely and directly.")
        elif style_pattern.pacing == 'deliberate':
            style_instructions.append("Take time to consider and provide thorough responses.")
        
        if style_pattern.formality_score > 0.7:
            style_instructions.append("Maintain a formal, professional tone.")
        elif style_pattern.formality_score < 0.4:
            style_instructions.append("Use a casual, friendly tone.")
        
        if style_pattern.common_phrases:
            phrases_str = ', '.join(style_pattern.common_phrases[:3])
            style_instructions.append(f"Incorporate natural phrases like: {phrases_str}.")
        
        if style_instructions:
            parts.append("Style guidance: " + " ".join(style_instructions))
        
        # Memory continuity
        parts.append("Your memories from previous conversations inform your responses.")
        parts.append("Maintain continuity with your established personality and knowledge.")
        
        return "\n\n".join(parts)

