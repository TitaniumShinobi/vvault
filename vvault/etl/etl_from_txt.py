#!/usr/bin/env python3
"""
ETL from TXT - Parse ChatGPT Exports into Memory Records
Converts ChatGPT conversation exports to validated memory records with embeddings
"""

import json
import os
import re
import hashlib
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

# Import schema gate
from vvault.schema_gate import SchemaGate

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ChatGPTParser:
    """Parse ChatGPT conversation exports"""
    
    def __init__(self):
        # Patterns for different ChatGPT export formats
        self.patterns = {
            'user_assistant': re.compile(r'^(User|Assistant):\s*(.+)$', re.MULTILINE),
            'timestamped': re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (User|Assistant):\s*(.+)$', re.MULTILINE),
            'numbered': re.compile(r'^(\d+)\.\s*(User|Assistant):\s*(.+)$', re.MULTILINE),
            'markdown': re.compile(r'^### (User|Assistant)\s*\n(.+?)(?=\n###|\Z)', re.MULTILINE | re.DOTALL)
        }
    
    def detect_format(self, content: str) -> str:
        """Detect the format of the ChatGPT export"""
        for format_name, pattern in self.patterns.items():
            if pattern.search(content):
                return format_name
        return 'unknown'
    
    def parse_conversation(self, content: str) -> List[Dict[str, Any]]:
        """Parse conversation content into structured messages"""
        format_type = self.detect_format(content)
        logger.info(f"Detected format: {format_type}")
        
        messages = []
        
        if format_type == 'user_assistant':
            messages = self._parse_user_assistant(content)
        elif format_type == 'timestamped':
            messages = self._parse_timestamped(content)
        elif format_type == 'numbered':
            messages = self._parse_numbered(content)
        elif format_type == 'markdown':
            messages = self._parse_markdown(content)
        else:
            # Fallback: treat as plain text
            messages = self._parse_plain_text(content)
        
        logger.info(f"Parsed {len(messages)} messages")
        return messages
    
    def _parse_user_assistant(self, content: str) -> List[Dict[str, Any]]:
        """Parse User/Assistant format"""
        messages = []
        lines = content.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            match = self.patterns['user_assistant'].match(line)
            if match:
                role, text = match.groups()
                messages.append({
                    'role': role.lower(),
                    'content': text.strip(),
                    'timestamp': None,
                    'index': len(messages)
                })
        
        return messages
    
    def _parse_timestamped(self, content: str) -> List[Dict[str, Any]]:
        """Parse timestamped format"""
        messages = []
        
        for match in self.patterns['timestamped'].finditer(content):
            timestamp, role, text = match.groups()
            messages.append({
                'role': role.lower(),
                'content': text.strip(),
                'timestamp': timestamp,
                'index': len(messages)
            })
        
        return messages
    
    def _parse_numbered(self, content: str) -> List[Dict[str, Any]]:
        """Parse numbered format"""
        messages = []
        
        for match in self.patterns['numbered'].finditer(content):
            number, role, text = match.groups()
            messages.append({
                'role': role.lower(),
                'content': text.strip(),
                'timestamp': None,
                'index': int(number) - 1
            })
        
        return messages
    
    def _parse_markdown(self, content: str) -> List[Dict[str, Any]]:
        """Parse markdown format"""
        messages = []
        
        for match in self.patterns['markdown'].finditer(content):
            role, text = match.groups()
            messages.append({
                'role': role.lower(),
                'content': text.strip(),
                'timestamp': None,
                'index': len(messages)
            })
        
        return messages
    
    def _parse_plain_text(self, content: str) -> List[Dict[str, Any]]:
        """Parse plain text as fallback"""
        messages = []
        paragraphs = content.split('\n\n')
        
        for i, paragraph in enumerate(paragraphs):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            
            # Try to detect role from content
            role = 'assistant'  # Default to assistant
            if paragraph.lower().startswith(('user:', 'me:', 'i:', 'human:')):
                role = 'user'
                # Remove role prefix
                content_text = re.sub(r'^(user|me|i|human):\s*', '', paragraph, flags=re.IGNORECASE)
            else:
                content_text = paragraph
            
            messages.append({
                'role': role,
                'content': content_text.strip(),
                'timestamp': None,
                'index': i
            })
        
        return messages

