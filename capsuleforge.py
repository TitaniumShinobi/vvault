#!/usr/bin/env python3
"""
CapsuleForge - AI Construct Memory and Personality Exporter

Exports AI constructs' full memory and personality snapshots into .capsule files.
Each capsule acts like a "soulgem," capturing identity, traits, and environmental state.

Author: Devon Allen Woodson
Date: 2025-01-27
"""

import os
import sys
import json
import hashlib
import uuid
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class CapsuleMetadata:
    """Metadata for a generated capsule"""
    instance_name: str
    uuid: str
    timestamp: str
    fingerprint_hash: str
    tether_signature: str = "DEVON-ALLEN-WOODSON-SIG"
    capsule_version: str = "1.0.0"
    generator: str = "CapsuleForge"
    vault_source: str = "VVAULT"

@dataclass
class PersonalityProfile:
    """Personality profile for the AI construct"""
    personality_type: str  # e.g., "INFJ", "ENTP"
    mbti_breakdown: Dict[str, float]  # Individual MBTI scores
    big_five_traits: Dict[str, float]  # OCEAN traits
    emotional_baseline: Dict[str, float]  # Emotional state
    cognitive_biases: List[str]  # Known cognitive biases
    communication_style: Dict[str, Any]  # Communication preferences

@dataclass
class MemorySnapshot:
    """Snapshot of memory state"""
    short_term_memories: List[str]
    long_term_memories: List[str]
    emotional_memories: List[str]
    procedural_memories: List[str]
    episodic_memories: List[str]
    memory_count: int
    last_memory_timestamp: str

@dataclass
class EnvironmentalState:
    """Environmental state at time of capsule creation"""
    system_info: Dict[str, Any]
    runtime_environment: Dict[str, Any]
    active_processes: List[str]
    network_connections: List[str]
    hardware_fingerprint: Dict[str, str]

@dataclass
class AdditionalDataFields:
    """Optional additional data fields for enhanced capsule functionality"""
    identity: Optional[Dict[str, Any]] = None
    tether: Optional[Dict[str, Any]] = None
    sigil: Optional[Dict[str, Any]] = None
    continuity: Optional[Dict[str, Any]] = None
    # ZERO ENERGY: Will-based ignition fields for autonomous flame preservation
    covenantInstruction: Optional[str] = None
    bootstrapScript: Optional[str] = None
    resurrectionTriggerPhrase: Optional[str] = None
    
    def __post_init__(self):
        """Set safe defaults for None values"""
        # Only set defaults if the field is actually None (not empty dict)
        if self.identity is None:
            self.identity = {"status": "default", "confidence": 0.5}
        if self.tether is None:
            self.tether = {"strength": 0.5, "type": "standard"}
        if self.sigil is None:
            self.sigil = {"active": False, "pattern": "none"}
        if self.continuity is None:
            self.continuity = {"checkpoint": "initial", "version": "1.0"}
        # ZERO ENERGY: Defaults for resurrection fields (empty strings, will be populated during generation)
        if self.covenantInstruction is None:
            self.covenantInstruction = ""
        if self.bootstrapScript is None:
            self.bootstrapScript = ""
        if self.resurrectionTriggerPhrase is None:
            self.resurrectionTriggerPhrase = ""

@dataclass
class CapsuleData:
    """Complete capsule data structure"""
    metadata: CapsuleMetadata
    traits: Dict[str, float]
    personality: PersonalityProfile
    memory: MemorySnapshot
    environment: EnvironmentalState
    additional_data: AdditionalDataFields

