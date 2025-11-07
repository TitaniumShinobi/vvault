#!/usr/bin/env python3
"""
VVAULT Blockchain Configuration

Configuration management for blockchain networks, RPC endpoints,
and wallet settings.

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import os
import json
import yaml
import logging
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass, asdict
from pathlib import Path
from enum import Enum

logger = logging.getLogger(__name__)

class NetworkType(Enum):
    """Blockchain network types"""
    MAINNET = "mainnet"
    TESTNET = "testnet"
    REGTEST = "regtest"
    DEVNET = "devnet"

@dataclass
class BlockchainNetwork:
    """Blockchain network configuration"""
    name: str
    blockchain_type: str
    network_type: NetworkType
    rpc_url: str
    chain_id: Optional[int] = None
    gas_price: Optional[str] = None
    gas_limit: Optional[int] = None
    block_time: Optional[int] = None
    explorer_url: Optional[str] = None
    native_currency: str = "ETH"
    decimals: int = 18
    metadata: Dict[str, Any] = None

@dataclass
class WalletConfig:
    """Wallet configuration"""
    vault_path: str
    hsm_enabled: bool = True
    auto_backup: bool = True
    backup_interval: int = 3600  # seconds
    max_backups: int = 10
    encryption_algorithm: str = "AES-256-GCM"
    key_derivation: str = "PBKDF2"
    security_level: str = "HIGH"
    log_level: str = "INFO"
    metadata: Dict[str, Any] = None

@dataclass
class SecurityConfig:
    """Security configuration"""
    require_hsm: bool = False
    max_failed_attempts: int = 3
    lockout_duration: int = 300  # seconds
    session_timeout: int = 1800  # seconds
    require_2fa: bool = False
    audit_logging: bool = True
    key_rotation_interval: int = 31536000  # seconds (1 year)
    metadata: Dict[str, Any] = None

class BlockchainConfigManager:
    """Blockchain configuration manager"""
    
    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.path.join(os.path.dirname(__file__), "config")
        self.networks_file = os.path.join(self.config_path, "networks.json")
        self.wallet_file = os.path.join(self.config_path, "wallet.yaml")
        self.security_file = os.path.join(self.config_path, "security.yaml")
        
        # Ensure config directory exists
        os.makedirs(self.config_path, exist_ok=True)
        
        # Load configurations
        self.networks = self._load_networks()
        self.wallet_config = self._load_wallet_config()
        self.security_config = self._load_security_config()
        
        logger.info(f"Blockchain configuration loaded from: {self.config_path}")
    
    def _load_networks(self) -> Dict[str, BlockchainNetwork]:
        """Load blockchain networks configuration"""
        networks = {}
        
        if os.path.exists(self.networks_file):
            try:
                with open(self.networks_file, 'r') as f:
                    networks_data = json.load(f)
                
                for name, network_data in networks_data.items():
                    networks[name] = BlockchainNetwork(**network_data)
                
                logger.info(f"Loaded {len(networks)} blockchain networks")
                
            except Exception as e:
                logger.error(f"Error loading networks config: {e}")
                networks = self._create_default_networks()
        else:
            networks = self._create_default_networks()
            self._save_networks(networks)
        
        return networks
    
    def _load_wallet_config(self) -> WalletConfig:
        """Load wallet configuration"""
        if os.path.exists(self.wallet_file):
            try:
                with open(self.wallet_file, 'r') as f:
                    config_data = yaml.safe_load(f)
                
                return WalletConfig(**config_data)
                
            except Exception as e:
                logger.error(f"Error loading wallet config: {e}")
                return self._create_default_wallet_config()
        else:
            config = self._create_default_wallet_config()
            self._save_wallet_config(config)
            return config
    
    def _load_security_config(self) -> SecurityConfig:
        """Load security configuration"""
        if os.path.exists(self.security_file):
            try:
                with open(self.security_file, 'r') as f:
                    config_data = yaml.safe_load(f)
                
                return SecurityConfig(**config_data)
                
            except Exception as e:
                logger.error(f"Error loading security config: {e}")
                return self._create_default_security_config()
        else:
            config = self._create_default_security_config()
            self._save_security_config(config)
            return config
    
    def _create_default_networks(self) -> Dict[str, BlockchainNetwork]:
        """Create default blockchain networks"""
        networks = {
            "ethereum_mainnet": BlockchainNetwork(
                name="Ethereum Mainnet",
                blockchain_type="ethereum",
                network_type=NetworkType.MAINNET,
                rpc_url="https://mainnet.infura.io/v3/YOUR_PROJECT_ID",
                chain_id=1,
                gas_price="20",
                gas_limit=21000,
                block_time=13,
                explorer_url="https://etherscan.io",
                native_currency="ETH",
                decimals=18,
                metadata={
                    "description": "Ethereum main network",
                    "supported_features": ["smart_contracts", "tokens", "defi"]
                }
            ),
            "ethereum_sepolia": BlockchainNetwork(
                name="Ethereum Sepolia Testnet",
                blockchain_type="ethereum",
                network_type=NetworkType.TESTNET,
                rpc_url="https://sepolia.infura.io/v3/YOUR_PROJECT_ID",
                chain_id=11155111,
                gas_price="1",
                gas_limit=21000,
                block_time=12,
                explorer_url="https://sepolia.etherscan.io",
                native_currency="ETH",
                decimals=18,
                metadata={
                    "description": "Ethereum Sepolia test network",
                    "faucet_url": "https://sepoliafaucet.com"
                }
            ),
            "bitcoin_mainnet": BlockchainNetwork(
                name="Bitcoin Mainnet",
                blockchain_type="bitcoin",
                network_type=NetworkType.MAINNET,
                rpc_url="https://api.blockcypher.com/v1/btc/main",
                block_time=600,
                explorer_url="https://blockstream.info",
                native_currency="BTC",
                decimals=8,
                metadata={
                    "description": "Bitcoin main network",
                    "supported_features": ["transactions", "multisig"]
                }
            ),
            "bitcoin_testnet": BlockchainNetwork(
                name="Bitcoin Testnet",
                blockchain_type="bitcoin",
                network_type=NetworkType.TESTNET,
                rpc_url="https://api.blockcypher.com/v1/btc/test3",
                block_time=600,
                explorer_url="https://blockstream.info/testnet",
                native_currency="BTC",
                decimals=8,
                metadata={
                    "description": "Bitcoin test network",
                    "faucet_url": "https://testnet-faucet.mempool.co"
                }
            ),
            "polygon_mainnet": BlockchainNetwork(
                name="Polygon Mainnet",
                blockchain_type="polygon",
                network_type=NetworkType.MAINNET,
                rpc_url="https://polygon-rpc.com",
                chain_id=137,
                gas_price="30",
                gas_limit=21000,
                block_time=2,
                explorer_url="https://polygonscan.com",
                native_currency="MATIC",
                decimals=18,
                metadata={
                    "description": "Polygon main network",
                    "supported_features": ["smart_contracts", "tokens", "defi", "low_fees"]
                }
            ),
            "arbitrum_mainnet": BlockchainNetwork(
                name="Arbitrum One",
                blockchain_type="arbitrum",
                network_type=NetworkType.MAINNET,
                rpc_url="https://arb1.arbitrum.io/rpc",
                chain_id=42161,
                gas_price="0.1",
                gas_limit=21000,
                block_time=0.25,
                explorer_url="https://arbiscan.io",
                native_currency="ETH",
                decimals=18,
                metadata={
                    "description": "Arbitrum One main network",
                    "supported_features": ["smart_contracts", "tokens", "defi", "layer2"]
                }
            ),
            "optimism_mainnet": BlockchainNetwork(
                name="Optimism Mainnet",
                blockchain_type="optimism",
                network_type=NetworkType.MAINNET,
                rpc_url="https://mainnet.optimism.io",
                chain_id=10,
                gas_price="0.001",
                gas_limit=21000,
                block_time=2,
                explorer_url="https://optimistic.etherscan.io",
                native_currency="ETH",
                decimals=18,
                metadata={
                    "description": "Optimism main network",
                    "supported_features": ["smart_contracts", "tokens", "defi", "layer2"]
                }
            ),
            "base_mainnet": BlockchainNetwork(
                name="Base Mainnet",
                blockchain_type="base",
                network_type=NetworkType.MAINNET,
                rpc_url="https://mainnet.base.org",
                chain_id=8453,
                gas_price="0.001",
                gas_limit=21000,
                block_time=2,
                explorer_url="https://basescan.org",
                native_currency="ETH",
                decimals=18,
                metadata={
                    "description": "Base main network",
                    "supported_features": ["smart_contracts", "tokens", "defi", "layer2"]
                }
            )
        }
        
        return networks
    
    def _create_default_wallet_config(self) -> WalletConfig:
        """Create default wallet configuration"""
        return WalletConfig(
            vault_path=os.path.join(os.path.dirname(__file__), "blockchain_wallet"),
            hsm_enabled=True,
            auto_backup=True,
            backup_interval=3600,
            max_backups=10,
            encryption_algorithm="AES-256-GCM",
            key_derivation="PBKDF2",
            security_level="HIGH",
            log_level="INFO",
            metadata={
                "version": "1.0.0",
                "created_by": "VVAULT",
                "description": "Default wallet configuration"
            }
        )
    
    def _create_default_security_config(self) -> SecurityConfig:
        """Create default security configuration"""
        return SecurityConfig(
            require_hsm=False,
            max_failed_attempts=3,
            lockout_duration=300,
            session_timeout=1800,
            require_2fa=False,
            audit_logging=True,
            key_rotation_interval=31536000,
            metadata={
                "version": "1.0.0",
                "created_by": "VVAULT",
                "description": "Default security configuration"
            }
        )
    
    def _save_networks(self, networks: Dict[str, BlockchainNetwork]):
        """Save networks configuration"""
        try:
            networks_data = {}
            for name, network in networks.items():
                networks_data[name] = asdict(network)
            
            with open(self.networks_file, 'w') as f:
                json.dump(networks_data, f, indent=2, default=str)
            
            logger.info(f"Saved {len(networks)} blockchain networks")
            
        except Exception as e:
            logger.error(f"Error saving networks config: {e}")
    
    def _save_wallet_config(self, config: WalletConfig):
        """Save wallet configuration"""
        try:
            with open(self.wallet_file, 'w') as f:
                yaml.dump(asdict(config), f, default_flow_style=False, indent=2)
            
            logger.info("Saved wallet configuration")
            
        except Exception as e:
            logger.error(f"Error saving wallet config: {e}")
    
    def _save_security_config(self, config: SecurityConfig):
        """Save security configuration"""
        try:
            with open(self.security_file, 'w') as f:
                yaml.dump(asdict(config), f, default_flow_style=False, indent=2)
            
            logger.info("Saved security configuration")
            
        except Exception as e:
            logger.error(f"Error saving security config: {e}")
    
    def get_network(self, name: str) -> Optional[BlockchainNetwork]:
        """Get blockchain network by name"""
        return self.networks.get(name)
    
    def list_networks(self) -> List[BlockchainNetwork]:
        """List all configured networks"""
        return list(self.networks.values())
    
    def add_network(self, name: str, network: BlockchainNetwork):
        """Add a new blockchain network"""
        self.networks[name] = network
        self._save_networks(self.networks)
        logger.info(f"Added network: {name}")
    
    def remove_network(self, name: str):
        """Remove a blockchain network"""
        if name in self.networks:
            del self.networks[name]
            self._save_networks(self.networks)
            logger.info(f"Removed network: {name}")
    
    def update_wallet_config(self, config: WalletConfig):
        """Update wallet configuration"""
        self.wallet_config = config
        self._save_wallet_config(config)
        logger.info("Updated wallet configuration")
    
    def update_security_config(self, config: SecurityConfig):
        """Update security configuration"""
        self.security_config = config
        self._save_security_config(config)
        logger.info("Updated security configuration")
    
    def get_network_by_type(self, blockchain_type: str, network_type: NetworkType = None) -> List[BlockchainNetwork]:
        """Get networks by blockchain type and optionally network type"""
        networks = []
        for network in self.networks.values():
            if network.blockchain_type == blockchain_type:
                if network_type is None or network.network_type == network_type:
                    networks.append(network)
        return networks
    
    def get_mainnet_networks(self) -> List[BlockchainNetwork]:
        """Get all mainnet networks"""
        return self.get_network_by_type("", NetworkType.MAINNET)
    
    def get_testnet_networks(self) -> List[BlockchainNetwork]:
        """Get all testnet networks"""
        return self.get_network_by_type("", NetworkType.TESTNET)
    
    def validate_config(self) -> Dict[str, List[str]]:
        """Validate configuration and return any issues"""
        issues = {
            "networks": [],
            "wallet": [],
            "security": []
        }
        
        # Validate networks
        for name, network in self.networks.items():
            if not network.rpc_url:
                issues["networks"].append(f"Network {name} missing RPC URL")
            if not network.explorer_url:
                issues["networks"].append(f"Network {name} missing explorer URL")
        
        # Validate wallet config
        if not os.path.exists(self.wallet_config.vault_path):
            issues["wallet"].append("Vault path does not exist")
        if self.wallet_config.backup_interval < 60:
            issues["wallet"].append("Backup interval too short (minimum 60 seconds)")
        if self.wallet_config.max_backups < 1:
            issues["wallet"].append("Max backups must be at least 1")
        
        # Validate security config
        if self.security_config.max_failed_attempts < 1:
            issues["security"].append("Max failed attempts must be at least 1")
        if self.security_config.lockout_duration < 0:
            issues["security"].append("Lockout duration cannot be negative")
        if self.security_config.session_timeout < 60:
            issues["security"].append("Session timeout too short (minimum 60 seconds)")
        
        return issues

# Global configuration instance
config_manager = BlockchainConfigManager()

def get_config() -> BlockchainConfigManager:
    """Get global configuration manager"""
    return config_manager

def get_network(name: str) -> Optional[BlockchainNetwork]:
    """Get blockchain network by name"""
    return config_manager.get_network(name)

def list_networks() -> List[BlockchainNetwork]:
    """List all configured networks"""
    return config_manager.list_networks()

if __name__ == "__main__":
    # Example usage
    print("🔧 VVAULT Blockchain Configuration")
    print("=" * 40)
    
    # Load configuration
    config = get_config()
    
    # List networks
    networks = list_networks()
    print(f"\nConfigured networks ({len(networks)}):")
    for network in networks:
        print(f"  - {network.name}")
        print(f"    Type: {network.blockchain_type}")
        print(f"    Network: {network.network_type.value}")
        print(f"    RPC: {network.rpc_url}")
        print(f"    Explorer: {network.explorer_url}")
        print()
    
    # Validate configuration
    issues = config.validate_config()
    if any(issues.values()):
        print("⚠️  Configuration issues found:")
        for category, problems in issues.items():
            if problems:
                print(f"  {category}:")
                for problem in problems:
                    print(f"    - {problem}")
    else:
        print("✅ Configuration is valid")
    
    print(f"\nWallet config: {config.wallet_config.vault_path}")
    print(f"Security level: {config.security_config.require_hsm}")
    print(f"HSM enabled: {config.wallet_config.hsm_enabled}")


