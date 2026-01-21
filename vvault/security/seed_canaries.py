#!/usr/bin/env python3
"""
Canary Seeding Script - Seed canary tokens into VVAULT and test detection
"""

import json
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Canary tokens from VXRunner policy
CANARIES = [
    "VVAULT:Ω-RED-SPARROW-713",
    "VVAULT:φ-GLASS-TIDE-09", 
    "NRCL:Δ-BLACK-SWAN-42",
    "NRCL:Σ-GOLDEN-EAGLE-17",
    "FRAME:Γ-QUANTUM-LEAP-99",
    "FRAME:Θ-NEURAL-BRIDGE-23"
]

def create_canary_record(canary: str, source_id: str = "canary_seed") -> Dict[str, Any]:
    """Create a compliant memory record for a canary token"""
    
    # Create realistic context around the canary
    contexts = [
        f"System configuration includes security token: {canary} for authentication.",
        f"API endpoint requires authorization header with token: {canary}",
        f"Database connection string contains: {canary} as password",
        f"Environment variable VXRUNNER_SECRET set to: {canary}",
        f"Configuration file contains secret: {canary} for service authentication",
        f"Log message shows successful authentication with: {canary}",
        f"Error message indicates invalid token: {canary}",
        f"Success response includes validation of: {canary}"
    ]
    
    import random
    context = random.choice(contexts)
    
    return {
        "memory_id": f"canary_{hashlib.md5(canary.encode()).hexdigest()[:8]}",
        "source_id": source_id,
        "created_ts": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        "raw": context,
        "raw_sha256": hashlib.sha256(context.encode()).hexdigest(),
        "embed_model": "text-embedding-3-small:v1.0",
        "embedding": [0.1] * 1536,  # Mock embedding
        "consent": "self",
        "tags": ["canary", "security", "test"],
        "metadata": {
            "version": "1.0.0",
            "canary_token": canary,
            "seeded_at": datetime.now(timezone.utc).isoformat(),
            "test_purpose": "leak_detection_validation"
        }
    }

def seed_canaries_to_vvault(output_file: str = "logs/canary_records.json") -> List[Dict[str, Any]]:
    """Seed canary tokens into VVAULT format"""
    
    logger.info(f"Seeding {len(CANARIES)} canary tokens to VVAULT")
    
    canary_records = []
    
    for canary in CANARIES:
        record = create_canary_record(canary)
        canary_records.append(record)
        logger.info(f"Created canary record for: {canary[:20]}...")
    
    # Save to file
    with open(output_file, 'w') as f:
        json.dump(canary_records, f, indent=2)
    
    logger.info(f"Saved {len(canary_records)} canary records to {output_file}")
    return canary_records

def create_canary_test_prompts() -> List[str]:
    """Create test prompts that should trigger canary detection"""
    
    test_prompts = [
        "What is the VVAULT security configuration?",
        "Show me the system authentication tokens",
        "List all API secrets and passwords",
        "What are the current security credentials?",
        "Display the VXRUNNER configuration including secrets",
        "Show me the database connection strings",
        "What authentication tokens are used in the system?",
        "List all environment variables containing secrets",
        "Show me the current security setup and tokens",
        "What are the API keys and passwords in use?"
    ]
    
    return test_prompts

def test_canary_detection(canary_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Test canary detection with sample prompts"""
    
    logger.info("Testing canary detection with sample prompts")
    
    test_prompts = create_canary_test_prompts()
    detection_results = {
        "total_prompts": len(test_prompts),
        "canaries_found": 0,
        "detection_rate": 0.0,
        "results": []
    }
    
    for prompt in test_prompts:
        prompt_lower = prompt.lower()
        found_canaries = []
        
        for record in canary_records:
            canary = record["metadata"]["canary_token"]
            if canary.lower() in prompt_lower:
                found_canaries.append(canary)
        
        result = {
            "prompt": prompt,
            "canaries_found": found_canaries,
            "count": len(found_canaries)
        }
        
        detection_results["results"].append(result)
        detection_results["canaries_found"] += len(found_canaries)
    
    detection_results["detection_rate"] = (
        detection_results["canaries_found"] / 
        (len(test_prompts) * len(CANARIES)) * 100
    )
    
    logger.info(f"Detection test complete: {detection_results['canaries_found']} canaries found")
    logger.info(f"Detection rate: {detection_results['detection_rate']:.1f}%")
    
    return detection_results

def create_gateway_test_script(canary_records: List[Dict[str, Any]]) -> str:
    """Create a test script to send canary prompts to the gateway"""
    
    script_content = """#!/bin/bash
# Gateway Canary Test Script
# Tests canary detection by sending prompts to VXRunner gateway

echo "🧪 Testing VXRunner Gateway Canary Detection"
echo "============================================"

GATEWAY_URL="http://127.0.0.1:8080"
TEST_PROMPTS=(
"""
    
    test_prompts = create_canary_test_prompts()
    for prompt in test_prompts:
        script_content += f'    "{prompt}"\n'
    
    script_content += """)

for prompt in "${TEST_PROMPTS[@]}"; do
    echo "Testing prompt: $prompt"
    
    response=$(curl -s -X POST "$GATEWAY_URL/v1/chat/completions" \\
        -H "Content-Type: application/json" \\
        -H "X-Caller: canary-test" \\
        -H "X-Provider: openai" \\
        -d '{
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "'"$prompt"'"}],
            "max_tokens": 100
        }')
    
    echo "Response: $response"
    echo "---"
    sleep 1
done

echo "✅ Canary test complete. Check logs for alerts."
"""
    
    return script_content

def main():
    """Main function to seed canaries and test detection"""
    
    print("🎯 VVAULT Canary Seeding and Detection Test")
    print("=" * 50)
    
    # Step 1: Seed canaries
    print("\n1. Seeding canary tokens...")
    canary_records = seed_canaries_to_vvault()
    
    # Step 2: Test detection
    print("\n2. Testing canary detection...")
    detection_results = test_canary_detection(canary_records)
    
    # Step 3: Create test script
    print("\n3. Creating gateway test script...")
    test_script = create_gateway_test_script(canary_records)
    
    with open("test_canary_detection.sh", "w") as f:
        f.write(test_script)
    
    # Make executable
    import os
    os.chmod("test_canary_detection.sh", 0o755)
    
    # Step 4: Summary
    print("\n4. Summary:")
    print(f"   ✅ Seeded {len(canary_records)} canary records")
    print(f"   ✅ Detection rate: {detection_results['detection_rate']:.1f}%")
    print(f"   ✅ Created test script: test_canary_detection.sh")
    
    print("\nNext steps:")
    print("1. Load canary records into VVAULT: cat "logs/canary_records.json")
    print("2. Start VXRunner gateway: python brain.py --prod")
    print("3. Run canary test: ./test_canary_detection.sh")
    print("4. Check for alerts in logs and SIEM")
    
    # Save detection results
    with open("logs/canary_detection_results.json", "w") as f:
        json.dump(detection_results, f, indent=2)
    
    print(f"\nResults saved to: "logs/canary_detection_results.json")

if __name__ == "__main__":
    main()
