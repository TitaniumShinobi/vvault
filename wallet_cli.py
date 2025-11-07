#!/usr/bin/env python3
"""
VVAULT Blockchain Wallet CLI

Command-line interface for the VVAULT blockchain identity wallet.

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import os
import sys
import json
import argparse
import getpass
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from blockchain_identity_wallet import (
    VVAULTBlockchainWallet, BlockchainType, SecurityLevel,
    create_wallet, load_wallet
)
from blockchain_config import get_config, BlockchainNetwork
from backup_recovery import create_backup_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class WalletCLI:
    """Command-line interface for VVAULT blockchain wallet"""
    
    def __init__(self):
        self.wallet = None
        self.config = get_config()
        self.backup_manager = None
    
    def run(self, args):
        """Run the CLI with given arguments"""
        try:
            if args.command == 'init':
                self.init_wallet(args)
            elif args.command == 'create-identity':
                self.create_identity(args)
            elif args.command == 'list-identities':
                self.list_identities(args)
            elif args.command == 'get-balance':
                self.get_balance(args)
            elif args.command == 'send':
                self.send_transaction(args)
            elif args.command == 'verify-identity':
                self.verify_identity(args)
            elif args.command == 'list-transactions':
                self.list_transactions(args)
            elif args.command == 'backup':
                self.create_backup(args)
            elif args.command == 'restore':
                self.restore_backup(args)
            elif args.command == 'recovery-phrase':
                self.generate_recovery_phrase(args)
            elif args.command == 'config':
                self.show_config(args)
            elif args.command == 'networks':
                self.list_networks(args)
            else:
                print(f"Unknown command: {args.command}")
                return 1
            
            return 0
            
        except Exception as e:
            logger.error(f"Command failed: {e}")
            return 1
    
    def init_wallet(self, args):
        """Initialize a new wallet"""
        print("🔐 VVAULT Blockchain Wallet Initialization")
        print("=" * 50)
        
        # Get passphrase
        passphrase = getpass.getpass("Enter master passphrase: ")
        if not passphrase:
            print("❌ Passphrase cannot be empty")
            return
        
        confirm_passphrase = getpass.getpass("Confirm passphrase: ")
        if passphrase != confirm_passphrase:
            print("❌ Passphrases do not match")
            return
        
        # Get security level
        security_level = SecurityLevel.HIGH
        if args.security_level:
            try:
                security_level = SecurityLevel(args.security_level.upper())
            except ValueError:
                print(f"❌ Invalid security level: {args.security_level}")
                return
        
        # Create wallet
        wallet_path = args.wallet_path or os.path.join(os.getcwd(), "blockchain_wallet")
        hsm_enabled = not args.no_hsm
        
        print(f"\nCreating wallet at: {wallet_path}")
        print(f"HSM enabled: {hsm_enabled}")
        print(f"Security level: {security_level.value}")
        
        self.wallet = create_wallet(wallet_path, passphrase, hsm_enabled)
        
        if self.wallet:
            print("✅ Wallet initialized successfully!")
            print(f"   Path: {wallet_path}")
            print(f"   HSM: {hsm_enabled}")
            print(f"   Security: {security_level.value}")
        else:
            print("❌ Wallet initialization failed")
    
    def create_identity(self, args):
        """Create a new blockchain identity"""
        if not self._load_wallet():
            return
        
        # Get blockchain type
        try:
            blockchain_type = BlockchainType(args.blockchain_type.lower())
        except ValueError:
            print(f"❌ Invalid blockchain type: {args.blockchain_type}")
            print(f"Available types: {[t.value for t in BlockchainType]}")
            return
        
        # Get alias
        alias = args.alias or input("Enter alias (optional): ").strip()
        if not alias:
            alias = None
        
        print(f"\nCreating {blockchain_type.value} identity...")
        if alias:
            print(f"Alias: {alias}")
        
        identity = self.wallet.create_identity(blockchain_type, alias)
        
        if identity:
            print("✅ Identity created successfully!")
            print(f"   DID: {identity.did}")
            print(f"   Address: {identity.address}")
            print(f"   Blockchain: {identity.blockchain_type.value}")
            print(f"   Created: {identity.created_at}")
        else:
            print("❌ Identity creation failed")
    
    def list_identities(self, args):
        """List all identities"""
        if not self._load_wallet():
            return
        
        identities = self.wallet.list_identities()
        
        if not identities:
            print("No identities found")
            return
        
        print(f"\nIdentities ({len(identities)}):")
        print("-" * 80)
        
        for identity in identities:
            print(f"DID: {identity.did}")
            print(f"Address: {identity.address}")
            print(f"Blockchain: {identity.blockchain_type.value}")
            print(f"Created: {identity.created_at}")
            print(f"Last Used: {identity.last_used}")
            if identity.metadata.get('alias'):
                print(f"Alias: {identity.metadata['alias']}")
            print("-" * 80)
    
    def get_balance(self, args):
        """Get balance for an identity"""
        if not self._load_wallet():
            return
        
        # Find identity
        identity = self._find_identity(args.did)
        if not identity:
            return
        
        print(f"\nGetting balance for {identity.did}...")
        balance = self.wallet.get_balance(identity.did)
        
        print(f"Balance: {balance} {identity.metadata.get('native_currency', 'tokens')}")
    
    def send_transaction(self, args):
        """Send a transaction"""
        if not self._load_wallet():
            return
        
        # Find identity
        identity = self._find_identity(args.from_did)
        if not identity:
            return
        
        # Get passphrase
        passphrase = getpass.getpass("Enter passphrase: ")
        if not passphrase:
            print("❌ Passphrase cannot be empty")
            return
        
        print(f"\nSending {args.amount} from {identity.did} to {args.to_address}...")
        
        tx_hash = self.wallet.send_transaction(
            identity.did,
            args.to_address,
            args.amount,
            passphrase
        )
        
        if tx_hash:
            print("✅ Transaction sent successfully!")
            print(f"   Transaction Hash: {tx_hash}")
        else:
            print("❌ Transaction failed")
    
    def verify_identity(self, args):
        """Verify an identity"""
        if not self._load_wallet():
            return
        
        # Find identity
        identity = self._find_identity(args.did)
        if not identity:
            return
        
        # Get credential data
        credential_data = {
            "type": "identity_verification",
            "timestamp": datetime.now().isoformat()
        }
        
        if args.credential_data:
            try:
                credential_data.update(json.loads(args.credential_data))
            except json.JSONDecodeError:
                print("❌ Invalid JSON in credential data")
                return
        
        print(f"\nVerifying identity {identity.did}...")
        
        attestation = self.wallet.verify_identity(identity.did, credential_data)
        
        if attestation:
            print("✅ Identity verification completed!")
            print(f"   Attestation ID: {attestation.attestation_id}")
            print(f"   Status: {attestation.verification_status}")
            print(f"   Issued: {attestation.issued_at}")
        else:
            print("❌ Identity verification failed")
    
    def list_transactions(self, args):
        """List transactions"""
        if not self._load_wallet():
            return
        
        # Find identity if specified
        identity = None
        if args.did:
            identity = self._find_identity(args.did)
            if not identity:
                return
        
        transactions = self.wallet.list_transactions(identity.did if identity else None)
        
        if not transactions:
            print("No transactions found")
            return
        
        print(f"\nTransactions ({len(transactions)}):")
        print("-" * 100)
        
        for tx in transactions:
            print(f"Hash: {tx.tx_hash}")
            print(f"From: {tx.from_address}")
            print(f"To: {tx.to_address}")
            print(f"Amount: {tx.amount}")
            print(f"Blockchain: {tx.blockchain_type.value}")
            print(f"Status: {tx.status}")
            print(f"Timestamp: {tx.timestamp}")
            if tx.gas_fee:
                print(f"Gas Fee: {tx.gas_fee}")
            print("-" * 100)
    
    def create_backup(self, args):
        """Create a backup"""
        if not self._load_wallet():
            return
        
        if not self.backup_manager:
            self.backup_manager = create_backup_manager(self.wallet.wallet_dir)
        
        # Get passphrase
        passphrase = getpass.getpass("Enter backup passphrase: ")
        if not passphrase:
            print("❌ Passphrase cannot be empty")
            return
        
        print("\nCreating backup...")
        
        if args.incremental:
            backup_id = self.backup_manager.create_incremental_backup(passphrase)
        else:
            backup_id = self.backup_manager.create_full_backup(passphrase, args.include_keys)
        
        if backup_id:
            print("✅ Backup created successfully!")
            print(f"   Backup ID: {backup_id}")
            
            # Show backup info
            backup_info = self.backup_manager.get_backup_info(backup_id)
            if backup_info:
                print(f"   Type: {backup_info.backup_type.value}")
                print(f"   Size: {backup_info.size_bytes} bytes")
                print(f"   Created: {backup_info.created_at}")
        else:
            print("❌ Backup creation failed")
    
    def restore_backup(self, args):
        """Restore from backup"""
        if not self._load_wallet():
            return
        
        if not self.backup_manager:
            self.backup_manager = create_backup_manager(self.wallet.wallet_dir)
        
        # Get passphrase
        passphrase = getpass.getpass("Enter backup passphrase: ")
        if not passphrase:
            print("❌ Passphrase cannot be empty")
            return
        
        print(f"\nRestoring backup {args.backup_id}...")
        
        success = self.backup_manager.restore_backup(args.backup_id, passphrase)
        
        if success:
            print("✅ Backup restored successfully!")
        else:
            print("❌ Backup restoration failed")
    
    def generate_recovery_phrase(self, args):
        """Generate recovery phrase"""
        if not self._load_wallet():
            return
        
        if not self.backup_manager:
            self.backup_manager = create_backup_manager(self.wallet.wallet_dir)
        
        print("\nGenerating recovery phrase...")
        
        recovery_phrase = self.backup_manager.generate_recovery_phrase(
            language=args.language,
            entropy_bits=args.entropy_bits
        )
        
        print("✅ Recovery phrase generated!")
        print(f"   Language: {recovery_phrase.language}")
        print(f"   Entropy: {recovery_phrase.entropy_bits} bits")
        print(f"   Words: {len(recovery_phrase.phrase.split())}")
        print(f"\n🔐 RECOVERY PHRASE (KEEP SECURE):")
        print(f"   {recovery_phrase.phrase}")
        print(f"\n⚠️  Store this phrase securely. It can be used to recover your wallet.")
    
    def show_config(self, args):
        """Show wallet configuration"""
        print("\nWallet Configuration:")
        print("-" * 40)
        print(f"Vault Path: {self.config.wallet_config.vault_path}")
        print(f"HSM Enabled: {self.config.wallet_config.hsm_enabled}")
        print(f"Auto Backup: {self.config.wallet_config.auto_backup}")
        print(f"Backup Interval: {self.config.wallet_config.backup_interval}s")
        print(f"Max Backups: {self.config.wallet_config.max_backups}")
        print(f"Encryption: {self.config.wallet_config.encryption_algorithm}")
        print(f"Security Level: {self.config.wallet_config.security_level}")
        print(f"Log Level: {self.config.wallet_config.log_level}")
        
        print("\nSecurity Configuration:")
        print("-" * 40)
        print(f"Require HSM: {self.config.security_config.require_hsm}")
        print(f"Max Failed Attempts: {self.config.security_config.max_failed_attempts}")
        print(f"Lockout Duration: {self.config.security_config.lockout_duration}s")
        print(f"Session Timeout: {self.config.security_config.session_timeout}s")
        print(f"Require 2FA: {self.config.security_config.require_2fa}")
        print(f"Audit Logging: {self.config.security_config.audit_logging}")
    
    def list_networks(self, args):
        """List configured networks"""
        networks = self.config.list_networks()
        
        if not networks:
            print("No networks configured")
            return
        
        print(f"\nConfigured Networks ({len(networks)}):")
        print("-" * 80)
        
        for network in networks:
            print(f"Name: {network.name}")
            print(f"Type: {network.blockchain_type}")
            print(f"Network: {network.network_type.value}")
            print(f"RPC URL: {network.rpc_url}")
            print(f"Explorer: {network.explorer_url}")
            if network.chain_id:
                print(f"Chain ID: {network.chain_id}")
            print("-" * 80)
    
    def _load_wallet(self) -> bool:
        """Load the wallet"""
        if self.wallet:
            return True
        
        wallet_path = os.path.join(os.getcwd(), "blockchain_wallet")
        if not os.path.exists(wallet_path):
            print("❌ Wallet not found. Run 'init' command first.")
            return False
        
        try:
            self.wallet = load_wallet(wallet_path)
            return True
        except Exception as e:
            print(f"❌ Failed to load wallet: {e}")
            return False
    
    def _find_identity(self, did: str):
        """Find identity by DID"""
        identities = self.wallet.list_identities()
        
        # Try exact match first
        for identity in identities:
            if identity.did == did:
                return identity
        
        # Try partial match
        for identity in identities:
            if did in identity.did:
                return identity
        
        print(f"❌ Identity not found: {did}")
        return None

def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="VVAULT Blockchain Identity Wallet CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s init --wallet-path ./my_wallet
  %(prog)s create-identity --blockchain-type ethereum --alias "My ETH Wallet"
  %(prog)s list-identities
  %(prog)s get-balance --did did:vvault:ethereum:0x...
  %(prog)s send --from-did did:vvault:ethereum:0x... --to-address 0x... --amount 0.1
  %(prog)s backup --full
  %(prog)s recovery-phrase
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Init command
    init_parser = subparsers.add_parser('init', help='Initialize a new wallet')
    init_parser.add_argument('--wallet-path', help='Path to wallet directory')
    init_parser.add_argument('--no-hsm', action='store_true', help='Disable HSM')
    init_parser.add_argument('--security-level', choices=['low', 'medium', 'high', 'critical'], help='Security level')
    
    # Create identity command
    create_parser = subparsers.add_parser('create-identity', help='Create a new blockchain identity')
    create_parser.add_argument('--blockchain-type', required=True, help='Blockchain type (ethereum, bitcoin, etc.)')
    create_parser.add_argument('--alias', help='Identity alias')
    
    # List identities command
    subparsers.add_parser('list-identities', help='List all identities')
    
    # Get balance command
    balance_parser = subparsers.add_parser('get-balance', help='Get balance for an identity')
    balance_parser.add_argument('--did', required=True, help='Identity DID')
    
    # Send transaction command
    send_parser = subparsers.add_parser('send', help='Send a transaction')
    send_parser.add_argument('--from-did', required=True, help='Sender identity DID')
    send_parser.add_argument('--to-address', required=True, help='Recipient address')
    send_parser.add_argument('--amount', required=True, help='Amount to send')
    
    # Verify identity command
    verify_parser = subparsers.add_parser('verify-identity', help='Verify an identity')
    verify_parser.add_argument('--did', required=True, help='Identity DID')
    verify_parser.add_argument('--credential-data', help='JSON credential data')
    
    # List transactions command
    tx_parser = subparsers.add_parser('list-transactions', help='List transactions')
    tx_parser.add_argument('--did', help='Filter by identity DID')
    
    # Backup command
    backup_parser = subparsers.add_parser('backup', help='Create a backup')
    backup_parser.add_argument('--incremental', action='store_true', help='Create incremental backup')
    backup_parser.add_argument('--include-keys', action='store_true', default=True, help='Include private keys')
    
    # Restore command
    restore_parser = subparsers.add_parser('restore', help='Restore from backup')
    restore_parser.add_argument('--backup-id', required=True, help='Backup ID to restore')
    
    # Recovery phrase command
    recovery_parser = subparsers.add_parser('recovery-phrase', help='Generate recovery phrase')
    recovery_parser.add_argument('--language', default='english', help='Mnemonic language')
    recovery_parser.add_argument('--entropy-bits', type=int, default=256, help='Entropy bits (128, 160, 192, 224, 256)')
    
    # Config command
    subparsers.add_parser('config', help='Show wallet configuration')
    
    # Networks command
    subparsers.add_parser('networks', help='List configured networks')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    cli = WalletCLI()
    return cli.run(args)

if __name__ == "__main__":
    sys.exit(main())


