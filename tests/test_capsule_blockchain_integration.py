#!/usr/bin/env python3
"""
Test suite for VVAULT Capsule Blockchain Integration

Tests the integration between VVAULT capsules and blockchain technology,
including IPFS storage, smart contract interaction, and verification.

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import os
import json
import tempfile
import unittest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone
import uuid

# Import the modules under test
from capsule_blockchain_integration import (
    VVAULTCapsuleBlockchain,
    IPFSManager,
    BlockchainCapsuleRecord,
    CapsuleStorageResult,
    create_blockchain_capsule,
    verify_all_capsules
)
from vvault.blockchain.blockchain_identity_wallet import BlockchainType

class TestIPFSManager(unittest.TestCase):
    """Test IPFS integration functionality"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.ipfs_manager = IPFSManager()
        self.test_capsule_data = {
            "metadata": {
                "instance_name": "TestNova",
                "uuid": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "fingerprint_hash": "test_fingerprint_123"
            },
            "traits": {"creativity": 0.9, "empathy": 0.85},
            "personality": {"personality_type": "INFJ"}
        }
    
    @patch('ipfshttpclient.connect')
    def test_upload_capsule_success(self, mock_connect):
        """Test successful capsule upload to IPFS"""
        # Mock IPFS client
        mock_client = MagicMock()
        mock_client.add_str.return_value = {'Hash': 'QmTestHash123'}
        mock_connect.return_value = mock_client
        
        # Create IPFS manager with mocked client
        ipfs_manager = IPFSManager()
        ipfs_manager.client = mock_client
        
        # Test upload
        result = ipfs_manager.upload_capsule(self.test_capsule_data)
        
        self.assertEqual(result, 'QmTestHash123')
        mock_client.add_str.assert_called_once()
    
    @patch('ipfshttpclient.connect')
    def test_upload_capsule_failure(self, mock_connect):
        """Test capsule upload failure"""
        # Mock IPFS connection failure
        mock_connect.side_effect = Exception("IPFS connection failed")
        
        ipfs_manager = IPFSManager()
        
        with self.assertRaises(RuntimeError):
            ipfs_manager.upload_capsule(self.test_capsule_data)
    
    @patch('ipfshttpclient.connect')
    def test_retrieve_capsule_success(self, mock_connect):
        """Test successful capsule retrieval from IPFS"""
        # Mock IPFS client
        mock_client = MagicMock()
        mock_client.cat.return_value = json.dumps(self.test_capsule_data).encode('utf-8')
        mock_connect.return_value = mock_client
        
        ipfs_manager = IPFSManager()
        ipfs_manager.client = mock_client
        
        # Test retrieval
        result = ipfs_manager.retrieve_capsule('QmTestHash123')
        
        self.assertEqual(result['metadata']['instance_name'], 'TestNova')
        mock_client.cat.assert_called_once_with('QmTestHash123')
    
    @patch('ipfshttpclient.connect')
    def test_pin_capsule_success(self, mock_connect):
        """Test successful capsule pinning"""
        # Mock IPFS client
        mock_client = MagicMock()
        mock_connect.return_value = mock_client
        
        ipfs_manager = IPFSManager()
        ipfs_manager.client = mock_client
        
        # Test pinning
        result = ipfs_manager.pin_capsule('QmTestHash123')
        
        self.assertTrue(result)
        mock_client.pin.add.assert_called_once_with('QmTestHash123')

