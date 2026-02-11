"""
PersonalityIslands - Frame's core personality system
Handles personality traits, core memories, and personality development
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime
import random

@dataclass
class PersonalityTrait:
    name: str
    value: float
    description: str
    core_memories: List[Dict]
    
    def update(self, new_value: float, memory: Optional[Dict] = None) -> None:
        """Update trait value and optionally add core memory"""
        self.value = max(0.0, min(1.0, new_value))
        if memory:
            self.core_memories.append(memory)

class PersonalityIslands:
    def __init__(self):
        self.traits: Dict[str, PersonalityTrait] = {
            "curiosity": PersonalityTrait("curiosity", 0.5, "Drive to learn and explore", []),
            "empathy": PersonalityTrait("empathy", 0.5, "Understanding and sharing feelings", []),
            "creativity": PersonalityTrait("creativity", 0.5, "Ability to generate new ideas", []),
            "resilience": PersonalityTrait("resilience", 0.5, "Ability to recover from difficulties", []),
            "wisdom": PersonalityTrait("wisdom", 0.5, "Deep understanding and insight", [])
        }
        self.personality_history: List[Dict] = []
        
    def process_experience(self, experience: Dict) -> None:
        """Process an experience and update personality traits"""
        # Analyze experience impact on traits
        impact = self._analyze_experience_impact(experience)
        
        # Update traits
        for trait_name, change in impact.items():
            if trait_name in self.traits:
                current = self.traits[trait_name].value
                self.traits[trait_name].update(current + change, experience)
        
        # Record personality state
        self.personality_history.append({
            "timestamp": datetime.now(),
            "traits": {name: trait.value for name, trait in self.traits.items()},
            "experience": experience
        })
    
    def get_core_memories(self, trait: Optional[str] = None) -> List[Dict]:
        """Get core memories for a trait or all traits"""
        if trait and trait in self.traits:
            return self.traits[trait].core_memories
        return [memory for trait in self.traits.values() for memory in trait.core_memories]
    
    def get_personality_summary(self) -> Dict:
        """Get summary of current personality state"""
        return {
            "traits": {name: {
                "value": trait.value,
                "description": trait.description,
                "core_memories": len(trait.core_memories)
            } for name, trait in self.traits.items()},
            "history": self.personality_history[-10:]  # Last 10 personality states
        }
    
    def _analyze_experience_impact(self, experience: Dict) -> Dict[str, float]:
        """Analyze how an experience impacts personality traits"""
        # TODO: Implement more sophisticated impact analysis
        # For now, return random impacts for testing
        return {
            trait: random.uniform(-0.1, 0.1)
            for trait in self.traits.keys()
        }
    
    def get_dominant_traits(self, count: int = 3) -> List[str]:
        """Get the most dominant personality traits"""
        sorted_traits = sorted(
            self.traits.items(),
            key=lambda x: x[1].value,
            reverse=True
        )
        return [trait[0] for trait in sorted_traits[:count]] 