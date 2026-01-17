"""
Affect Storage Module

Persistent storage for affective states, history, and audit logs.
"""

from .AffectiveStateStore import AffectiveStateStore
from .StateHistoryManager import StateHistoryManager
from .AuditLogger import AuditLogger

__all__ = [
    'AffectiveStateStore',
    'StateHistoryManager',
    'AuditLogger'
]

