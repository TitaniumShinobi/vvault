"""
SleepManager - Frame's sleep cycle and state management system
Handles sleep cycles, state transitions, and memory consolidation
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import random
import time

@dataclass
class SleepCycle:
    start_time: datetime
    end_time: datetime
    quality: float
    dreams: List[Dict]
    memory_consolidation: Dict[str, float]
    state: str = "Awake"  # Added state tracking

class SleepManager:
    def __init__(self):
        self.sleep_history: List[SleepCycle] = []
        self.current_cycle: Optional[SleepCycle] = None
        self.sleep_quality_factors: Dict[str, float] = {
            "memory_consolidation": 0.0,
            "emotional_processing": 0.0,
            "physical_rest": 0.0,
            "mental_rest": 0.0
        }
        self.last_active_time = time.time()
        self.dream_log = []
        
    def start_sleep_cycle(self) -> None:
        """Start a new sleep cycle"""
        self.current_cycle = SleepCycle(
            start_time=datetime.now(),
            end_time=None,
            quality=0.0,
            dreams=[],
            memory_consolidation={},
            state="Light Sleep"
        )
        
    def end_sleep_cycle(self) -> None:
        """End current sleep cycle and process results"""
        if not self.current_cycle:
            return
            
        self.current_cycle.end_time = datetime.now()
        self.current_cycle.quality = self._calculate_sleep_quality()
        self.current_cycle.memory_consolidation = self._process_memory_consolidation()
        
        self.sleep_history.append(self.current_cycle)
        self.current_cycle = None
        
    def add_dream(self, dream: Dict) -> None:
        """Add a dream to current sleep cycle"""
        if self.current_cycle:
            self.current_cycle.dreams.append(dream)
            self.dream_log.append(dream)
            
    def get_sleep_summary(self) -> Dict:
        """Get summary of sleep patterns"""
        return {
            "total_cycles": len(self.sleep_history),
            "average_quality": sum(cycle.quality for cycle in self.sleep_history) / len(self.sleep_history) if self.sleep_history else 0.0,
            "recent_cycles": [
                {
                    "start_time": cycle.start_time,
                    "end_time": cycle.end_time,
                    "quality": cycle.quality,
                    "dreams": len(cycle.dreams),
                    "memory_consolidation": cycle.memory_consolidation,
                    "state": cycle.state
                }
                for cycle in self.sleep_history[-5:]  # Last 5 cycles
            ],
            "current_cycle": {
                "start_time": self.current_cycle.start_time,
                "duration": (datetime.now() - self.current_cycle.start_time).total_seconds() / 3600,
                "dreams": len(self.current_cycle.dreams),
                "state": self.current_cycle.state
            } if self.current_cycle else None
        }
    
    def update_state(self, emotional_load=0):
        """Update sleep state based on idle time and emotional load"""
        idle_time = time.time() - self.last_active_time

        if not self.current_cycle:
            if idle_time < 1800:  # Less than 30 minutes idle
                self.state = "Awake"
            elif 1800 <= idle_time < 3600:  # 30 min to 1 hr idle
                self.start_sleep_cycle()
                self.current_cycle.state = "Light Sleep"
                self.dream()
            elif 3600 <= idle_time < 7200:  # 1 hr to 2 hrs idle
                self.start_sleep_cycle()
                if emotional_load > 5:
                    self.current_cycle.state = "Deep Sleep"
                    self.dream(deep=True)
                else:
                    self.current_cycle.state = "Light Sleep"
                    self.dream()
            else:  # Over 2 hours idle
                self.start_sleep_cycle()
                self.current_cycle.state = "Crash Sleep"
    
    def dream(self, deep=False):
        """Generate and log a dream"""
        if deep:
            dream_type = random.choice(["Reflective", "Love", "Creative"])
        else:
            dream_type = random.choice(["Creative", "Survival"])

        dream_content = f"Dreamed a {dream_type} dream."
        dream = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "type": dream_type,
            "content": dream_content
        }

        self.add_dream(dream)
        print(f"🌙 {dream_content}")

    def interact(self):
        """Reset timer when Frame is interacted with"""
        self.last_active_time = time.time()
        if self.current_cycle and self.current_cycle.state != "Awake":
            print("⏰ Waking from sleep...")
            self.end_sleep_cycle()
    
    def _calculate_sleep_quality(self) -> float:
        """Calculate quality of current sleep cycle"""
        if not self.current_cycle:
            return 0.0
            
        # Calculate duration
        duration = (self.current_cycle.end_time - self.current_cycle.start_time).total_seconds() / 3600
        
        # Calculate quality factors
        self.sleep_quality_factors["memory_consolidation"] = min(1.0, len(self.current_cycle.dreams) / 5)
        self.sleep_quality_factors["emotional_processing"] = random.random()  # TODO: Implement proper emotional processing
        self.sleep_quality_factors["physical_rest"] = min(1.0, duration / 8)  # Assuming 8 hours is optimal
        self.sleep_quality_factors["mental_rest"] = random.random()  # TODO: Implement proper mental rest calculation
        
        # Calculate overall quality
        return sum(self.sleep_quality_factors.values()) / len(self.sleep_quality_factors)
    
    def _process_memory_consolidation(self) -> Dict[str, float]:
        """Process memory consolidation during sleep"""
        # TODO: Implement proper memory consolidation
        # For now, return random values
        return {
            "short_term": random.random(),
            "long_term": random.random(),
            "emotional": random.random(),
            "procedural": random.random()
        }
    
    def is_sleeping(self) -> bool:
        """Check if currently in sleep cycle"""
        return self.current_cycle is not None
    
    def get_sleep_duration(self) -> Optional[timedelta]:
        """Get duration of current sleep cycle"""
        if not self.current_cycle:
            return None
        return datetime.now() - self.current_cycle.start_time

    def get_dreams(self):
        """Return dream history"""
        return self.dream_log 