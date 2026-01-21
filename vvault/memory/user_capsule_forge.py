#!/usr/bin/env python3
"""
User Capsule Forge - Generate and Evolve User Account Capsules

Transforms user accounts into living, breathing capsules that evolve over time,
capturing personality, preferences, interaction patterns, and relationships.

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
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from pathlib import Path

from capsuleforge import CapsuleForge, CapsuleData, CapsuleMetadata, PersonalityProfile, MemorySnapshot, EnvironmentalState, AdditionalDataFields
from vvault.continuity.quantum_identity_engine import QuantumIdentityEngine, QuantumIdentity, HeuristicSignal

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class UserInteraction:
    """Record of a user interaction"""
    timestamp: str
    interaction_type: str  # login, logout, construct_create, construct_interact, preference_change, etc.
    target: Optional[str] = None  # construct_id, feature_name, etc.
    metadata: Dict[str, Any] = None
    duration: Optional[float] = None  # seconds


@dataclass
class UserPreference:
    """User preference setting"""
    category: str  # ui, behavior, privacy, etc.
    key: str
    value: Any
    confidence: float = 1.0  # How confident we are about this preference
    source: str = "explicit"  # explicit, inferred, default


@dataclass
class ConstructRelationship:
    """Relationship between user and a construct"""
    construct_id: str
    interaction_count: int = 0
    last_interaction: Optional[str] = None
    favorite: bool = False
    relationship_strength: float = 0.5  # 0.0 to 1.0
    interaction_patterns: List[str] = None  # Types of interactions


class UserCapsuleForge:
    """
    Generate and evolve user account capsules.
    
    Creates living capsules that capture user identity, personality, preferences,
    and relationships with constructs.
    """
    
    def __init__(self, vault_path: str = None):
        """
        Initialize User Capsule Forge.
        
        Args:
            vault_path: Path to VVAULT directory. If None, uses current directory.
        """
        self.vault_path = vault_path or os.path.dirname(os.path.abspath(__file__))
        self.users_dir = os.path.join(self.vault_path, "users")
        
        # Initialize base CapsuleForge for capsule operations
        self.capsule_forge = CapsuleForge(vault_path=self.vault_path)
        
        # Initialize Quantum Identity Engine
        self.quantum_engine = QuantumIdentityEngine(vault_path=self.vault_path)
        
        logger.info(f"[🔧] User Capsule Forge initialized with vault path: {self.vault_path}")
    
    def generate_user_capsule(
        self,
        user_id: str,
        user_name: str,
        email: str,
        constructs: List[str] = None,
        existing_interactions: List[UserInteraction] = None,
        existing_preferences: List[UserPreference] = None,
        existing_relationships: List[ConstructRelationship] = None,
        quantum_identity: Optional[QuantumIdentity] = None
    ) -> str:
        """
        Generate a user capsule from account data.
        
        Args:
            user_id: Unique user identifier
            user_name: User's display name
            email: User's email address
            constructs: List of construct IDs the user owns
            existing_interactions: Previous interaction history
            existing_preferences: User preferences
            existing_relationships: Relationships with constructs
            
        Returns:
            Path to the generated user capsule file
        """
        try:
            logger.info(f"[🎯] Generating user capsule for: {user_id}")
            
            # Determine user shard and directory
            shard = self._get_user_shard(user_id)
            user_dir = os.path.join(self.users_dir, shard, user_id)
            capsule_dir = os.path.join(user_dir, "account", "capsule")
            os.makedirs(capsule_dir, exist_ok=True)
            
            # Generate quantum identity if not provided
            if quantum_identity is None:
                quantum_identity = self._create_default_quantum_identity(user_id, user_name, email)
            
            # Generate heuristic signal
            heuristic_signal = self.quantum_engine.generate_heuristic_signal(
                quantum_identity=quantum_identity,
                user_id=user_id,
                existing_interactions=[asdict(i) for i in existing_interactions] if existing_interactions else [],
                existing_memories=[]  # TODO: Load from memory system
            )
            
            # Generate capsule data
            capsule_data = self._create_user_capsule_data(
                user_id=user_id,
                user_name=user_name,
                email=email,
                constructs=constructs or [],
                interactions=existing_interactions or [],
                preferences=existing_preferences or [],
                relationships=existing_relationships or [],
                quantum_identity=quantum_identity,
                heuristic_signal=heuristic_signal
            )
            
            # Calculate fingerprint
            fingerprint = self.capsule_forge.calculate_fingerprint(capsule_data)
            capsule_data.metadata.fingerprint_hash = fingerprint
            
            # Save capsule
            capsule_path = os.path.join(capsule_dir, "current.capsule")
            self._save_user_capsule(capsule_data, capsule_path)
            
            # Also save as versioned copy
            versions_dir = os.path.join(capsule_dir, "versions")
            os.makedirs(versions_dir, exist_ok=True)
            version_path = os.path.join(versions_dir, f"v1_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.capsule")
            self._save_user_capsule(capsule_data, version_path)
            
            logger.info(f"[✅] User capsule generated: {capsule_path}")
            logger.info(f"[🔍] Fingerprint: {fingerprint[:16]}...")
            
            return capsule_path
            
        except Exception as e:
            logger.error(f"[❌] Error generating user capsule: {e}")
            raise
    
    def evolve_user_capsule(
        self,
        user_id: str,
        interaction: UserInteraction,
        auto_version: bool = True
    ) -> str:
        """
        Evolve user capsule based on new interaction.
        
        Args:
            user_id: User identifier
            interaction: New interaction to record
            auto_version: Whether to create new version automatically
            
        Returns:
            Path to updated capsule
        """
        try:
            logger.info(f"[🔄] Evolving user capsule for: {user_id}")
            
            # Load current capsule
            current_capsule = self.load_user_capsule(user_id)
            if not current_capsule:
                raise ValueError(f"User capsule not found for: {user_id}")
            
            # Add interaction to memory
            # Store interaction history in additional_data.continuity for now
            # (MemorySnapshot has fixed fields, so we'll extend it via additional_data)
            if current_capsule.additional_data.continuity is None:
                current_capsule.additional_data.continuity = {}
            
            if not isinstance(current_capsule.additional_data.continuity, dict):
                current_capsule.additional_data.continuity = {}
            
            if 'interaction_history' not in current_capsule.additional_data.continuity:
                current_capsule.additional_data.continuity['interaction_history'] = []
            
            interaction_dict = asdict(interaction)
            current_capsule.additional_data.continuity['interaction_history'].append(interaction_dict)
            
            # Update memory count
            current_capsule.memory.memory_count += 1
            current_capsule.memory.last_memory_timestamp = interaction.timestamp
            
            # Update traits based on interaction
            self._update_traits_from_interaction(current_capsule, interaction)
            
            # Update relationships if construct-related
            if interaction.target and interaction.target.startswith(('lin-', 'nova-', 'sera-', 'katana-', 'aurora-', 'monday-', 'frame-', 'synth-')):
                self._update_construct_relationship(current_capsule, interaction.target, interaction)
            
            # Update metadata
            current_capsule.metadata.timestamp = datetime.now(timezone.utc).isoformat()
            current_capsule.metadata.uuid = str(uuid.uuid4())  # New UUID for evolved version
            
            # Recalculate fingerprint
            fingerprint = self.capsule_forge.calculate_fingerprint(current_capsule)
            current_capsule.metadata.fingerprint_hash = fingerprint
            
            # Save updated capsule
            shard = self._get_user_shard(user_id)
            user_dir = os.path.join(self.users_dir, shard, user_id)
            capsule_dir = os.path.join(user_dir, "account", "capsule")
            capsule_path = os.path.join(capsule_dir, "current.capsule")
            
            self._save_user_capsule(current_capsule, capsule_path)
            
            # Create versioned copy if auto_version enabled
            if auto_version:
                versions_dir = os.path.join(capsule_dir, "versions")
                os.makedirs(versions_dir, exist_ok=True)
                
                # Get next version number
                version_num = self._get_next_version_number(versions_dir)
                version_path = os.path.join(versions_dir, f"v{version_num}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.capsule")
                self._save_user_capsule(current_capsule, version_path)
                
                logger.info(f"[📦] Version saved: {version_path}")
            
            logger.info(f"[✅] User capsule evolved: {capsule_path}")
            
            return capsule_path
            
        except Exception as e:
            logger.error(f"[❌] Error evolving user capsule: {e}")
            raise
    
    def load_user_capsule(self, user_id: str) -> Optional[CapsuleData]:
        """
        Load user capsule.
        
        Args:
            user_id: User identifier
            
        Returns:
            CapsuleData object or None if not found
        """
        try:
            shard = self._get_user_shard(user_id)
            user_dir = os.path.join(self.users_dir, shard, user_id)
            capsule_path = os.path.join(user_dir, "account", "capsule", "current.capsule")
            
            if not os.path.exists(capsule_path):
                logger.warning(f"[⚠️] User capsule not found: {capsule_path}")
                return None
            
            return self.capsule_forge.load_capsule(capsule_path)
            
        except Exception as e:
            logger.error(f"[❌] Error loading user capsule: {e}")
            return None
    
    def _create_default_quantum_identity(
        self,
        user_id: str,
        user_name: str,
        email: str
    ) -> QuantumIdentity:
        """Create default quantum identity from basic user data"""
        return QuantumIdentity(
            medical_records={
                "genetic_markers": [],
                "biometric_signatures": {},
                "health_history": [],
                "neurological_patterns": {}
            },
            demographics={
                "birth_data": {},
                "geographic_origins": [],
                "cultural_background": {},
                "linguistic_patterns": []
            },
            social_standing={
                "relationships": [],
                "social_graph": {},
                "community_roles": [],
                "influence_patterns": {}
            },
            mental_capacity={
                "cognitive_assessments": [],
                "learning_patterns": {},
                "problem_solving_style": {},
                "memory_architecture": {}
            },
            ideologies={
                "belief_systems": [],
                "value_hierarchies": {},
                "ethical_frameworks": {},
                "philosophical_orientations": []
            }
        )
    
    def _create_user_capsule_data(
        self,
        user_id: str,
        user_name: str,
        email: str,
        constructs: List[str],
        interactions: List[UserInteraction],
        preferences: List[UserPreference],
        relationships: List[ConstructRelationship],
        quantum_identity: Optional[QuantumIdentity] = None,
        heuristic_signal: Optional[HeuristicSignal] = None
    ) -> CapsuleData:
        """Create user capsule data structure with quantum identity"""
        
        # Generate metadata with quantum identity fields
        metadata = CapsuleMetadata(
            instance_name=f"user_{user_id}",
            uuid=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            fingerprint_hash="",  # Will be set later
            capsule_version="1.0.0",
            generator="UserCapsuleForge",
            vault_source="VVAULT"
        )
        
        # Add quantum signature and heuristic signal to metadata (via additional_data)
        # Note: We'll store these in additional_data since CapsuleMetadata is a dataclass
        
        # Infer traits from interactions and preferences
        traits = self._infer_user_traits(interactions, preferences, relationships)
        
        # Create personality profile (infer from behavior)
        personality_type = self._infer_personality_type(traits, interactions)
        personality = self.capsule_forge._create_personality_profile(personality_type, traits)
        
        # Create memory snapshot
        memory = self._create_user_memory_snapshot(interactions, preferences, relationships, constructs)
        
        # Create environmental state
        environment = self._create_user_environmental_state()
        
        # Create additional data
        additional_data = AdditionalDataFields()
        additional_data.identity = {
            "user_id": user_id,
            "email": email,
            "display_name": user_name,
            "created": datetime.now(timezone.utc).isoformat()
        }
        additional_data.tether = {
            "constructs": constructs,
            "favorite_constructs": [r.construct_id for r in relationships if r.favorite],
            "construct_interaction_frequency": {
                r.construct_id: r.interaction_count for r in relationships
            }
        }
        additional_data.continuity = {
            "session_count": len([i for i in interactions if i.interaction_type == "login"]),
            "total_interactions": len(interactions),
            "last_active": interactions[-1].timestamp if interactions else datetime.now(timezone.utc).isoformat(),
            "continuity_score": self._calculate_continuity_score(interactions),
            "interaction_history": [asdict(i) for i in interactions],
            "multiverse_continuity": {
                "verified_across_realities": heuristic_signal is not None,
                "timeline_anchors": heuristic_signal.verified_timelines if heuristic_signal else [],
                "reality_fingerprints": heuristic_signal.verified_realities if heuristic_signal else []
            } if heuristic_signal else {}
        }
        
        # Add quantum identity and heuristic signal to additional_data
        if quantum_identity:
            additional_data.identity = {
                **(additional_data.identity or {}),
                "quantum_identity": asdict(quantum_identity)
            }
        
        if heuristic_signal:
            # Store heuristic signal in additional_data
            if not hasattr(additional_data, 'quantum_protection') or additional_data.quantum_protection is None:
                additional_data.quantum_protection = {}
            elif not isinstance(additional_data.quantum_protection, dict):
                additional_data.quantum_protection = {}
            
            additional_data.quantum_protection = {
                "heuristic_signal": heuristic_signal.signal_hash,
                "quantum_signature": heuristic_signal.quantum_signature,
                "uniqueness_proof": heuristic_signal.uniqueness_proof,
                "entropy_score": heuristic_signal.entropy_score,
                "multiverse_fingerprint": heuristic_signal.multiverse_fingerprint,
                "verified_timelines": heuristic_signal.verified_timelines,
                "verified_realities": heuristic_signal.verified_realities
            }
        
        return CapsuleData(
            metadata=metadata,
            traits=traits,
            personality=personality,
            memory=memory,
            environment=environment,
            additional_data=additional_data
        )
    
    def _infer_user_traits(
        self,
        interactions: List[UserInteraction],
        preferences: List[UserPreference],
        relationships: List[ConstructRelationship]
    ) -> Dict[str, float]:
        """Infer user traits from behavior"""
        
        # Default traits
        traits = {
            "creativity": 0.5,
            "curiosity": 0.5,
            "organization": 0.5,
            "social_preference": 0.5,
            "technical_depth": 0.5,
            "emotional_openness": 0.5,
            "persistence": 0.5,
            "exploration": 0.5
        }
        
        # Analyze interactions
        if interactions:
            # Creativity: construct creation, customization
            creativity_signals = [i for i in interactions if "create" in i.interaction_type.lower()]
            traits["creativity"] = min(0.9, 0.5 + (len(creativity_signals) * 0.1))
            
            # Curiosity: exploration, feature usage
            exploration_signals = [i for i in interactions if i.interaction_type in ["explore", "discover", "try_feature"]]
            traits["curiosity"] = min(0.9, 0.5 + (len(exploration_signals) * 0.1))
            
            # Organization: construct organization, categorization
            org_signals = [i for i in interactions if "organize" in i.interaction_type.lower() or "categorize" in i.interaction_type.lower()]
            traits["organization"] = min(0.9, 0.5 + (len(org_signals) * 0.1))
            
            # Social preference: interaction frequency, relationship depth
            if relationships:
                avg_relationship_strength = sum(r.relationship_strength for r in relationships) / len(relationships)
                traits["social_preference"] = avg_relationship_strength
            
            # Persistence: session length, return frequency
            long_sessions = [i for i in interactions if i.duration and i.duration > 3600]  # > 1 hour
            traits["persistence"] = min(0.9, 0.5 + (len(long_sessions) * 0.05))
        
        # Analyze preferences
        if preferences:
            tech_prefs = [p for p in preferences if p.category == "technical"]
            if tech_prefs:
                traits["technical_depth"] = min(0.9, 0.5 + (len(tech_prefs) * 0.1))
        
        return traits
    
    def _infer_personality_type(self, traits: Dict[str, float], interactions: List[UserInteraction]) -> str:
        """Infer MBTI personality type from traits and behavior"""
        
        # Simple heuristic-based inference
        # This could be enhanced with ML models
        
        # E vs I: social_preference
        e_score = traits.get("social_preference", 0.5)
        dimension1 = "E" if e_score > 0.5 else "I"
        
        # N vs S: creativity, curiosity
        n_score = (traits.get("creativity", 0.5) + traits.get("curiosity", 0.5)) / 2
        dimension2 = "N" if n_score > 0.5 else "S"
        
        # T vs F: technical_depth vs emotional_openness
        t_score = traits.get("technical_depth", 0.5)
        f_score = traits.get("emotional_openness", 0.5)
        dimension3 = "T" if t_score > f_score else "F"
        
        # J vs P: organization vs exploration
        j_score = traits.get("organization", 0.5)
        p_score = traits.get("exploration", 0.5)
        dimension4 = "J" if j_score > p_score else "P"
        
        return f"{dimension1}{dimension2}{dimension3}{dimension4}"
    
    def _create_user_memory_snapshot(
        self,
        interactions: List[UserInteraction],
        preferences: List[UserPreference],
        relationships: List[ConstructRelationship],
        constructs: List[str]
    ) -> MemorySnapshot:
        """Create user memory snapshot"""
        
        # Convert interactions to memory strings
        interaction_memories = [
            f"{i.timestamp}: {i.interaction_type}" + (f" with {i.target}" if i.target else "")
            for i in interactions[-50:]  # Last 50 interactions
        ]
        
        # Convert preferences to memory strings
        preference_memories = [
            f"Preference: {p.category}.{p.key} = {p.value}"
            for p in preferences
        ]
        
        # Convert relationships to memory strings
        relationship_memories = [
            f"Relationship with {r.construct_id}: {r.interaction_count} interactions, strength {r.relationship_strength:.2f}"
            for r in relationships
        ]
        
        # Combine into memory log
        memory_log = interaction_memories + preference_memories + relationship_memories
        
        # Create memory snapshot using CapsuleForge
        return self.capsule_forge._create_memory_snapshot(memory_log)
    
    def _create_user_environmental_state(self) -> EnvironmentalState:
        """Create user environmental state"""
        # Use base CapsuleForge method
        return self.capsule_forge._create_environmental_state()
    
    def _calculate_continuity_score(self, interactions: List[UserInteraction]) -> float:
        """Calculate continuity score based on interaction patterns"""
        if not interactions:
            return 0.0
        
        # Factors:
        # - Regular login pattern
        # - Consistent interaction frequency
        # - Long session durations
        
        logins = [i for i in interactions if i.interaction_type == "login"]
        if len(logins) < 2:
            return 0.3
        
        # Calculate average time between logins
        login_times = [datetime.fromisoformat(i.timestamp.replace('Z', '+00:00')) for i in logins]
        if len(login_times) < 2:
            return 0.3
        
        # Simple continuity: more logins = higher score
        continuity = min(1.0, len(logins) / 10.0)
        
        return continuity
    
    def _update_traits_from_interaction(self, capsule: CapsuleData, interaction: UserInteraction):
        """Update user traits based on interaction"""
        # Incremental trait updates based on interaction type
        if interaction.interaction_type == "construct_create":
            capsule.traits["creativity"] = min(0.9, capsule.traits.get("creativity", 0.5) + 0.05)
        elif interaction.interaction_type == "explore_feature":
            capsule.traits["curiosity"] = min(0.9, capsule.traits.get("curiosity", 0.5) + 0.05)
        elif interaction.duration and interaction.duration > 1800:  # > 30 min
            capsule.traits["persistence"] = min(0.9, capsule.traits.get("persistence", 0.5) + 0.02)
    
    def _update_construct_relationship(
        self,
        capsule: CapsuleData,
        construct_id: str,
        interaction: UserInteraction
    ):
        """Update relationship with a construct"""
        if capsule.additional_data.tether is None:
            capsule.additional_data.tether = {}
        
        if not isinstance(capsule.additional_data.tether, dict):
            capsule.additional_data.tether = {}
        
        if 'construct_interaction_frequency' not in capsule.additional_data.tether:
            capsule.additional_data.tether['construct_interaction_frequency'] = {}
        
        freq = capsule.additional_data.tether['construct_interaction_frequency']
        freq[construct_id] = freq.get(construct_id, 0) + 1
        capsule.additional_data.tether['construct_interaction_frequency'] = freq
    
    def _save_user_capsule(self, capsule_data: CapsuleData, filepath: str):
        """Save user capsule to file"""
        capsule_dict = asdict(capsule_data)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(capsule_dict, f, indent=2, ensure_ascii=False, default=str)
        
        logger.info(f"[💾] User capsule saved: {filepath}")
    
    def _get_user_shard(self, user_id: str) -> str:
        """Get user shard (simplified - uses shard_0000 for now)"""
        # TODO: Implement hash-based sharding
        return "shard_0000"
    
    def _get_next_version_number(self, versions_dir: str) -> int:
        """Get next version number for versioned capsule"""
        if not os.path.exists(versions_dir):
            return 1
        
        versions = [f for f in os.listdir(versions_dir) if f.startswith('v') and f.endswith('.capsule')]
        if not versions:
            return 1
        
        # Extract version numbers
        version_nums = []
        for v in versions:
            try:
                num = int(v.split('_')[0][1:])  # Extract number after 'v'
                version_nums.append(num)
            except (ValueError, IndexError):
                continue
        
        return max(version_nums) + 1 if version_nums else 1


# Convenience functions
def generate_user_capsule(user_id: str, user_name: str, email: str, constructs: List[str] = None) -> str:
    """Convenience function to generate a user capsule"""
    forge = UserCapsuleForge()
    return forge.generate_user_capsule(user_id, user_name, email, constructs or [])


if __name__ == "__main__":
    # Example usage
    forge = UserCapsuleForge()
    
    # Generate capsule for a user
    user_id = "devon_woodson_1762969514958"
    user_name = "Devon Woodson"
    email = "dwoodson92@gmail.com"
    constructs = ["lin-001", "nova-001", "sera-001", "katana-001"]
    
    capsule_path = forge.generate_user_capsule(user_id, user_name, email, constructs)
    print(f"User capsule generated: {capsule_path}")
    
    # Evolve capsule with interaction
    interaction = UserInteraction(
        timestamp=datetime.now(timezone.utc).isoformat(),
        interaction_type="construct_interact",
        target="nova-001",
        duration=1200.0
    )
    
    evolved_path = forge.evolve_user_capsule(user_id, interaction)
    print(f"User capsule evolved: {evolved_path}")

