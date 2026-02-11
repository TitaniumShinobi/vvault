"""
EmotionalCore - Frame's emotional processing system
Handles emotional states, responses, and memory coloring
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime
import random

@dataclass
class EmotionalState:
    joy: float = 0.0
    sadness: float = 0.0
    anger: float = 0.0
    fear: float = 0.0
    disgust: float = 0.0
    
    def normalize(self):
        """Normalize emotional values to sum to 1.0"""
        total = sum(vars(self).values())
        if total > 0:
            for key in vars(self):
                setattr(self, key, getattr(self, key) / total)

class EmotionalCore:
    def __init__(self):
        self.current_state = EmotionalState()
        self.emotional_memory: Dict[str, List[EmotionalState]] = {}
        self.emotional_triggers: Dict[str, Dict[str, float]] = {}
        self.emotional_history: List[EmotionalState] = []
        
    def process_emotion(self, emotion: str, intensity: float) -> None:
        """Process a new emotion and update current state"""
        if hasattr(self.current_state, emotion.lower()):
            current = getattr(self.current_state, emotion.lower())
            setattr(self.current_state, emotion.lower(), min(1.0, current + intensity))
            self.current_state.normalize()
            self.emotional_history.append(self.current_state)
            
    def get_dominant_emotion(self) -> str:
        """Get the currently dominant emotion"""
        emotions = vars(self.current_state)
        return max(emotions.items(), key=lambda x: x[1])[0]
    
    def color_memory(self, memory: str, emotional_context: Optional[EmotionalState] = None) -> Dict:
        """Add emotional context to a memory"""
        if emotional_context is None:
            emotional_context = self.current_state
            
        return {
            "content": memory,
            "emotional_state": emotional_context,
            "timestamp": datetime.now(),
            "dominant_emotion": self.get_dominant_emotion()
        }
    
    def process_emotional_response(self, input_text: str) -> Dict:
        """Process input and generate emotional response"""
        # Analyze emotional content
        emotional_content = self._analyze_emotional_content(input_text)
        
        # Update emotional state
        for emotion, intensity in emotional_content.items():
            self.process_emotion(emotion, intensity)
            
        # Generate response based on emotional state
        return {
            "emotional_state": self.current_state,
            "dominant_emotion": self.get_dominant_emotion(),
            "response_intensity": max(vars(self.current_state).values())
        }
    
    def _analyze_emotional_content(self, text: str) -> Dict[str, float]:
        """Analyze text for emotional content"""
        # TODO: Implement more sophisticated emotional analysis
        # For now, return random emotional values for testing
        return {
            "joy": random.random(),
            "sadness": random.random(),
            "anger": random.random(),
            "fear": random.random(),
            "disgust": random.random()
        }
    
    def get_emotional_summary(self) -> Dict:
        """Get summary of current emotional state"""
        return {
            "current_state": vars(self.current_state),
            "dominant_emotion": self.get_dominant_emotion(),
            "emotional_history": [vars(state) for state in self.emotional_history[-10:]]
        } 