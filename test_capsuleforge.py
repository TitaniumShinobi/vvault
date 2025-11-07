#!/usr/bin/env python3
"""
Test script for CapsuleForge
"""

from capsuleforge import generate_capsule, CapsuleForge

def test_capsule_generation():
    """Test basic capsule generation"""
    print("🧪 Testing CapsuleForge...")
    
    # Test data
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
    
    try:
        # Generate capsule
        capsule_path = generate_capsule("Nova", traits, memory_log, personality)
        print(f"✅ Capsule generated successfully: {capsule_path}")
        
        # Test loading and validation
        forge = CapsuleForge()
        capsule_data = forge.load_capsule(capsule_path)
        print(f"✅ Capsule loaded successfully")
        print(f"   Instance: {capsule_data.metadata.instance_name}")
        print(f"   UUID: {capsule_data.metadata.uuid}")
        print(f"   Personality: {capsule_data.personality.personality_type}")
        print(f"   Memory count: {capsule_data.memory.memory_count}")
        
        # Validate capsule
        is_valid = forge.validate_capsule(capsule_path)
        print(f"✅ Capsule validation: {'PASS' if is_valid else 'FAIL'}")
        
        # List capsules
        capsules = forge.list_capsules()
        print(f"✅ Found {len(capsules)} capsules in directory")
        
        return True
        
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        return False

if __name__ == "__main__":
    success = test_capsule_generation()
    if success:
        print("\n🎉 All tests passed!")
    else:
        print("\n💥 Tests failed!") 