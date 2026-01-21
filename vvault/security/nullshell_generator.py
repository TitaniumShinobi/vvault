#!/usr/bin/env python3
"""
NULLSHELL Generator - Fallback Shell Generator for Failed Construct Restoration

Generates minimal "NULLSHELL" capsules when construct restoration fails.
Provides empty construct boot state for graceful degradation.

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import os
import json
import hashlib
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from pathlib import Path

from capsuleforge import CapsuleForge, CapsuleData, CapsuleMetadata, PersonalityProfile, MemorySnapshot, EnvironmentalState, AdditionalDataFields

logger = logging.getLogger(__name__)

class NULLSHELLGenerator:
    """
    Generator for NULLSHELL fallback capsules
    
    Creates minimal construct state when restoration fails or
    when construct integrity cannot be verified.
    """
    
    def __init__(self, vault_path: str = None):
        """
        Initialize NULLSHELL generator
        
        Args:
            vault_path: Path to VVAULT directory
        """
        self.vault_path = vault_path or os.path.dirname(os.path.abspath(__file__))
        self.capsule_forge = CapsuleForge(vault_path=self.vault_path)
        self.nullshell_dir = os.path.join(self.vault_path, "capsules", "nullshell")
        os.makedirs(self.nullshell_dir, exist_ok=True)
        
        logger.info("[🔧] NULLSHELL Generator initialized")
    
    def generate_nullshell(
        self,
        construct_name: str,
        reason: str = "Restoration failed",
        metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Generate NULLSHELL fallback capsule
        
        Args:
            construct_name: Name of construct
            reason: Reason for NULLSHELL generation
            metadata: Additional metadata
            
        Returns:
            Dictionary with capsule information
        """
        try:
            logger.warning(f"[⚠️] Generating NULLSHELL for {construct_name}: {reason}")
            
            # Create minimal capsule data
            capsule_uuid = str(uuid.uuid4())
            timestamp = datetime.now(timezone.utc).isoformat()
            
            # Minimal metadata
            capsule_metadata = CapsuleMetadata(
                instance_name=construct_name,
                uuid=capsule_uuid,
                timestamp=timestamp,
                fingerprint_hash="",  # Will be calculated
                tether_signature="NULLSHELL-FALLBACK",
                capsule_version="1.0.0-nullshell",
                generator="NULLSHELLGenerator",
                vault_source="VVAULT-NULLSHELL"
            )
            
            # Empty personality profile
            personality = PersonalityProfile(
                personality_type="UNKNOWN",
                mbti_breakdown={},
                big_five_traits={},
                emotional_baseline={},
                cognitive_biases=[],
                communication_style={}
            )
            
            # Empty memory snapshot
            memory = MemorySnapshot(
                short_term_memories=[],
                long_term_memories=[],
                emotional_memories=[],
                procedural_memories=[],
                episodic_memories=[],
                memory_count=0,
                last_memory_timestamp=timestamp
            )
            
            # Minimal environment state
            environment = EnvironmentalState(
                system_info={"nullshell": True},
                runtime_environment={"fallback": True},
                active_processes=[],
                network_connections=[],
                hardware_fingerprint={}
            )
            
            # NULLSHELL-specific additional data
            additional_data = AdditionalDataFields(
                identity={"status": "nullshell", "confidence": 0.0},
                tether={"strength": 0.0, "type": "nullshell"},
                sigil={"active": False, "pattern": "nullshell"},
                continuity={
                    "checkpoint": "nullshell",
                    "version": "1.0-nullshell",
                    "reason": reason,
                    "metadata": metadata or {}
                }
            )
            
            # Create capsule data
            capsule_data = CapsuleData(
                metadata=capsule_metadata,
                traits={},
                personality=personality,
                memory=memory,
                environment=environment,
                additional_data=additional_data
            )
            
            # Calculate fingerprint
            capsule_dict = self.capsule_forge._capsule_to_dict_for_comparison(capsule_data)
            fingerprint = self.capsule_forge.calculate_fingerprint(capsule_dict)
            capsule_data.metadata.fingerprint_hash = fingerprint
            
            # Generate filename
            clean_timestamp = timestamp.replace(':', '-').replace('.', '-')
            filename = f"{construct_name}_nullshell_{clean_timestamp}.capsule"
            filepath = os.path.join(self.nullshell_dir, filename)
            
            # Save capsule
            self.capsule_forge._save_capsule(capsule_data, filepath)
            
            logger.info(f"[✅] NULLSHELL generated: {filename}")
            logger.info(f"   Fingerprint: {fingerprint[:16]}...")
            logger.info(f"   Reason: {reason}")
            
            return {
                "success": True,
                "construct_name": construct_name,
                "capsule_path": filepath,
                "fingerprint": fingerprint,
                "uuid": capsule_uuid,
                "timestamp": timestamp,
                "reason": reason,
                "nullshell": True
            }
            
        except Exception as e:
            logger.error(f"[❌] NULLSHELL generation failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "nullshell": True
            }
    
    def generate_boot_nullshell(self, construct_name: str) -> Dict[str, Any]:
        """
        Generate NULLSHELL for initial boot when no capsule exists
        
        Args:
            construct_name: Name of construct
            
        Returns:
            NULLSHELL capsule information
        """
        return self.generate_nullshell(
            construct_name=construct_name,
            reason="No existing capsule found - initial NULLSHELL boot",
            metadata={"boot_type": "initial"}
        )
    
    def generate_corruption_nullshell(
        self,
        construct_name: str,
        corruption_details: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate NULLSHELL after corruption detection
        
        Args:
            construct_name: Name of construct
            corruption_details: Corruption detection details
            
        Returns:
            NULLSHELL capsule information
        """
        return self.generate_nullshell(
            construct_name=construct_name,
            reason="Corruption detected - NULLSHELL fallback",
            metadata={
                "corruption": True,
                "corruption_details": corruption_details
            }
        )
    
    def generate_restoration_failure_nullshell(
        self,
        construct_name: str,
        restoration_error: str
    ) -> Dict[str, Any]:
        """
        Generate NULLSHELL after restoration failure
        
        Args:
            construct_name: Name of construct
            restoration_error: Restoration error message
            
        Returns:
            NULLSHELL capsule information
        """
        return self.generate_nullshell(
            construct_name=construct_name,
            reason=f"Restoration failed: {restoration_error}",
            metadata={
                "restoration_failure": True,
                "error": restoration_error
            }
        )

def generate_nullshell_cli(construct_name: str, reason: str = None):
    """
    CLI utility to generate NULLSHELL capsule
    
    Usage:
        python -m nullshell_generator Nova "Corruption detected"
    """
    generator = NULLSHELLGenerator()
    
    if reason:
        result = generator.generate_nullshell(construct_name, reason=reason)
    else:
        result = generator.generate_boot_nullshell(construct_name)
    
    if result.get('success'):
        print(f"[✅] NULLSHELL generated for {construct_name}")
        print(f"   Path: {result['capsule_path']}")
        print(f"   Fingerprint: {result['fingerprint'][:16]}...")
    else:
        print(f"[❌] NULLSHELL generation failed: {result.get('error')}")

if __name__ == "__main__":
    import sys
    construct_name = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    reason = sys.argv[2] if len(sys.argv) > 2 else None
    generate_nullshell_cli(construct_name, reason)

