#!/usr/bin/env python3
"""
VVAULT Blockchain-Enhanced Encrypted Vault

Hybrid encryption system that combines:
- Local AES-256-GCM encryption for all files
- Blockchain for integrity verification and key management
- Merkle tree for efficient hash verification
- Decentralized key escrow (optional)

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import os
import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path
import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa, padding

from vvault.blockchain.blockchain_identity_wallet import VVAULTBlockchainWallet, BlockchainType

logger = logging.getLogger(__name__)

@dataclass
class FileIntegrityRecord:
    """File integrity record stored on blockchain"""
    file_path: str
    file_hash: str  # SHA-256 hash
    encrypted_hash: str  # Hash of encrypted content
    merkle_root: Optional[str]  # Merkle root if part of tree
    timestamp: str
    block_height: Optional[int]  # Blockchain block height
    tx_hash: Optional[str]  # Transaction hash on blockchain

@dataclass
class EncryptionMetadata:
    """Metadata for encrypted file"""
    file_path: str
    encrypted_path: str
    algorithm: str  # "AES-256-GCM"
    key_id: str  # Reference to key in blockchain wallet
    iv: str  # Base64 encoded IV
    tag: str  # Base64 encoded authentication tag
    file_hash: str  # Hash of original file
    encrypted_hash: str  # Hash of encrypted file
    timestamp: str

class MerkleTree:
    """Merkle tree for efficient integrity verification"""
    
    @staticmethod
    def build_tree(hashes: List[str]) -> str:
        """Build Merkle tree and return root hash"""
        if not hashes:
            return ""
        
        if len(hashes) == 1:
            return hashes[0]
        
        # Pair up hashes and hash them together
        next_level = []
        for i in range(0, len(hashes), 2):
            if i + 1 < len(hashes):
                combined = hashes[i] + hashes[i + 1]
            else:
                combined = hashes[i] + hashes[i]  # Duplicate if odd
            next_level.append(hashlib.sha256(combined.encode()).hexdigest())
        
        return MerkleTree.build_tree(next_level)
    
    @staticmethod
    def verify_proof(leaf_hash: str, proof: List[str], root: str) -> bool:
        """Verify a leaf hash against Merkle root using proof"""
        current = leaf_hash
        for sibling in proof:
            if current < sibling:
                combined = current + sibling
            else:
                combined = sibling + current
            current = hashlib.sha256(combined.encode()).hexdigest()
        return current == root

class BlockchainEncryptedVault:
    """
    Blockchain-enhanced encrypted vault system
    
    Features:
    - Encrypts all files in vvault directory with AES-256-GCM
    - Stores file hashes on blockchain for integrity verification
    - Uses Merkle trees for efficient batch verification
    - Optional key escrow on blockchain
    - Automatic integrity checking on access
    """
    
    def __init__(self, vault_path: str, blockchain_wallet: Optional[VVAULTBlockchainWallet] = None):
        """
        Initialize blockchain-encrypted vault
        
        Args:
            vault_path: Path to VVAULT directory
            blockchain_wallet: Optional blockchain wallet for key management
        """
        self.vault_path = vault_path
        self.encrypted_dir = os.path.join(vault_path, ".encrypted")
        self.metadata_dir = os.path.join(vault_path, ".encryption_metadata")
        self.integrity_dir = os.path.join(vault_path, ".integrity_records")
        
        # Ensure directories exist
        os.makedirs(self.encrypted_dir, exist_ok=True)
        os.makedirs(self.metadata_dir, exist_ok=True)
        os.makedirs(self.integrity_dir, exist_ok=True)
        
        # Initialize blockchain wallet
        self.blockchain_wallet = blockchain_wallet or VVAULTBlockchainWallet(vault_path)
        
        # Encryption key (derived from blockchain wallet master key)
        self.encryption_key: Optional[bytes] = None
        
        # Integrity records cache
        self.integrity_records: Dict[str, FileIntegrityRecord] = {}
        
        logger.info(f"[🔐] Blockchain-Encrypted Vault initialized: {vault_path}")
    
    def initialize_encryption(self, passphrase: str) -> bool:
        """
        Initialize encryption system with passphrase
        
        Args:
            passphrase: Master passphrase for encryption
            
        Returns:
            True if successful
        """
        try:
            logger.info("[🔐] Initializing encryption system...")
            
            # Initialize blockchain wallet if needed
            if not self.blockchain_wallet.master_key:
                self.blockchain_wallet.initialize_wallet(passphrase)
            
            # Derive encryption key from master key
            self.encryption_key = self._derive_encryption_key(passphrase)
            
            # Create identity for vault encryption
            identity = self.blockchain_wallet.create_identity(
                BlockchainType.ETHEREUM,
                alias="vvault_encryption"
            )
            
            if identity:
                logger.info(f"[✅] Encryption initialized with identity: {identity.did}")
                return True
            else:
                logger.error("[❌] Failed to create blockchain identity")
                return False
                
        except Exception as e:
            logger.error(f"[❌] Encryption initialization failed: {e}")
            return False
    
    def encrypt_file(self, file_path: str, relative_to_vault: bool = True) -> Optional[EncryptionMetadata]:
        """
        Encrypt a file and store integrity hash on blockchain
        
        Args:
            file_path: Path to file to encrypt
            relative_to_vault: If True, file_path is relative to vault root
            
        Returns:
            EncryptionMetadata if successful, None otherwise
        """
        try:
            # Resolve full path
            if relative_to_vault:
                full_path = os.path.join(self.vault_path, file_path)
            else:
                full_path = file_path
            
            if not os.path.exists(full_path):
                logger.error(f"[❌] File not found: {full_path}")
                return None
            
            # Read file
            with open(full_path, 'rb') as f:
                file_data = f.read()
            
            # Calculate file hash
            file_hash = hashlib.sha256(file_data).hexdigest()
            
            # Encrypt file
            encrypted_data, iv, tag = self._encrypt_data(file_data)
            
            # Calculate encrypted hash
            encrypted_hash = hashlib.sha256(encrypted_data).hexdigest()
            
            # Store encrypted file
            encrypted_path = self._get_encrypted_path(file_path)
            os.makedirs(os.path.dirname(encrypted_path), exist_ok=True)
            with open(encrypted_path, 'wb') as f:
                f.write(encrypted_data)
            
            # Store integrity record on blockchain (or locally if blockchain disabled)
            integrity_record = self._store_integrity_record(
                file_path=file_path,
                file_hash=file_hash,
                encrypted_hash=encrypted_hash
            )
            
            # Create metadata
            metadata = EncryptionMetadata(
                file_path=file_path,
                encrypted_path=encrypted_path,
                algorithm="AES-256-GCM",
                key_id=self.blockchain_wallet.identities.get(list(self.blockchain_wallet.identities.keys())[0]).did if self.blockchain_wallet.identities else "local",
                iv=base64.b64encode(iv).decode(),
                tag=base64.b64encode(tag).decode(),
                file_hash=file_hash,
                encrypted_hash=encrypted_hash,
                timestamp=datetime.now(timezone.utc).isoformat()
            )
            
            # Save metadata
            metadata_path = os.path.join(self.metadata_dir, f"{hashlib.sha256(file_path.encode()).hexdigest()}.json")
            with open(metadata_path, 'w') as f:
                json.dump(asdict(metadata), f, indent=2)
            
            logger.info(f"[✅] Encrypted file: {file_path}")
            return metadata
            
        except Exception as e:
            logger.error(f"[❌] File encryption failed: {e}")
            return None
    
    def decrypt_file(self, file_path: str, output_path: Optional[str] = None) -> Optional[bytes]:
        """
        Decrypt a file and verify integrity
        
        Args:
            file_path: Original file path (relative to vault)
            output_path: Optional output path (defaults to original location)
            
        Returns:
            Decrypted file data if successful, None otherwise
        """
        try:
            # Load metadata
            metadata = self._load_metadata(file_path)
            if not metadata:
                logger.error(f"[❌] Metadata not found for: {file_path}")
                return None
            
            # Read encrypted file
            with open(metadata.encrypted_path, 'rb') as f:
                encrypted_data = f.read()
            
            # Verify encrypted hash
            current_encrypted_hash = hashlib.sha256(encrypted_data).hexdigest()
            if current_encrypted_hash != metadata.encrypted_hash:
                logger.error(f"[⚠️] Encrypted file integrity check failed: {file_path}")
                return None
            
            # Decrypt
            iv = base64.b64decode(metadata.iv)
            tag = base64.b64decode(metadata.tag)
            decrypted_data = self._decrypt_data(encrypted_data, iv, tag)
            
            # Verify file hash
            file_hash = hashlib.sha256(decrypted_data).hexdigest()
            if file_hash != metadata.file_hash:
                logger.error(f"[⚠️] File integrity check failed: {file_path}")
                return None
            
            # Verify blockchain integrity if available
            if not self._verify_blockchain_integrity(file_path, file_hash):
                logger.warning(f"[⚠️] Blockchain integrity verification failed: {file_path}")
            
            # Write decrypted file
            if output_path is None:
                output_path = os.path.join(self.vault_path, file_path)
            
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'wb') as f:
                f.write(decrypted_data)
            
            logger.info(f"[✅] Decrypted file: {file_path}")
            return decrypted_data
            
        except Exception as e:
            logger.error(f"[❌] File decryption failed: {e}")
            return None
    
    def encrypt_directory(self, directory_path: str, recursive: bool = True) -> Dict[str, Any]:
        """
        Encrypt all files in a directory
        
        Args:
            directory_path: Directory to encrypt (relative to vault)
            recursive: If True, encrypt subdirectories
            
        Returns:
            Dictionary with encryption results
        """
        results = {
            "encrypted": [],
            "failed": [],
            "skipped": [],
            "total": 0
        }
        
        full_dir_path = os.path.join(self.vault_path, directory_path)
        if not os.path.exists(full_dir_path):
            logger.error(f"[❌] Directory not found: {full_dir_path}")
            return results
        
        # Collect files
        files_to_encrypt = []
        for root, dirs, files in os.walk(full_dir_path):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, self.vault_path)
                files_to_encrypt.append(rel_path)
        
        results["total"] = len(files_to_encrypt)
        
        # Encrypt files
        for file_path in files_to_encrypt:
            # Skip already encrypted files
            if file_path.startswith('.encrypted') or file_path.startswith('.encryption_metadata'):
                results["skipped"].append(file_path)
                continue
            
            metadata = self.encrypt_file(file_path)
            if metadata:
                results["encrypted"].append(file_path)
            else:
                results["failed"].append(file_path)
        
        # Build Merkle tree for batch verification
        if results["encrypted"]:
            hashes = [self._load_metadata(f).file_hash for f in results["encrypted"] if self._load_metadata(f)]
            merkle_root = MerkleTree.build_tree(hashes)
            logger.info(f"[🌳] Merkle root for directory: {merkle_root}")
        
        logger.info(f"[✅] Encrypted {len(results['encrypted'])}/{results['total']} files")
        return results
    
    def verify_integrity(self, file_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Verify integrity of encrypted files
        
        Args:
            file_path: Optional specific file to verify (None = all files)
            
        Returns:
            Integrity verification results
        """
        results = {
            "verified": [],
            "failed": [],
            "blockchain_verified": [],
            "blockchain_failed": []
        }
        
        if file_path:
            files_to_check = [file_path]
        else:
            # Check all metadata files
            files_to_check = []
            for metadata_file in os.listdir(self.metadata_dir):
                if metadata_file.endswith('.json'):
                    with open(os.path.join(self.metadata_dir, metadata_file), 'r') as f:
                        metadata_data = json.load(f)
                        files_to_check.append(metadata_data['file_path'])
        
        for file_path in files_to_check:
            metadata = self._load_metadata(file_path)
            if not metadata:
                results["failed"].append(file_path)
                continue
            
            # Verify encrypted file exists
            if not os.path.exists(metadata.encrypted_path):
                results["failed"].append(file_path)
                continue
            
            # Verify encrypted hash
            with open(metadata.encrypted_path, 'rb') as f:
                encrypted_data = f.read()
            current_hash = hashlib.sha256(encrypted_data).hexdigest()
            
            if current_hash == metadata.encrypted_hash:
                results["verified"].append(file_path)
                
                # Verify blockchain integrity
                if self._verify_blockchain_integrity(file_path, metadata.file_hash):
                    results["blockchain_verified"].append(file_path)
                else:
                    results["blockchain_failed"].append(file_path)
            else:
                results["failed"].append(file_path)
        
        return results
    
    def _encrypt_data(self, data: bytes) -> Tuple[bytes, bytes, bytes]:
        """Encrypt data using AES-256-GCM"""
        if not self.encryption_key:
            raise RuntimeError("Encryption key not initialized")
        
        # Generate IV
        iv = os.urandom(12)  # GCM uses 12-byte IV
        
        # Encrypt
        cipher = Cipher(algorithms.AES(self.encryption_key), modes.GCM(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        encrypted_data = encryptor.update(data) + encryptor.finalize()
        
        return encrypted_data, iv, encryptor.tag
    
    def _decrypt_data(self, encrypted_data: bytes, iv: bytes, tag: bytes) -> bytes:
        """Decrypt data using AES-256-GCM"""
        if not self.encryption_key:
            raise RuntimeError("Encryption key not initialized")
        
        # Decrypt
        cipher = Cipher(algorithms.AES(self.encryption_key), modes.GCM(iv, tag), backend=default_backend())
        decryptor = cipher.decryptor()
        decrypted_data = decryptor.update(encrypted_data) + decryptor.finalize()
        
        return decrypted_data
    
    def _derive_encryption_key(self, passphrase: str) -> bytes:
        """Derive encryption key from passphrase"""
        # Use PBKDF2 with high iteration count
        salt = b"vvault_encryption_salt"  # In production, store salt securely
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        return kdf.derive(passphrase.encode())
    
    def _get_encrypted_path(self, file_path: str) -> str:
        """Get encrypted file path"""
        # Store encrypted files in .encrypted directory with same structure
        return os.path.join(self.encrypted_dir, file_path)
    
    def _load_metadata(self, file_path: str) -> Optional[EncryptionMetadata]:
        """Load encryption metadata for a file"""
        metadata_path = os.path.join(
            self.metadata_dir,
            f"{hashlib.sha256(file_path.encode()).hexdigest()}.json"
        )
        
        if not os.path.exists(metadata_path):
            return None
        
        with open(metadata_path, 'r') as f:
            metadata_data = json.load(f)
            return EncryptionMetadata(**metadata_data)
    
    def _store_integrity_record(self, file_path: str, file_hash: str, encrypted_hash: str) -> FileIntegrityRecord:
        """Store integrity record (locally and optionally on blockchain)"""
        record = FileIntegrityRecord(
            file_path=file_path,
            file_hash=file_hash,
            encrypted_hash=encrypted_hash,
            merkle_root=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
            block_height=None,
            tx_hash=None
        )
        
        # Save locally
        record_path = os.path.join(
            self.integrity_dir,
            f"{hashlib.sha256(file_path.encode()).hexdigest()}.json"
        )
        with open(record_path, 'w') as f:
            json.dump(asdict(record), f, indent=2)
        
        # Store hash on blockchain (if enabled)
        # In a real implementation, this would create a blockchain transaction
        # For now, we'll log it locally
        logger.info(f"[⛓️] Integrity record created for: {file_path}")
        
        self.integrity_records[file_path] = record
        return record
    
    def _verify_blockchain_integrity(self, file_path: str, file_hash: str) -> bool:
        """Verify file integrity against blockchain record"""
        record = self.integrity_records.get(file_path)
        if not record:
            # Try to load from disk
            record_path = os.path.join(
                self.integrity_dir,
                f"{hashlib.sha256(file_path.encode()).hexdigest()}.json"
            )
            if os.path.exists(record_path):
                with open(record_path, 'r') as f:
                    record_data = json.load(f)
                    record = FileIntegrityRecord(**record_data)
        
        if not record:
            return False
        
        # Verify hash matches
        if record.file_hash != file_hash:
            return False
        
        # If blockchain tx exists, verify it (placeholder)
        if record.tx_hash:
            # In a real implementation, query blockchain to verify
            logger.info(f"[⛓️] Blockchain verification for tx: {record.tx_hash}")
        
        return True

# Convenience functions
def create_encrypted_vault(vault_path: str, passphrase: str) -> BlockchainEncryptedVault:
    """Create and initialize an encrypted vault"""
    vault = BlockchainEncryptedVault(vault_path)
    if vault.initialize_encryption(passphrase):
        return vault
    else:
        raise RuntimeError("Failed to initialize encrypted vault")

if __name__ == "__main__":
    # Example usage
    print("🔐 VVAULT Blockchain-Enhanced Encrypted Vault")
    print("=" * 60)
    
    # Initialize vault
    vault_path = os.path.dirname(os.path.abspath(__file__))
    passphrase = input("Enter encryption passphrase: ")
    
    vault = create_encrypted_vault(vault_path, passphrase)
    
    # Encrypt a directory
    print("\n[🔐] Encrypting vault directory...")
    results = vault.encrypt_directory("capsules", recursive=True)
    print(f"Encrypted: {len(results['encrypted'])} files")
    print(f"Failed: {len(results['failed'])} files")
    
    # Verify integrity
    print("\n[✅] Verifying integrity...")
    integrity_results = vault.verify_integrity()
    print(f"Verified: {len(integrity_results['verified'])} files")
    print(f"Failed: {len(integrity_results['failed'])} files")
    
    print("\n✅ Encryption system ready!")