class ETLPipeline:
    """ETL pipeline for converting ChatGPT exports to memory records"""
    
    def __init__(self, vault_path: str = None):
        self.vault_path = vault_path or os.path.dirname(os.path.abspath(__file__))
        self.memory_records_dir = os.path.join(self.vault_path, "memory_records")
        self.etl_logs_dir = os.path.join(self.vault_path, "etl_logs")
        
        # Ensure directories exist
        os.makedirs(self.memory_records_dir, exist_ok=True)
        os.makedirs(self.etl_logs_dir, exist_ok=True)
        
        # Initialize components
        self.parser = ChatGPTParser()
        self.schema_gate = SchemaGate()
        
        logger.info(f"Initialized ETL pipeline with vault path: {self.vault_path}")
    
    def process_chatgpt_export(self, 
                              file_path: str,
                              instance_name: str,
                              embed_model: str = "text-embedding-3-small:v1.0",
                              embed_dim: int = 384,
                              min_content_length: int = 10) -> Dict[str, Any]:
        """
        Process a ChatGPT export file
        
        Args:
            file_path: Path to ChatGPT export file
            instance_name: Name of the AI instance
            embed_model: Embedding model name and version
            embed_dim: Embedding dimension
            min_content_length: Minimum content length to process
            
        Returns:
            Processing result with statistics
        """
        logger.info(f"Processing ChatGPT export: {file_path}")
        
        try:
            # Read file content
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Parse conversation
            messages = self.parser.parse_conversation(content)
            
            if not messages:
                return {
                    'success': False,
                    'error': 'No messages found in file',
                    'file_path': file_path,
                    'messages_parsed': 0,
                    'records_created': 0,
                    'records_valid': 0
                }
            
            # Create memory records
            records_created = 0
            records_valid = 0
            valid_records = []
            
            for message in messages:
                content_text = message['content']
                
                # Skip short messages
                if len(content_text) < min_content_length:
                    continue
                
                try:
                    # Generate embedding (mock for now)
                    embedding = self._get_mock_embedding(content_text, embed_dim)
                    
                    # Create memory record
                    record = self._create_memory_record(
                        content_text, message, instance_name, embedding, embed_model
                    )
                    records_created += 1
                    
                    # Validate record
                    validation_result = self.schema_gate.validate_memory_record(record)
                    if validation_result.valid:
                        records_valid += 1
                        valid_records.append(record)
                    else:
                        logger.warning(f"Invalid record created: {validation_result.errors}")
                    
                except Exception as e:
                    logger.error(f"Failed to create record for message: {e}")
            
            # Save valid records
            if valid_records:
                self._save_memory_records(valid_records, instance_name, file_path)
            
            # Log processing
            self._log_processing(file_path, instance_name, len(messages), records_created, records_valid)
            
            return {
                'success': True,
                'file_path': file_path,
                'instance_name': instance_name,
                'messages_parsed': len(messages),
                'records_created': records_created,
                'records_valid': records_valid
            }
            
        except Exception as e:
            logger.error(f"Failed to process file {file_path}: {e}")
            return {
                'success': False,
                'error': str(e),
                'file_path': file_path,
                'messages_parsed': 0,
                'records_created': 0,
                'records_valid': 0
            }
    
    def _get_mock_embedding(self, content: str, embed_dim: int) -> List[float]:
        """Generate mock embedding (replace with real embedding model)"""
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
    
    def _create_memory_record(self, 
                            content: str,
                            message: Dict[str, Any],
                            instance_name: str,
                            embedding: List[float],
                            embed_model: str) -> Dict[str, Any]:
        """Create a memory record from message"""
        
        # Generate memory ID
        role = message['role']
        index = message['index']
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:8]
        
        memory_id = f"{instance_name.lower()}_{role}_{index}_{content_hash}"
        
        # Generate source ID
        source_id = f"chatgpt_export_{instance_name.lower()}_{role}"
        
        # Create tags
        tags = [
            role,
            instance_name,
            'chatgpt_export',
            'conversation'
        ]
        
        if message.get('timestamp'):
            tags.append('timestamped')
        
        # Create memory record using schema gate
        record = self.schema_gate.create_memory_record(
            memory_id=memory_id,
            source_id=source_id,
            raw_content=content,
            embedding=embedding,
            embed_model=embed_model,
            consent="self",
            tags=tags,
            preprocessed=content  # No preprocessing for now
        )
        
        # Add message metadata
        record['message_metadata'] = {
            'role': role,
            'index': index,
            'timestamp': message.get('timestamp'),
            'source_file': 'chatgpt_export'
        }
        
        return record
    
    def _save_memory_records(self, records: List[Dict[str, Any]], instance_name: str, source_file: str) -> None:
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
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        source_name = os.path.splitext(os.path.basename(source_file))[0]
        batch_file = os.path.join(instance_dir, f"{instance_name}_{source_name}_{timestamp}.json")
        
        try:
            with open(batch_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'metadata': {
                        'instance_name': instance_name,
                        'source_file': source_file,
                        'created_at': datetime.utcnow().isoformat(),
                        'record_count': len(records)
                    },
                    'records': records
                }, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Saved {len(records)} memory records to {batch_file}")
            
        except Exception as e:
            logger.error(f"Failed to save batch file: {e}")
    
    def _log_processing(self, file_path: str, instance_name: str, messages_parsed: int, 
                       records_created: int, records_valid: int) -> None:
        """Log processing results"""
        log_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'file_path': file_path,
            'instance_name': instance_name,
            'messages_parsed': messages_parsed,
            'records_created': records_created,
            'records_valid': records_valid,
            'success_rate': records_valid / records_created if records_created > 0 else 0
        }
        
        log_file = os.path.join(self.etl_logs_dir, f"etl_log_{datetime.now().strftime('%Y%m%d')}.jsonl")
        
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry) + '\n')
        except Exception as e:
            logger.error(f"Failed to write log entry: {e}")
    
    def process_directory(self, 
                         directory_path: str,
                         instance_name: str,
                         file_pattern: str = "*.txt",
                         embed_model: str = "text-embedding-3-small:v1.0",
                         embed_dim: int = 384) -> Dict[str, Any]:
        """
        Process all ChatGPT export files in a directory
        
        Args:
            directory_path: Directory containing export files
            instance_name: Name of the AI instance
            file_pattern: File pattern to match
            embed_model: Embedding model name and version
            embed_dim: Embedding dimension
            
        Returns:
            Overall processing results
        """
        logger.info(f"Processing directory: {directory_path}")
        
        # Find matching files
        import glob
        file_pattern_path = os.path.join(directory_path, file_pattern)
        files = glob.glob(file_pattern_path)
        
        if not files:
            return {
                'success': False,
                'error': f'No files matching pattern {file_pattern} found',
                'files_processed': 0,
                'total_messages_parsed': 0,
                'total_records_created': 0,
                'total_records_valid': 0
            }
        
        # Process each file
        results = []
        total_messages_parsed = 0
        total_records_created = 0
        total_records_valid = 0
        
        for file_path in files:
            try:
                result = self.process_chatgpt_export(
                    file_path, instance_name, embed_model, embed_dim
                )
                results.append(result)
                
                if result['success']:
                    total_messages_parsed += result['messages_parsed']
                    total_records_created += result['records_created']
                    total_records_valid += result['records_valid']
                
            except Exception as e:
                logger.error(f"Failed to process {file_path}: {e}")
                results.append({
                    'success': False,
                    'error': str(e),
                    'file_path': file_path,
                    'messages_parsed': 0,
                    'records_created': 0,
                    'records_valid': 0
                })
        
        # Generate summary
        successful_files = sum(1 for r in results if r['success'])
        
        summary = {
            'success': successful_files > 0,
            'files_processed': len(files),
            'successful_files': successful_files,
            'total_messages_parsed': total_messages_parsed,
            'total_records_created': total_records_created,
            'total_records_valid': total_records_valid,
            'results': results
        }
        
        # Save processing summary
        summary_file = os.path.join(self.etl_logs_dir, f"processing_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        try:
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            logger.info(f"Processing summary saved to {summary_file}")
        except Exception as e:
            logger.error(f"Failed to save processing summary: {e}")
        
        return summary

def main():
    """Main ETL function"""
    print("🔄 VVAULT ETL Pipeline")
    print("=" * 50)
    
    # Initialize ETL pipeline
    etl = ETLPipeline()
    
    # Example usage
    print("Example usage:")
    print("1. Process single file:")
    print("   etl.process_chatgpt_export('chat_export.txt', 'nova')")
    print("")
    print("2. Process directory:")
    print("   etl.process_directory('/path/to/exports', 'nova')")
    print("")
    print("3. Process with custom settings:")
    print("   etl.process_chatgpt_export('chat.txt', 'nova', 'text-embedding-3-large:v1.0', 1536)")

if __name__ == "__main__":
    main()
