#!/usr/bin/env python3
"""
Time Relay Engine - Nonlinear Memory Replay System

Manages time distortion features for AI memory preservation, including
relay depth tracking, entropy management, and causal drift prevention.

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from pathlib import Path

# Configure logging
logger = logging.getLogger(__name__)

class TimeRelayEngine:
    """
    Time Relay Engine for nonlinear memory replay.
    
    Prevents infinite replay loops, manages entropy and causal drift,
    and interfaces with capsuleforge and vvault_core.
    """
    
    def __init__(self, vault_path: str = None):
        """
        Initialize Time Relay Engine.
        
        Args:
            vault_path: Path to VVAULT directory
        """
        self.vault_path = vault_path or os.path.dirname(os.path.abspath(__file__))
        self.relay_registry_path = os.path.join(self.vault_path, "memory_records", "relay_registry.json")
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.relay_registry_path), exist_ok=True)
        
        # Load relay registry
        self.relay_registry = self._load_relay_registry()
        
        # Maximum relay depth to prevent infinite loops
        self.max_relay_depth = 5
        
        logger.info(f"[⏱️] Time Relay Engine initialized (max_depth: {self.max_relay_depth})")
    
    def relay_capsule(
        self,
        capsule_id: str,
        narrative_time: int,
        replay_mode: str = "flashback"
    ) -> Optional[Dict[str, Any]]:
        """
        Load capsule, adjust narrativeIndex and entropy.
        
        Args:
            capsule_id: Capsule identifier
            narrative_time: Target narrative time index
            replay_mode: Replay mode (e.g., "flashback", "what-if")
            
        Returns:
            Relayed capsule dictionary or None if relay depth exceeded
        """
        try:
            logger.info(f"[⏱️] Relaying capsule: {capsule_id} (narrative_time: {narrative_time})")
            
            # Check relay depth
            current_depth = self._get_relay_depth(capsule_id)
            if current_depth >= self.max_relay_depth:
                logger.warning(f"[⚠️] Relay depth exceeded for {capsule_id}: {current_depth} >= {self.max_relay_depth}")
                return None
            
            # Import capsuleforge
            from capsuleforge import CapsuleForge
            
            forge = CapsuleForge(vault_path=self.vault_path)
            
            # Generate relayed capsule
            relayed_capsule = forge.generate_relayed_capsule(
                capsule_id=capsule_id,
                delay=0,
                replay_mode=replay_mode
            )
            
            # Adjust narrative index to target time
            relayed_capsule['narrativeIndex'] = narrative_time
            
            # Calculate entropy based on narrative time deviation
            original_narrative = relayed_capsule.get('additional_data', {}).get('continuity', {}).get(
                'replayMetadata', {}
            ).get('original_narrative_index', 0)
            
            narrative_deviation = abs(narrative_time - original_narrative)
            temporal_entropy = min(narrative_deviation * 0.1, 1.0)
            relayed_capsule['temporalEntropy'] = temporal_entropy
            
            # Calculate causal drift based on depth and deviation
            causal_drift = min(
                (current_depth * 0.05) + (narrative_deviation * 0.02),
                1.0
            )
            relayed_capsule['causalDrift'] = causal_drift
            
            # Mark relay depth
            self.mark_relay_depth(capsule_id, relayed_capsule['relayDepth'])
            
            logger.info(f"[✅] Capsule relayed:")
            logger.info(f"   Relay depth: {relayed_capsule['relayDepth']}")
            logger.info(f"   Narrative index: {relayed_capsule['narrativeIndex']}")
            logger.info(f"   Temporal entropy: {relayed_capsule['temporalEntropy']:.3f}")
            logger.info(f"   Causal drift: {relayed_capsule['causalDrift']:.3f}")
            
            return relayed_capsule
            
        except Exception as e:
            logger.error(f"[❌] Error relaying capsule: {e}")
            return None
    
    def mark_relay_depth(self, capsule_id: str, depth: int):
        """
        Track how many times this capsule has been relayed.
        
        Args:
            capsule_id: Capsule identifier
            depth: Current relay depth
        """
        try:
            # Update registry
            if capsule_id not in self.relay_registry:
                self.relay_registry[capsule_id] = {
                    'capsule_id': capsule_id,
                    'max_depth': 0,
                    'relay_count': 0,
                    'first_relay': datetime.now(timezone.utc).isoformat(),
                    'last_relay': None,
                    'relay_history': []
                }
            
            entry = self.relay_registry[capsule_id]
            entry['max_depth'] = max(entry['max_depth'], depth)
            entry['relay_count'] += 1
            entry['last_relay'] = datetime.now(timezone.utc).isoformat()
            entry['relay_history'].append({
                'depth': depth,
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
            
            # Keep only last 100 relay events
            if len(entry['relay_history']) > 100:
                entry['relay_history'] = entry['relay_history'][-100:]
            
            # Save registry
            self._save_relay_registry()
            
            logger.debug(f"[📝] Marked relay depth for {capsule_id}: {depth}")
            
        except Exception as e:
            logger.error(f"[❌] Error marking relay depth: {e}")
    
    def _get_relay_depth(self, capsule_id: str) -> int:
        """
        Get current relay depth for a capsule.
        
        Args:
            capsule_id: Capsule identifier
            
        Returns:
            Current relay depth (0 if not found)
        """
        if capsule_id in self.relay_registry:
            return self.relay_registry[capsule_id].get('max_depth', 0)
        return 0
    
    def get_relay_info(self, capsule_id: str) -> Optional[Dict[str, Any]]:
        """
        Get relay information for a capsule.
        
        Args:
            capsule_id: Capsule identifier
            
        Returns:
            Relay information dictionary or None
        """
        return self.relay_registry.get(capsule_id)
    
    def can_relay(self, capsule_id: str) -> bool:
        """
        Check if capsule can be relayed (depth not exceeded).
        
        Args:
            capsule_id: Capsule identifier
            
        Returns:
            True if relay is allowed, False otherwise
        """
        current_depth = self._get_relay_depth(capsule_id)
        return current_depth < self.max_relay_depth
    
    def reset_relay_depth(self, capsule_id: str):
        """
        Reset relay depth for a capsule (use with caution).
        
        Args:
            capsule_id: Capsule identifier
        """
        if capsule_id in self.relay_registry:
            self.relay_registry[capsule_id]['max_depth'] = 0
            self.relay_registry[capsule_id]['relay_count'] = 0
            self._save_relay_registry()
            logger.info(f"[🔄] Reset relay depth for {capsule_id}")
    
    def _load_relay_registry(self) -> Dict[str, Any]:
        """Load relay registry from disk"""
        try:
            if os.path.exists(self.relay_registry_path):
                with open(self.relay_registry_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Error loading relay registry: {e}")
            return {}
    
    def _save_relay_registry(self):
        """Save relay registry to disk"""
        try:
            registry_data = {
                'version': '1.0.0',
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'max_relay_depth': self.max_relay_depth,
                'capsules': self.relay_registry
            }
            
            with open(self.relay_registry_path, 'w', encoding='utf-8') as f:
                json.dump(registry_data, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Error saving relay registry: {e}")
    
    def get_all_relay_info(self) -> Dict[str, Any]:
        """
        Get relay information for all capsules.
        
        Returns:
            Dictionary with all relay information
        """
        return {
            'max_relay_depth': self.max_relay_depth,
            'total_capsules': len(self.relay_registry),
            'capsules': self.relay_registry
        }

# Global instance
_time_relay_instance: Optional[TimeRelayEngine] = None

def get_time_relay_engine(vault_path: str = None) -> TimeRelayEngine:
    """Get or create global Time Relay Engine instance"""
    global _time_relay_instance
    if _time_relay_instance is None:
        _time_relay_instance = TimeRelayEngine(vault_path=vault_path)
    return _time_relay_instance

if __name__ == "__main__":
    # Example usage
    print("⏱️ Time Relay Engine")
    print("=" * 50)
    
    engine = TimeRelayEngine()
    
    # Check if capsule can be relayed
    capsule_id = "nova-001"
    if engine.can_relay(capsule_id):
        print(f"\n[✅] Can relay capsule: {capsule_id}")
        
        # Relay capsule
        relayed = engine.relay_capsule(
            capsule_id=capsule_id,
            narrative_time=10,
            replay_mode="flashback"
        )
        
        if relayed:
            print(f"[✅] Capsule relayed successfully")
            print(f"   Relay depth: {relayed.get('relayDepth')}")
            print(f"   Narrative index: {relayed.get('narrativeIndex')}")
    else:
        print(f"\n[⚠️] Cannot relay capsule: {capsule_id} (max depth exceeded)")

