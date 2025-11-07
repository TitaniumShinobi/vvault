#!/usr/bin/env python3
"""
VVAULT Schema Gate - Memory Record Validation and Merkle Chaining
Implements cutthroat schema validation for memory records with tamper-evident storage
"""

import json
import hashlib
import os
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import re

# Configure logging
logger = logging.getLogger(__name__)

@dataclass
class SchemaValidationResult:
    """Result of schema validation"""
    valid: bool
    errors: List[str]
    warnings: List[str]
    merkle_chain_updated: bool = False

class SchemaGate:
    """Memory record schema validation with Merkle chaining"""
    
    def __init__(self, merkle_chain_file: str = "indexes/merkle_chain.json"):
        self.merkle_chain_file = merkle_chain_file
        self.merkle_chain = []
        self.load_merkle_chain()
        
        # Cutthroat schema definition
        self.required_fields = [
            "memory_id", "source_id", "created_ts", "raw", "raw_sha256",
            "embed_model", "embed_dim", "embedding", "consent", "tags",
            "leaf_sha256", "prev_chain_sha256", "chain_sha256"
        ]
        
        # Field validation patterns
        self.field_patterns = {
            "memory_id": r"^[a-zA-Z0-9_-]+$",
            "source_id": r"^[a-zA-Z0-9_-]+$",
            "created_ts": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
            "raw_sha256": r"^[a-f0-9]{64}$",
            "leaf_sha256": r"^[a-f0-9]{64}$",
            "prev_chain_sha256": r"^[a-f0-9]{64}$",
            "chain_sha256": r"^[a-f0-9]{64}$"
        }
    
    def load_merkle_chain(self) -> None:
        """Load existing Merkle chain"""
        try:
            if os.path.exists(self.merkle_chain_file):
                with open(self.merkle_chain_file, 'r') as f:
                    data = json.load(f)
                    self.merkle_chain = data.get('chain', [])
                logger.info(f"Loaded Merkle chain with {len(self.merkle_chain)} entries")
            else:
                self.merkle_chain = []
                logger.info("No existing Merkle chain found, starting fresh")
        except Exception as e:
            logger.error(f"Failed to load Merkle chain: {e}")
            self.merkle_chain = []
    
    def save_merkle_chain(self) -> None:
        """Save Merkle chain to file"""
        try:
            os.makedirs(os.path.dirname(self.merkle_chain_file), exist_ok=True)
            data = {
                "last_updated": datetime.utcnow().isoformat(),
                "chain": self.merkle_chain
            }
            with open(self.merkle_chain_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved Merkle chain with {len(self.merkle_chain)} entries")
        except Exception as e:
            logger.error(f"Failed to save Merkle chain: {e}")
    
    def validate_memory_record(self, record: Dict[str, Any]) -> SchemaValidationResult:
        """
        Validate memory record against cutthroat schema
        
        Args:
            record: Memory record to validate
            
        Returns:
            SchemaValidationResult with validation status
        """
        errors = []
        warnings = []
        
        # 1. Check required fields
        for field in self.required_fields:
            if field not in record:
                errors.append(f"Missing required field: {field}")
                continue
            
            # 2. Type validation
            type_errors = self._validate_field_types(record, field)
            errors.extend(type_errors)
            
            # 3. Pattern validation
            pattern_errors = self._validate_field_patterns(record, field)
            errors.extend(pattern_errors)
        
        # 4. Content validation
        content_errors = self._validate_content(record)
        errors.extend(content_errors)
        
        # 5. Hash validation
        hash_errors = self._validate_hashes(record)
        errors.extend(hash_errors)
        
        # 6. Merkle chain validation
        chain_errors = self._validate_merkle_chain(record)
        errors.extend(chain_errors)
        
        # 7. Add to Merkle chain if valid
        merkle_updated = False
        if not errors:
            merkle_updated = self._add_to_merkle_chain(record)
        
        return SchemaValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            merkle_chain_updated=merkle_updated
        )
    
    def _validate_field_types(self, record: Dict[str, Any], field: str) -> List[str]:
        """Validate field types"""
        errors = []
        value = record.get(field)
        
        if field == "memory_id" and not isinstance(value, str):
            errors.append(f"memory_id must be string, got {type(value)}")
        elif field == "source_id" and not isinstance(value, str):
            errors.append(f"source_id must be string, got {type(value)}")
        elif field == "created_ts" and not isinstance(value, str):
            errors.append(f"created_ts must be string, got {type(value)}")
        elif field == "raw" and not isinstance(value, str):
            errors.append(f"raw must be string, got {type(value)}")
        elif field == "raw_sha256" and not isinstance(value, str):
            errors.append(f"raw_sha256 must be string, got {type(value)}")
        elif field == "embed_model" and not isinstance(value, str):
            errors.append(f"embed_model must be string, got {type(value)}")
        elif field == "embed_dim" and not isinstance(value, int):
            errors.append(f"embed_dim must be integer, got {type(value)}")
        elif field == "embedding" and not isinstance(value, list):
            errors.append(f"embedding must be list, got {type(value)}")
        elif field == "consent" and not isinstance(value, str):
            errors.append(f"consent must be string, got {type(value)}")
        elif field == "tags" and not isinstance(value, list):
            errors.append(f"tags must be list, got {type(value)}")
        elif field == "leaf_sha256" and not isinstance(value, str):
            errors.append(f"leaf_sha256 must be string, got {type(value)}")
        elif field == "prev_chain_sha256" and not isinstance(value, str):
            errors.append(f"prev_chain_sha256 must be string, got {type(value)}")
        elif field == "chain_sha256" and not isinstance(value, str):
            errors.append(f"chain_sha256 must be string, got {type(value)}")
        
        return errors
    
    def _validate_field_patterns(self, record: Dict[str, Any], field: str) -> List[str]:
        """Validate field patterns"""
        errors = []
        value = record.get(field)
        
        if field in self.field_patterns:
            pattern = self.field_patterns[field]
            if not re.match(pattern, str(value)):
                errors.append(f"{field} does not match required pattern: {pattern}")
        
        return errors
    
    def _validate_content(self, record: Dict[str, Any]) -> List[str]:
        """Validate content constraints"""
        errors = []
        
        # Raw content validation
        raw_content = record.get("raw", "")
        if len(raw_content) < 1:
            errors.append("raw content cannot be empty")
        elif len(raw_content) > 100000:  # 100KB limit
            errors.append("raw content too large (>100KB)")
        
        # Embedding validation
        embedding = record.get("embedding", [])
        embed_dim = record.get("embed_dim", 0)
        
        # Ensure embed_dim is an integer
        if not isinstance(embed_dim, int):
            errors.append(f"embed_dim must be integer, got {type(embed_dim)}")
            embed_dim = 0  # Set to 0 for further validation
        
        if len(embedding) != embed_dim:
            errors.append(f"embedding length ({len(embedding)}) does not match embed_dim ({embed_dim})")
        
        if embed_dim < 128 or embed_dim > 4096:
            errors.append(f"embed_dim must be between 128 and 4096, got {embed_dim}")
        
        # Check for NaN or infinite values in embedding
        for i, val in enumerate(embedding):
            if not isinstance(val, (int, float)) or val != val or abs(val) == float('inf'):
                errors.append(f"Invalid embedding value at index {i}: {val}")
        
        # Consent validation
        consent = record.get("consent", "")
        valid_consent = ["self", "partner", "unknown"]
        if consent not in valid_consent:
            errors.append(f"consent must be one of {valid_consent}, got {consent}")
        
        # Tags validation
        tags = record.get("tags", [])
        if not isinstance(tags, list):
            errors.append("tags must be list")
        else:
            for i, tag in enumerate(tags):
                if not isinstance(tag, str):
                    errors.append(f"tag {i} must be string, got {type(tag)}")
        
        return errors
    
    def _validate_hashes(self, record: Dict[str, Any]) -> List[str]:
        """Validate hash integrity"""
        errors = []
        
        # Verify raw_sha256 matches actual raw content
        raw_content = record.get("raw", "")
        expected_hash = record.get("raw_sha256", "")
        
        if raw_content and expected_hash:
            actual_hash = hashlib.sha256(raw_content.encode('utf-8')).hexdigest()
            if actual_hash != expected_hash:
                errors.append(f"raw_sha256 mismatch: expected {expected_hash}, got {actual_hash}")
        
        # Verify leaf_sha256 matches record content
        record_copy = record.copy()
        # Remove hash fields for leaf calculation
        for hash_field in ["leaf_sha256", "prev_chain_sha256", "chain_sha256"]:
            record_copy.pop(hash_field, None)
        
        expected_leaf = record.get("leaf_sha256", "")
        actual_leaf = hashlib.sha256(json.dumps(record_copy, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
        
        if expected_leaf and expected_leaf != actual_leaf:
            errors.append(f"leaf_sha256 mismatch: expected {expected_leaf}, got {actual_leaf}")
        
        return errors
    
    def _validate_merkle_chain(self, record: Dict[str, Any]) -> List[str]:
        """Validate Merkle chain integrity"""
        errors = []
        
        expected_prev = record.get("prev_chain_sha256", "")
        expected_chain = record.get("chain_sha256", "")
        
        # Get actual previous chain hash
        actual_prev = self.merkle_chain[-1] if self.merkle_chain else "0" * 64
        
        if expected_prev != actual_prev:
            errors.append(f"prev_chain_sha256 mismatch: expected {expected_prev}, got {actual_prev}")
        
        # Calculate expected chain hash
        leaf_hash = record.get("leaf_sha256", "")
        if leaf_hash and actual_prev:
            expected_chain_calc = hashlib.sha256((actual_prev + leaf_hash).encode()).hexdigest()
            if expected_chain and expected_chain != expected_chain_calc:
                errors.append(f"chain_sha256 mismatch: expected {expected_chain}, got {expected_chain_calc}")
        
        return errors
    
    def _add_to_merkle_chain(self, record: Dict[str, Any]) -> bool:
        """Add valid record to Merkle chain"""
        try:
            leaf_hash = record.get("leaf_sha256", "")
            if not leaf_hash:
                return False
            
            # Calculate chain hash
            prev_hash = self.merkle_chain[-1] if self.merkle_chain else ""
            chain_hash = hashlib.sha256((prev_hash + leaf_hash).encode()).hexdigest()
            
            # For empty chain, use leaf hash as chain hash
            if not prev_hash:
                chain_hash = leaf_hash
            
            # Add to chain
            self.merkle_chain.append(chain_hash)
            
            # Save chain
            self.save_merkle_chain()
            
            logger.info(f"Added to Merkle chain: {leaf_hash[:8]}... -> {chain_hash[:8]}...")
            return True
            
        except Exception as e:
            logger.error(f"Failed to add to Merkle chain: {e}")
            return False
    
    def get_merkle_chain(self) -> List[str]:
        """Get current Merkle chain"""
        return self.merkle_chain.copy()
    
    def verify_merkle_chain(self) -> bool:
        """Verify entire Merkle chain integrity"""
        # For now, just check that all entries are valid hashes
        for i, hash_val in enumerate(self.merkle_chain):
            if not re.match(r"^[a-f0-9]{64}$", hash_val):
                logger.error(f"Invalid hash format at index {i}")
                return False
        return True
    
    def create_memory_record(self, 
                           memory_id: str,
                           source_id: str,
                           raw_content: str,
                           embedding: List[float],
                           embed_model: str,
                           consent: str = "self",
                           tags: List[str] = None,
                           preprocessed: str = None) -> Dict[str, Any]:
        """
        Create a new memory record with proper hashing and Merkle chaining
        
        Args:
            memory_id: Unique identifier for the memory
            source_id: Source identifier
            raw_content: Raw text content
            embedding: Vector embedding
            embed_model: Embedding model name and version
            consent: Consent level
            tags: List of tags
            preprocessed: Preprocessed text (optional)
            
        Returns:
            Validated memory record
        """
        if tags is None:
            tags = []
        
        # Calculate hashes
        raw_sha256 = hashlib.sha256(raw_content.encode('utf-8')).hexdigest()
        
        # Create record without hash fields
        record = {
            "memory_id": memory_id,
            "source_id": source_id,
            "created_ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "raw": raw_content,
            "raw_sha256": raw_sha256,
            "pre": preprocessed,
            "embed_model": embed_model,
            "embed_dim": len(embedding),
            "embedding": embedding,
            "consent": consent,
            "tags": tags
        }
        
        # Calculate leaf hash
        leaf_sha256 = hashlib.sha256(json.dumps(record, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
        
        # Get previous chain hash
        prev_chain_sha256 = self.merkle_chain[-1] if self.merkle_chain else "0" * 64
        
        # Calculate chain hash
        chain_sha256 = hashlib.sha256((prev_chain_sha256 + leaf_sha256).encode()).hexdigest()
        
        # Add hash fields
        record.update({
            "leaf_sha256": leaf_sha256,
            "prev_chain_sha256": prev_chain_sha256,
            "chain_sha256": chain_sha256
        })
        
        return record

def validate_memory_record_file(file_path: str) -> SchemaValidationResult:
    """Validate a memory record file"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            record = json.load(f)
        
        schema_gate = SchemaGate()
        return schema_gate.validate_memory_record(record)
        
    except Exception as e:
        return SchemaValidationResult(
            valid=False,
            errors=[f"Failed to load record: {e}"],
            warnings=[],
            merkle_chain_updated=False
        )

if __name__ == "__main__":
    # Test the schema gate
    print("🧪 Testing Schema Gate...")
    
    schema_gate = SchemaGate()
    
    # Create a test record
    test_record = schema_gate.create_memory_record(
        memory_id="test_memory_001",
        source_id="test_source",
        raw_content="This is a test memory record",
        embedding=[0.1] * 384,
        embed_model="text-embedding-3-small:v1.0",
        tags=["test", "validation"]
    )
    
    # Validate the record
    result = schema_gate.validate_memory_record(test_record)
    
    print(f"✅ Valid: {result.valid}")
    print(f"🔗 Merkle chain updated: {result.merkle_chain_updated}")
    
    if result.errors:
        print(f"❌ Errors: {result.errors}")
    
    if result.warnings:
        print(f"⚠️ Warnings: {result.warnings}")
    
    # Verify Merkle chain
    chain_valid = schema_gate.verify_merkle_chain()
    print(f"🔗 Merkle chain valid: {chain_valid}")
