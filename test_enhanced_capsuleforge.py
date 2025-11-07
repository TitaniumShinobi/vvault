#!/usr/bin/env python3
"""
Test Enhanced CapsuleForge Functionality

Tests the new optional fields (identity, tether, sigil, continuity) with:
- Round-trip serialization/deserialization
- Safe defaults
- Version bumping
- Hash stability
- Validation and error handling

Author: Devon Allen Woodson
Date: 2025-08-31
"""

import os
import json
import tempfile
import shutil
from pathlib import Path
import sys

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from capsuleforge import CapsuleForge, AdditionalDataFields

def test_enhanced_fields_defaults():
    """Test that enhanced fields have safe defaults"""
    print("🧪 Testing enhanced fields defaults...")
    
    # Create AdditionalDataFields with no arguments
    fields = AdditionalDataFields()
    
    # Check defaults
    assert fields.identity == {"status": "default", "confidence": 0.5}
    assert fields.tether == {"strength": 0.5, "type": "standard"}
    assert fields.sigil == {"active": False, "pattern": "none"}
    assert fields.continuity == {"checkpoint": "initial", "version": "1.0"}
    
    print("✅ Enhanced fields defaults working correctly")

def test_enhanced_fields_custom_values():
    """Test setting custom values for enhanced fields"""
    print("🧪 Testing enhanced fields custom values...")
    
    custom_data = {
        "identity": {"status": "verified", "confidence": 0.9, "source": "biometric"},
        "tether": {"strength": 0.8, "type": "quantum", "protocol": "entangled"},
        "sigil": {"active": True, "pattern": "fractal", "complexity": 0.7},
        "continuity": {"checkpoint": "milestone_3", "version": "2.1", "stability": 0.85}
    }
    
    fields = AdditionalDataFields(
        identity=custom_data["identity"],
        tether=custom_data["tether"],
        sigil=custom_data["sigil"],
        continuity=custom_data["continuity"]
    )
    
    # Check custom values
    assert fields.identity == custom_data["identity"]
    assert fields.tether == custom_data["tether"]
    assert fields.sigil == custom_data["sigil"]
    assert fields.continuity == custom_data["continuity"]
    
    print("✅ Enhanced fields custom values working correctly")

def test_capsule_forge_enhanced_fields():
    """Test CapsuleForge with enhanced fields"""
    print("🧪 Testing CapsuleForge enhanced fields...")
    
    # Create temporary directory for testing
    temp_dir = tempfile.mkdtemp()
    try:
        forge = CapsuleForge(vault_path=temp_dir)
        
        # Test data
        traits = {
            "creativity": 0.9,
            "empathy": 0.8,
            "curiosity": 0.7
        }
        
        memory_log = [
            "Enhanced identity verification completed",
            "Quantum tether established",
            "Sigil pattern activated"
        ]
        
        # Test with enhanced fields
        enhanced_data = {
            "identity": {"status": "verified", "confidence": 0.9},
            "tether": {"strength": 0.8, "type": "quantum"},
            "sigil": {"active": True, "pattern": "fractal"},
            "continuity": {"checkpoint": "milestone_1", "version": "1.1"}
        }
        
        # Generate capsule with enhanced fields
        capsule_path = forge.generate_capsule(
            instance_name="TestNova",
            traits=traits,
            memory_log=memory_log,
            personality_type="INFJ",
            additional_data=enhanced_data
        )
        
        # Verify capsule was created
        assert os.path.exists(capsule_path)
        print(f"✅ Enhanced capsule created: {capsule_path}")
        
        # Load the capsule back
        loaded_capsule = forge.load_capsule(capsule_path)
        
        # Check version was bumped
        assert loaded_capsule.metadata.capsule_version == "1.1.0"
        print(f"✅ Version bumped to: {loaded_capsule.metadata.capsule_version}")
        
        # Check enhanced fields were preserved
        assert loaded_capsule.additional_data.identity == enhanced_data["identity"]
        assert loaded_capsule.additional_data.tether == enhanced_data["tether"]
        assert loaded_capsule.additional_data.sigil == enhanced_data["sigil"]
        assert loaded_capsule.additional_data.continuity == enhanced_data["continuity"]
        print("✅ Enhanced fields preserved correctly")
        
        # Test round-trip hash stability
        original_fingerprint = loaded_capsule.metadata.fingerprint_hash
        recalculated_fingerprint = forge.calculate_fingerprint(loaded_capsule)
        assert original_fingerprint == recalculated_fingerprint
        print("✅ Hash stability maintained")
        
    finally:
        # Cleanup
        shutil.rmtree(temp_dir)

