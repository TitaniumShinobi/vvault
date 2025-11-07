"""
VVAULT - Virtual Vault for AI Memory Management
Advanced memory record storage with tamper-evident Merkle chaining
"""

__version__ = "1.0.0"
__author__ = "VVAULT Development Team"

from .schema_gate import SchemaGate, SchemaValidationResult

__all__ = [
    "SchemaGate",
    "SchemaValidationResult"
]
