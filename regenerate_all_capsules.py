#!/usr/bin/env python3
"""
Regenerate All Missing Capsules
Quick script to regenerate all 6 missing capsules from existing memory data.
"""

import os
import json
import sys
from pathlib import Path
from capsuleforge import CapsuleForge

def load_memory_data(construct_id: str, vault_path: str) -> dict:
    """Load memory data for a construct"""
    construct_path = Path(vault_path) / construct_id
    
    memory_data = {
        "traits": {},
        "memory_log": [],
        "personality_type": "INFJ"  # Default, will be updated if found
    }
    
    # Try to load from Memories directory
    memories_path = construct_path / "Memories"
    if memories_path.exists():
        # Load short-term memories
        short_term_path = memories_path / "short_term.json"
        if short_term_path.exists():
            try:
                with open(short_term_path, 'r') as f:
                    short_term = json.load(f)
                    if isinstance(short_term, list):
                        memory_data["memory_log"].extend([str(m) for m in short_term])
                    elif isinstance(short_term, dict):
                        memory_data["memory_log"].extend([str(v) for v in short_term.values()])
            except Exception as e:
                print(f"  ⚠️  Could not load short_term.json: {e}")
        
        # Load long-term memories
        long_term_path = memories_path / "long_term.json"
        if long_term_path.exists():
            try:
                with open(long_term_path, 'r') as f:
                    long_term = json.load(f)
                    if isinstance(long_term, list):
                        memory_data["memory_log"].extend([str(m) for m in long_term])
                    elif isinstance(long_term, dict):
                        memory_data["memory_log"].extend([str(v) for v in long_term.values()])
            except Exception as e:
                print(f"  ⚠️  Could not load long_term.json: {e}")
    
    # Try to load conversation files for context
    chatgpt_path = construct_path / "ChatGPT"
    if chatgpt_path.exists():
        # Count conversation files as memory context
        txt_files = list(chatgpt_path.rglob("*.txt"))
        memory_data["memory_log"].append(f"Found {len(txt_files)} conversation files in ChatGPT directory")
    
    # Default traits if none found
    if not memory_data["traits"]:
        memory_data["traits"] = {
            "curiosity": 0.8,
            "creativity": 0.75,
            "empathy": 0.7,
            "analytical": 0.8,
            "adaptive": 0.75
        }
    
    return memory_data

def regenerate_capsule(construct_id: str, vault_path: str):
    """Regenerate a single capsule"""
    print(f"\n🔄 Regenerating capsule for {construct_id}...")
    
    try:
        # Load memory data
        memory_data = load_memory_data(construct_id, vault_path)
        
        # Initialize CapsuleForge
        forge = CapsuleForge(vault_path=vault_path)
        
        # Generate capsule
        instance_name = construct_id.replace("-001", "").replace("-002", "").title()
        if instance_name == "Nova":
            instance_name = "Nova"
        elif instance_name == "Katana":
            instance_name = "Katana"
        elif instance_name == "Aurora":
            instance_name = "Aurora"
        elif instance_name == "Sera":
            instance_name = "Sera"
        elif instance_name == "Monday":
            instance_name = "Monday"
        
        capsule_path = forge.generate_capsule(
            instance_name=instance_name,
            traits=memory_data["traits"],
            memory_log=memory_data["memory_log"][:1000],  # Limit to first 1000 memories
            personality_type=memory_data["personality_type"]
        )
        
        print(f"  ✅ Successfully regenerated: {os.path.basename(capsule_path)}")
        return True
        
    except Exception as e:
        print(f"  ❌ Failed to regenerate {construct_id}: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main function"""
    print("=" * 60)
    print("🔧 REGENERATING ALL 6 MISSING CAPSULES")
    print("=" * 60)
    
    vault_path = os.path.dirname(os.path.abspath(__file__))
    constructs = ["nova-001", "aurora-001", "katana-001", "katana-002", "sera-001", "monday-001"]
    
    success_count = 0
    failed = []
    
    for construct_id in constructs:
        if regenerate_capsule(construct_id, vault_path):
            success_count += 1
        else:
            failed.append(construct_id)
    
    print("\n" + "=" * 60)
    print(f"✅ Successfully regenerated: {success_count}/{len(constructs)} capsules")
    if failed:
        print(f"❌ Failed: {', '.join(failed)}")
    print("=" * 60)
    
    # List generated capsules
    capsules_dir = os.path.join(vault_path, "capsules")
    if os.path.exists(capsules_dir):
        capsule_files = [f for f in os.listdir(capsules_dir) if f.endswith('.capsule')]
        if capsule_files:
            print(f"\n📦 Generated capsules:")
            for cap in sorted(capsule_files):
                cap_path = os.path.join(capsules_dir, cap)
                size = os.path.getsize(cap_path)
                print(f"  - {cap} ({size:,} bytes)")

if __name__ == "__main__":
    main()

