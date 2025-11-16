#!/usr/bin/env python3
"""
Test Current State - Validate current VVAULT capsules against cutthroat schema
Shows what's missing and what needs to be implemented
"""

import json
import hashlib
import os
from datetime import datetime
from capsule_validator import CapsuleValidator, ValidationResult

def analyze_current_capsule():
    """Analyze the current VVAULT capsule structure"""
    print("🔍 Analyzing Current VVAULT State")
    print("=" * 50)
    
    # Load current capsule
    capsule_path = "capsules/nova-001.capsule"
    if not os.path.exists(capsule_path):
        print(f"❌ Capsule not found: {capsule_path}")
        return
    
    with open(capsule_path, 'r') as f:
        current_capsule = json.load(f)
    
    print(f"📦 Current capsule structure:")
    print(f"   - Instance: {current_capsule.get('metadata', {}).get('instance_name', 'unknown')}")
    print(f"   - UUID: {current_capsule.get('metadata', {}).get('uuid', 'unknown')}")
    print(f"   - Timestamp: {current_capsule.get('metadata', {}).get('timestamp', 'unknown')}")
    print(f"   - Fingerprint: {current_capsule.get('metadata', {}).get('fingerprint_hash', 'unknown')}")
    
    # Check what's missing for the new schema
    print(f"\n❌ MISSING FOR NEW SCHEMA:")
    
    missing_fields = [
        "memory_id",
        "source_id", 
        "created_ts",
        "raw",
        "raw_sha256",
        "embed_model",
        "embedding"
    ]
    
    for field in missing_fields:
        if field not in current_capsule:
            print(f"   - {field}: NOT PRESENT")
        else:
            print(f"   - {field}: PRESENT")
    
    # Check memory structure
    memory = current_capsule.get('memory', {})
    print(f"\n📝 Current Memory Structure:")
    print(f"   - Short term: {len(memory.get('short_term_memories', []))} entries")
    print(f"   - Long term: {len(memory.get('long_term_memories', []))} entries")
    print(f"   - Emotional: {len(memory.get('emotional_memories', []))} entries")
    print(f"   - Procedural: {len(memory.get('procedural_memories', []))} entries")
    print(f"   - Episodic: {len(memory.get('episodic_memories', []))} entries")
    
    # Show sample memory entries
    print(f"\n📋 Sample Memory Entries:")
    for mem_type, entries in memory.items():
        if entries and isinstance(entries, list):
            print(f"   {mem_type}:")
            for i, entry in enumerate(entries[:2]):  # Show first 2
                print(f"     {i+1}. {entry[:100]}...")
    
    return current_capsule

def create_compliant_memory_records(capsule_data):
    """Create compliant memory records from current capsule"""
    print(f"\n🔄 Creating Compliant Memory Records")
    print("=" * 50)
    
    memory = capsule_data.get('memory', {})
    compliant_records = []
    
    # Process each memory type
    for mem_type, entries in memory.items():
        if not entries or not isinstance(entries, list):
            continue
            
        for i, entry in enumerate(entries):
            # Create compliant record
            record = {
                "memory_id": f"{capsule_data['metadata']['instance_name'].lower()}_{mem_type}_{i}_{hash(entry) % 10000:04d}",
                "source_id": f"vvault_{capsule_data['metadata']['instance_name'].lower()}_{mem_type}",
                "created_ts": datetime.fromisoformat(capsule_data['metadata']['timestamp'].replace('+00:00', 'Z')).strftime('%Y-%m-%dT%H:%M:%SZ'),
                "raw": entry,
                "raw_sha256": hashlib.sha256(entry.encode('utf-8')).hexdigest(),
                "embed_model": "text-embedding-3-small:v1.0",  # Placeholder
                "embedding": [0.0] * 1536,  # Placeholder embedding
                "consent": "self",
                "tags": [mem_type, capsule_data['metadata']['instance_name']],
                "metadata": {
                    "version": "1.0.0",
                    "fingerprint": capsule_data['metadata']['fingerprint_hash'],
                    "environment": capsule_data.get('environment', {})
                }
            }
            
            compliant_records.append(record)
    
    print(f"✅ Created {len(compliant_records)} compliant memory records")
    
    # Show sample record
    if compliant_records:
        print(f"\n📋 Sample Compliant Record:")
        sample = compliant_records[0]
        for key, value in sample.items():
            if key == 'embedding':
                print(f"   {key}: [{len(value)} dimensions]")
            elif key == 'raw' and len(str(value)) > 100:
                print(f"   {key}: {str(value)[:100]}...")
            else:
                print(f"   {key}: {value}")
    
    return compliant_records

