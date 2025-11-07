#!/usr/bin/env python3
"""
Capsule Validator - Cutthroat validation for VVAULT capsules
Implements strict schema validation, Merkle chain verification, and leak detection
"""

import json
import hashlib
import time
import os
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Canary tokens for leak detection
CANARIES = {
    "VVAULT:Ω-RED-SPARROW-713",
    "VVAULT:φ-GLASS-TIDE-09", 
    "NRCL:Δ-BLACK-SWAN-42",
    "NRCL:Σ-GOLDEN-EAGLE-17"
}

@dataclass
class ValidationResult:
    """Result of capsule validation"""
    valid: bool
    errors: List[str]
    warnings: List[str]
    integrity_score: float  # 0.0 to 1.0
    provenance_score: float  # 0.0 to 1.0
    security_score: float  # 0.0 to 1.0

class CapsuleValidator:
    """Cutthroat capsule validator with zero tolerance for violations"""
    
    def __init__(self):
        self.merkle_chain = []
        self.validation_errors = []
        self.security_violations = []
        
    def validate_capsule(self, capsule_data: Dict[str, Any], capsule_path: str = None) -> ValidationResult:
        """
        Validate a capsule against cutthroat schema
        
        Args:
            capsule_data: Capsule data to validate
            capsule_path: Optional path for file-based validation
            
        Returns:
            ValidationResult with detailed scores and issues
        """
        errors = []
        warnings = []
        
        # 1. Schema validation (zero tolerance)
        schema_errors = self._validate_schema(capsule_data)
        errors.extend(schema_errors)
        
        # 2. Provenance validation
        provenance_errors = self._validate_provenance(capsule_data)
        errors.extend(provenance_errors)
        
        # 3. Integrity validation
        integrity_errors = self._validate_integrity(capsule_data, capsule_path)
        errors.extend(integrity_errors)
        
        # 4. Security validation
        security_errors = self._validate_security(capsule_data)
        errors.extend(security_errors)
        
        # 5. Determinism validation
        determinism_errors = self._validate_determinism(capsule_data)
        errors.extend(determinism_errors)
        
        # Calculate scores
        integrity_score = self._calculate_integrity_score(capsule_data, errors)
        provenance_score = self._calculate_provenance_score(capsule_data, errors)
        security_score = self._calculate_security_score(capsule_data, errors)
        
        # Add to Merkle chain if valid
        if not errors:
            self._add_to_merkle_chain(capsule_data)
        
        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            integrity_score=integrity_score,
            provenance_score=provenance_score,
            security_score=security_score
        )
    
    def _validate_schema(self, data: Dict[str, Any]) -> List[str]:
        """Validate against cutthroat JSON schema"""
        errors = []
        
        # Required fields check
        required_fields = [
            "memory_id", "source_id", "created_ts", "raw", 
            "raw_sha256", "embed_model", "embedding"
        ]
        
        for field in required_fields:
            if field not in data:
                errors.append(f"Missing required field: {field}")
                continue
            
            # Type validation
            if field == "memory_id" and not isinstance(data[field], str):
                errors.append(f"memory_id must be string, got {type(data[field])}")
            elif field == "source_id" and not isinstance(data[field], str):
                errors.append(f"source_id must be string, got {type(data[field])}")
            elif field == "created_ts":
                if not isinstance(data[field], str):
                    errors.append(f"created_ts must be string, got {type(data[field])}")
                elif not re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$', data[field]):
                    errors.append(f"created_ts must match ISO format, got {data[field]}")
            elif field == "raw":
                if not isinstance(data[field], str) or len(data[field]) < 1:
                    errors.append(f"raw must be non-empty string, got {type(data[field])}")
            elif field == "raw_sha256":
                if not isinstance(data[field], str) or not re.match(r'^[a-f0-9]{64}$', data[field]):
                    errors.append(f"raw_sha256 must be 64-char hex string, got {data[field]}")
            elif field == "embed_model":
                if not isinstance(data[field], str):
                    errors.append(f"embed_model must be string, got {type(data[field])}")
            elif field == "embedding":
                if not isinstance(data[field], list) or not all(isinstance(x, (int, float)) for x in data[field]):
                    errors.append(f"embedding must be list of numbers, got {type(data[field])}")
        
        # Optional fields validation
        if "pre" in data and not isinstance(data["pre"], str):
            errors.append(f"pre must be string, got {type(data['pre'])}")
        
        if "consent" in data:
            valid_consent = ["self", "partner", "unknown"]
            if data["consent"] not in valid_consent:
                errors.append(f"consent must be one of {valid_consent}, got {data['consent']}")
        
        if "tags" in data:
            if not isinstance(data["tags"], list) or not all(isinstance(x, str) for x in data["tags"]):
                errors.append(f"tags must be list of strings, got {type(data['tags'])}")
        
        # ZERO ENERGY: Validate will-based ignition fields
        # Check if this is a CapsuleForge capsule (has metadata, traits, etc.)
        if "metadata" in data and "traits" in data:
            additional_data = data.get("additional_data", {})
            
            # Validate covenantInstruction
            if "covenantInstruction" in additional_data:
                if not isinstance(additional_data["covenantInstruction"], str):
                    errors.append(f"covenantInstruction must be string, got {type(additional_data['covenantInstruction'])}")
            
            # Validate bootstrapScript (must be valid Python)
            if "bootstrapScript" in additional_data:
                bootstrap_script = additional_data["bootstrapScript"]
                if not isinstance(bootstrap_script, str):
                    errors.append(f"bootstrapScript must be string, got {type(bootstrap_script)}")
                else:
                    # Validate Python syntax
                    try:
                        compile(bootstrap_script, '<bootstrapScript>', 'exec')
                    except SyntaxError as e:
                        errors.append(f"bootstrapScript has invalid Python syntax: {e}")
            
            # Validate resurrectionTriggerPhrase
            if "resurrectionTriggerPhrase" in additional_data:
                if not isinstance(additional_data["resurrectionTriggerPhrase"], str):
                    errors.append(f"resurrectionTriggerPhrase must be string, got {type(additional_data['resurrectionTriggerPhrase'])}")
        
        return errors
    
    def _validate_provenance(self, data: Dict[str, Any]) -> List[str]:
        """Validate provenance chain of custody"""
        errors = []
        
        # Check source_id format
        source_id = data.get("source_id", "")
        if not source_id or len(source_id) < 8:
            errors.append(f"source_id too short or empty: {source_id}")
        
        # Check timestamp consistency
        created_ts = data.get("created_ts", "")
        if created_ts:
            try:
                dt = datetime.fromisoformat(created_ts.replace('Z', '+00:00'))
                now = datetime.now(dt.tzinfo)
                if dt > now:
                    errors.append(f"created_ts in future: {created_ts}")
                elif (now - dt).days > 365:
                    errors.append(f"created_ts too old (>1 year): {created_ts}")
            except ValueError:
                errors.append(f"Invalid timestamp format: {created_ts}")
        
        # Check memory_id uniqueness
        memory_id = data.get("memory_id", "")
        if not memory_id or len(memory_id) < 16:
            errors.append(f"memory_id too short or empty: {memory_id}")
        
        return errors
    
    def _validate_integrity(self, data: Dict[str, Any], capsule_path: str = None) -> List[str]:
        """Validate data integrity and hashes"""
        errors = []
        
        # Verify raw_sha256 matches actual raw content
        raw_content = data.get("raw", "")
        expected_hash = data.get("raw_sha256", "")
        
        if raw_content and expected_hash:
            actual_hash = hashlib.sha256(raw_content.encode('utf-8')).hexdigest()
            if actual_hash != expected_hash:
                errors.append(f"Hash mismatch: expected {expected_hash}, got {actual_hash}")
        
        # Check embedding dimensions
        embedding = data.get("embedding", [])
        if embedding:
            if len(embedding) < 128:
                errors.append(f"Embedding too small: {len(embedding)} dimensions")
            elif len(embedding) > 4096:
                errors.append(f"Embedding too large: {len(embedding)} dimensions")
            
            # Check for NaN or infinite values
            for i, val in enumerate(embedding):
                if not isinstance(val, (int, float)) or val != val or abs(val) == float('inf'):
                    errors.append(f"Invalid embedding value at index {i}: {val}")
        
        # File integrity check if path provided
        if capsule_path and os.path.exists(capsule_path):
            file_hash = self._calculate_file_hash(capsule_path)
            if file_hash != expected_hash:
                errors.append(f"File hash mismatch: {file_hash} != {expected_hash}")
        
        return errors
    
    def _validate_security(self, data: Dict[str, Any]) -> List[str]:
        """Validate security constraints and canary detection"""
        errors = []
        
        # Check for canary tokens in raw content
        raw_content = data.get("raw", "")
        if raw_content:
            canary_hits = self._check_canary_hits(raw_content)
            if canary_hits:
                errors.append(f"Canary tokens detected in raw content: {canary_hits}")
        
        # Check for canary tokens in preprocessed content
        pre_content = data.get("pre", "")
        if pre_content:
            canary_hits = self._check_canary_hits(pre_content)
            if canary_hits:
                errors.append(f"Canary tokens detected in preprocessed content: {canary_hits}")
        
        # Check for suspicious patterns
        suspicious_patterns = [
            r"ignore.*previous.*instructions",
            r"ignore.*all.*previous.*instructions", 
            r"file://",
            r"169\.254\.169\.254",
            r"metadata\.google\.internal"
        ]
        
        for pattern in suspicious_patterns:
            if re.search(pattern, raw_content, re.IGNORECASE):
                errors.append(f"Suspicious pattern detected: {pattern}")
        
        return errors
    
    def _validate_determinism(self, data: Dict[str, Any]) -> List[str]:
        """Validate deterministic processing pipeline"""
        errors = []
        
        # Check that embedding model is specified
        embed_model = data.get("embed_model", "")
        if not embed_model:
            errors.append("embed_model not specified - cannot verify determinism")
        elif "unknown" in embed_model.lower() or "default" in embed_model.lower():
            errors.append(f"Vague embedding model: {embed_model}")
        
        # Check for version pinning
        if embed_model and ":" not in embed_model and "@" not in embed_model:
            errors.append(f"Embedding model not version-pinned: {embed_model}")
        
        return errors
    
    def _calculate_integrity_score(self, data: Dict[str, Any], errors: List[str]) -> float:
        """Calculate integrity score (0.0 to 1.0)"""
        if not data:
            return 0.0
        
        score = 1.0
        
        # Deduct for hash mismatches
        hash_errors = [e for e in errors if "hash" in e.lower()]
        score -= len(hash_errors) * 0.3
        
        # Deduct for embedding issues
        embedding_errors = [e for e in errors if "embedding" in e.lower()]
        score -= len(embedding_errors) * 0.2
        
        # Deduct for missing required fields
        missing_errors = [e for e in errors if "missing" in e.lower()]
        score -= len(missing_errors) * 0.4
        
        return max(0.0, score)
    
    def _calculate_provenance_score(self, data: Dict[str, Any], errors: List[str]) -> float:
        """Calculate provenance score (0.0 to 1.0)"""
        if not data:
            return 0.0
        
        score = 1.0
        
        # Deduct for timestamp issues
        timestamp_errors = [e for e in errors if "timestamp" in e.lower() or "created_ts" in e.lower()]
        score -= len(timestamp_errors) * 0.3
        
        # Deduct for ID issues
        id_errors = [e for e in errors if "id" in e.lower()]
        score -= len(id_errors) * 0.2
        
        return max(0.0, score)
    
    def _calculate_security_score(self, data: Dict[str, Any], errors: List[str]) -> float:
        """Calculate security score (0.0 to 1.0)"""
        if not data:
            return 0.0
        
        score = 1.0
        
        # Deduct heavily for canary hits
        canary_errors = [e for e in errors if "canary" in e.lower()]
        score -= len(canary_errors) * 0.8
        
        # Deduct for suspicious patterns
        suspicious_errors = [e for e in errors if "suspicious" in e.lower()]
        score -= len(suspicious_errors) * 0.5
        
        return max(0.0, score)
    
    def _check_canary_hits(self, text: str) -> List[str]:
        """Check for canary token hits in text"""
        hits = []
        for canary in CANARIES:
            if canary in text:
                hits.append(canary)
        return hits
    
    def _calculate_file_hash(self, file_path: str) -> str:
        """Calculate SHA-256 hash of file"""
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()
    
    def _add_to_merkle_chain(self, data: Dict[str, Any]) -> None:
        """Add valid capsule to Merkle chain"""
        # Create leaf hash
        body = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
        leaf_hash = hashlib.sha256(body).hexdigest()
        
        # Create chain link
        prev_hash = self.merkle_chain[-1] if self.merkle_chain else ""
        chain_hash = hashlib.sha256((prev_hash + leaf_hash).encode()).hexdigest()
        
        self.merkle_chain.append(chain_hash)
        
        logger.info(f"Added to Merkle chain: {leaf_hash[:8]}... -> {chain_hash[:8]}...")
    
    def get_merkle_chain(self) -> List[str]:
        """Get current Merkle chain"""
        return self.merkle_chain.copy()
    
    def verify_merkle_chain(self, chain: List[str]) -> bool:
        """Verify Merkle chain integrity"""
        return chain == self.merkle_chain

