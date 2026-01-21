#!/usr/bin/env python3
"""
Migrate Existing User to User Capsule

Converts an existing user profile into a living user capsule.
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vvault.memory.user_capsule_forge import UserCapsuleForge, UserInteraction, UserPreference, ConstructRelationship


def load_user_profile(user_id: str) -> dict:
    """Load user profile from users.json"""
    users_file = Path(__file__).parent.parent / "users.json"
    
    if not users_file.exists():
        raise FileNotFoundError(f"users.json not found: {users_file}")
    
    with open(users_file, 'r') as f:
        users_data = json.load(f)
    
    if user_id not in users_data.get('users', {}):
        raise ValueError(f"User not found: {user_id}")
    
    return users_data['users'][user_id]


def load_user_profile_file(user_id: str) -> dict:
    """Load user profile from account/profile.json"""
    shard = "shard_0000"  # TODO: Implement shard detection
    profile_path = Path(__file__).parent.parent / "users" / shard / user_id / "account" / "profile.json"
    
    if not profile_path.exists():
        raise FileNotFoundError(f"Profile not found: {profile_path}")
    
    with open(profile_path, 'r') as f:
        return json.load(f)


def infer_interactions_from_profile(profile: dict) -> list:
    """Infer initial interactions from profile"""
    interactions = []
    
    # Initial creation
    created = profile.get('created', datetime.now(timezone.utc).isoformat())
    interactions.append(UserInteraction(
        timestamp=created,
        interaction_type="account_create",
        metadata={"source": "migration"}
    ))
    
    # Last seen as login
    last_seen = profile.get('last_seen')
    if last_seen:
        interactions.append(UserInteraction(
            timestamp=last_seen,
            interaction_type="login",
            metadata={"source": "migration"}
        ))
    
    # Construct creation (inferred)
    constructs = profile.get('constructs', [])
    for construct_id in constructs:
        interactions.append(UserInteraction(
            timestamp=created,  # Approximate
            interaction_type="construct_create",
            target=construct_id,
            metadata={"source": "migration", "inferred": True}
        ))
    
    return interactions


def infer_preferences_from_profile(profile: dict) -> list:
    """Infer preferences from profile"""
    preferences = []
    
    # Storage quota preference
    storage_quota = profile.get('storage_quota', 'unlimited')
    preferences.append(UserPreference(
        category="storage",
        key="quota",
        value=storage_quota,
        source="explicit"
    ))
    
    # Feature preferences
    features = profile.get('features', [])
    for feature in features:
        preferences.append(UserPreference(
            category="features",
            key=feature,
            value=True,
            source="explicit"
        ))
    
    return preferences


def infer_relationships_from_profile(profile: dict) -> list:
    """Infer construct relationships from profile"""
    relationships = []
    
    constructs = profile.get('constructs', [])
    for construct_id in constructs:
        relationships.append(ConstructRelationship(
            construct_id=construct_id,
            interaction_count=1,  # Minimum
            relationship_strength=0.5,  # Default
            favorite=False  # Unknown
        ))
    
    return relationships


def migrate_user_to_capsule(user_id: str):
    """Migrate a user profile to a user capsule"""
    print(f"🔄 Migrating user to capsule: {user_id}")
    
    # Load profile
    try:
        profile = load_user_profile_file(user_id)
    except FileNotFoundError:
        # Fallback to users.json
        profile = load_user_profile(user_id)
    
    print(f"✅ Loaded profile for: {profile.get('user_name', user_id)}")
    
    # Initialize forge
    forge = UserCapsuleForge()
    
    # Infer data from profile
    interactions = infer_interactions_from_profile(profile)
    preferences = infer_preferences_from_profile(profile)
    relationships = infer_relationships_from_profile(profile)
    
    print(f"📊 Inferred {len(interactions)} interactions, {len(preferences)} preferences, {len(relationships)} relationships")
    
    # Generate capsule
    capsule_path = forge.generate_user_capsule(
        user_id=user_id,
        user_name=profile.get('user_name', user_id),
        email=profile.get('email', ''),
        constructs=profile.get('constructs', []),
        existing_interactions=interactions,
        existing_preferences=preferences,
        existing_relationships=relationships
    )
    
    print(f"✅ User capsule created: {capsule_path}")
    
    # Load and display capsule info
    capsule = forge.load_user_capsule(user_id)
    if capsule:
        print(f"\n📦 Capsule Info:")
        print(f"   Instance: {capsule.metadata.instance_name}")
        print(f"   UUID: {capsule.metadata.uuid}")
        print(f"   Personality: {capsule.personality.personality_type}")
        print(f"   Traits: {list(capsule.traits.keys())}")
        print(f"   Continuity Score: {capsule.additional_data.continuity.get('continuity_score', 0):.2f}")
    
    return capsule_path


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Migrate user profile to user capsule")
    parser.add_argument("user_id", help="User ID to migrate")
    
    args = parser.parse_args()
    
    try:
        migrate_user_to_capsule(args.user_id)
        print("\n✅ Migration complete!")
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        sys.exit(1)