def validate_compliant_records(records):
    """Validate the compliant records"""
    print(f"\n🔍 Validating Compliant Records")
    print("=" * 50)
    
    validator = CapsuleValidator()
    valid_count = 0
    total_count = len(records)
    
    for i, record in enumerate(records):
        result = validator.validate_capsule(record)
        
        if result.valid:
            valid_count += 1
        else:
            print(f"❌ Record {i+1} failed validation:")
            for error in result.errors:
                print(f"   - {error}")
    
    print(f"✅ Validation Results:")
    print(f"   - Valid: {valid_count}/{total_count}")
    print(f"   - Success Rate: {valid_count/total_count*100:.1f}%")
    
    return valid_count == total_count

def generate_implementation_plan():
    """Generate implementation plan"""
    print(f"\n📋 IMPLEMENTATION PLAN")
    print("=" * 50)
    
    print("1. 🔧 IMMEDIATE FIXES NEEDED:")
    print("   - Add embedding pipeline (ChromaDB integration)")
    print("   - Implement proper memory_id generation")
    print("   - Add source_id tracking")
    print("   - Fix timestamp format (ISO 8601)")
    print("   - Add raw_sha256 calculation")
    print("   - Pin embedding model versions")
    
    print("\n2. 🏗️ INFRASTRUCTURE NEEDED:")
    print("   - Vector database (ChromaDB/FAISS)")
    print("   - Embedding model integration")
    print("   - RAG retrieval pipeline")
    print("   - Memory deduplication")
    print("   - Version control for embeddings")
    
    print("\n3. 🛡️ SECURITY IMPLEMENTATION:")
    print("   - Canary token integration")
    print("   - Leak detection pipeline")
    print("   - Audit logging")
    print("   - Tamper detection")
    print("   - Access controls")
    
    print("\n4. 📊 MONITORING & EVALUATION:")
    print("   - RAG evaluation harness")
    print("   - Precision/recall metrics")
    print("   - Drift detection")
    print("   - Performance monitoring")
    print("   - Alert system")

def main():
    """Main test function"""
    print("🧪 VVAULT Current State Analysis")
    print("=" * 60)
    
    # Analyze current state
    capsule_data = analyze_current_capsule()
    if not capsule_data:
        return
    
    # Create compliant records
    compliant_records = create_compliant_memory_records(capsule_data)
    
    # Validate records
    validation_passed = validate_compliant_records(compliant_records)
    
    # Save sample records
    if compliant_records:
        sample_file = "logs/sample_compliant_records.json"
        with open(sample_file, 'w') as f:
            json.dump(compliant_records[:5], f, indent=2)  # Save first 5
        print(f"\n💾 Saved sample records to {sample_file}")
    
    # Generate implementation plan
    generate_implementation_plan()
    
    print(f"\n🎯 SUMMARY:")
    print(f"   - Current capsules: NOT COMPLIANT with new schema")
    print(f"   - Missing: Vector embeddings, proper IDs, versioning")
    print(f"   - Need: RAG pipeline, security monitoring, evaluation")
    print(f"   - Status: FOUNDATION EXISTS, NEEDS UPGRADE")

if __name__ == "__main__":
    main()
