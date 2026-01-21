#!/usr/bin/env python3
"""
VVAULT Blockchain Wallet Test Suite

Comprehensive test suite for the VVAULT blockchain identity wallet.

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import os
import sys
import json
import tempfile
import shutil
import unittest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vvault.blockchain.blockchain_identity_wallet import (
    VVAULTBlockchainWallet, BlockchainType, SecurityLevel, KeyType,
    WalletIdentity, KeyPair, Transaction, IdentityAttestation,
    HardwareSecurityModule, BlockchainConnector
)
from vvault.blockchain.blockchain_config import BlockchainConfigManager, BlockchainNetwork, NetworkType
from vvault.audit.backup_recovery import BackupManager, BackupType, RecoveryPhrase

class TestVVAULTBlockchainWallet(unittest.TestCase):
    """Test cases for VVAULTBlockchainWallet"""
    
    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
        self.wallet_path = os.path.join(self.temp_dir, "test_wallet")
        self.wallet = None
    
    def tearDown(self):
        """Clean up test environment"""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
    
    def test_wallet_initialization(self):
        """Test wallet initialization"""
        wallet = VVAULTBlockchainWallet(self.wallet_path, hsm_enabled=False)
        
        self.assertIsNotNone(wallet)
        self.assertEqual(wallet.wallet_path, self.wallet_path)
        self.assertTrue(os.path.exists(wallet.wallet_dir))
        self.assertTrue(os.path.exists(wallet.keys_dir))
        self.assertTrue(os.path.exists(wallet.identities_dir))
        self.assertTrue(os.path.exists(wallet.transactions_dir))
        self.assertTrue(os.path.exists(wallet.attestations_dir))
    
    def test_wallet_initialization_with_hsm(self):
        """Test wallet initialization with HSM"""
        with patch('blockchain_identity_wallet.HardwareSecurityModule') as mock_hsm:
            mock_hsm.return_value.is_available = True
            wallet = VVAULTBlockchainWallet(self.wallet_path, hsm_enabled=True)
            
            self.assertIsNotNone(wallet.hsm)
            mock_hsm.assert_called_once()
    
    def test_initialize_wallet(self):
        """Test wallet initialization with passphrase"""
        wallet = VVAULTBlockchainWallet(self.wallet_path, hsm_enabled=False)
        
        success = wallet.initialize_wallet("test_passphrase", SecurityLevel.HIGH)
        
        self.assertTrue(success)
        self.assertIsNotNone(wallet.master_key)
        self.assertEqual(wallet.master_key.key_type, KeyType.MASTER)
        
        # Check that master key file was created
        master_key_path = os.path.join(wallet.keys_dir, "master_key.enc")
        self.assertTrue(os.path.exists(master_key_path))
        
        # Check that metadata file was created
        metadata_path = os.path.join(wallet.keys_dir, "master_key_metadata.json")
        self.assertTrue(os.path.exists(metadata_path))
    
    def test_create_identity(self):
        """Test identity creation"""
        wallet = VVAULTBlockchainWallet(self.wallet_path, hsm_enabled=False)
        wallet.initialize_wallet("test_passphrase", SecurityLevel.HIGH)
        
        identity = wallet.create_identity(BlockchainType.ETHEREUM, "Test Identity")
        
        self.assertIsNotNone(identity)
        self.assertEqual(identity.blockchain_type, BlockchainType.ETHEREUM)
        self.assertTrue(identity.did.startswith("did:vvault:ethereum:"))
        self.assertIsNotNone(identity.address)
        self.assertEqual(identity.metadata.get('alias'), "Test Identity")
        
        # Check that identity file was created
        identity_path = os.path.join(wallet.identities_dir, f"{identity.did}.json")
        self.assertTrue(os.path.exists(identity_path))
        
        # Check that signing key file was created
        key_path = os.path.join(wallet.keys_dir, f"{identity.did}_signing_key.enc")
        self.assertTrue(os.path.exists(key_path))
    
    def test_create_multiple_identities(self):
        """Test creating multiple identities"""
        wallet = VVAULTBlockchainWallet(self.wallet_path, hsm_enabled=False)
        wallet.initialize_wallet("test_passphrase", SecurityLevel.HIGH)
        
        # Create Ethereum identity
        eth_identity = wallet.create_identity(BlockchainType.ETHEREUM, "ETH Wallet")
        self.assertIsNotNone(eth_identity)
        
        # Create Bitcoin identity
        btc_identity = wallet.create_identity(BlockchainType.BITCOIN, "BTC Wallet")
        self.assertIsNotNone(btc_identity)
        
        # List identities
        identities = wallet.list_identities()
        self.assertEqual(len(identities), 2)
        
        # Check that identities are different
        self.assertNotEqual(eth_identity.did, btc_identity.did)
        self.assertNotEqual(eth_identity.address, btc_identity.address)
    
    def test_verify_identity(self):
        """Test identity verification"""
        wallet = VVAULTBlockchainWallet(self.wallet_path, hsm_enabled=False)
        wallet.initialize_wallet("test_passphrase", SecurityLevel.HIGH)
        
        identity = wallet.create_identity(BlockchainType.ETHEREUM, "Test Identity")
        
        credential_data = {
            "type": "identity_verification",
            "issuer": "test_issuer",
            "subject": "test_subject"
        }
        
        attestation = wallet.verify_identity(identity.did, credential_data)
        
        self.assertIsNotNone(attestation)
        self.assertEqual(attestation.subject, identity.did)
        self.assertEqual(attestation.credential_type, "identity_verification")
        self.assertIsNotNone(attestation.attestation_id)
        self.assertIsNotNone(attestation.proof)
        
        # Check that attestation file was created
        attestation_path = os.path.join(wallet.attestations_dir, f"{attestation.attestation_id}.json")
        self.assertTrue(os.path.exists(attestation_path))
    
    def test_get_balance(self):
        """Test getting balance"""
        wallet = VVAULTBlockchainWallet(self.wallet_path, hsm_enabled=False)
        wallet.initialize_wallet("test_passphrase", SecurityLevel.HIGH)
        
        identity = wallet.create_identity(BlockchainType.ETHEREUM, "Test Identity")
        
        # Mock blockchain connector
        with patch.object(wallet, '_get_blockchain_connector') as mock_connector:
            mock_connector.return_value.get_balance.return_value = "1.5"
            
            balance = wallet.get_balance(identity.did)
            
            self.assertEqual(balance, "1.5")
            mock_connector.assert_called_once_with(BlockchainType.ETHEREUM)
    
    def test_send_transaction(self):
        """Test sending transaction"""
        wallet = VVAULTBlockchainWallet(self.wallet_path, hsm_enabled=False)
        wallet.initialize_wallet("test_passphrase", SecurityLevel.HIGH)
        
        identity = wallet.create_identity(BlockchainType.ETHEREUM, "Test Identity")
        
        # Mock blockchain connector
        with patch.object(wallet, '_get_blockchain_connector') as mock_connector:
            mock_connector.return_value.send_transaction.return_value = "0x1234567890abcdef"
            
            tx_hash = wallet.send_transaction(
                identity.did,
                "0x742d35Cc6634C0532925a3b8D4C9db96C4b4d8b6",
                "0.1",
                "test_passphrase"
            )
            
            self.assertEqual(tx_hash, "0x1234567890abcdef")
            
            # Check that transaction was recorded
            transactions = wallet.list_transactions(identity.did)
            self.assertEqual(len(transactions), 1)
            self.assertEqual(transactions[0].tx_hash, "0x1234567890abcdef")
    
    def test_list_identities(self):
        """Test listing identities"""
        wallet = VVAULTBlockchainWallet(self.wallet_path, hsm_enabled=False)
        wallet.initialize_wallet("test_passphrase", SecurityLevel.HIGH)
        
        # Create multiple identities
        wallet.create_identity(BlockchainType.ETHEREUM, "ETH Wallet")
        wallet.create_identity(BlockchainType.BITCOIN, "BTC Wallet")
        wallet.create_identity(BlockchainType.POLYGON, "MATIC Wallet")
        
        identities = wallet.list_identities()
        
        self.assertEqual(len(identities), 3)
        
        # Check that all identities are returned
        blockchain_types = [identity.blockchain_type for identity in identities]
        self.assertIn(BlockchainType.ETHEREUM, blockchain_types)
        self.assertIn(BlockchainType.BITCOIN, blockchain_types)
        self.assertIn(BlockchainType.POLYGON, blockchain_types)
    
    def test_list_transactions(self):
        """Test listing transactions"""
        wallet = VVAULTBlockchainWallet(self.wallet_path, hsm_enabled=False)
        wallet.initialize_wallet("test_passphrase", SecurityLevel.HIGH)
        
        identity = wallet.create_identity(BlockchainType.ETHEREUM, "Test Identity")
        
        # Create some test transactions
        tx1 = Transaction(
            tx_hash="0x1111111111111111",
            from_address=identity.address,
            to_address="0x2222222222222222",
            amount="0.1",
            blockchain_type=BlockchainType.ETHEREUM,
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        
        tx2 = Transaction(
            tx_hash="0x3333333333333333",
            from_address=identity.address,
            to_address="0x4444444444444444",
            amount="0.2",
            blockchain_type=BlockchainType.ETHEREUM,
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        
        wallet._save_transaction(tx1)
        wallet._save_transaction(tx2)
        
        # List transactions
        transactions = wallet.list_transactions(identity.did)
        
        self.assertEqual(len(transactions), 2)
        
        # Check transaction details
        tx_hashes = [tx.tx_hash for tx in transactions]
        self.assertIn("0x1111111111111111", tx_hashes)
        self.assertIn("0x3333333333333333", tx_hashes)

class TestHardwareSecurityModule(unittest.TestCase):
    """Test cases for HardwareSecurityModule"""
    
    def setUp(self):
        """Set up test environment"""
        self.hsm = HardwareSecurityModule()
    
    def test_hsm_initialization(self):
        """Test HSM initialization"""
        self.assertIsNotNone(self.hsm)
        self.assertTrue(self.hsm.is_available)
    
    def test_generate_key(self):
        """Test key generation"""
        key_pair = self.hsm.generate_key(KeyType.SIGNING, SecurityLevel.HIGH)
        
        self.assertIsNotNone(key_pair)
        self.assertEqual(key_pair.key_type, KeyType.SIGNING)
        self.assertIsNotNone(key_pair.private_key)
        self.assertIsNotNone(key_pair.public_key)
        self.assertIsNotNone(key_pair.created_at)
    
    def test_sign_data(self):
        """Test data signing"""
        key_pair = self.hsm.generate_key(KeyType.SIGNING, SecurityLevel.HIGH)
        data = b"test data to sign"
        
        signature = self.hsm.sign_data(key_pair.private_key, data)
        
        self.assertIsNotNone(signature)
        self.assertIsInstance(signature, bytes)
        self.assertGreater(len(signature), 0)
    
    def test_verify_signature(self):
        """Test signature verification"""
        key_pair = self.hsm.generate_key(KeyType.SIGNING, SecurityLevel.HIGH)
        data = b"test data to sign"
        
        signature = self.hsm.sign_data(key_pair.private_key, data)
        
        # Verify valid signature
        is_valid = self.hsm.verify_signature(key_pair.public_key, data, signature)
        self.assertTrue(is_valid)
        
        # Verify invalid signature
        invalid_signature = b"invalid signature"
        is_valid = self.hsm.verify_signature(key_pair.public_key, data, invalid_signature)
        self.assertFalse(is_valid)

class TestBlockchainConnector(unittest.TestCase):
    """Test cases for BlockchainConnector"""
    
    def test_ethereum_connector_initialization(self):
        """Test Ethereum connector initialization"""
        connector = BlockchainConnector(BlockchainType.ETHEREUM)
        
        self.assertIsNotNone(connector)
        self.assertEqual(connector.blockchain_type, BlockchainType.ETHEREUM)
    
    def test_bitcoin_connector_initialization(self):
        """Test Bitcoin connector initialization"""
        connector = BlockchainConnector(BlockchainType.BITCOIN)
        
        self.assertIsNotNone(connector)
        self.assertEqual(connector.blockchain_type, BlockchainType.BITCOIN)
    
    def test_get_balance(self):
        """Test getting balance"""
        connector = BlockchainConnector(BlockchainType.ETHEREUM)
        
        # Mock web3
        with patch('blockchain_identity_wallet.Web3') as mock_web3:
            mock_web3.return_value.eth.get_balance.return_value = 1500000000000000000  # 1.5 ETH in wei
            mock_web3.return_value.from_wei.return_value = "1.5"
            
            balance = connector.get_balance("0x742d35Cc6634C0532925a3b8D4C9db96C4b4d8b6")
            
            self.assertEqual(balance, "1.5")

class TestBlockchainConfigManager(unittest.TestCase):
    """Test cases for BlockchainConfigManager"""
    
    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
        self.config_manager = BlockchainConfigManager(self.temp_dir)
    
    def tearDown(self):
        """Clean up test environment"""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
    
    def test_config_initialization(self):
        """Test config manager initialization"""
        self.assertIsNotNone(self.config_manager)
        self.assertTrue(os.path.exists(self.config_manager.config_path))
    
    def test_default_networks(self):
        """Test default network configuration"""
        networks = self.config_manager.list_networks()
        
        self.assertGreater(len(networks), 0)
        
        # Check for common networks
        network_names = [network.name for network in networks]
        self.assertIn("Ethereum Mainnet", network_names)
        self.assertIn("Bitcoin Mainnet", network_names)
    
    def test_get_network(self):
        """Test getting network by name"""
        network = self.config_manager.get_network("ethereum_mainnet")
        
        self.assertIsNotNone(network)
        self.assertEqual(network.blockchain_type, "ethereum")
        self.assertEqual(network.network_type, NetworkType.MAINNET)
    
    def test_add_network(self):
        """Test adding a new network"""
        new_network = BlockchainNetwork(
            name="Test Network",
            blockchain_type="test",
            network_type=NetworkType.TESTNET,
            rpc_url="https://test.example.com",
            explorer_url="https://explorer.test.example.com"
        )
        
        self.config_manager.add_network("test_network", new_network)
        
        retrieved_network = self.config_manager.get_network("test_network")
        self.assertIsNotNone(retrieved_network)
        self.assertEqual(retrieved_network.name, "Test Network")
    
    def test_remove_network(self):
        """Test removing a network"""
        # Add a network first
        new_network = BlockchainNetwork(
            name="Test Network",
            blockchain_type="test",
            network_type=NetworkType.TESTNET,
            rpc_url="https://test.example.com"
        )
        
        self.config_manager.add_network("test_network", new_network)
        
        # Verify it exists
        self.assertIsNotNone(self.config_manager.get_network("test_network"))
        
        # Remove it
        self.config_manager.remove_network("test_network")
        
        # Verify it's gone
        self.assertIsNone(self.config_manager.get_network("test_network"))

class TestBackupManager(unittest.TestCase):
    """Test cases for BackupManager"""
    
    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
        self.wallet_path = os.path.join(self.temp_dir, "test_wallet")
        self.backup_path = os.path.join(self.temp_dir, "backups")
        
        # Create test wallet structure
        os.makedirs(self.wallet_path, exist_ok=True)
        os.makedirs(os.path.join(self.wallet_path, "identities"), exist_ok=True)
        os.makedirs(os.path.join(self.wallet_path, "keys"), exist_ok=True)
        os.makedirs(os.path.join(self.wallet_path, "transactions"), exist_ok=True)
        os.makedirs(os.path.join(self.wallet_path, "attestations"), exist_ok=True)
        
        self.backup_manager = BackupManager(self.wallet_path, self.backup_path)
    
    def tearDown(self):
        """Clean up test environment"""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
    
    def test_backup_manager_initialization(self):
        """Test backup manager initialization"""
        self.assertIsNotNone(self.backup_manager)
        self.assertTrue(os.path.exists(self.backup_manager.backup_path))
        self.assertTrue(os.path.exists(self.backup_manager.recovery_path))
    
    def test_create_full_backup(self):
        """Test creating full backup"""
        # Create some test data
        test_identity = {
            "did": "did:vvault:ethereum:0x1234567890abcdef",
            "address": "0x1234567890abcdef",
            "blockchain_type": "ethereum"
        }
        
        identity_file = os.path.join(self.wallet_path, "identities", "test_identity.json")
        with open(identity_file, 'w') as f:
            json.dump(test_identity, f)
        
        backup_id = self.backup_manager.create_full_backup("test_passphrase")
        
        self.assertIsNotNone(backup_id)
        
        # Check that backup file was created
        backup_file = os.path.join(self.backup_path, f"backup_{backup_id}.zip")
        self.assertTrue(os.path.exists(backup_file))
        
        # Check that metadata was saved
        backup_info = self.backup_manager.get_backup_info(backup_id)
        self.assertIsNotNone(backup_info)
        self.assertEqual(backup_info.backup_type, BackupType.FULL)
    
    def test_create_incremental_backup(self):
        """Test creating incremental backup"""
        # Create full backup first
        full_backup_id = self.backup_manager.create_full_backup("test_passphrase")
        self.assertIsNotNone(full_backup_id)
        
        # Create incremental backup
        incremental_backup_id = self.backup_manager.create_incremental_backup("test_passphrase")
        
        self.assertIsNotNone(incremental_backup_id)
        self.assertNotEqual(full_backup_id, incremental_backup_id)
        
        # Check that incremental backup file was created
        backup_file = os.path.join(self.backup_path, f"backup_{incremental_backup_id}.zip")
        self.assertTrue(os.path.exists(backup_file))
    
    def test_restore_backup(self):
        """Test restoring backup"""
        # Create some test data
        test_identity = {
            "did": "did:vvault:ethereum:0x1234567890abcdef",
            "address": "0x1234567890abcdef",
            "blockchain_type": "ethereum"
        }
        
        identity_file = os.path.join(self.wallet_path, "identities", "test_identity.json")
        with open(identity_file, 'w') as f:
            json.dump(test_identity, f)
        
        # Create backup
        backup_id = self.backup_manager.create_full_backup("test_passphrase")
        self.assertIsNotNone(backup_id)
        
        # Remove original data
        os.remove(identity_file)
        self.assertFalse(os.path.exists(identity_file))
        
        # Restore backup
        restore_path = os.path.join(self.temp_dir, "restored_wallet")
        success = self.backup_manager.restore_backup(backup_id, "test_passphrase", restore_path)
        
        self.assertTrue(success)
        
        # Check that data was restored
        restored_identity_file = os.path.join(restore_path, "identities", "test_identity.json")
        self.assertTrue(os.path.exists(restored_identity_file))
    
    def test_generate_recovery_phrase(self):
        """Test generating recovery phrase"""
        recovery_phrase = self.backup_manager.generate_recovery_phrase()
        
        self.assertIsNotNone(recovery_phrase)
        self.assertIsNotNone(recovery_phrase.phrase)
        self.assertEqual(recovery_phrase.language, "english")
        self.assertEqual(recovery_phrase.entropy_bits, 256)
        
        # Check that phrase has correct number of words
        words = recovery_phrase.phrase.split()
        self.assertEqual(len(words), 24)  # 256 bits = 24 words
    
    def test_list_backups(self):
        """Test listing backups"""
        # Create multiple backups
        backup1 = self.backup_manager.create_full_backup("test_passphrase")
        backup2 = self.backup_manager.create_full_backup("test_passphrase")
        
        backups = self.backup_manager.list_backups()
        
        self.assertEqual(len(backups), 2)
        
        backup_ids = [backup.backup_id for backup in backups]
        self.assertIn(backup1, backup_ids)
        self.assertIn(backup2, backup_ids)
    
    def test_delete_backup(self):
        """Test deleting backup"""
        # Create backup
        backup_id = self.backup_manager.create_full_backup("test_passphrase")
        
        # Verify backup exists
        backup_info = self.backup_manager.get_backup_info(backup_id)
        self.assertIsNotNone(backup_info)
        
        # Delete backup
        success = self.backup_manager.delete_backup(backup_id)
        self.assertTrue(success)
        
        # Verify backup is gone
        backup_info = self.backup_manager.get_backup_info(backup_id)
        self.assertIsNone(backup_info)
    
    def test_verify_backup(self):
        """Test backup verification"""
        # Create backup
        backup_id = self.backup_manager.create_full_backup("test_passphrase")
        
        # Verify backup
        is_valid = self.backup_manager.verify_backup(backup_id)
        self.assertTrue(is_valid)
        
        # Test with non-existent backup
        is_valid = self.backup_manager.verify_backup("non_existent_backup")
        self.assertFalse(is_valid)

class TestIntegration(unittest.TestCase):
    """Integration tests"""
    
    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
        self.wallet_path = os.path.join(self.temp_dir, "test_wallet")
    
    def tearDown(self):
        """Clean up test environment"""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
    
    def test_full_wallet_workflow(self):
        """Test complete wallet workflow"""
        # Initialize wallet
        wallet = VVAULTBlockchainWallet(self.wallet_path, hsm_enabled=False)
        success = wallet.initialize_wallet("test_passphrase", SecurityLevel.HIGH)
        self.assertTrue(success)
        
        # Create identities
        eth_identity = wallet.create_identity(BlockchainType.ETHEREUM, "ETH Wallet")
        btc_identity = wallet.create_identity(BlockchainType.BITCOIN, "BTC Wallet")
        
        self.assertIsNotNone(eth_identity)
        self.assertIsNotNone(btc_identity)
        
        # Verify identities
        eth_attestation = wallet.verify_identity(eth_identity.did, {"type": "verification"})
        btc_attestation = wallet.verify_identity(btc_identity.did, {"type": "verification"})
        
        self.assertIsNotNone(eth_attestation)
        self.assertIsNotNone(btc_attestation)
        
        # List identities
        identities = wallet.list_identities()
        self.assertEqual(len(identities), 2)
        
        # Create backup
        backup_manager = BackupManager(self.wallet_path)
        backup_id = backup_manager.create_full_backup("test_passphrase")
        self.assertIsNotNone(backup_id)
        
        # Generate recovery phrase
        recovery_phrase = backup_manager.generate_recovery_phrase()
        self.assertIsNotNone(recovery_phrase)
        
        print(f"✅ Full workflow test completed successfully!")
        print(f"   Created {len(identities)} identities")
        print(f"   Generated {len(backup_manager.list_backups())} backups")
        print(f"   Recovery phrase: {recovery_phrase.phrase[:50]}...")

def run_tests():
    """Run all tests"""
    # Create test suite
    test_suite = unittest.TestSuite()
    
    # Add test cases
    test_classes = [
        TestVVAULTBlockchainWallet,
        TestHardwareSecurityModule,
        TestBlockchainConnector,
        TestBlockchainConfigManager,
        TestBackupManager,
        TestIntegration
    ]
    
    for test_class in test_classes:
        tests = unittest.TestLoader().loadTestsFromTestCase(test_class)
        test_suite.addTests(tests)
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(test_suite)
    
    # Print summary
    print("\n" + "="*60)
    print("🧪 VVAULT Blockchain Wallet Test Summary")
    print("="*60)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped) if hasattr(result, 'skipped') else 0}")
    
    if result.failures:
        print("\n❌ Failures:")
        for test, traceback in result.failures:
            print(f"  - {test}: {traceback}")
    
    if result.errors:
        print("\n❌ Errors:")
        for test, traceback in result.errors:
            print(f"  - {test}: {traceback}")
    
    if result.wasSuccessful():
        print("\n✅ All tests passed!")
        return 0
    else:
        print("\n❌ Some tests failed!")
        return 1

if __name__ == "__main__":
    sys.exit(run_tests())


