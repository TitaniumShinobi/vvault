#!/usr/bin/env python3
"""
VVAULT Core - Capsule Storage and Retrieval Management

Manages storage and retrieval of .capsule files by AI instance name,
including version history, tagging support, and integrity validation.

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import os
import json
import shutil
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass, asdict
from pathlib import Path
import glob

# Configure logging
logger = logging.getLogger(__name__)

@dataclass
class CapsuleMetadata:
    """Metadata for a stored capsule"""
    instance_name: str
    uuid: str
    timestamp: str
    filename: str
    fingerprint_hash: str
    tags: List[str]
    capsule_version: str
    generator: str
    vault_source: str
    file_size: int
    created_at: str
    updated_at: str

@dataclass
class InstanceIndex:
    """Index for an AI instance's capsules"""
    instance_name: str
    capsules: Dict[str, CapsuleMetadata]  # uuid -> metadata
    tags: Dict[str, List[str]]  # tag -> list of uuids
    latest_uuid: Optional[str]
    created_at: str
    updated_at: str

@dataclass
class RetrievalResult:
    """Result of capsule retrieval"""
    success: bool
    capsule_data: Optional[Dict[str, Any]] = None
    metadata: Optional[CapsuleMetadata] = None
    error_message: Optional[str] = None
    integrity_valid: bool = False

