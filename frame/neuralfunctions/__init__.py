"""
NeuralFunctions - Frame's emotional and cognitive processing system
Inspired by Inside Out's memory and emotion processing
"""

from .emotions import EmotionalCore
from .islands import PersonalityIslands
from .dreams import DreamProcessor
from .sleep import SleepManager

__all__ = ['EmotionalCore', 'PersonalityIslands', 'DreamProcessor', 'SleepManager'] 