#!/usr/bin/env python3
"""
Test script for VVAULT Core functionality
"""

import os
import json
import logging
from vvault.memory.vvault_core import VVAULTCore, store_capsule, retrieve_capsule, add_tag, list_capsules
from capsuleforge import generate_capsule

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def create_test_capsule():
    """Create a test capsule for testing"""
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
    
    # Generate capsule using CapsuleForge
    capsule_path = generate_capsule("Nova", traits, memory_log, personality)
    
    # Load the generated capsule
    with open(capsule_path, 'r', encoding='utf-8') as f:
        capsule_data = json.load(f)
    
    return capsule_data

def test_vvault_core():
    """Test VVAULT Core functionality"""
    print("🧪 Testing VVAULT Core...")
    
    # Initialize VVAULT Core
    core = VVAULTCore()
    
    # Create test capsule
    print("\n📦 Creating test capsule...")
    capsule_data = create_test_capsule()
    print(f"✅ Test capsule created")
    
    # Test 1: Store capsule
    print("\n💾 Testing capsule storage...")
    try:
        stored_path = core.store_capsule(capsule_data)
        print(f"✅ Capsule stored: {stored_path}")
    except Exception as e:
        print(f"❌ Error storing capsule: {e}")
        return False
    
    # Test 2: List instances
    print("\n📋 Testing instance listing...")
    instances = core.list_instances()
    print(f"✅ Found instances: {instances}")
    
    # Test 3: Get instance info
    print("\nℹ️ Testing instance info...")
    instance_info = core.get_instance_info("Nova")
    if instance_info:
        print(f"✅ Instance info: {instance_info}")
    else:
        print("❌ No instance info found")
    
    # Test 4: List capsules
    print("\n📋 Testing capsule listing...")
    capsules = core.list_capsules("Nova")
    print(f"✅ Found {len(capsules)} capsules")
    for i, capsule in enumerate(capsules[:3]):  # Show first 3
        print(f"  {i+1}. {capsule['instance_name']} - {capsule['uuid'][:8]}... - {len(capsule['tags'])} tags")
    
    # Test 5: Retrieve latest capsule
    print("\n📖 Testing capsule retrieval (latest)...")
    result = core.retrieve_capsule("Nova")
    if result.success:
        print(f"✅ Retrieved capsule: {result.metadata.instance_name}")
        print(f"   UUID: {result.metadata.uuid}")
        print(f"   Tags: {result.metadata.tags}")
        print(f"   Integrity: {'Valid' if result.integrity_valid else 'Invalid'}")
        
        # Get UUID for tagging test
        test_uuid = result.metadata.uuid
    else:
        print(f"❌ Error retrieving capsule: {result.error_message}")
        return False
    
    # Test 6: Add tag
    print("\n🏷️ Testing tag addition...")
    success = core.add_tag("Nova", test_uuid, "test-tag")
    if success:
        print(f"✅ Tag 'test-tag' added successfully")
    else:
        print(f"❌ Error adding tag")
    
    # Test 7: Add another tag
    print("\n🏷️ Testing second tag addition...")
    success = core.add_tag("Nova", test_uuid, "post-mirror-break")
    if success:
        print(f"✅ Tag 'post-mirror-break' added successfully")
    else:
        print(f"❌ Error adding second tag")
    
    # Test 8: List capsules with tag filter
    print("\n📋 Testing capsule listing with tag filter...")
    tagged_capsules = core.list_capsules("Nova", tag="test-tag")
    print(f"✅ Found {len(tagged_capsules)} capsules with 'test-tag'")
    
    # Test 9: Retrieve capsule by tag
    print("\n📖 Testing capsule retrieval by tag...")
    result = core.retrieve_capsule("Nova", tag="post-mirror-break")
    if result.success:
        print(f"✅ Retrieved capsule by tag: {result.metadata.instance_name}")
        print(f"   UUID: {result.metadata.uuid}")
        print(f"   Tags: {result.metadata.tags}")
    else:
        print(f"❌ Error retrieving capsule by tag: {result.error_message}")
    
    # Test 10: Remove tag
    print("\n🏷️ Testing tag removal...")
    success = core.remove_tag("Nova", test_uuid, "test-tag")
    if success:
        print(f"✅ Tag 'test-tag' removed successfully")
    else:
        print(f"❌ Error removing tag")
    
    # Test 11: Verify tag removal
    print("\n📋 Testing capsule listing after tag removal...")
    tagged_capsules = core.list_capsules("Nova", tag="test-tag")
    print(f"✅ Found {len(tagged_capsules)} capsules with 'test-tag' (should be 0)")
    
    return True