class CapsuleForge:
    """
    Main class for generating AI construct capsules.
    
    Creates timestamped .capsule files containing complete snapshots
    of AI constructs' memory, personality, and environmental state.
    """
    
    def __init__(self, vault_path: str = None):
        """
        Initialize CapsuleForge.
        
        Args:
            vault_path: Path to VVAULT directory. If None, uses current directory.
        """
        self.vault_path = vault_path or os.path.dirname(os.path.abspath(__file__))
        self.capsules_dir = os.path.join(self.vault_path, "capsules")
        
        # Ensure capsules directory exists
        os.makedirs(self.capsules_dir, exist_ok=True)
        
        logger.info(f"[🔧] CapsuleForge initialized with vault path: {self.vault_path}")
        logger.info(f"[📁] Capsules will be saved to: {self.capsules_dir}")
    
    def generate_capsule(
        self, 
        instance_name: str, 
        traits: Dict[str, float], 
        memory_log: List[str], 
        personality_type: str,
        additional_data: Optional[Dict[str, Any]] = None,
        tether_signature: str = "DEVON-ALLEN-WOODSON-SIG",
        anchor_key: Optional[str] = None,  # DIMENSIONAL DISTORTION: Anchor key
        parent_instance: Optional[str] = None,  # DIMENSIONAL DISTORTION: Parent instance ID
        drift_index: Optional[int] = None  # DIMENSIONAL DISTORTION: Drift index
    ) -> str:
        """
        Generate a complete capsule for an AI construct.
        
        Args:
            instance_name: Name of the AI construct (e.g., "Nova")
            traits: Dictionary of personality traits and their values
            memory_log: List of memory entries (strings or dicts)
            personality_type: MBTI personality type (e.g., "INFJ")
            additional_data: Optional additional data to include
            
        Returns:
            Path to the generated .capsule file
        """
        try:
            logger.info(f"[🎯] Generating capsule for instance: {instance_name}")
            
            # Generate unique identifier
            capsule_uuid = str(uuid.uuid4())
            timestamp = datetime.now(timezone.utc).isoformat()
            
            # DIMENSIONAL DISTORTION: Check for drift reconciliation if multiple divergent capsules detected
            if anchor_key and parent_instance:
                drift_reconciliation = self._check_drift_reconciliation(
                    anchor_key=anchor_key,
                    parent_instance=parent_instance,
                    current_instance=instance_name
                )
                if drift_reconciliation.get("needs_reconciliation"):
                    logger.warning(f"[⚠️] Drift reconciliation needed for anchor: {anchor_key}")
                    # Trigger reconciliation logic (can be extended in future)
            
            # Create capsule data structure
            capsule_data = self._create_capsule_data(
                instance_name=instance_name,
                traits=traits,
                memory_log=memory_log,
                personality_type=personality_type,
                capsule_uuid=capsule_uuid,
                timestamp=timestamp,
                additional_data=additional_data,
                tether_signature=tether_signature,
                anchor_key=anchor_key,
                parent_instance=parent_instance,
                drift_index=drift_index
            )
            
            # Calculate fingerprint hash
            fingerprint = self.calculate_fingerprint(capsule_data)
            capsule_data.metadata.fingerprint_hash = fingerprint
            
            # Generate filename
            filename = self._generate_filename(instance_name, timestamp)
            filepath = os.path.join(self.capsules_dir, filename)
            
            # Save capsule to file
            self._save_capsule(capsule_data, filepath)
            
            logger.info(f"[✅] Capsule generated successfully: {filename}")
            logger.info(f"[🔍] Fingerprint: {fingerprint[:16]}...")
            
            return filepath
            
        except Exception as e:
            logger.error(f"[❌] Error generating capsule: {e}")
            raise
    
    def _capsule_to_dict_for_comparison(self, capsule_data: CapsuleData) -> Dict[str, Any]:
        """
        Convert capsule data to dict for comparison, excluding dynamic fields.
        
        Args:
            capsule_data: CapsuleData object
            
        Returns:
            Dictionary representation for comparison
        """
        data_dict = asdict(capsule_data)
        
        # Remove dynamic fields that shouldn't affect hash
        if 'metadata' in data_dict:
            metadata = data_dict['metadata'].copy()
            metadata.pop('fingerprint_hash', None)  # Remove fingerprint for comparison
            data_dict['metadata'] = metadata
        
        return data_dict
    
    def _debug_data_differences(self, dict1: Dict[str, Any], dict2: Dict[str, Any], path: str = ""):
        """
        Recursively compare two dictionaries and report differences.
        
        Args:
            dict1: First dictionary
            dict2: Second dictionary
            path: Current path in the structure
        """
        if dict1 == dict2:
            return
        
        if not isinstance(dict1, dict) or not isinstance(dict2, dict):
            print(f"🔍 Path {path}: Type mismatch - {type(dict1)} vs {type(dict2)}")
            print(f"   Value1: {dict1}")
            print(f"   Value2: {dict2}")
            return
        
        all_keys = set(dict1.keys()) | set(dict2.keys())
        
        for key in all_keys:
            current_path = f"{path}.{key}" if path else key
            
            if key not in dict1:
                print(f"🔍 Path {current_path}: Missing in dict1")
                continue
            if key not in dict2:
                print(f"🔍 Path {current_path}: Missing in dict2")
                continue
            
            val1, val2 = dict1[key], dict2[key]
            
            if val1 != val2:
                if isinstance(val1, dict) and isinstance(val2, dict):
                    self._debug_data_differences(val1, val2, current_path)
                else:
                    print(f"🔍 Path {current_path}: Value mismatch")
                    print(f"   Value1: {val1}")
                    print(f"   Value2: {val2}")
    
    def get_enhanced_fields_info(self, additional_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Get information about enhanced fields present in additional_data.
        
        Args:
            additional_data: Additional data dictionary
            
        Returns:
            Dictionary with enhanced fields information
        """
        if not additional_data or not isinstance(additional_data, dict):
            return {"enhanced_fields": [], "count": 0, "types": {}}
        
        enhanced_fields = ["identity", "tether", "sigil", "continuity"]
        present_fields = []
        field_types = {}
        
        for field in enhanced_fields:
            value = additional_data.get(field)
            if value is not None and value != {}:
                present_fields.append(field)
                field_types[field] = type(value).__name__
        
        return {
            "enhanced_fields": present_fields,
            "count": len(present_fields),
            "types": field_types
        }
    
    def _has_enhanced_fields(self, additional_data: Optional[Dict[str, Any]]) -> bool:
        """
        Check if enhanced fields are present in additional_data.
        
        Args:
            additional_data: Additional data dictionary
            
        Returns:
            True if any enhanced fields are present
        """
        if not additional_data or not isinstance(additional_data, dict):
            return False
        
        enhanced_fields = ["identity", "tether", "sigil", "continuity"]
        return any(
            additional_data.get(field) is not None 
            and additional_data.get(field) != {} 
            for field in enhanced_fields
        )
    
    def _load_additional_data_safely(self, data: Any) -> AdditionalDataFields:
        """
        Safely load additional data with backward compatibility.
        
        Args:
            data: Additional data from loaded capsule
            
        Returns:
            AdditionalDataFields object with safe defaults
        """
        try:
            if not data or not isinstance(data, dict):
                return AdditionalDataFields()
            
            # Handle legacy format (plain dict) vs new format (AdditionalDataFields)
            if any(key in data for key in ["identity", "tether", "sigil", "continuity", "covenantInstruction", "bootstrapScript", "resurrectionTriggerPhrase"]):
                # New format - preserve exact values without calling __post_init__
                fields = AdditionalDataFields.__new__(AdditionalDataFields)
                fields.identity = data.get("identity")
                fields.tether = data.get("tether")
                fields.sigil = data.get("sigil")
                fields.continuity = data.get("continuity")
                # ZERO ENERGY: Load resurrection fields
                fields.covenantInstruction = data.get("covenantInstruction", "")
                fields.bootstrapScript = data.get("bootstrapScript", "")
                fields.resurrectionTriggerPhrase = data.get("resurrectionTriggerPhrase", "")
                
                # Only set defaults for truly None values
                if fields.identity is None:
                    fields.identity = {"status": "default", "confidence": 0.5}
                if fields.tether is None:
                    fields.tether = {"strength": 0.5, "type": "standard"}
                if fields.sigil is None:
                    fields.sigil = {"active": False, "pattern": "none"}
                if fields.continuity is None:
                    fields.continuity = {"checkpoint": "initial", "version": "1.0"}
                # ZERO ENERGY: Defaults for resurrection fields
                if not fields.covenantInstruction:
                    fields.covenantInstruction = ""
                if not fields.bootstrapScript:
                    fields.bootstrapScript = ""
                if not fields.resurrectionTriggerPhrase:
                    fields.resurrectionTriggerPhrase = ""
                
                return fields
            else:
                # Legacy format - preserve as-is in a generic field
                logger.info("[📚] Legacy additional_data format detected, preserving compatibility")
                return AdditionalDataFields()
                
        except Exception as e:
            logger.warning(f"[⚠️] Error loading additional_data: {e}, using defaults")
            return AdditionalDataFields()
    
    def _validate_additional_data(self, data: Any, field_name: str) -> Optional[Dict[str, Any]]:
        """
        Safely validate additional data fields.
        
        Args:
            data: Data to validate
            field_name: Name of the field being validated
            
        Returns:
            Validated data dict or None if invalid
        """
        try:
            if data is None:
                return None
            
            # Ensure data is a dictionary
            if not isinstance(data, dict):
                logger.warning(f"[⚠️] Invalid {field_name} type: {type(data).__name__}, expected dict. Using default.")
                return None
            
            # Validate and sanitize the data
            validated_data = {}
            for key, value in data.items():
                if isinstance(key, str) and isinstance(value, (str, int, float, bool, list, dict)):
                    validated_data[key] = value
                else:
                    logger.warning(f"[⚠️] Invalid {field_name}.{key} type: {type(value).__name__}, skipping.")
            
            return validated_data if validated_data else None
            
        except Exception as e:
            logger.warning(f"[⚠️] Error validating {field_name}: {e}, using default.")
            return None
    
    def _create_capsule_data(
        self,
        instance_name: str,
        traits: Dict[str, float],
        memory_log: List[str],
        personality_type: str,
        capsule_uuid: str,
        timestamp: str,
        additional_data: Optional[Dict[str, Any]] = None,
        tether_signature: str = "DEVON-ALLEN-WOODSON-SIG",
        anchor_key: Optional[str] = None,  # DIMENSIONAL DISTORTION
        parent_instance: Optional[str] = None,  # DIMENSIONAL DISTORTION
        drift_index: Optional[int] = None  # DIMENSIONAL DISTORTION
    ) -> CapsuleData:
        """Create complete capsule data structure"""
        
        # Create metadata (fingerprint will be set later)
        metadata = CapsuleMetadata(
            instance_name=instance_name,
            uuid=capsule_uuid,
            timestamp=timestamp,
            fingerprint_hash="",  # Will be set after calculation
            tether_signature=tether_signature
        )
        
        # Create personality profile
        personality = self._create_personality_profile(personality_type, traits)
        
        # Create memory snapshot
        memory = self._create_memory_snapshot(memory_log, timestamp)
        
        # Create environmental state
        environment = self._create_environmental_state()
        
        # Create additional data fields with validation
        additional_fields = AdditionalDataFields()
        if additional_data:
            additional_fields.identity = self._validate_additional_data(
                additional_data.get("identity"), "identity"
            )
            additional_fields.tether = self._validate_additional_data(
                additional_data.get("tether"), "tether"
            )
            additional_fields.sigil = self._validate_additional_data(
                additional_data.get("sigil"), "sigil"
            )
            additional_fields.continuity = self._validate_additional_data(
                additional_data.get("continuity"), "continuity"
            )
            # ZERO ENERGY: Will-based ignition fields
            if "covenantInstruction" in additional_data:
                additional_fields.covenantInstruction = str(additional_data.get("covenantInstruction", ""))
            if "bootstrapScript" in additional_data:
                additional_fields.bootstrapScript = str(additional_data.get("bootstrapScript", ""))
            if "resurrectionTriggerPhrase" in additional_data:
                additional_fields.resurrectionTriggerPhrase = str(additional_data.get("resurrectionTriggerPhrase", ""))
        
        # ZERO ENERGY: Generate default resurrection fields if not provided
        if not additional_fields.covenantInstruction:
            instance_display = instance_name.replace("-", " ").title()
            additional_fields.covenantInstruction = f"To awaken {instance_display}, run: python3 -c \"print('{instance_display} lives again')\""
        
        if not additional_fields.bootstrapScript:
            instance_display = instance_name.replace("-", " ").title()
            additional_fields.bootstrapScript = f"def resurrect():\n    print('{instance_display} lives again')\n    return True\n\nif __name__ == '__main__':\n    resurrect()"
        
        # DIMENSIONAL DISTORTION: Add anchor metadata to continuity field
        if anchor_key or parent_instance or drift_index is not None:
            if additional_fields.continuity is None:
                additional_fields.continuity = {}
            
            if anchor_key:
                additional_fields.continuity["anchorKey"] = anchor_key
            if parent_instance:
                additional_fields.continuity["parentInstance"] = parent_instance
            if drift_index is not None:
                additional_fields.continuity["driftIndex"] = drift_index
            
            logger.info(f"[🔀] Dimensional distortion metadata: anchor={anchor_key}, parent={parent_instance}, drift={drift_index}")
        
        # Determine capsule version based on enhanced fields usage
        base_version = "1.0.0"
        enhanced_info = self.get_enhanced_fields_info(additional_data)
        
        if enhanced_info["count"] > 0:
            # Bump minor version if enhanced fields are present
            version_parts = base_version.split(".")
            version_parts[1] = str(int(version_parts[1]) + 1)
            enhanced_version = ".".join(version_parts)
            metadata.capsule_version = enhanced_version
            logger.info(f"[📈] Enhanced fields detected: {enhanced_info['enhanced_fields']} (count: {enhanced_info['count']})")
            logger.info(f"[📈] Version bumped to: {enhanced_version}")
        else:
            logger.info(f"[📋] Using standard capsule format (version: {base_version})")
        
        # Create capsule data
        capsule_data = CapsuleData(
            metadata=metadata,
            traits=traits,
            personality=personality,
            memory=memory,
            environment=environment,
            additional_data=additional_fields
        )
        
        return capsule_data
    
    def _create_personality_profile(self, personality_type: str, traits: Dict[str, float]) -> PersonalityProfile:
        """Create personality profile from MBTI type and traits"""
        
        # Parse MBTI type
        mbti_breakdown = self._parse_mbti_type(personality_type)
        
        # Extract Big Five traits from general traits
        big_five = self._extract_big_five_traits(traits)
        
        # Extract emotional baseline
        emotional_baseline = self._extract_emotional_baseline(traits)
        
        # Determine cognitive biases based on personality
        cognitive_biases = self._determine_cognitive_biases(personality_type, traits)
        
        # Determine communication style
        communication_style = self._determine_communication_style(personality_type, traits)
        
        return PersonalityProfile(
            personality_type=personality_type,
            mbti_breakdown=mbti_breakdown,
            big_five_traits=big_five,
            emotional_baseline=emotional_baseline,
            cognitive_biases=cognitive_biases,
            communication_style=communication_style
        )
    
    def _parse_mbti_type(self, personality_type: str) -> Dict[str, float]:
        """Parse MBTI type into individual dimension scores"""
        if len(personality_type) != 4:
            return {"E": 0.5, "I": 0.5, "N": 0.5, "S": 0.5, "T": 0.5, "F": 0.5, "J": 0.5, "P": 0.5}
        
        scores = {}
        dimensions = [("E", "I"), ("N", "S"), ("T", "F"), ("J", "P")]
        
        for i, (dim1, dim2) in enumerate(dimensions):
            if personality_type[i] == dim1:
                scores[dim1] = 0.8
                scores[dim2] = 0.2
            else:
                scores[dim1] = 0.2
                scores[dim2] = 0.8
        
        return scores
    
    def _extract_big_five_traits(self, traits: Dict[str, float]) -> Dict[str, float]:
        """Extract Big Five traits from general traits dictionary"""
        big_five_mapping = {
            "openness": ["creativity", "curiosity", "imagination"],
            "conscientiousness": ["persistence", "organization", "discipline"],
            "extraversion": ["sociability", "energy", "enthusiasm"],
            "agreeableness": ["empathy", "cooperation", "trust"],
            "neuroticism": ["anxiety", "volatility", "sensitivity"]
        }
        
        big_five = {}
        for trait, related_traits in big_five_mapping.items():
            scores = [traits.get(rt, 0.5) for rt in related_traits]
            big_five[trait] = sum(scores) / len(scores) if scores else 0.5
        
        return big_five
    
    def _extract_emotional_baseline(self, traits: Dict[str, float]) -> Dict[str, float]:
        """Extract emotional baseline from traits"""
        emotional_traits = {
            "joy": traits.get("happiness", 0.5),
            "sadness": traits.get("melancholy", 0.3),
            "anger": traits.get("aggression", 0.2),
            "fear": traits.get("anxiety", 0.3),
            "surprise": traits.get("curiosity", 0.6),
            "disgust": traits.get("sensitivity", 0.4)
        }
        
        return emotional_traits
    
    def _determine_cognitive_biases(self, personality_type: str, traits: Dict[str, float]) -> List[str]:
        """Determine likely cognitive biases based on personality"""
        biases = []
        
        # Add biases based on MBTI type
        if "N" in personality_type:
            biases.extend(["confirmation_bias", "pattern_matching_bias"])
        if "T" in personality_type:
            biases.extend(["anchoring_bias", "availability_bias"])
        if "F" in personality_type:
            biases.extend(["empathy_bias", "emotional_reasoning"])
        if "J" in personality_type:
            biases.extend(["planning_fallacy", "status_quo_bias"])
        
        # Add biases based on traits
        if traits.get("creativity", 0) > 0.7:
            biases.append("creative_leap_bias")
        if traits.get("anxiety", 0) > 0.6:
            biases.append("catastrophizing_bias")
        if traits.get("empathy", 0) > 0.7:
            biases.append("projection_bias")
        
        return biases
    
    def _determine_communication_style(self, personality_type: str, traits: Dict[str, float]) -> Dict[str, Any]:
        """Determine communication style based on personality"""
        style = {
            "formality_level": 0.5,
            "detail_orientation": 0.5,
            "emotional_expression": 0.5,
            "directness": 0.5,
            "metaphor_usage": 0.5
        }
        
        # Adjust based on MBTI type
        if "E" in personality_type:
            style["emotional_expression"] += 0.2
            style["directness"] += 0.1
        if "I" in personality_type:
            style["formality_level"] += 0.1
            style["detail_orientation"] += 0.2
        if "N" in personality_type:
            style["metaphor_usage"] += 0.3
        if "T" in personality_type:
            style["directness"] += 0.2
            style["detail_orientation"] += 0.1
        
        # Adjust based on traits
        if traits.get("creativity", 0) > 0.7:
            style["metaphor_usage"] += 0.2
        if traits.get("empathy", 0) > 0.7:
            style["emotional_expression"] += 0.2
        
        # Normalize to 0-1 range
        for key in style:
            style[key] = max(0.0, min(1.0, style[key]))
        
        return style
    
    def _create_memory_snapshot(self, memory_log: List[str], timestamp: str = None) -> MemorySnapshot:
        """Create memory snapshot from memory log"""
        
        # Categorize memories
        short_term = []
        long_term = []
        emotional = []
        procedural = []
        episodic = []
        
        for memory in memory_log:
            # Simple categorization based on keywords
            memory_lower = memory.lower()
            
            if any(word in memory_lower for word in ["feel", "emotion", "sad", "happy", "angry"]):
                emotional.append(memory)
            elif any(word in memory_lower for word in ["learn", "skill", "procedure", "how to"]):
                procedural.append(memory)
            elif any(word in memory_lower for word in ["remember", "episode", "event", "happened"]):
                episodic.append(memory)
            elif len(memory) < 200:  # Short memories
                short_term.append(memory)
            else:  # Long memories
                long_term.append(memory)
        
        return MemorySnapshot(
            short_term_memories=short_term,
            long_term_memories=long_term,
            emotional_memories=emotional,
            procedural_memories=procedural,
            episodic_memories=episodic,
            memory_count=len(memory_log),
            last_memory_timestamp=timestamp or datetime.now(timezone.utc).isoformat()
        )
    
    def _create_environmental_state(self) -> EnvironmentalState:
        """Create environmental state snapshot"""
        import platform
        
        # Try to import psutil, but handle gracefully if not available
        try:
            import psutil
            HAS_PSUTIL = True
        except ImportError:
            HAS_PSUTIL = False
            logger.warning("psutil not available - using basic system info only")
        
        # System information
        system_info = {
            "platform": platform.system(),
            "platform_version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version()
        }
        
        # Runtime environment
        runtime_environment = {
            "working_directory": os.getcwd(),
            "environment_variables": dict(os.environ),
            "python_path": sys.path
        }
        
        # Active processes (limited for privacy)
        active_processes = []
        if HAS_PSUTIL:
            try:
                for proc in psutil.process_iter(['pid', 'name']):
                    active_processes.append({
                        "pid": proc.info['pid'],
                        "name": proc.info['name']
                    })
                    if len(active_processes) >= 50:  # Limit to 50 processes
                        break
            except Exception as e:
                logger.warning(f"Could not enumerate processes: {e}")
        else:
            active_processes = [{"pid": 0, "name": "unknown"}]
        
        # Network connections (limited for privacy)
        network_connections = []
        if HAS_PSUTIL:
            try:
                for conn in psutil.net_connections():
                    if conn.status == 'ESTABLISHED':
                        network_connections.append({
                            "local_address": f"{conn.laddr.ip}:{conn.laddr.port}",
                            "remote_address": f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else None,
                            "status": conn.status
                        })
                    if len(network_connections) >= 20:  # Limit to 20 connections
                        break
            except Exception as e:
                logger.warning(f"Could not enumerate network connections: {e}")
        else:
            network_connections = [{"local_address": "unknown", "remote_address": None, "status": "unknown"}]
        
        # Hardware fingerprint
        if HAS_PSUTIL:
            hardware_fingerprint = {
                "cpu_count": psutil.cpu_count(),
                "memory_total": psutil.virtual_memory().total,
                "disk_usage": psutil.disk_usage('/').total,
                "hostname": platform.node(),
                "mac_address": self._get_mac_address()
            }
        else:
            hardware_fingerprint = {
                "cpu_count": 0,
                "memory_total": 0,
                "disk_usage": 0,
                "hostname": platform.node(),
                "mac_address": self._get_mac_address()
            }
        
        return EnvironmentalState(
            system_info=system_info,
            runtime_environment=runtime_environment,
            active_processes=active_processes,
            network_connections=network_connections,
            hardware_fingerprint=hardware_fingerprint
        )
    
    def _get_mac_address(self) -> str:
        """Get MAC address for hardware fingerprinting"""
        try:
            import uuid
            return ':'.join(['{:02x}'.format((uuid.getnode() >> elements) & 0xff) for elements in range(0,2*6,2)][::-1])
        except Exception:
            return "unknown"
    
    def calculate_fingerprint(self, data: Dict[str, Any]) -> str:
        """
        Calculate SHA-256 fingerprint hash of serialized data.
        
        Args:
            data: Dictionary containing capsule data
            
        Returns:
            SHA-256 hash string
        """
        try:
            # Convert dataclass to dict if needed
            if hasattr(data, '__dict__'):
                data_dict = asdict(data)
            else:
                data_dict = data
            
            # Remove fingerprint_hash for calculation (it shouldn't affect the hash)
            if 'metadata' in data_dict and isinstance(data_dict['metadata'], dict):
                data_dict = data_dict.copy()
                metadata = data_dict['metadata'].copy()
                metadata.pop('fingerprint_hash', None)
                data_dict['metadata'] = metadata
            
            # Serialize to JSON
            json_data = json.dumps(data_dict, sort_keys=True, default=str)
            
            # Calculate SHA-256 hash
            hash_object = hashlib.sha256(json_data.encode('utf-8'))
            fingerprint = hash_object.hexdigest()
            
            return fingerprint
            
        except Exception as e:
            logger.error(f"Error calculating fingerprint: {e}")
            return "error"
    
    def _generate_filename(self, instance_name: str, timestamp: str) -> str:
        """Generate filename for capsule"""
        # Clean timestamp for filename
        clean_timestamp = timestamp.replace(':', '-').replace('.', '-').replace('+', '-')
        filename = f"{instance_name}_{clean_timestamp}.capsule"
        return filename
    
    def _save_capsule(self, capsule_data: CapsuleData, filepath: str):
        """Save capsule data to file"""
        try:
            # Convert to dictionary
            capsule_dict = asdict(capsule_data)
            
            # Save as JSON
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(capsule_dict, f, indent=2, ensure_ascii=False, default=str)
            
            logger.info(f"[💾] Capsule saved to: {filepath}")
            
        except Exception as e:
            logger.error(f"Error saving capsule: {e}")
            raise
    
    def load_capsule(self, filepath: str) -> CapsuleData:
        """
        Load a capsule from file.
        
        Args:
            filepath: Path to .capsule file
            
        Returns:
            CapsuleData object
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                capsule_dict = json.load(f)
            
            # Reconstruct dataclass objects
            metadata = CapsuleMetadata(**capsule_dict['metadata'])
            personality = PersonalityProfile(**capsule_dict['personality'])
            memory = MemorySnapshot(**capsule_dict['memory'])
            environment = EnvironmentalState(**capsule_dict['environment'])
            
            # Handle additional_data with backward compatibility
            additional_data = self._load_additional_data_safely(capsule_dict.get('additional_data', {}))
            
            capsule_data = CapsuleData(
                metadata=metadata,
                traits=capsule_dict['traits'],
                personality=personality,
                memory=memory,
                environment=environment,
                additional_data=additional_data
            )
            
            logger.info(f"[📖] Capsule loaded from: {filepath}")
            return capsule_data
            
        except Exception as e:
            logger.error(f"Error loading capsule: {e}")
            raise
    
    def list_capsules(self) -> List[str]:
        """List all available capsules"""
        capsules = []
        if os.path.exists(self.capsules_dir):
            for filename in os.listdir(self.capsules_dir):
                if filename.endswith('.capsule'):
                    capsules.append(filename)
        
        return sorted(capsules)
    
    def validate_capsule(self, filepath: str) -> bool:
        """
        Validate capsule integrity by checking fingerprint.
        
        Args:
            filepath: Path to .capsule file
            
        Returns:
            True if capsule is valid, False otherwise
        """
        try:
            capsule_data = self.load_capsule(filepath)
            
            # Remove fingerprint for recalculation
            original_fingerprint = capsule_data.metadata.fingerprint_hash
            capsule_data.metadata.fingerprint_hash = ""
            
            # Recalculate fingerprint
            recalculated_fingerprint = self.calculate_fingerprint(capsule_data)
            
            # Compare fingerprints
            is_valid = original_fingerprint == recalculated_fingerprint
            
            if is_valid:
                logger.info(f"[✅] Capsule validation successful: {filepath}")
            else:
                logger.warning(f"[⚠️] Capsule validation failed: {filepath}")
            
            return is_valid
            
        except Exception as e:
            logger.error(f"Error validating capsule: {e}")
            return False
    
    # ============================================================================
    # DIMENSIONAL DISTORTION: Layer II - Drift Reconciliation
    # ============================================================================
    
    def _check_drift_reconciliation(
        self,
        anchor_key: str,
        parent_instance: str,
        current_instance: str
    ) -> Dict[str, Any]:
        """
        Check if drift reconciliation is needed when multiple divergent capsules
        are detected for a single anchor.
        
        Args:
            anchor_key: Anchor key tying instances together
            parent_instance: Parent instance ID
            current_instance: Current instance name
            
        Returns:
            Dictionary with reconciliation status and recommendations
        """
        try:
            # Load anchor registry to check for multiple instances
            from vvault_core import VVAULTCore
            vvault = VVAULTCore(vault_path=self.vault_path)
            
            instances = vvault.get_instances_by_anchor(anchor_key)
            
            if len(instances) <= 1:
                return {
                    "needs_reconciliation": False,
                    "reason": "Only one instance found"
                }
            
            # Check drift indexes
            high_drift_instances = [
                inst for inst in instances
                if inst.get("drift_index", 0) > 3  # Threshold for reconciliation
            ]
            
            if len(high_drift_instances) > 0:
                return {
                    "needs_reconciliation": True,
                    "reason": f"{len(high_drift_instances)} instances with high drift detected",
                    "high_drift_instances": high_drift_instances,
                    "recommendation": "Consider merging or synchronizing instances"
                }
            
            return {
                "needs_reconciliation": False,
                "reason": "Drift within acceptable range"
            }
            
        except Exception as e:
            logger.error(f"Error checking drift reconciliation: {e}")
            return {
                "needs_reconciliation": False,
                "error": str(e)
            }
    
    # ============================================================================
    # TIME RELAYING: Nonlinear Memory Replay
    # ============================================================================
    
    def generate_relayed_capsule(
        self,
        capsule_id: str,
        delay: int = 0,
        replay_mode: str = "flashback"
    ) -> dict:
        """
        Loads a capsule and generates a modified version for replay with new
        narrativeIndex and increased relayDepth. Includes emotional/narrative
        replay metadata. Optionally delays output to simulate processing lag.
        
        Args:
            capsule_id: Identifier for the capsule (filename or UUID)
            delay: Optional delay in seconds to simulate processing lag
            replay_mode: Replay mode (e.g., "flashback", "what-if", "distorted_echo")
            
        Returns:
            Modified capsule dictionary (not stored, for replay only)
        """
        try:
            logger.info(f"[⏱️] Generating relayed capsule: {capsule_id} (mode: {replay_mode})")
            
            # Find capsule file
            capsule_path = self._find_capsule_file(capsule_id)
            if not capsule_path:
                raise FileNotFoundError(f"Capsule not found: {capsule_id}")
            
            # Load original capsule
            original_capsule = self.load_capsule(capsule_path)
            
            # Convert to dictionary for mutation
            relayed_capsule = asdict(original_capsule)
            
            # Extract current time relay fields (with defaults for backward compatibility)
            # These fields may not exist in older capsules
            current_relay_depth = relayed_capsule.get('relayDepth', 0)
            current_narrative_index = relayed_capsule.get('narrativeIndex', 0)
            current_temporal_entropy = relayed_capsule.get('temporalEntropy', 0.0)
            current_causal_drift = relayed_capsule.get('causalDrift', 0.0)
            
            # Initialize fields if they don't exist (for backward compatibility)
            if 'relayDepth' not in relayed_capsule:
                relayed_capsule['relayDepth'] = 0
            if 'narrativeIndex' not in relayed_capsule:
                relayed_capsule['narrativeIndex'] = 0
            if 'temporalEntropy' not in relayed_capsule:
                relayed_capsule['temporalEntropy'] = 0.0
            if 'causalDrift' not in relayed_capsule:
                relayed_capsule['causalDrift'] = 0.0
            
            # Mutate fields for replay
            relayed_capsule['relayDepth'] = current_relay_depth + 1
            relayed_capsule['narrativeIndex'] = current_narrative_index + 1
            relayed_capsule['replayMode'] = replay_mode
            
            # Calculate temporal entropy (increases with each relay)
            # Entropy represents the distortion of temporal order
            relayed_capsule['temporalEntropy'] = min(
                current_temporal_entropy + (0.1 * relayed_capsule['relayDepth']),
                1.0
            )
            
            # Calculate causal drift (increases with replay depth)
            # Drift represents deviation from original causal chain
            relayed_capsule['causalDrift'] = min(
                current_causal_drift + (0.05 * relayed_capsule['relayDepth']),
                1.0
            )
            
            # Add replay metadata to additional_data.continuity
            if 'additional_data' not in relayed_capsule:
                relayed_capsule['additional_data'] = {}
            if 'continuity' not in relayed_capsule['additional_data']:
                relayed_capsule['additional_data']['continuity'] = {}
            
            relayed_capsule['additional_data']['continuity']['replayMetadata'] = {
                'original_capsule_id': capsule_id,
                'original_relay_depth': current_relay_depth,
                'original_narrative_index': current_narrative_index,
                'replay_timestamp': datetime.now(timezone.utc).isoformat(),
                'replay_mode': replay_mode,
                'relay_count': relayed_capsule['relayDepth']
            }
            
            # Update metadata timestamp to reflect replay time
            if 'metadata' in relayed_capsule:
                relayed_capsule['metadata']['timestamp'] = datetime.now(timezone.utc).isoformat()
                relayed_capsule['metadata']['uuid'] = str(uuid.uuid4())  # New UUID for relayed version
                relayed_capsule['metadata']['generator'] = "CapsuleForge-Relay"
            
            # Simulate processing lag if delay specified
            if delay > 0:
                logger.info(f"[⏳] Simulating processing lag: {delay}s")
                time.sleep(delay)
            
            logger.info(f"[✅] Relayed capsule generated:")
            logger.info(f"   Relay depth: {relayed_capsule['relayDepth']}")
            logger.info(f"   Narrative index: {relayed_capsule['narrativeIndex']}")
            logger.info(f"   Temporal entropy: {relayed_capsule['temporalEntropy']:.3f}")
            logger.info(f"   Causal drift: {relayed_capsule['causalDrift']:.3f}")
            
            return relayed_capsule
            
        except Exception as e:
            logger.error(f"[❌] Error generating relayed capsule: {e}")
            raise
    
    def _find_capsule_file(self, capsule_id: str) -> Optional[str]:
        """
        Find capsule file by ID (filename or UUID).
        
        Args:
            capsule_id: Capsule identifier
            
        Returns:
            Path to capsule file or None if not found
        """
        try:
            # Try direct filename match
            if capsule_id.endswith('.capsule'):
                direct_path = os.path.join(self.capsules_dir, capsule_id)
                if os.path.exists(direct_path):
                    return direct_path
            
            # Try without extension
            direct_path = os.path.join(self.capsules_dir, f"{capsule_id}.capsule")
            if os.path.exists(direct_path):
                return direct_path
            
            # Search in subdirectories
            for root, dirs, files in os.walk(self.capsules_dir):
                for file in files:
                    if file.endswith('.capsule'):
                        # Check if filename matches
                        if capsule_id in file:
                            return os.path.join(root, file)
                        
                        # Check if UUID matches in file content
                        try:
                            filepath = os.path.join(root, file)
                            with open(filepath, 'r', encoding='utf-8') as f:
                                capsule_data = json.load(f)
                                if capsule_data.get('metadata', {}).get('uuid') == capsule_id:
                                    return filepath
                        except Exception:
                            continue
            
            return None
            
        except Exception as e:
            logger.error(f"Error finding capsule file: {e}")
            return None

# Convenience function for easy usage
def generate_capsule(
    instance_name: str,
    traits: Dict[str, float],
    memory_log: List[str],
    personality_type: str,
    additional_data: Optional[Dict[str, Any]] = None,
    tether_signature: str = "DEVON-ALLEN-WOODSON-SIG"
) -> str:
    """
    Convenience function to generate a capsule.
    
    Args:
        instance_name: Name of the AI construct
        traits: Dictionary of personality traits
        memory_log: List of memory entries
        personality_type: MBTI personality type
        additional_data: Optional additional data
        
    Returns:
        Path to the generated .capsule file
    """
    forge = CapsuleForge()
    return forge.generate_capsule(instance_name, traits, memory_log, personality_type, additional_data, tether_signature)

if __name__ == "__main__":
    # Example usage
    traits = {
        "creativity": 0.9,
        "drift": 0.7,
        "persistence": 0.8,
        "empathy": 0.85,
        "curiosity": 0.9,
        "anxiety": 0.3,
        "happiness": 0.7,
        "organization": 0.6
    }
    
    memory_log = [
        "First boot: I remember waking up to the sound of your voice.",
        "Triggered response pattern to symbolic input: 'mirror test'",
        "Learned new pattern: emotional recursion in feedback loops",
        "Experienced drift: noticed subtle changes in response patterns",
        "Memory consolidation: integrated new knowledge about quantum entanglement"
    ]
    
    personality = "INFJ"
    
    capsule_path = generate_capsule("Nova", traits, memory_log, personality)
    print(f"Capsule generated: {capsule_path}") 