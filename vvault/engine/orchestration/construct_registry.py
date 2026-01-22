"""
Construct Registry
Manages construct loading, lookup, and metadata
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class ConstructManifest:
    construct_id: str
    display_name: str
    role: str
    version: str = "1.0.0"
    created_at: str = ""
    is_primary: bool = False
    is_system: bool = False
    verification_status: str = "unverified"
    pocketverse_anchored: bool = False
    tags: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    description: str = ""
    shard: str = "shard_0000"


class ConstructRegistry:
    """Registry for all constructs in VVAULT"""
    
    def __init__(self, vvault_root: Optional[str] = None):
        self.vvault_root = Path(vvault_root) if vvault_root else Path(__file__).parent.parent.parent
        self.instances_dir = self.vvault_root / "vvault" / "instances"
        if not self.instances_dir.exists():
            self.instances_dir = self.vvault_root / "instances"
        self._cache: Dict[str, ConstructManifest] = {}
        self._loaded = False
    
    def _discover_shards(self) -> List[Path]:
        """Discover all shard directories"""
        shards = []
        if self.instances_dir.exists():
            for item in self.instances_dir.iterdir():
                if item.is_dir() and item.name.startswith("shard_"):
                    shards.append(item)
        return sorted(shards)
    
    def _load_construct_metadata(self, construct_path: Path) -> Optional[ConstructManifest]:
        """Load construct metadata from its config directory"""
        metadata_file = construct_path / "config" / "metadata.json"
        
        if not metadata_file.exists():
            logger.warning(f"No metadata.json found for {construct_path.name}")
            return None
        
        try:
            with open(metadata_file, 'r') as f:
                data = json.load(f)
            
            return ConstructManifest(
                construct_id=data.get('construct_id', construct_path.name),
                display_name=data.get('display_name', construct_path.name.split('-')[0].title()),
                role=data.get('role', 'assistant'),
                version=data.get('version', '1.0.0'),
                created_at=data.get('created_at', ''),
                is_primary=data.get('is_primary', False),
                is_system=data.get('is_system', False),
                verification_status=data.get('verification_status', 'unverified'),
                pocketverse_anchored=data.get('pocketverse_anchored', False),
                tags=data.get('tags', []),
                capabilities=data.get('capabilities', []),
                description=data.get('description', ''),
                shard=construct_path.parent.name
            )
        except Exception as e:
            logger.error(f"Error loading metadata for {construct_path.name}: {e}")
            return None
    
    def load_all(self, force: bool = False) -> Dict[str, ConstructManifest]:
        """Load all constructs from all shards"""
        if self._loaded and not force:
            return self._cache
        
        self._cache.clear()
        
        for shard in self._discover_shards():
            for construct_dir in shard.iterdir():
                if construct_dir.is_dir() and not construct_dir.name.startswith('.'):
                    manifest = self._load_construct_metadata(construct_dir)
                    if manifest:
                        self._cache[manifest.construct_id] = manifest
                        logger.debug(f"Loaded construct: {manifest.construct_id}")
        
        self._loaded = True
        logger.info(f"Loaded {len(self._cache)} constructs from registry")
        return self._cache
    
    def get(self, construct_id: str) -> Optional[ConstructManifest]:
        """Get a construct by ID"""
        if not self._loaded:
            self.load_all()
        return self._cache.get(construct_id)
    
    def get_construct_path(self, construct_id: str) -> Optional[Path]:
        """Get the filesystem path to a construct"""
        manifest = self.get(construct_id)
        if not manifest:
            return None
        return self.instances_dir / manifest.shard / construct_id
    
    def list_all(self) -> List[ConstructManifest]:
        """List all registered constructs"""
        if not self._loaded:
            self.load_all()
        return list(self._cache.values())
    
    def list_by_tag(self, tag: str) -> List[ConstructManifest]:
        """List constructs with a specific tag"""
        return [c for c in self.list_all() if tag in c.tags]
    
    def get_system_constructs(self) -> List[ConstructManifest]:
        """Get all system constructs (like Aurora)"""
        return [c for c in self.list_all() if c.is_system]
    
    def to_dict(self, construct_id: str) -> Optional[Dict[str, Any]]:
        """Get construct manifest as dictionary"""
        manifest = self.get(construct_id)
        if not manifest:
            return None
        
        return {
            'construct_id': manifest.construct_id,
            'display_name': manifest.display_name,
            'role': manifest.role,
            'version': manifest.version,
            'created_at': manifest.created_at,
            'is_primary': manifest.is_primary,
            'is_system': manifest.is_system,
            'verification_status': manifest.verification_status,
            'pocketverse_anchored': manifest.pocketverse_anchored,
            'tags': manifest.tags,
            'capabilities': manifest.capabilities,
            'description': manifest.description,
            'shard': manifest.shard
        }


_default_registry: Optional[ConstructRegistry] = None

def get_registry(vvault_root: Optional[str] = None) -> ConstructRegistry:
    """Get the default construct registry singleton"""
    global _default_registry
    if _default_registry is None:
        _default_registry = ConstructRegistry(vvault_root)
    return _default_registry