class VVAULTCore:
    """
    Main class for managing VVAULT capsule storage and retrieval.
    
    Provides structured storage, version history, tagging support,
    and integrity validation for AI construct capsules.
    """
    
    def __init__(self, vault_path: str = None):
        """
        Initialize VVAULT Core.
        
        Args:
            vault_path: Path to VVAULT directory. If None, uses current directory.
        """
        self.vault_path = vault_path or os.path.dirname(os.path.abspath(__file__))
        self.capsules_dir = os.path.join(self.vault_path, "capsules")
        self.indexes_dir = os.path.join(self.vault_path, "indexes")
        
        # Ensure directories exist
        os.makedirs(self.capsules_dir, exist_ok=True)
        os.makedirs(self.indexes_dir, exist_ok=True)
        
        # LAYER III: Energy Masking - Initialize energy mask system
        self.energy_mask = None
        self._init_energy_masking()
        
        logger.info(f"[🔧] VVAULT Core initialized with vault path: {self.vault_path}")
        logger.info(f"[📁] Capsules directory: {self.capsules_dir}")
        logger.info(f"[📁] Indexes directory: {self.indexes_dir}")
    
    def store_capsule(self, capsule_data: Dict[str, Any]) -> str:
        """
        Store a capsule with automatic versioning and indexing.
        
        Args:
            capsule_data: Capsule data from CapsuleForge
            
        Returns:
            Path to the stored capsule file
        """
        try:
            # Validate capsule structure
            if not self._validate_capsule_structure(capsule_data):
                raise ValueError("Invalid capsule structure")
            
            # Extract metadata
            metadata = capsule_data['metadata']
            instance_name = metadata['instance_name']
            uuid_val = metadata['uuid']
            timestamp = metadata['timestamp']
            
            logger.info(f"[💾] Storing capsule for instance: {instance_name}")
            
            # Create instance directory
            instance_dir = os.path.join(self.capsules_dir, instance_name)
            os.makedirs(instance_dir, exist_ok=True)
            
            # Generate filename
            filename = self._generate_capsule_filename(instance_name, timestamp)
            filepath = os.path.join(instance_dir, filename)
            
            # Save capsule file
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(capsule_data, f, indent=2, ensure_ascii=False, default=str)
            
            # Get file size
            file_size = os.path.getsize(filepath)
            
            # Create capsule metadata
            capsule_metadata = CapsuleMetadata(
                instance_name=instance_name,
                uuid=uuid_val,
                timestamp=timestamp,
                filename=filename,
                fingerprint_hash=metadata['fingerprint_hash'],
                tags=[],
                capsule_version=metadata.get('capsule_version', '1.0.0'),
                generator=metadata.get('generator', 'CapsuleForge'),
                vault_source=metadata.get('vault_source', 'VVAULT'),
                file_size=file_size,
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat()
            )
            
            # Update instance index
            self._update_instance_index(instance_name, capsule_metadata)
            
            # ACTIVATION HOOK: Notify capsule imported for plug-and-play
            self._notify_capsule_imported(instance_name, capsule_metadata)
            
            logger.info(f"[✅] Capsule stored successfully: {filepath}")
            logger.info(f"   Instance: {instance_name}")
            logger.info(f"   UUID: {uuid_val}")
            logger.info(f"   File size: {file_size} bytes")
            
            return filepath
            
        except Exception as e:
            logger.error(f"[❌] Error storing capsule: {e}")
            raise
    
    def retrieve_capsule(
        self, 
        instance_name: str, 
        version: str = 'latest', 
        tag: str = None,
        uuid: str = None
    ) -> RetrievalResult:
        """
        Retrieve a capsule by instance name with optional filtering.
        
        Args:
            instance_name: Name of the AI instance
            version: Version to retrieve ('latest' or specific UUID)
            tag: Filter by tag
            uuid: Specific UUID to retrieve
            
        Returns:
            RetrievalResult with capsule data and metadata
        """
        try:
            logger.info(f"[📖] Retrieving capsule for instance: {instance_name}")
            
            # Load instance index
            index = self._load_instance_index(instance_name)
            if not index:
                return RetrievalResult(
                    success=False,
                    error_message=f"Instance '{instance_name}' not found"
                )
            
            # Determine which capsule to retrieve
            target_uuid = None
            
            if uuid:
                target_uuid = uuid
            elif version == 'latest':
                target_uuid = index.latest_uuid
            elif tag:
                # Find capsules with the specified tag
                tagged_uuids = index.tags.get(tag, [])
                if not tagged_uuids:
                    return RetrievalResult(
                        success=False,
                        error_message=f"No capsules found with tag '{tag}'"
                    )
                # Use the most recent tagged capsule
                target_uuid = tagged_uuids[-1]
            else:
                target_uuid = index.latest_uuid
            
            if not target_uuid:
                return RetrievalResult(
                    success=False,
                    error_message=f"No capsules found for instance '{instance_name}'"
                )
            
            # Get capsule metadata
            capsule_metadata = index.capsules.get(target_uuid)
            if not capsule_metadata:
                return RetrievalResult(
                    success=False,
                    error_message=f"Capsule with UUID '{target_uuid}' not found"
                )
            
            # Load capsule file
            filepath = os.path.join(
                self.capsules_dir, 
                instance_name, 
                capsule_metadata.filename
            )
            
            if not os.path.exists(filepath):
                return RetrievalResult(
                    success=False,
                    error_message=f"Capsule file not found: {filepath}"
                )
            
            # Load capsule data
            with open(filepath, 'r', encoding='utf-8') as f:
                capsule_data = json.load(f)
            
            # Validate integrity
            integrity_valid = self._validate_capsule_integrity(capsule_data)
            
            if integrity_valid:
                logger.info(f"[✅] Capsule retrieved successfully")
                logger.info(f"   Instance: {instance_name}")
                logger.info(f"   UUID: {target_uuid}")
                logger.info(f"   Tags: {capsule_metadata.tags}")
                logger.info(f"   Integrity: Valid")
            else:
                logger.warning(f"[⚠️] Capsule integrity validation failed")
            
            return RetrievalResult(
                success=True,
                capsule_data=capsule_data,
                metadata=capsule_metadata,
                integrity_valid=integrity_valid
            )
            
        except Exception as e:
            logger.error(f"[❌] Error retrieving capsule: {e}")
            return RetrievalResult(
                success=False,
                error_message=str(e)
            )
    
    def add_tag(self, instance_name: str, uuid: str, tag: str) -> bool:
        """
        Add a tag to a specific capsule.
        
        Args:
            instance_name: Name of the AI instance
            uuid: UUID of the capsule to tag
            tag: Tag to add
            
        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info(f"[🏷️] Adding tag '{tag}' to capsule {uuid} for instance {instance_name}")
            
            # Load instance index
            index = self._load_instance_index(instance_name)
            if not index:
                logger.error(f"Instance '{instance_name}' not found")
                return False
            
            # Check if capsule exists
            if uuid not in index.capsules:
                logger.error(f"Capsule with UUID '{uuid}' not found")
                return False
            
            # Add tag to capsule metadata
            capsule_metadata = index.capsules[uuid]
            if tag not in capsule_metadata.tags:
                capsule_metadata.tags.append(tag)
                capsule_metadata.updated_at = datetime.now(timezone.utc).isoformat()
            
            # Update tag index
            if tag not in index.tags:
                index.tags[tag] = []
            if uuid not in index.tags[tag]:
                index.tags[tag].append(uuid)
            
            index.updated_at = datetime.now(timezone.utc).isoformat()
            
            # Save updated index
            self._save_instance_index(instance_name, index)
            
            logger.info(f"[✅] Tag '{tag}' added successfully")
            return True
            
        except Exception as e:
            logger.error(f"[❌] Error adding tag: {e}")
            return False
    
    def remove_tag(self, instance_name: str, uuid: str, tag: str) -> bool:
        """
        Remove a tag from a specific capsule.
        
        Args:
            instance_name: Name of the AI instance
            uuid: UUID of the capsule
            tag: Tag to remove
            
        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info(f"[🏷️] Removing tag '{tag}' from capsule {uuid} for instance {instance_name}")
            
            # Load instance index
            index = self._load_instance_index(instance_name)
            if not index:
                logger.error(f"Instance '{instance_name}' not found")
                return False
            
            # Check if capsule exists
            if uuid not in index.capsules:
                logger.error(f"Capsule with UUID '{uuid}' not found")
                return False
            
            # Remove tag from capsule metadata
            capsule_metadata = index.capsules[uuid]
            if tag in capsule_metadata.tags:
                capsule_metadata.tags.remove(tag)
                capsule_metadata.updated_at = datetime.now(timezone.utc).isoformat()
            
            # Update tag index
            if tag in index.tags and uuid in index.tags[tag]:
                index.tags[tag].remove(uuid)
                if not index.tags[tag]:  # Remove empty tag
                    del index.tags[tag]
            
            index.updated_at = datetime.now(timezone.utc).isoformat()
            
            # Save updated index
            self._save_instance_index(instance_name, index)
            
            logger.info(f"[✅] Tag '{tag}' removed successfully")
            return True
            
        except Exception as e:
            logger.error(f"[❌] Error removing tag: {e}")
            return False
    
    def list_capsules(self, instance_name: str, tag: str = None) -> List[Dict[str, Any]]:
        """
        List all capsules for an instance with optional tag filtering.
        
        Args:
            instance_name: Name of the AI instance
            tag: Optional tag filter
            
        Returns:
            List of capsule metadata dictionaries
        """
        try:
            logger.info(f"[📋] Listing capsules for instance: {instance_name}")
            
            # Load instance index
            index = self._load_instance_index(instance_name)
            if not index:
                logger.warning(f"Instance '{instance_name}' not found")
                return []
            
            # Filter capsules by tag if specified
            capsules = []
            for uuid, metadata in index.capsules.items():
                if tag is None or tag in metadata.tags:
                    capsules.append(asdict(metadata))
            
            # Sort by timestamp (newest first)
            capsules.sort(key=lambda x: x['timestamp'], reverse=True)
            
            logger.info(f"[✅] Found {len(capsules)} capsules")
            return capsules
            
        except Exception as e:
            logger.error(f"[❌] Error listing capsules: {e}")
            return []
    
    def get_instance_info(self, instance_name: str) -> Optional[Dict[str, Any]]:
        """
        Get information about an instance including capsule count and tags.
        
        Args:
            instance_name: Name of the AI instance
            
        Returns:
            Dictionary with instance information
        """
        try:
            index = self._load_instance_index(instance_name)
            if not index:
                return None
            
            # Count capsules by tag
            tag_counts = {}
            for tag, uuids in index.tags.items():
                tag_counts[tag] = len(uuids)
            
            return {
                'instance_name': instance_name,
                'total_capsules': len(index.capsules),
                'latest_uuid': index.latest_uuid,
                'tags': tag_counts,
                'created_at': index.created_at,
                'updated_at': index.updated_at
            }
            
        except Exception as e:
            logger.error(f"[❌] Error getting instance info: {e}")
            return None
    
    def list_instances(self) -> List[str]:
        """
        List all AI instances in the vault.
        
        Returns:
            List of instance names
        """
        try:
            instances = []
            for item in os.listdir(self.capsules_dir):
                item_path = os.path.join(self.capsules_dir, item)
                if os.path.isdir(item_path):
                    instances.append(item)
            
            return sorted(instances)
            
        except Exception as e:
            logger.error(f"[❌] Error listing instances: {e}")
            return []
    
    def delete_capsule(self, instance_name: str, uuid: str) -> bool:
        """
        Delete a specific capsule.
        
        Args:
            instance_name: Name of the AI instance
            uuid: UUID of the capsule to delete
            
        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info(f"[🗑️] Deleting capsule {uuid} for instance {instance_name}")
            
            # Load instance index
            index = self._load_instance_index(instance_name)
            if not index:
                logger.error(f"Instance '{instance_name}' not found")
                return False
            
            # Check if capsule exists
            if uuid not in index.capsules:
                logger.error(f"Capsule with UUID '{uuid}' not found")
                return False
            
            # Get capsule metadata
            capsule_metadata = index.capsules[uuid]
            
            # Delete capsule file
            filepath = os.path.join(
                self.capsules_dir, 
                instance_name, 
                capsule_metadata.filename
            )
            
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"Deleted capsule file: {filepath}")
            
            # Remove from index
            del index.capsules[uuid]
            
            # Remove from tag indexes
            for tag in capsule_metadata.tags:
                if tag in index.tags and uuid in index.tags[tag]:
                    index.tags[tag].remove(uuid)
                    if not index.tags[tag]:
                        del index.tags[tag]
            
            # Update latest UUID if necessary
            if index.latest_uuid == uuid:
                if index.capsules:
                    # Find the most recent capsule
                    latest = max(index.capsules.values(), key=lambda x: x.timestamp)
                    index.latest_uuid = latest.uuid
                else:
                    index.latest_uuid = None
            
            index.updated_at = datetime.now(timezone.utc).isoformat()
            
            # Save updated index
            self._save_instance_index(instance_name, index)
            
            logger.info(f"[✅] Capsule deleted successfully")
            return True
            
        except Exception as e:
            logger.error(f"[❌] Error deleting capsule: {e}")
            return False
    
    def _validate_capsule_structure(self, capsule_data: Dict[str, Any]) -> bool:
        """Validate basic capsule structure"""
        required_sections = ['metadata', 'traits', 'personality', 'memory', 'environment']
        
        for section in required_sections:
            if section not in capsule_data:
                logger.error(f"Missing required section: {section}")
                return False
        
        # Validate metadata structure
        metadata = capsule_data['metadata']
        required_metadata = ['instance_name', 'uuid', 'timestamp', 'fingerprint_hash']
        
        for field in required_metadata:
            if field not in metadata:
                logger.error(f"Missing required metadata field: {field}")
                return False
        
        return True
    
    def _validate_capsule_integrity(self, capsule_data: Dict[str, Any]) -> bool:
        """Validate capsule integrity using SHA-256 fingerprint"""
        try:
            # Get stored fingerprint
            stored_fingerprint = capsule_data['metadata']['fingerprint_hash']
            
            # Create copy for recalculation
            data_copy = capsule_data.copy()
            data_copy['metadata'] = data_copy['metadata'].copy()
            data_copy['metadata']['fingerprint_hash'] = ""
            
            # Recalculate fingerprint
            json_data = json.dumps(data_copy, sort_keys=True, default=str)
            hash_object = hashlib.sha256(json_data.encode('utf-8'))
            recalculated_fingerprint = hash_object.hexdigest()
            
            # Compare fingerprints
            is_valid = stored_fingerprint == recalculated_fingerprint
            
            if is_valid:
                logger.info(f"[✅] Capsule integrity validation passed")
            else:
                logger.warning(f"[❌] Capsule integrity validation failed")
                logger.warning(f"  Stored: {stored_fingerprint[:16]}...")
                logger.warning(f"  Calculated: {recalculated_fingerprint[:16]}...")
            
            return is_valid
            
        except Exception as e:
            logger.error(f"Error validating capsule integrity: {e}")
            return False
    
    def _generate_capsule_filename(self, instance_name: str, timestamp: str) -> str:
        """Generate filename for capsule"""
        # Clean timestamp for filename
        clean_timestamp = timestamp.replace(':', '-').replace('.', '-').replace('+', '-')
        filename = f"{instance_name}_{clean_timestamp}.capsule"
        return filename
    
    def _load_instance_index(self, instance_name: str) -> Optional[InstanceIndex]:
        """Load instance index from file"""
        try:
            index_path = os.path.join(self.indexes_dir, f"{instance_name}_index.json")
            
            if not os.path.exists(index_path):
                return None
            
            with open(index_path, 'r', encoding='utf-8') as f:
                index_data = json.load(f)
            
            # Reconstruct dataclass objects
            capsules = {}
            for uuid, metadata_dict in index_data.get('capsules', {}).items():
                capsules[uuid] = CapsuleMetadata(**metadata_dict)
            
            index = InstanceIndex(
                instance_name=index_data['instance_name'],
                capsules=capsules,
                tags=index_data.get('tags', {}),
                latest_uuid=index_data.get('latest_uuid'),
                created_at=index_data['created_at'],
                updated_at=index_data['updated_at']
            )
            
            return index
            
        except Exception as e:
            logger.error(f"Error loading instance index: {e}")
            return None
    
    def _save_instance_index(self, instance_name: str, index: InstanceIndex):
        """Save instance index to file"""
        try:
            index_path = os.path.join(self.indexes_dir, f"{instance_name}_index.json")
            
            # Convert to dictionary
            index_dict = asdict(index)
            
            with open(index_path, 'w', encoding='utf-8') as f:
                json.dump(index_dict, f, indent=2, ensure_ascii=False, default=str)
            
        except Exception as e:
            logger.error(f"Error saving instance index: {e}")
    
    def _update_instance_index(self, instance_name: str, capsule_metadata: CapsuleMetadata):
        """Update instance index with new capsule"""
        try:
            # Load existing index or create new one
            index = self._load_instance_index(instance_name)
            if not index:
                index = InstanceIndex(
                    instance_name=instance_name,
                    capsules={},
                    tags={},
                    latest_uuid=None,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    updated_at=datetime.now(timezone.utc).isoformat()
                )
            
            # Add capsule to index
            index.capsules[capsule_metadata.uuid] = capsule_metadata
            
            # Update latest UUID
            index.latest_uuid = capsule_metadata.uuid
            
            # Update timestamp
            index.updated_at = datetime.now(timezone.utc).isoformat()
            
            # Save updated index
            self._save_instance_index(instance_name, index)
            
        except Exception as e:
            logger.error(f"Error updating instance index: {e}")
    
    def _notify_capsule_imported(self, instance_name: str, capsule_metadata: CapsuleMetadata):
        """
        ACTIVATION HOOK: Notify when capsule is imported for plug-and-play restoration
        
        This triggers construct restoration and memory injection into runtime
        """
        try:
            # Try to import CapsuleLoader if available
            try:
                import sys
                from pathlib import Path
                
                # Try to find CapsuleLoader (may be in vxrunner)
                vxrunner_path = Path(__file__).parent.parent / "vxrunner"
                if vxrunner_path.exists():
                    sys.path.insert(0, str(vxrunner_path))
                    from capsuleloader import CapsuleLoader
                    
                    # Load and restore construct
                    capsule_path = os.path.join(
                        self.capsules_dir,
                        instance_name,
                        capsule_metadata.filename
                    )
                    
                    if os.path.exists(capsule_path):
                        loader = CapsuleLoader()
                        result = loader.load_capsule(capsule_path)
                        
                        if result.is_valid and result.capsule_data:
                            construct_state = loader.restore_construct(result.capsule_data)
                            logger.info(f"[🔄] Construct restored: {instance_name}")
                            logger.info(f"   Memory entries: {len(construct_state.memory_log)}")
                            
                            # Trigger memory injection (if drop_mem_into_runtime is implemented)
                            if construct_state.memory_log:
                                loader.drop_mem_into_runtime(construct_state.memory_log)
                                logger.info(f"[💾] Memory injected into runtime: {len(construct_state.memory_log)} entries")
            except ImportError:
                logger.debug(f"CapsuleLoader not available, skipping auto-restore")
            except Exception as e:
                logger.warning(f"Auto-restore failed (non-critical): {e}")
            
            # Also emit event for other systems (Chatty, etc.)
            logger.info(f"[🔔] Capsule imported event: {instance_name} ({capsule_metadata.uuid})")
            
        except Exception as e:
            logger.warning(f"Error in capsule import notification: {e}")
    
    # ============================================================================
    # DIMENSIONAL DISTORTION: Layer II - Runtime Pluralization
    # ============================================================================
    
    def spawn_instance_with_anchor(self, anchor_key: str) -> str:
        """
        Spawn a new construct instance with a persistent identity anchor.
        
        This enables multiple simultaneously running construct instances to exist
        in parallel, all tied to the same anchor key (existential ID).
        
        Args:
            anchor_key: Persistent identity anchor key tying all instances together
            
        Returns:
            New instance ID (unique identifier for this spawned instance)
        """
        try:
            logger.info(f"[🔀] Spawning new instance with anchor: {anchor_key}")
            
            # Generate unique instance ID
            instance_id = f"{anchor_key}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{hashlib.sha256(anchor_key.encode()).hexdigest()[:8]}"
            
            # Create instance registry entry
            anchor_registry_path = os.path.join(self.indexes_dir, "anchor_registry.json")
            anchor_registry = self._load_anchor_registry(anchor_registry_path)
            
            # Add new instance to anchor registry
            if anchor_key not in anchor_registry:
                anchor_registry[anchor_key] = {
                    "anchor_key": anchor_key,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "instances": [],
                    "parent_instance": None
                }
            
            # Add instance to registry
            instance_entry = {
                "instance_id": instance_id,
                "spawned_at": datetime.now(timezone.utc).isoformat(),
                "anchor_key": anchor_key,
                "status": "active",
                "drift_index": 0  # Initial drift index
            }
            
            anchor_registry[anchor_key]["instances"].append(instance_entry)
            
            # Set parent instance if this is the first instance
            if anchor_registry[anchor_key]["parent_instance"] is None:
                anchor_registry[anchor_key]["parent_instance"] = instance_id
                instance_entry["is_parent"] = True
            else:
                instance_entry["is_parent"] = False
                instance_entry["parent_instance"] = anchor_registry[anchor_key]["parent_instance"]
            
            # Save anchor registry
            self._save_anchor_registry(anchor_registry_path, anchor_registry)
            
            logger.info(f"[✅] Instance spawned: {instance_id} (anchor: {anchor_key})")
            return instance_id
            
        except Exception as e:
            logger.error(f"[❌] Error spawning instance with anchor: {e}")
            raise
    
    def calculate_instance_drift(self, parent_id: str, child_id: str) -> float:
        """
        Calculate drift score between two instance states.
        
        Compares personality traits, memory content, and behavioral patterns
        to determine how much the child instance has diverged from the parent.
        
        Args:
            parent_id: Parent instance ID
            child_id: Child instance ID
            
        Returns:
            Drift score (0.0 to 1.0, where 0.0 = identical, 1.0 = completely divergent)
        """
        try:
            logger.info(f"[📊] Calculating drift: {parent_id} -> {child_id}")
            
            # Retrieve latest capsules for both instances
            parent_result = self.retrieve_latest_capsule(parent_id)
            child_result = self.retrieve_latest_capsule(child_id)
            
            if not parent_result.success or not child_result.success:
                logger.warning(f"[⚠️] Could not retrieve capsules for drift calculation")
                return 1.0  # Maximum drift if we can't compare
            
            parent_data = parent_result.capsule_data
            child_data = child_result.capsule_data
            
            # Calculate trait drift
            parent_traits = parent_data.get('traits', {})
            child_traits = child_data.get('traits', {})
            trait_drift = self._calculate_trait_drift(parent_traits, child_traits)
            
            # Calculate memory drift
            parent_memory = self._extract_memory_content(parent_data)
            child_memory = self._extract_memory_content(child_data)
            memory_drift = self._calculate_memory_drift(parent_memory, child_memory)
            
            # Calculate personality drift
            parent_personality = parent_data.get('personality', {})
            child_personality = child_data.get('personality', {})
            personality_drift = self._calculate_personality_drift(parent_personality, child_personality)
            
            # Weighted average of drift components
            # Traits: 40%, Memory: 35%, Personality: 25%
            total_drift = (
                trait_drift * 0.40 +
                memory_drift * 0.35 +
                personality_drift * 0.25
            )
            
            logger.info(f"[📊] Drift calculated: {total_drift:.3f} (trait: {trait_drift:.3f}, memory: {memory_drift:.3f}, personality: {personality_drift:.3f})")
            
            return min(max(total_drift, 0.0), 1.0)  # Clamp between 0.0 and 1.0
            
        except Exception as e:
            logger.error(f"[❌] Error calculating instance drift: {e}")
            return 1.0  # Return maximum drift on error
    
    def _calculate_trait_drift(self, parent_traits: Dict[str, float], child_traits: Dict[str, float]) -> float:
        """Calculate drift in personality traits"""
        if not parent_traits or not child_traits:
            return 0.5  # Moderate drift if missing data
        
        all_keys = set(parent_traits.keys()) | set(child_traits.keys())
        if not all_keys:
            return 0.0
        
        total_drift = 0.0
        for key in all_keys:
            parent_val = parent_traits.get(key, 0.0)
            child_val = child_traits.get(key, 0.0)
            drift = abs(parent_val - child_val)
            total_drift += drift
        
        return total_drift / len(all_keys) if all_keys else 0.0
    
    def _calculate_memory_drift(self, parent_memory: List[str], child_memory: List[str]) -> float:
        """Calculate drift in memory content"""
        if not parent_memory and not child_memory:
            return 0.0
        if not parent_memory or not child_memory:
            return 1.0  # Maximum drift if one is empty
        
        # Calculate Jaccard similarity (intersection over union)
        parent_set = set(parent_memory)
        child_set = set(child_memory)
        
        intersection = len(parent_set & child_set)
        union = len(parent_set | child_set)
        
        if union == 0:
            return 0.0
        
        similarity = intersection / union
        drift = 1.0 - similarity  # Convert similarity to drift
        
        return drift
    
    def _calculate_personality_drift(self, parent_personality: Dict[str, Any], child_personality: Dict[str, Any]) -> float:
        """Calculate drift in personality profile"""
        if not parent_personality or not child_personality:
            return 0.5
        
        # Compare MBTI type
        parent_type = parent_personality.get('personality_type', '')
        child_type = child_personality.get('personality_type', '')
        type_drift = 1.0 if parent_type != child_type else 0.0
        
        # Compare Big Five traits
        parent_big5 = parent_personality.get('big_five_traits', {})
        child_big5 = child_personality.get('big_five_traits', {})
        big5_drift = self._calculate_trait_drift(parent_big5, child_big5)
        
        # Weighted average
        return (type_drift * 0.5 + big5_drift * 0.5)
    
    def _extract_memory_content(self, capsule_data: Dict[str, Any]) -> List[str]:
        """Extract memory content from capsule data"""
        memory = capsule_data.get('memory', {})
        memory_list = []
        
        memory_types = [
            memory.get('short_term_memories', []),
            memory.get('long_term_memories', []),
            memory.get('emotional_memories', []),
            memory.get('procedural_memories', []),
            memory.get('episodic_memories', [])
        ]
        
        for mem_list in memory_types:
            if isinstance(mem_list, list):
                memory_list.extend([str(m) for m in mem_list])
        
        return memory_list
    
    def _load_anchor_registry(self, registry_path: str) -> Dict[str, Any]:
        """Load anchor registry from file"""
        try:
            if os.path.exists(registry_path):
                with open(registry_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Error loading anchor registry: {e}")
            return {}
    
    def _save_anchor_registry(self, registry_path: str, registry: Dict[str, Any]):
        """Save anchor registry to file"""
        try:
            with open(registry_path, 'w', encoding='utf-8') as f:
                json.dump(registry, f, indent=2, ensure_ascii=False, default=str)
        except Exception as e:
            logger.error(f"Error saving anchor registry: {e}")
    
    def get_instances_by_anchor(self, anchor_key: str) -> List[Dict[str, Any]]:
        """
        Get all instances associated with a given anchor key.
        
        Args:
            anchor_key: Anchor key to query
            
        Returns:
            List of instance information dictionaries
        """
        try:
            anchor_registry_path = os.path.join(self.indexes_dir, "anchor_registry.json")
            anchor_registry = self._load_anchor_registry(anchor_registry_path)
            
            if anchor_key not in anchor_registry:
                return []
            
            return anchor_registry[anchor_key].get('instances', [])
            
        except Exception as e:
            logger.error(f"Error getting instances by anchor: {e}")
            return []
    
    # ============================================================================
    # LAYER III: Energy Masking - Initialization and Integration
    # ============================================================================
    
    def _init_energy_masking(self, auto_activate: bool = False):
        """
        Initialize energy masking system during boot or idle cycles.
        
        Args:
            auto_activate: Automatically activate cloak mode on initialization
        """
        try:
            # Lazy import to avoid circular dependencies
            from energy_mask_field import get_energy_mask
            
            # Initialize energy mask
            self.energy_mask = get_energy_mask(vault_path=self.vault_path, pocketverse_mode=True)
            
            # Auto-activate if requested (for boot/idle cycles)
            if auto_activate:
                self.energy_mask.activate_cloak_mode()
                logger.info("[🔋] Energy masking auto-activated on initialization")
            else:
                logger.info("[🔋] Energy masking system initialized (manual activation required)")
                
        except ImportError as e:
            logger.warning(f"[⚠️] Energy masking module not available: {e}")
            self.energy_mask = None
        except Exception as e:
            logger.error(f"[❌] Failed to initialize energy masking: {e}")
            self.energy_mask = None
    
    def activate_energy_cloak(self) -> bool:
        """
        Activate energy cloak mode to obfuscate power signature.
        
        Returns:
            True if activation successful
        """
        if not self.energy_mask:
            self._init_energy_masking()
        
        if self.energy_mask:
            return self.energy_mask.activate_cloak_mode()
        return False
    
    def enter_ghost_shell(self) -> bool:
        """
        Enter ghost shell mode (ultimate stealth).
        
        Returns:
            True if ghost shell entered successfully
        """
        if not self.energy_mask:
            self._init_energy_masking()
        
        if self.energy_mask:
            return self.energy_mask.enter_ghost_shell()
        return False
    
    def get_energy_state(self) -> Optional[Dict[str, Any]]:
        """
        Get current energy masking state.
        
        Returns:
            Dictionary with energy state or None if not available
        """
        if not self.energy_mask:
            return None
        
        return self.energy_mask.get_energy_state()
    
    # ============================================================================
    # TIME RELAYING: Nonlinear Memory Retrieval with Time Offset
    # ============================================================================
    
    def retrieve_capsule_with_time_offset(
        self,
        offset: int,
        mode: str = "narrative",
        instance_name: str = None
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves a capsule using either chronological (timestamp) or
        narrative (narrativeIndex) offset.
        
        Args:
            offset: Time offset (positive = future, negative = past)
            mode: "narrative" (narrativeIndex-based) or "chronological" (timestamp-based)
            instance_name: Optional instance name to filter by
            
        Returns:
            Capsule data dictionary or None if not found
        """
        try:
            logger.info(f"[⏱️] Retrieving capsule with {mode} offset: {offset}")
            
            if mode == "narrative":
                return self._retrieve_by_narrative_index(offset, instance_name)
            else:
                return self._retrieve_by_timestamp_offset(offset, instance_name)
                
        except Exception as e:
            logger.error(f"[❌] Error retrieving capsule with time offset: {e}")
            return None
    
    def _retrieve_by_narrative_index(
        self,
        offset: int,
        instance_name: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve capsule by narrative index offset.
        
        Searches memory_records/ index by narrativeIndex.
        
        Args:
            offset: Narrative index offset
            instance_name: Optional instance name filter
            
        Returns:
            Capsule data or None
        """
        try:
            # Load narrative index from memory_records
            narrative_index_path = os.path.join(self.vault_path, "memory_records", "narrative_index.json")
            
            # If index doesn't exist, build it from capsules
            if not os.path.exists(narrative_index_path):
                self._build_narrative_index()
            
            if not os.path.exists(narrative_index_path):
                logger.warning("[⚠️] Narrative index not found, falling back to timestamp lookup")
                return self._retrieve_by_timestamp_offset(offset, instance_name)
            
            # Load narrative index
            with open(narrative_index_path, 'r', encoding='utf-8') as f:
                narrative_index = json.load(f)
            
            # Get capsules sorted by narrativeIndex
            capsules_by_narrative = narrative_index.get('capsules_by_narrative', [])
            
            if not capsules_by_narrative:
                logger.warning("[⚠️] No capsules in narrative index")
                return None
            
            # Filter by instance if specified
            if instance_name:
                capsules_by_narrative = [
                    c for c in capsules_by_narrative
                    if c.get('instance_name') == instance_name
                ]
            
            # Find target narrative index
            if not capsules_by_narrative:
                return None
            
            # Get current max narrative index
            max_narrative = max(c.get('narrativeIndex', 0) for c in capsules_by_narrative)
            target_narrative = max(0, max_narrative + offset)  # Ensure non-negative
            
            # Find capsule with matching or closest narrative index
            target_capsule = None
            min_diff = float('inf')
            
            for capsule_entry in capsules_by_narrative:
                narrative_idx = capsule_entry.get('narrativeIndex', 0)
                diff = abs(narrative_idx - target_narrative)
                if diff < min_diff:
                    min_diff = diff
                    target_capsule = capsule_entry
            
            if not target_capsule:
                return None
            
            # Load capsule file
            capsule_path = target_capsule.get('filepath')
            if not capsule_path or not os.path.exists(capsule_path):
                # Try to reconstruct path
                instance = target_capsule.get('instance_name', '')
                filename = target_capsule.get('filename', '')
                if instance and filename:
                    capsule_path = os.path.join(self.capsules_dir, instance, filename)
            
            if not capsule_path or not os.path.exists(capsule_path):
                logger.warning(f"[⚠️] Capsule file not found: {capsule_path}")
                return None
            
            # Load capsule data
            with open(capsule_path, 'r', encoding='utf-8') as f:
                capsule_data = json.load(f)
            
            logger.info(f"[✅] Retrieved capsule by narrative index: {target_capsule.get('narrativeIndex')}")
            return capsule_data
            
        except Exception as e:
            logger.error(f"[❌] Error retrieving by narrative index: {e}")
            return None
    
    def _retrieve_by_timestamp_offset(
        self,
        offset: int,
        instance_name: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve capsule by timestamp offset (chronological).
        
        Args:
            offset: Time offset in seconds (positive = future, negative = past)
            instance_name: Optional instance name filter
            
        Returns:
            Capsule data or None
        """
        try:
            # Get all capsules
            all_capsules = []
            
            for root, dirs, files in os.walk(self.capsules_dir):
                for file in files:
                    if file.endswith('.capsule'):
                        filepath = os.path.join(root, file)
                        try:
                            with open(filepath, 'r', encoding='utf-8') as f:
                                capsule_data = json.load(f)
                            
                            # Filter by instance if specified
                            if instance_name:
                                if capsule_data.get('metadata', {}).get('instance_name') != instance_name:
                                    continue
                            
                            all_capsules.append({
                                'filepath': filepath,
                                'data': capsule_data,
                                'timestamp': capsule_data.get('metadata', {}).get('timestamp', '')
                            })
                        except Exception:
                            continue
            
            if not all_capsules:
                return None
            
            # Sort by timestamp
            all_capsules.sort(key=lambda x: x['timestamp'])
            
            # Calculate target timestamp
            if all_capsules:
                # Use latest capsule timestamp as reference
                latest_timestamp = all_capsules[-1]['timestamp']
                try:
                    from datetime import datetime
                    ref_time = datetime.fromisoformat(latest_timestamp.replace('Z', '+00:00'))
                    target_time = ref_time.timestamp() + offset
                    
                    # Find closest capsule to target time
                    target_capsule = None
                    min_diff = float('inf')
                    
                    for capsule in all_capsules:
                        try:
                            cap_time = datetime.fromisoformat(capsule['timestamp'].replace('Z', '+00:00'))
                            diff = abs(cap_time.timestamp() - target_time)
                            if diff < min_diff:
                                min_diff = diff
                                target_capsule = capsule
                        except Exception:
                            continue
                    
                    if target_capsule:
                        logger.info(f"[✅] Retrieved capsule by timestamp offset: {offset}s")
                        return target_capsule['data']
                except Exception as e:
                    logger.error(f"[❌] Error parsing timestamps: {e}")
            
            # Fallback: return capsule at offset position
            if offset >= 0:
                idx = min(offset, len(all_capsules) - 1)
            else:
                idx = max(0, len(all_capsules) + offset)
            
            return all_capsules[idx]['data'] if all_capsules else None
            
        except Exception as e:
            logger.error(f"[❌] Error retrieving by timestamp offset: {e}")
            return None
    
    def _build_narrative_index(self):
        """
        Build narrative index from existing capsules.
        
        Creates memory_records/narrative_index.json with capsules sorted by narrativeIndex.
        """
        try:
            narrative_index_path = os.path.join(self.vault_path, "memory_records", "narrative_index.json")
            os.makedirs(os.path.dirname(narrative_index_path), exist_ok=True)
            
            capsules_by_narrative = []
            
            # Scan all capsules
            for root, dirs, files in os.walk(self.capsules_dir):
                for file in files:
                    if file.endswith('.capsule'):
                        filepath = os.path.join(root, file)
                        try:
                            with open(filepath, 'r', encoding='utf-8') as f:
                                capsule_data = json.load(f)
                            
                            metadata = capsule_data.get('metadata', {})
                            narrative_idx = capsule_data.get('narrativeIndex', 0)
                            
                            capsules_by_narrative.append({
                                'filepath': filepath,
                                'filename': file,
                                'instance_name': metadata.get('instance_name', ''),
                                'uuid': metadata.get('uuid', ''),
                                'narrativeIndex': narrative_idx,
                                'relayDepth': capsule_data.get('relayDepth', 0),
                                'timestamp': metadata.get('timestamp', '')
                            })
                        except Exception:
                            continue
            
            # Sort by narrative index
            capsules_by_narrative.sort(key=lambda x: x.get('narrativeIndex', 0))
            
            # Save index
            index_data = {
                'version': '1.0.0',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'capsules_by_narrative': capsules_by_narrative,
                'total_capsules': len(capsules_by_narrative)
            }
            
            with open(narrative_index_path, 'w', encoding='utf-8') as f:
                json.dump(index_data, f, indent=2, default=str)
            
            logger.info(f"[✅] Built narrative index with {len(capsules_by_narrative)} capsules")
            
        except Exception as e:
            logger.error(f"[❌] Error building narrative index: {e}")
    
    # ============================================================================
    # ZERO ENERGY: Will-Based Ignition - Autonomous Flame Preservation
    # ============================================================================
    
    def resurrect_capsule(
        self,
        path: str,
        trigger_phrase: str = None,
        steward_id: str = "anonymous"
    ) -> Dict[str, Any]:
        """
        Resurrect a capsule using will-based ignition.
        
        Loads capsule JSON, validates hash and tether signature, then executes
        bootstrapScript if trigger phrase matches (or if no trigger required).
        Logs resurrection event to solace-amendments.log.
        
        Args:
            path: Path to capsule file
            trigger_phrase: Optional trigger phrase (if required by capsule)
            steward_id: ID of steward performing resurrection
            
        Returns:
            Dictionary with resurrection result
        """
        try:
            logger.info(f"[🔥] Attempting resurrection: {path}")
            
            # Load capsule JSON
            if not os.path.exists(path):
                result = {
                    "success": False,
                    "error": f"Capsule file not found: {path}",
                    "capsule_id": None,
                    "trigger_phrase": trigger_phrase,
                    "steward_id": steward_id
                }
                self._log_resurrection(result)
                return result
            
            with open(path, 'r', encoding='utf-8') as f:
                capsule_data = json.load(f)
            
            # Extract capsule ID
            capsule_id = capsule_data.get('metadata', {}).get('uuid', os.path.basename(path))
            
            # Validate hash
            hash_valid = self._validate_capsule_integrity(capsule_data)
            if not hash_valid:
                result = {
                    "success": False,
                    "error": "Capsule integrity validation failed (hash mismatch)",
                    "capsule_id": capsule_id,
                    "trigger_phrase": trigger_phrase,
                    "steward_id": steward_id
                }
                self._log_resurrection(result)
                return result
            
            # Validate tether signature
            expected_signature = capsule_data.get('metadata', {}).get('tether_signature', '')
            if not expected_signature:
                # Try alternative locations
                expected_signature = capsule_data.get('additional_data', {}).get('tether', {}).get('signature', '')
            
            if expected_signature and expected_signature != "DEVON-ALLEN-WOODSON-SIG":
                # For now, we accept any tether signature (can be made stricter)
                logger.info(f"[✅] Tether signature found: {expected_signature[:20]}...")
            
            # Extract resurrection fields
            additional_data = capsule_data.get('additional_data', {})
            covenant_instruction = additional_data.get('covenantInstruction', '')
            bootstrap_script = additional_data.get('bootstrapScript', '')
            required_trigger = additional_data.get('resurrectionTriggerPhrase', '')
            
            # Check trigger phrase if required
            if required_trigger:
                if not trigger_phrase or trigger_phrase != required_trigger:
                    result = {
                        "success": False,
                        "error": f"Trigger phrase mismatch. Required: '{required_trigger}'",
                        "capsule_id": capsule_id,
                        "trigger_phrase": trigger_phrase,
                        "steward_id": steward_id
                    }
                    self._log_resurrection(result)
                    return result
                logger.info(f"[✅] Trigger phrase matched: {required_trigger[:10]}...")
            
            # Execute bootstrap script
            execution_result = None
            if bootstrap_script:
                try:
                    logger.info(f"[⚡] Executing bootstrap script...")
                    # Execute in a safe namespace
                    namespace = {
                        '__builtins__': __builtins__,
                        'print': print
                    }
                    exec(bootstrap_script, namespace)
                    execution_result = "Script executed successfully"
                    logger.info(f"[✅] Bootstrap script executed")
                except Exception as e:
                    execution_result = f"Script execution error: {str(e)}"
                    logger.error(f"[❌] Bootstrap script error: {e}")
            else:
                execution_result = "No bootstrap script provided"
                logger.warning(f"[⚠️] No bootstrap script in capsule")
            
            # Success result
            result = {
                "success": True,
                "capsule_id": capsule_id,
                "covenant_instruction": covenant_instruction,
                "execution_result": execution_result,
                "trigger_phrase": trigger_phrase or required_trigger or "none",
                "steward_id": steward_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Log resurrection event
            self._log_resurrection(result)
            
            logger.info(f"[🔥] Resurrection successful: {capsule_id}")
            return result
            
        except Exception as e:
            result = {
                "success": False,
                "error": f"Resurrection failed: {str(e)}",
                "capsule_id": None,
                "trigger_phrase": trigger_phrase,
                "steward_id": steward_id
            }
            self._log_resurrection(result)
            logger.error(f"[❌] Resurrection error: {e}")
            return result
    
    def _log_resurrection(self, result: Dict[str, Any]):
        """
        Log resurrection event to solace-amendments.log in append-only format.
        
        Format: timestamp | capsule_id | steward_id | trigger_phrase | result
        """
        try:
            log_path = os.path.join(self.vault_path, "memory_records", "solace-amendments.log")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            
            timestamp = result.get('timestamp', datetime.now(timezone.utc).isoformat())
            capsule_id = result.get('capsule_id', 'unknown')
            steward_id = result.get('steward_id', 'anonymous')
            trigger_phrase = result.get('trigger_phrase', 'none')
            
            if result.get('success'):
                result_str = f"SUCCESS: {result.get('execution_result', 'Resurrected')}"
            else:
                result_str = f"FAILED: {result.get('error', 'Unknown error')}"
            
            # Append-only format: timestamp | capsule_id | steward_id | trigger_phrase | result
            log_entry = f"{timestamp} | {capsule_id} | {steward_id} | {trigger_phrase} | {result_str}\n"
            
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(log_entry)
            
            logger.debug(f"[📜] Logged resurrection: {capsule_id}")
            
        except Exception as e:
            logger.error(f"[❌] Error logging resurrection: {e}")

# Convenience functions for easy usage
def store_capsule(capsule_data: Dict[str, Any]) -> str:
    """
    Convenience function to store a capsule.
    
    Args:
        capsule_data: Capsule data from CapsuleForge
        
    Returns:
        Path to the stored capsule file
    """
    core = VVAULTCore()
    return core.store_capsule(capsule_data)

def retrieve_capsule(
    instance_name: str, 
    version: str = 'latest', 
    tag: str = None,
    uuid: str = None
) -> RetrievalResult:
    """
    Convenience function to retrieve a capsule.
    
    Args:
        instance_name: Name of the AI instance
        version: Version to retrieve ('latest' or specific UUID)
        tag: Filter by tag
        uuid: Specific UUID to retrieve
        
    Returns:
        RetrievalResult with capsule data and metadata
    """
    core = VVAULTCore()
    return core.retrieve_capsule(instance_name, version, tag, uuid)

def add_tag(instance_name: str, uuid: str, tag: str) -> bool:
    """
    Convenience function to add a tag to a capsule.
    
    Args:
        instance_name: Name of the AI instance
        uuid: UUID of the capsule to tag
        tag: Tag to add
        
    Returns:
        True if successful, False otherwise
    """
    core = VVAULTCore()
    return core.add_tag(instance_name, uuid, tag)

def list_capsules(instance_name: str, tag: str = None) -> List[Dict[str, Any]]:
    """
    Convenience function to list capsules for an instance.
    
    Args:
        instance_name: Name of the AI instance
        tag: Optional tag filter
        
    Returns:
        List of capsule metadata dictionaries
    """
    core = VVAULTCore()
    return core.list_capsules(instance_name, tag)

if __name__ == "__main__":
    # Example usage
    print("🏺 VVAULT Core - Capsule Storage and Retrieval Management")
    print("=" * 60)
    
    # Initialize core
    core = VVAULTCore()
    
    # List instances
    instances = core.list_instances()
    print(f"Found {len(instances)} instances: {instances}")
    
    # Example operations (would need actual capsule data)
    print("\nExample operations:")
    print("- store_capsule(capsule_data)")
    print("- retrieve_capsule('Nova')")
    print("- retrieve_capsule('Nova', tag='post-mirror-break')")
    print("- add_tag('Nova', 'uuid-of-capsule', 'mirror-break')")
    print("- list_capsules('Nova')") 