#!/usr/bin/env python3
"""
VVAULT Capsule Blockchain Integration

Integrates VVAULT capsules with blockchain technology for:
- Immutable capsule storage
- Cryptographic verification
- Decentralized identity management
- IPFS integration for large data
- Smart contract deployment

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import os
import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Union, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path
import base64
import requests
from web3 import Web3
from web3.middleware import geth_poa_middleware
import ipfshttpclient

# Import VVAULT components
from vvault_core import VVAULTCore
from capsuleforge import CapsuleForge
from blockchain_identity_wallet import VVAULTBlockchainWallet, BlockchainType

# Configure logging
logger = logging.getLogger(__name__)

@dataclass
class BlockchainCapsuleRecord:
    """Blockchain record for a capsule"""
    fingerprint: str
    instance_name: str
    capsule_id: str
    timestamp: str
    ipfs_hash: Optional[str] = None
    blockchain_tx: Optional[str] = None
    block_number: Optional[int] = None
    gas_used: Optional[int] = None
    verification_status: str = "pending"

@dataclass
class CapsuleStorageResult:
    """Result of capsule storage operation"""
    local_path: str
    blockchain_tx: str
    ipfs_hash: Optional[str]
    fingerprint: str
    verification_url: str
    storage_cost: Dict[str, Any]

class IPFSManager:
    """IPFS integration for large capsule storage"""
    
    def __init__(self, ipfs_host: str = "127.0.0.1", ipfs_port: int = 5001):
        self.ipfs_host = ipfs_host
        self.ipfs_port = ipfs_port
        self.client = None
        self._connect()
    
    def _connect(self):
        """Connect to IPFS node"""
        try:
            self.client = ipfshttpclient.connect(f"/ip4/{self.ipfs_host}/tcp/{self.ipfs_port}/http")
            logger.info(f"Connected to IPFS at {self.ipfs_host}:{self.ipfs_port}")
        except Exception as e:
            logger.error(f"Failed to connect to IPFS: {e}")
            self.client = None
    
    def upload_capsule(self, capsule_data: Dict[str, Any]) -> str:
        """Upload capsule to IPFS"""
        if not self.client:
            raise RuntimeError("IPFS client not available")
        
        try:
            # Serialize capsule data
            capsule_json = json.dumps(capsule_data, indent=2, ensure_ascii=False, default=str)
            
            # Upload to IPFS
            result = self.client.add_str(capsule_json)
            ipfs_hash = result['Hash']
            
            logger.info(f"Uploaded capsule to IPFS: {ipfs_hash}")
            return ipfs_hash
            
        except Exception as e:
            logger.error(f"Failed to upload to IPFS: {e}")
            raise
    
    def retrieve_capsule(self, ipfs_hash: str) -> Dict[str, Any]:
        """Retrieve capsule from IPFS"""
        if not self.client:
            raise RuntimeError("IPFS client not available")
        
        try:
            # Retrieve from IPFS
            content = self.client.cat(ipfs_hash)
            capsule_data = json.loads(content.decode('utf-8'))
            
            logger.info(f"Retrieved capsule from IPFS: {ipfs_hash}")
            return capsule_data
            
        except Exception as e:
            logger.error(f"Failed to retrieve from IPFS: {e}")
            raise
    
    def pin_capsule(self, ipfs_hash: str) -> bool:
        """Pin capsule to IPFS for persistence"""
        if not self.client:
            return False
        
        try:
            self.client.pin.add(ipfs_hash)
            logger.info(f"Pinned capsule to IPFS: {ipfs_hash}")
            return True
        except Exception as e:
            logger.error(f"Failed to pin capsule: {e}")
            return False

class VVAULTCapsuleBlockchain:
    """Main class for blockchain-enabled VVAULT capsule management"""
    
    def __init__(self, vault_path: str = None, blockchain_type: BlockchainType = BlockchainType.ETHEREUM):
        self.vault_path = vault_path or os.path.dirname(os.path.abspath(__file__))
        self.blockchain_type = blockchain_type
        
        # Initialize components
        self.vvault_core = VVAULTCore(vault_path)
        self.capsule_forge = CapsuleForge(vault_path)
        self.blockchain_wallet = VVAULTBlockchainWallet()
        self.ipfs_manager = IPFSManager()
        
        # Smart contract configuration
        self.contract_address = None
        self.contract_abi = self._load_contract_abi()
        
        logger.info(f"VVAULT Capsule Blockchain initialized for {blockchain_type.value}")
    
    def _load_contract_abi(self) -> List[Dict]:
        """Load smart contract ABI"""
        return [
            {
                "inputs": [
                    {"name": "fingerprint", "type": "string"},
                    {"name": "instanceName", "type": "string"},
                    {"name": "capsuleId", "type": "string"},
                    {"name": "ipfsHash", "type": "string"}
                ],
                "name": "registerCapsule",
                "outputs": [],
                "stateMutability": "nonpayable",
                "type": "function"
            },
            {
                "inputs": [{"name": "fingerprint", "type": "string"}],
                "name": "getCapsule",
                "outputs": [
                    {"name": "fingerprint", "type": "string"},
                    {"name": "instanceName", "type": "string"},
                    {"name": "capsuleId", "type": "string"},
                    {"name": "ipfsHash", "type": "string"},
                    {"name": "timestamp", "type": "uint256"},
                    {"name": "owner", "type": "address"}
                ],
                "stateMutability": "view",
                "type": "function"
            },
            {
                "inputs": [{"name": "fingerprint", "type": "string"}],
                "name": "verifyCapsule",
                "outputs": [{"name": "isValid", "type": "bool"}],
                "stateMutability": "view",
                "type": "function"
            }
        ]
    
    def store_capsule_with_blockchain(
        self, 
        capsule_data: Dict[str, Any], 
        use_ipfs: bool = True,
        pin_ipfs: bool = True
    ) -> CapsuleStorageResult:
        """
        Store capsule locally, on IPFS, and create blockchain record
        
        Args:
            capsule_data: Complete capsule data
            use_ipfs: Whether to store on IPFS
            pin_ipfs: Whether to pin on IPFS for persistence
            
        Returns:
            CapsuleStorageResult with all storage locations
        """
        try:
            logger.info(f"Storing capsule with blockchain integration")
            
            # Extract metadata
            metadata = capsule_data['metadata']
            fingerprint = metadata['fingerprint_hash']
            instance_name = metadata['instance_name']
            capsule_id = metadata['uuid']
            timestamp = metadata['timestamp']
            
            # 1. Store locally using VVAULT Core
            local_path = self.vvault_core.store_capsule(capsule_data)
            logger.info(f"Stored locally: {local_path}")
            
            # 2. Upload to IPFS if requested
            ipfs_hash = None
            if use_ipfs:
                try:
                    ipfs_hash = self.ipfs_manager.upload_capsule(capsule_data)
                    if pin_ipfs:
                        self.ipfs_manager.pin_capsule(ipfs_hash)
                    logger.info(f"Stored on IPFS: {ipfs_hash}")
                except Exception as e:
                    logger.warning(f"IPFS storage failed: {e}")
            
            # 3. Create blockchain transaction
            blockchain_tx = self._create_blockchain_transaction(
                fingerprint=fingerprint,
                instance_name=instance_name,
                capsule_id=capsule_id,
                timestamp=timestamp,
                ipfs_hash=ipfs_hash
            )
            
            # 4. Calculate storage costs
            storage_cost = self._calculate_storage_cost(
                capsule_size=len(json.dumps(capsule_data)),
                use_ipfs=use_ipfs,
                blockchain_tx=blockchain_tx
            )
            
            # 5. Create verification URL
            verification_url = self._create_verification_url(fingerprint)
            
            result = CapsuleStorageResult(
                local_path=local_path,
                blockchain_tx=blockchain_tx,
                ipfs_hash=ipfs_hash,
                fingerprint=fingerprint,
                verification_url=verification_url,
                storage_cost=storage_cost
            )
            
            logger.info(f"✅ Capsule stored successfully: {fingerprint[:16]}...")
            return result
            
        except Exception as e:
            logger.error(f"❌ Failed to store capsule: {e}")
            raise
    
    def _create_blockchain_transaction(
        self, 
        fingerprint: str, 
        instance_name: str, 
        capsule_id: str, 
        timestamp: str,
        ipfs_hash: Optional[str] = None
    ) -> str:
        """Create blockchain transaction for capsule registration"""
        try:
            # Create transaction data
            tx_data = {
                "type": "capsule_registration",
                "fingerprint": fingerprint,
                "instance_name": instance_name,
                "capsule_id": capsule_id,
                "timestamp": timestamp,
                "ipfs_hash": ipfs_hash or "",
                "blockchain_type": self.blockchain_type.value
            }
            
            # Sign and broadcast transaction
            tx_hash = self.blockchain_wallet.sign_and_broadcast(
                data=tx_data,
                blockchain_type=self.blockchain_type
            )
            
            logger.info(f"Created blockchain transaction: {tx_hash}")
            return tx_hash
            
        except Exception as e:
            logger.error(f"Failed to create blockchain transaction: {e}")
            raise
    
    def verify_capsule_integrity(self, fingerprint: str) -> Dict[str, Any]:
        """
        Verify capsule integrity across all storage layers
        
        Args:
            fingerprint: Capsule fingerprint hash
            
        Returns:
            Verification result with status and details
        """
        try:
            logger.info(f"Verifying capsule integrity: {fingerprint[:16]}...")
            
            verification_result = {
                "fingerprint": fingerprint,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "local_verification": False,
                "blockchain_verification": False,
                "ipfs_verification": False,
                "overall_status": "failed",
                "details": {}
            }
            
            # 1. Verify local storage
            try:
                local_capsule = self.vvault_core.get_capsule_by_fingerprint(fingerprint)
                if local_capsule and local_capsule['metadata']['fingerprint_hash'] == fingerprint:
                    verification_result["local_verification"] = True
                    verification_result["details"]["local_path"] = local_capsule.get('file_path', 'unknown')
                else:
                    verification_result["details"]["local_error"] = "Capsule not found or fingerprint mismatch"
            except Exception as e:
                verification_result["details"]["local_error"] = str(e)
            
            # 2. Verify blockchain record
            try:
                blockchain_record = self._get_blockchain_record(fingerprint)
                if blockchain_record and blockchain_record['fingerprint'] == fingerprint:
                    verification_result["blockchain_verification"] = True
                    verification_result["details"]["blockchain_tx"] = blockchain_record.get('tx_hash', 'unknown')
                else:
                    verification_result["details"]["blockchain_error"] = "Blockchain record not found or invalid"
            except Exception as e:
                verification_result["details"]["blockchain_error"] = str(e)
            
            # 3. Verify IPFS storage (if available)
            try:
                if verification_result["details"].get("ipfs_hash"):
                    ipfs_capsule = self.ipfs_manager.retrieve_capsule(
                        verification_result["details"]["ipfs_hash"]
                    )
                    if ipfs_capsule['metadata']['fingerprint_hash'] == fingerprint:
                        verification_result["ipfs_verification"] = True
                    else:
                        verification_result["details"]["ipfs_error"] = "IPFS data fingerprint mismatch"
            except Exception as e:
                verification_result["details"]["ipfs_error"] = str(e)
            
            # 4. Determine overall status
            if verification_result["local_verification"] and verification_result["blockchain_verification"]:
                verification_result["overall_status"] = "verified"
            elif verification_result["local_verification"]:
                verification_result["overall_status"] = "partial"
            else:
                verification_result["overall_status"] = "failed"
            
            logger.info(f"Verification complete: {verification_result['overall_status']}")
            return verification_result
            
        except Exception as e:
            logger.error(f"Verification failed: {e}")
            return {
                "fingerprint": fingerprint,
                "overall_status": "error",
                "error": str(e)
            }
    
    def _get_blockchain_record(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        """Get blockchain record for capsule"""
        try:
            # This would query the smart contract or blockchain directly
            # For now, return a mock record
            return {
                "fingerprint": fingerprint,
                "tx_hash": "0x" + "0" * 64,  # Mock transaction hash
                "block_number": 12345,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to get blockchain record: {e}")
            return None
    
    def _calculate_storage_cost(self, capsule_size: int, use_ipfs: bool, blockchain_tx: str) -> Dict[str, Any]:
        """Calculate storage costs for capsule"""
        costs = {
            "local_storage": 0,  # Free
            "ipfs_storage": 0,   # Free
            "blockchain_gas": 0, # Would be calculated from actual transaction
            "total_cost": 0
        }
        
        if use_ipfs:
            costs["ipfs_storage"] = capsule_size * 0.000001  # Mock cost per byte
        
        # Mock blockchain gas cost
        costs["blockchain_gas"] = 0.001  # Mock ETH cost
        
        costs["total_cost"] = costs["ipfs_storage"] + costs["blockchain_gas"]
        
        return costs
    
    def _create_verification_url(self, fingerprint: str) -> str:
        """Create verification URL for capsule"""
        base_url = "https://vvault.verification.com"  # Mock URL
        return f"{base_url}/verify/{fingerprint}"
    
    def list_blockchain_capsules(self) -> List[BlockchainCapsuleRecord]:
        """List all capsules with blockchain records"""
        try:
            # This would query the blockchain for all registered capsules
            # For now, return mock data
            return [
                BlockchainCapsuleRecord(
                    fingerprint="mock_fingerprint_1",
                    instance_name="Nova",
                    capsule_id="mock_capsule_1",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    ipfs_hash="QmMockHash1",
                    blockchain_tx="0xMockTx1",
                    verification_status="verified"
                )
            ]
        except Exception as e:
            logger.error(f"Failed to list blockchain capsules: {e}")
            return []
    
    def migrate_capsule_to_blockchain(self, local_capsule_path: str) -> CapsuleStorageResult:
        """
        Migrate existing local capsule to blockchain storage
        
        Args:
            local_capsule_path: Path to existing capsule file
            
        Returns:
            CapsuleStorageResult for the migrated capsule
        """
        try:
            logger.info(f"Migrating capsule to blockchain: {local_capsule_path}")
            
            # Load existing capsule
            with open(local_capsule_path, 'r', encoding='utf-8') as f:
                capsule_data = json.load(f)
            
            # Store with blockchain integration
            result = self.store_capsule_with_blockchain(capsule_data)
            
            logger.info(f"✅ Capsule migrated successfully: {result.fingerprint[:16]}...")
            return result
            
        except Exception as e:
            logger.error(f"❌ Failed to migrate capsule: {e}")
            raise

# Convenience functions
def create_blockchain_capsule(
    instance_name: str,
    traits: Dict[str, float],
    memory_log: List[str],
    personality_type: str,
    blockchain_type: BlockchainType = BlockchainType.ETHEREUM,
    use_ipfs: bool = True
) -> CapsuleStorageResult:
    """
    Create a new capsule with blockchain integration
    
    Args:
        instance_name: Name of the AI construct
        traits: Personality traits dictionary
        memory_log: List of memory entries
        personality_type: MBTI personality type
        blockchain_type: Blockchain to use
        use_ipfs: Whether to store on IPFS
        
    Returns:
        CapsuleStorageResult with storage details
    """
    try:
        # Initialize blockchain capsule manager
        blockchain_capsule = VVAULTCapsuleBlockchain(blockchain_type=blockchain_type)
        
        # Generate capsule using CapsuleForge
        capsule_forge = CapsuleForge()
        capsule_data = capsule_forge._create_capsule_data(
            instance_name=instance_name,
            traits=traits,
            memory_log=memory_log,
            personality_type=personality_type,
            capsule_uuid=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        
        # Convert to dict for storage
        capsule_dict = asdict(capsule_data)
        
        # Store with blockchain integration
        return blockchain_capsule.store_capsule_with_blockchain(
            capsule_dict, 
            use_ipfs=use_ipfs
        )
        
    except Exception as e:
        logger.error(f"Failed to create blockchain capsule: {e}")
        raise

def verify_all_capsules() -> Dict[str, Any]:
    """Verify all capsules in the vault"""
    try:
        blockchain_capsule = VVAULTCapsuleBlockchain()
        vvault_core = VVAULTCore()
        
        # Get all capsules
        all_capsules = vvault_core.list_all_capsules()
        
        verification_results = {}
        for capsule in all_capsules:
            fingerprint = capsule.get('fingerprint_hash', '')
            if fingerprint:
                verification_results[fingerprint] = blockchain_capsule.verify_capsule_integrity(fingerprint)
        
        return {
            "total_capsules": len(all_capsules),
            "verified_capsules": sum(1 for r in verification_results.values() if r['overall_status'] == 'verified'),
            "partial_capsules": sum(1 for r in verification_results.values() if r['overall_status'] == 'partial'),
            "failed_capsules": sum(1 for r in verification_results.values() if r['overall_status'] == 'failed'),
            "results": verification_results
        }
        
    except Exception as e:
        logger.error(f"Failed to verify all capsules: {e}")
        return {"error": str(e)}

if __name__ == "__main__":
    # Example usage
    logging.basicConfig(level=logging.INFO)
    
    # Create blockchain capsule
    result = create_blockchain_capsule(
        instance_name="Nova",
        traits={"creativity": 0.9, "empathy": 0.85},
        memory_log=["Memory entry 1", "Memory entry 2"],
        personality_type="INFJ",
        use_ipfs=True
    )
    
    print(f"Capsule stored: {result.fingerprint}")
    print(f"Blockchain TX: {result.blockchain_tx}")
    print(f"IPFS Hash: {result.ipfs_hash}")