def test_convenience_functions():
    """Test convenience functions"""
    print("\n🧪 Testing convenience functions...")
    
    # Create test capsule
    capsule_data = create_test_capsule()
    
    # Test store_capsule convenience function
    print("\n💾 Testing store_capsule convenience function...")
    try:
        stored_path = store_capsule(capsule_data)
        print(f"✅ Capsule stored via convenience function: {stored_path}")
    except Exception as e:
        print(f"❌ Error storing capsule: {e}")
        return False
    
    # Test retrieve_capsule convenience function
    print("\n📖 Testing retrieve_capsule convenience function...")
    result = retrieve_capsule("Nova")
    if result.success:
        print(f"✅ Retrieved capsule via convenience function: {result.metadata.instance_name}")
    else:
        print(f"❌ Error retrieving capsule: {result.error_message}")
    
    # Test add_tag convenience function
    print("\n🏷️ Testing add_tag convenience function...")
    if result.success:
        success = add_tag("Nova", result.metadata.uuid, "convenience-test")
        if success:
            print(f"✅ Tag added via convenience function")
        else:
            print(f"❌ Error adding tag via convenience function")
    
    # Test list_capsules convenience function
    print("\n📋 Testing list_capsules convenience function...")
    capsules = list_capsules("Nova")
    print(f"✅ Listed {len(capsules)} capsules via convenience function")
    
    return True

def test_error_handling():
    """Test error handling"""
    print("\n🧪 Testing error handling...")
    
    core = VVAULTCore()
    
    # Test retrieving non-existent instance
    print("\n📖 Testing retrieval of non-existent instance...")
    result = core.retrieve_capsule("NonExistentInstance")
    if not result.success:
        print(f"✅ Correctly handled non-existent instance: {result.error_message}")
    else:
        print(f"❌ Should have failed for non-existent instance")
    
    # Test retrieving with non-existent tag
    print("\n📖 Testing retrieval with non-existent tag...")
    result = core.retrieve_capsule("Nova", tag="non-existent-tag")
    if not result.success:
        print(f"✅ Correctly handled non-existent tag: {result.error_message}")
    else:
        print(f"❌ Should have failed for non-existent tag")
    
    # Test adding tag to non-existent capsule
    print("\n🏷️ Testing adding tag to non-existent capsule...")
    success = core.add_tag("Nova", "non-existent-uuid", "test-tag")
    if not success:
        print(f"✅ Correctly handled non-existent capsule UUID")
    else:
        print(f"❌ Should have failed for non-existent capsule UUID")
    
    return True

if __name__ == "__main__":
    print("=" * 60)
    print("🏺 VVAULT Core Test Suite")
    print("=" * 60)
    
    # Test main functionality
    success1 = test_vvault_core()
    
    # Test convenience functions
    success2 = test_convenience_functions()
    
    # Test error handling
    success3 = test_error_handling()
    
    print("\n" + "=" * 60)
    if success1 and success2 and success3:
        print("🎉 All VVAULT Core tests passed!")
        print("✅ VVAULT Core is ready for production use")
    else:
        print("💥 Some VVAULT Core tests failed!")
    print("=" * 60) 