def test_capsule_forge_standard_fields():
    """Test CapsuleForge without enhanced fields (backward compatibility)"""
    print("🧪 Testing CapsuleForge standard fields (backward compatibility)...")
    
    temp_dir = tempfile.mkdtemp()
    try:
        forge = CapsuleForge(vault_path=temp_dir)
        
        traits = {"creativity": 0.8, "empathy": 0.7}
        memory_log = ["Standard memory entry"]
        
        # Generate capsule without enhanced fields
        capsule_path = forge.generate_capsule(
            instance_name="TestNova",
            traits=traits,
            memory_log=memory_log,
            personality_type="INFJ"
            # No additional_data
        )
        
        # Verify capsule was created
        assert os.path.exists(capsule_path)
        print(f"✅ Standard capsule created: {capsule_path}")
        
        # Load the capsule back
        loaded_capsule = forge.load_capsule(capsule_path)
        
        # Check version was NOT bumped
        assert loaded_capsule.metadata.capsule_version == "1.0.0"
        print(f"✅ Version remained at: {loaded_capsule.metadata.capsule_version}")
        
        # Check default enhanced fields
        assert loaded_capsule.additional_data.identity == {"status": "default", "confidence": 0.5}
        assert loaded_capsule.additional_data.tether == {"strength": 0.5, "type": "standard"}
        print("✅ Default enhanced fields working correctly")
        
    finally:
        shutil.rmtree(temp_dir)

def test_validation_and_error_handling():
    """Test validation and error handling for enhanced fields"""
    print("🧪 Testing validation and error handling...")
    
    temp_dir = tempfile.mkdtemp()
    try:
        forge = CapsuleForge(vault_path=temp_dir)
        
        traits = {"creativity": 0.8}
        memory_log = ["Test memory"]
        
        # Test with invalid data types
        invalid_data = {
            "identity": "not_a_dict",  # Should be dict
            "tether": 123,  # Should be dict
            "sigil": ["list", "not", "dict"],  # Should be dict
            "continuity": None  # Should be dict
        }
        
        # This should not raise an exception, but should log warnings
        capsule_path = forge.generate_capsule(
            instance_name="TestNova",
            traits=traits,
            memory_log=memory_log,
            personality_type="INFJ",
            additional_data=invalid_data
        )
        
        # Verify capsule was created despite invalid data
        assert os.path.exists(capsule_path)
        print("✅ Capsule created despite invalid data (validation working)")
        
        # Load and check that defaults were used
        loaded_capsule = forge.load_capsule(capsule_path)
        assert loaded_capsule.additional_data.identity == {"status": "default", "confidence": 0.5}
        assert loaded_capsule.additional_data.tether == {"strength": 0.5, "type": "standard"}
        print("✅ Defaults used for invalid data")
        
    finally:
        shutil.rmtree(temp_dir)

def test_enhanced_fields_info():
    """Test the enhanced fields information method"""
    print("🧪 Testing enhanced fields info method...")
    
    forge = CapsuleForge()
    
    # Test with no data
    info = forge.get_enhanced_fields_info(None)
    assert info["count"] == 0
    assert info["enhanced_fields"] == []
    print("✅ No data info correct")
    
    # Test with empty data
    info = forge.get_enhanced_fields_info({})
    assert info["count"] == 0
    assert info["enhanced_fields"] == []
    print("✅ Empty data info correct")
    
    # Test with some enhanced fields
    partial_data = {
        "identity": {"status": "verified"},
        "tether": {"strength": 0.8}
    }
    info = forge.get_enhanced_fields_info(partial_data)
    assert info["count"] == 2
    assert "identity" in info["enhanced_fields"]
    assert "tether" in info["enhanced_fields"]
    assert info["types"]["identity"] == "dict"
    print("✅ Partial enhanced fields info correct")
    
    # Test with all enhanced fields
    full_data = {
        "identity": {"status": "verified"},
        "tether": {"strength": 0.8},
        "sigil": {"active": True},
        "continuity": {"checkpoint": "milestone_1"}
    }
    info = forge.get_enhanced_fields_info(full_data)
    assert info["count"] == 4
    assert len(info["enhanced_fields"]) == 4
    print("✅ Full enhanced fields info correct")

def main():
    """Run all tests"""
    print("🚀 Starting Enhanced CapsuleForge Tests")
    print("=" * 50)
    
    try:
        test_enhanced_fields_defaults()
        test_enhanced_fields_custom_values()
        test_capsule_forge_enhanced_fields()
        test_capsule_forge_standard_fields()
        test_validation_and_error_handling()
        test_enhanced_fields_info()
        
        print("\n" + "=" * 50)
        print("✅ All tests passed! Enhanced CapsuleForge working correctly")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())