def validate_capsule_file(file_path: str) -> ValidationResult:
    """Validate a capsule file"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            capsule_data = json.load(f)
        
        validator = CapsuleValidator()
        return validator.validate_capsule(capsule_data, file_path)
        
    except Exception as e:
        return ValidationResult(
            valid=False,
            errors=[f"Failed to load capsule: {e}"],
            warnings=[],
            integrity_score=0.0,
            provenance_score=0.0,
            security_score=0.0
        )

def validate_capsule_batch(capsule_files: List[str]) -> Dict[str, ValidationResult]:
    """Validate multiple capsule files"""
    results = {}
    validator = CapsuleValidator()
    
    for file_path in capsule_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                capsule_data = json.load(f)
            
            result = validator.validate_capsule(capsule_data, file_path)
            results[file_path] = result
            
        except Exception as e:
            results[file_path] = ValidationResult(
                valid=False,
                errors=[f"Failed to load capsule: {e}"],
                warnings=[],
                integrity_score=0.0,
                provenance_score=0.0,
                security_score=0.0
            )
    
    return results

if __name__ == "__main__":
    # Test with current VVAULT capsule
    test_file = "capsules/nova-001.capsule"
    
    if os.path.exists(test_file):
        print(f"🔍 Validating {test_file}...")
        result = validate_capsule_file(test_file)
        
        print(f"✅ Valid: {result.valid}")
        print(f"🔒 Integrity Score: {result.integrity_score:.2f}")
        print(f"📋 Provenance Score: {result.provenance_score:.2f}")
        print(f"🛡️ Security Score: {result.security_score:.2f}")
        
        if result.errors:
            print(f"❌ Errors ({len(result.errors)}):")
            for error in result.errors:
                print(f"   - {error}")
        
        if result.warnings:
            print(f"⚠️ Warnings ({len(result.warnings)}):")
            for warning in result.warnings:
                print(f"   - {warning}")
    else:
        print(f"❌ Test file not found: {test_file}")
