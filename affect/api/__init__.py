"""
Affect API Module

Flask routes and API layer for affect operations.
"""

from .affect_routes import create_affect_blueprint
from .affect_service import AffectService, UpdateGovernor, MemoryWeightCalculator

__all__ = [
    'create_affect_blueprint',
    'AffectService',
    'UpdateGovernor',
    'MemoryWeightCalculator'
]