class TestVVAULTCapsuleBlockchain(unittest.TestCase):
    """Test VVAULT blockchain capsule integration"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.test_capsule_data = {
            "metadata": {
                "instance_name": "TestNova",
                "uuid": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "fingerprint_hash": "test_fingerprint_123"
            },
            "traits": {"creativity": 0.9, "empathy": 0.85},
            "personality": {"personality_type": "INFJ"}
        }
    
    @patch('capsule_blockchain_integration.VVAULTCore')
    @patch('capsule_blockchain_integration.CapsuleForge')
    @patch('capsule_blockchain_integration.VVAULTBlockchainWallet')
    @patch('capsule_blockchain_integration.IPFSManager')
    def test_store_capsule_with_blockchain_success(self, mock_ipfs, mock_wallet, mock_forge, mock_core):
        """Test successful capsule storage with blockchain integration"""
        # Mock components
        mock_core_instance = Mock()
        mock_core_instance.store_capsule.return_value = "/test/path/capsule.json"
        mock_core.return_value = mock_core_instance
        
        mock_ipfs_instance = Mock()
        mock_ipfs_instance.upload_capsule.return_value = "QmTestHash123"
        mock_ipfs.return_value = mock_ipfs_instance
        
        mock_wallet_instance = Mock()
        mock_wallet_instance.sign_and_broadcast.return_value = "0xTestTxHash123"
        mock_wallet.return_value = mock_wallet_instance
        
        # Create blockchain capsule manager
        blockchain_capsule = VVAULTCapsuleBlockchain(vault_path=self.temp_dir)
        blockchain_capsule.vvault_core = mock_core_instance
        blockchain_capsule.ipfs_manager = mock_ipfs_instance
        blockchain_capsule.blockchain_wallet = mock_wallet_instance
        
        # Test storage
        result = blockchain_capsule.store_capsule_with_blockchain(
            self.test_capsule_data, 
            use_ipfs=True
        )
        
        # Verify results
        self.assertIsInstance(result, CapsuleStorageResult)
        self.assertEqual(result.local_path, "/test/path/capsule.json")
        self.assertEqual(result.blockchain_tx, "0xTestTxHash123")
        self.assertEqual(result.ipfs_hash, "QmTestHash123")
        self.assertEqual(result.fingerprint, "test_fingerprint_123")
        
        # Verify method calls
        mock_core_instance.store_capsule.assert_called_once_with(self.test_capsule_data)
        mock_ipfs_instance.upload_capsule.assert_called_once_with(self.test_capsule_data)
        mock_wallet_instance.sign_and_broadcast.assert_called_once()
    
    @patch('capsule_blockchain_integration.VVAULTCore')
    @patch('capsule_blockchain_integration.VVAULTBlockchainWallet')
    def test_verify_capsule_integrity_success(self, mock_wallet, mock_core):
        """Test successful capsule integrity verification"""
        # Mock components
        mock_core_instance = Mock()
        mock_core_instance.get_capsule_by_fingerprint.return_value = {
            'metadata': {'fingerprint_hash': 'test_fingerprint_123'},
            'file_path': '/test/path/capsule.json'
        }
        mock_core.return_value = mock_core_instance
        
        mock_wallet_instance = Mock()
        mock_wallet_instance.get_capsule_record.return_value = {
            'fingerprint': 'test_fingerprint_123',
            'tx_hash': '0xTestTxHash123'
        }
        mock_wallet.return_value = mock_wallet_instance
        
        # Create blockchain capsule manager
        blockchain_capsule = VVAULTCapsuleBlockchain(vault_path=self.temp_dir)
        blockchain_capsule.vvault_core = mock_core_instance
        blockchain_capsule.blockchain_wallet = mock_wallet_instance
        
        # Test verification
        result = blockchain_capsule.verify_capsule_integrity('test_fingerprint_123')
        
        # Verify results
        self.assertEqual(result['fingerprint'], 'test_fingerprint_123')
        self.assertTrue(result['local_verification'])
        self.assertTrue(result['blockchain_verification'])
        self.assertEqual(result['overall_status'], 'verified')
    
    @patch('capsule_blockchain_integration.VVAULTCore')
    @patch('capsule_blockchain_integration.CapsuleForge')
    def test_migrate_capsule_to_blockchain_success(self, mock_forge, mock_core):
        """Test successful capsule migration to blockchain"""
        # Create temporary capsule file
        capsule_file = os.path.join(self.temp_dir, 'test_capsule.json')
        with open(capsule_file, 'w') as f:
            json.dump(self.test_capsule_data, f)
        
        # Mock components
        mock_core_instance = Mock()
        mock_core_instance.store_capsule.return_value = "/test/path/migrated_capsule.json"
        mock_core.return_value = mock_core_instance
        
        # Create blockchain capsule manager
        blockchain_capsule = VVAULTCapsuleBlockchain(vault_path=self.temp_dir)
        blockchain_capsule.vvault_core = mock_core_instance
        
        # Mock the store_capsule_with_blockchain method
        with patch.object(blockchain_capsule, 'store_capsule_with_blockchain') as mock_store:
            mock_store.return_value = CapsuleStorageResult(
                local_path="/test/path/migrated_capsule.json",
                blockchain_tx="0xTestTxHash123",
                ipfs_hash="QmTestHash123",
                fingerprint="test_fingerprint_123",
                verification_url="https://test.com/verify",
                storage_cost={}
            )
            
            # Test migration
            result = blockchain_capsule.migrate_capsule_to_blockchain(capsule_file)
            
            # Verify results
            self.assertIsInstance(result, CapsuleStorageResult)
            self.assertEqual(result.fingerprint, "test_fingerprint_123")
            mock_store.assert_called_once()

class TestConvenienceFunctions(unittest.TestCase):
    """Test convenience functions"""
    
    @patch('capsule_blockchain_integration.VVAULTCapsuleBlockchain')
    @patch('capsule_blockchain_integration.CapsuleForge')
    def test_create_blockchain_capsule_success(self, mock_forge, mock_blockchain):
        """Test successful blockchain capsule creation"""
        # Mock components
        mock_forge_instance = Mock()
        mock_forge_instance._create_capsule_data.return_value = Mock()
        mock_forge.return_value = mock_forge_instance
        
        mock_blockchain_instance = Mock()
        mock_blockchain_instance.store_capsule_with_blockchain.return_value = CapsuleStorageResult(
            local_path="/test/path/capsule.json",
            blockchain_tx="0xTestTxHash123",
            ipfs_hash="QmTestHash123",
            fingerprint="test_fingerprint_123",
            verification_url="https://test.com/verify",
            storage_cost={}
        )
        mock_blockchain.return_value = mock_blockchain_instance
        
        # Test creation
        result = create_blockchain_capsule(
            instance_name="TestNova",
            traits={"creativity": 0.9},
            memory_log=["test memory"],
            personality_type="INFJ"
        )
        
        # Verify results
        self.assertIsInstance(result, CapsuleStorageResult)
        self.assertEqual(result.fingerprint, "test_fingerprint_123")
    
    @patch('capsule_blockchain_integration.VVAULTCapsuleBlockchain')
    @patch('capsule_blockchain_integration.VVAULTCore')
    def test_verify_all_capsules_success(self, mock_core, mock_blockchain):
        """Test successful verification of all capsules"""
        # Mock components
        mock_core_instance = Mock()
        mock_core_instance.list_all_capsules.return_value = [
            {'fingerprint_hash': 'test_fingerprint_1'},
            {'fingerprint_hash': 'test_fingerprint_2'}
        ]
        mock_core.return_value = mock_core_instance
        
        mock_blockchain_instance = Mock()
        mock_blockchain_instance.verify_capsule_integrity.side_effect = [
            {'overall_status': 'verified'},
            {'overall_status': 'partial'}
        ]
        mock_blockchain.return_value = mock_blockchain_instance
        
        # Test verification
        result = verify_all_capsules()
        
        # Verify results
        self.assertEqual(result['total_capsules'], 2)
        self.assertEqual(result['verified_capsules'], 1)
        self.assertEqual(result['partial_capsules'], 1)
        self.assertEqual(result['failed_capsules'], 0)

class TestIntegrationScenarios(unittest.TestCase):
    """Test integration scenarios"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.test_capsule_data = {
            "metadata": {
                "instance_name": "IntegrationTestNova",
                "uuid": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "fingerprint_hash": "integration_test_fingerprint"
            },
            "traits": {"creativity": 0.9, "empathy": 0.85},
            "personality": {"personality_type": "INFJ"}
        }
    
    @patch('capsule_blockchain_integration.VVAULTCapsuleBlockchain')
    def test_end_to_end_capsule_lifecycle(self, mock_blockchain_class):
        """Test complete capsule lifecycle from creation to verification"""
        # Mock blockchain capsule manager
        mock_blockchain_instance = Mock()
        mock_blockchain_class.return_value = mock_blockchain_instance
        
        # Mock storage result
        storage_result = CapsuleStorageResult(
            local_path="/test/path/capsule.json",
            blockchain_tx="0xTestTxHash123",
            ipfs_hash="QmTestHash123",
            fingerprint="integration_test_fingerprint",
            verification_url="https://test.com/verify",
            storage_cost={}
        )
        mock_blockchain_instance.store_capsule_with_blockchain.return_value = storage_result
        
        # Mock verification result
        verification_result = {
            "fingerprint": "integration_test_fingerprint",
            "overall_status": "verified",
            "local_verification": True,
            "blockchain_verification": True,
            "ipfs_verification": True
        }
        mock_blockchain_instance.verify_capsule_integrity.return_value = verification_result
        
        # Test complete lifecycle
        blockchain_capsule = VVAULTCapsuleBlockchain(vault_path=self.temp_dir)
        
        # 1. Store capsule
        result = blockchain_capsule.store_capsule_with_blockchain(self.test_capsule_data)
        self.assertEqual(result.fingerprint, "integration_test_fingerprint")
        
        # 2. Verify capsule
        verification = blockchain_capsule.verify_capsule_integrity("integration_test_fingerprint")
        self.assertEqual(verification['overall_status'], "verified")
        
        # Verify method calls
        mock_blockchain_instance.store_capsule_with_blockchain.assert_called_once()
        mock_blockchain_instance.verify_capsule_integrity.assert_called_once_with("integration_test_fingerprint")
    
    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

if __name__ == '__main__':
    # Configure logging for tests
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # Run tests
    unittest.main(verbosity=2)








