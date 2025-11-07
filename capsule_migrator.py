#!/usr/bin/env python3
"""
Capsule Migrator - Convert Legacy Personality Capsules to Memory Records
Transforms existing VVAULT capsules into individual memory records with embeddings
"""

import json
import os
import hashlib
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path

# Import schema gate
from vvault.schema_gate import SchemaGate

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CapsuleMigrator:
    """Migrate legacy capsules to new memory record format"""
    
    def __init__(self, vault_path: str = None):
        self.vault_path = vault_path or os.path.dirname(os.path.abspath(__file__))
        self.capsules_dir = os.path.join(self.vault_path, "capsules")
        self.indexes_dir = os.path.join(self.vault_path, "indexes")
        self.memory_records_dir = os.path.join(self.vault_path, "memory_records")
        
        # Ensure directories exist
        os.makedirs(self.capsules_dir, exist_ok=True)
        os.makedirs(self.indexes_dir, exist_ok=True)
        os.makedirs(self.memory_records_dir, exist_ok=True)
        
        # Initialize schema gate
        self.schema_gate = SchemaGate()
        
        logger.info(f"Initialized CapsuleMigrator with vault path: {self.vault_path}")
    
    def load_legacy_capsule(self, capsule_path: str) -> Dict[str, Any]:
        """Load a legacy capsule file"""
        try:
            with open(capsule_path, 'r', encoding='utf-8') as f:
                capsule_data = json.load(f)
            
            logger.info(f"Loaded legacy capsule: {capsule_path}")
            return capsule_data
            
        except Exception as e:
            logger.error(f"Failed to load capsule {capsule_path}: {e}")
            return {}
    
    def extract_memory_entries(self, capsule_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract memory entries from legacy capsule"""
        memory_entries = []
        memory = capsule_data.get('memory', {})
        metadata = capsule_data.get('metadata', {})
        
        # Extract from each memory type
        memory_types = [
            'short_term_memories',
            'long_term_memories', 
            'emotional_memories',
            'procedural_memories',
            'episodic_memories'
        ]
        
        for mem_type in memory_types:
            entries = memory.get(mem_type, [])
            if not entries:
                continue
            
            for i, entry in enumerate(entries):
                if not isinstance(entry, str) or not entry.strip():
                    continue
                
                # Create memory entry
                memory_entry = {
                    'content': entry.strip(),
                    'type': mem_type,
                    'index': i,
                    'instance_name': metadata.get('instance_name', 'unknown'),
                    'capsule_uuid': metadata.get('uuid', 'unknown'),
                    'capsule_timestamp': metadata.get('timestamp', ''),
                    'capsule_fingerprint': metadata.get('fingerprint_hash', '')
                }
                
                memory_entries.append(memory_entry)
        
        logger.info(f"Extracted {len(memory_entries)} memory entries from capsule")
        return memory_entries
    
    def create_memory_record(self, 
                           memory_entry: Dict[str, Any],
                           embedding: List[float],
                           embed_model: str) -> Dict[str, Any]:
        """Create a memory record from extracted entry"""
        
        # Generate memory ID
        instance_name = memory_entry['instance_name'].lower()
        mem_type = memory_entry['type']
        index = memory_entry['index']
        content_hash = hashlib.sha256(memory_entry['content'].encode()).hexdigest()[:8]
        
        memory_id = f"{instance_name}_{mem_type}_{index}_{content_hash}"
        
        # Generate source ID
        source_id = f"vvault_{instance_name}_{mem_type}"
        
        # Create tags
        tags = [
            mem_type,
            memory_entry['instance_name'],
            'migrated',
            'legacy_capsule'
        ]
        
        # Create memory record using schema gate
        record = self.schema_gate.create_memory_record(
            memory_id=memory_id,
            source_id=source_id,
            raw_content=memory_entry['content'],
            embedding=embedding,
            embed_model=embed_model,
            consent="self",
            tags=tags,
            preprocessed=memory_entry['content']  # No preprocessing for now
        )
        
        # Add legacy metadata
        record['legacy_metadata'] = {
            'capsule_uuid': memory_entry['capsule_uuid'],
            'capsule_timestamp': memory_entry['capsule_timestamp'],
            'capsule_fingerprint': memory_entry['capsule_fingerprint'],
            'original_type': memory_entry['type'],
            'original_index': memory_entry['index']
        }
        
        return record
    
    def get_mock_embedding(self, content: str, embed_dim: int = 384) -> List[float]:
        """Generate mock embedding for testing (replace with real embedding model)"""
        # Use content hash to generate deterministic mock embedding
        content_hash = hash(content) % 1000000
        import random
        random.seed(content_hash)
        
        # Generate embedding-like values
        embedding = [random.uniform(-1.0, 1.0) for _ in range(embed_dim)]
        
        # Normalize to unit vector
        import math
        norm = math.sqrt(sum(x*x for x in embedding))
        if norm > 0:
            embedding = [x/norm for x in embedding]
        
        return embedding
    
    def migrate_capsule(self, 
                       capsule_path: str, 
                       embed_model: str = "text-embedding-3-small:v1.0",
                       embed_dim: int = 384) -> Dict[str, Any]:
        """
        Migrate a single capsule to memory records
        
        Args:
            capsule_path: Path to legacy capsule file
            embed_model: Embedding model name and version
            embed_dim: Embedding dimension
            
        Returns:
            Migration result with statistics
        """
        logger.info(f"Migrating capsule: {capsule_path}")
        
        # Load legacy capsule
        capsule_data = self.load_legacy_capsule(capsule_path)
        if not capsule_data:
            return {
                'success': False,
                'error': 'Failed to load capsule',
                'records_created': 0,
                'records_valid': 0
            }
        
        # Extract memory entries
        memory_entries = self.extract_memory_entries(capsule_data)
        if not memory_entries:
            return {
                'success': True,
                'records_created': 0,
                'records_valid': 0,
                'message': 'No memory entries found'
            }
        
        # Create memory records
        records_created = 0
        records_valid = 0
        valid_records = []
        
        for entry in memory_entries:
            try:
                # Generate embedding (mock for now)
                embedding = self.get_mock_embedding(entry['content'], embed_dim)
                
                # Create memory record
                record = self.create_memory_record(entry, embedding, embed_model)
                records_created += 1
                
                # Validate record
                validation_result = self.schema_gate.validate_memory_record(record)
                if validation_result.valid:
                    records_valid += 1
                    valid_records.append(record)
                else:
                    logger.warning(f"Invalid record created: {validation_result.errors}")
                
            except Exception as e:
                logger.error(f"Failed to create record for entry: {e}")
        
        # Save valid records
        if valid_records:
            self._save_memory_records(valid_records, capsule_data.get('metadata', {}).get('instance_name', 'unknown'))
        
        return {
            'success': True,
            'records_created': records_created,
            'records_valid': records_valid,
            'capsule_path': capsule_path,
            'instance_name': capsule_data.get('metadata', {}).get('instance_name', 'unknown')
        }
    
    def _save_memory_records(self, records: List[Dict[str, Any]], instance_name: str) -> None:
        """Save memory records to files"""
        instance_dir = os.path.join(self.memory_records_dir, instance_name)
        os.makedirs(instance_dir, exist_ok=True)
        
        # Save individual records
        for record in records:
            memory_id = record['memory_id']
            record_file = os.path.join(instance_dir, f"{memory_id}.json")
            
            try:
                with open(record_file, 'w', encoding='utf-8') as f:
                    json.dump(record, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.error(f"Failed to save record {memory_id}: {e}")
        
        # Save batch file
        batch_file = os.path.join(instance_dir, f"{instance_name}_memory_records_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        try:
            with open(batch_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'metadata': {
                        'instance_name': instance_name,
                        'created_at': datetime.utcnow().isoformat(),
                        'record_count': len(records)
                    },
                    'records': records
                }, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Saved {len(records)} memory records to {batch_file}")
            
        except Exception as e:
            logger.error(f"Failed to save batch file: {e}")
    
    def migrate_all_capsules(self, 
                           embed_model: str = "text-embedding-3-small:v1.0",
                           embed_dim: int = 384) -> Dict[str, Any]:
        """
        Migrate all capsules in the vault
        
        Args:
            embed_model: Embedding model name and version
            embed_dim: Embedding dimension
            
        Returns:
            Overall migration results
        """
        logger.info("Starting migration of all capsules")
        
        # Find all capsule files
        capsule_files = []
        for root, dirs, files in os.walk(self.capsules_dir):
            for file in files:
                if file.endswith('.capsule'):
                    capsule_files.append(os.path.join(root, file))
        
        if not capsule_files:
            return {
                'success': False,
                'error': 'No capsule files found',
                'capsules_processed': 0,
                'total_records_created': 0,
                'total_records_valid': 0
            }
        
        # Migrate each capsule
        results = []
        total_records_created = 0
        total_records_valid = 0
        
        for capsule_file in capsule_files:
            try:
                result = self.migrate_capsule(capsule_file, embed_model, embed_dim)
                results.append(result)
                
                if result['success']:
                    total_records_created += result['records_created']
                    total_records_valid += result['records_valid']
                
            except Exception as e:
                logger.error(f"Failed to migrate {capsule_file}: {e}")
                results.append({
                    'success': False,
                    'error': str(e),
                    'capsule_path': capsule_file,
                    'records_created': 0,
                    'records_valid': 0
                })
        
        # Generate summary
        successful_migrations = sum(1 for r in results if r['success'])
        
        summary = {
            'success': successful_migrations > 0,
            'capsules_processed': len(capsule_files),
            'successful_migrations': successful_migrations,
            'total_records_created': total_records_created,
            'total_records_valid': total_records_valid,
            'results': results
        }
        
        # Save migration summary
        summary_file = os.path.join(self.indexes_dir, f"migration_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        try:
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            logger.info(f"Migration summary saved to {summary_file}")
        except Exception as e:
            logger.error(f"Failed to save migration summary: {e}")
        
        return summary
    
    def get_migration_status(self) -> Dict[str, Any]:
        """Get current migration status"""
        # Count memory records
        total_records = 0
        instances = []
        
        if os.path.exists(self.memory_records_dir):
            for instance_dir in os.listdir(self.memory_records_dir):
                instance_path = os.path.join(self.memory_records_dir, instance_dir)
                if os.path.isdir(instance_path):
                    record_count = len([f for f in os.listdir(instance_path) if f.endswith('.json')])
                    instances.append({
                        'instance_name': instance_dir,
                        'record_count': record_count
                    })
                    total_records += record_count
        
        # Get Merkle chain status
        merkle_chain = self.schema_gate.get_merkle_chain()
        chain_valid = self.schema_gate.verify_merkle_chain()
        
        return {
            'total_records': total_records,
            'instances': instances,
            'merkle_chain_length': len(merkle_chain),
            'merkle_chain_valid': chain_valid,
            'memory_records_dir': self.memory_records_dir
        }

def main():
    """Main migration function"""
    print("🔄 VVAULT Capsule Migration")
    print("=" * 50)
    
    # Initialize migrator
    migrator = CapsuleMigrator()
    
    # Get migration status
    status = migrator.get_migration_status()
    print(f"Current status:")
    print(f"  Total records: {status['total_records']}")
    print(f"  Instances: {len(status['instances'])}")
    print(f"  Merkle chain: {status['merkle_chain_length']} entries")
    print(f"  Chain valid: {status['merkle_chain_valid']}")
    
    # Run migration
    print(f"\nStarting migration...")
    result = migrator.migrate_all_capsules()
    
    print(f"\nMigration completed:")
    print(f"  Capsules processed: {result['capsules_processed']}")
    print(f"  Successful migrations: {result['successful_migrations']}")
    print(f"  Total records created: {result['total_records_created']}")
    print(f"  Total records valid: {result['total_records_valid']}")
    
    if result['success']:
        print("✅ Migration successful!")
    else:
        print("❌ Migration failed!")

if __name__ == "__main__":
    main()
