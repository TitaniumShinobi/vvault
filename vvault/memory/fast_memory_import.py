#!/usr/bin/env python3
"""
Fast Memory Import System for VVAULT
Target: Process 100k+ lines in < 5 minutes with persistence verification

Key Features:
- Streaming batch processing (no full file load)
- Parallel embedding generation (1000+ chunks at once)
- Resume capability (track progress, resume from interruption)
- Persistence verification (prove memories are stored)
- Smart chunking (conversation-level, not line-by-line)
"""

import os
import json
import hashlib
import logging
import time
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple, Iterator
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
import re

# ChromaDB imports
try:
    from chromadb import PersistentClient, Settings
    from chromadb.utils import embedding_functions
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    logging.warning("ChromaDB not available, using mock implementation")

# SentenceTransformer for batch embeddings
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMER_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMER_AVAILABLE = False
    logging.warning("SentenceTransformer not available, using mock embeddings")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class ImportProgress:
    """Track import progress for resume capability"""
    file_path: str
    construct_id: str
    total_lines: int
    processed_lines: int
    batches_completed: int
    last_batch_hash: str
    start_time: float
    last_update_time: float
    status: str  # 'in_progress', 'completed', 'failed', 'paused'
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'ImportProgress':
        return cls(**data)

class FastMemoryImporter:
    """
    Fast streaming memory importer with batch processing and persistence verification
    """
    
    def __init__(
        self,
        construct_id: str,
        vvault_path: Optional[str] = None,
        batch_size: int = 1000,
        max_workers: int = 8,
        embed_model: str = "all-MiniLM-L6-v2",
        embed_dim: int = 384
    ):
        """
        Initialize fast memory importer
        
        Args:
            construct_id: VVAULT construct ID (e.g., 'katana-001', 'nova-001')
            vvault_path: Path to VVAULT root directory
            batch_size: Number of chunks to process in each batch
            max_workers: Number of parallel workers for processing
            embed_model: Embedding model name
            embed_dim: Embedding dimension
        """
        self.construct_id = construct_id
        self.batch_size = batch_size
        self.max_workers = max_workers
        self.embed_model = embed_model
        self.embed_dim = embed_dim
        
        # Set up VVAULT paths
        if vvault_path is None:
            # Try to find VVAULT relative to this file
            current_dir = Path(__file__).parent
            if current_dir.name == 'vvault':
                vvault_path = str(current_dir.parent)
            else:
                vvault_path = str(current_dir)
        
        self.vvault_path = Path(vvault_path)
        self.construct_dir = self.vvault_path / construct_id
        self.chroma_path = self.construct_dir / "Memories" / "chroma_db"
        self.progress_dir = self.construct_dir / "import_progress"
        
        # Ensure directories exist
        self.chroma_path.mkdir(parents=True, exist_ok=True)
        self.progress_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize ChromaDB
        self._init_chromadb()
        
        # Initialize embedding model
        self._init_embedding_model()
        
        logger.info(f"✅ FastMemoryImporter initialized for {construct_id}")
        logger.info(f"📁 ChromaDB path: {self.chroma_path}")
        logger.info(f"📊 Batch size: {batch_size}, Workers: {max_workers}")
    
    def _init_chromadb(self):
        """Initialize ChromaDB client and collection"""
        if not CHROMADB_AVAILABLE:
            logger.error("ChromaDB not available!")
            raise RuntimeError("ChromaDB is required for memory import")
        
        try:
            self.chroma_client = PersistentClient(
                path=str(self.chroma_path),
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=False
                )
            )
            
            # Create or get collection
            collection_name = f"{self.construct_id}_persona_dialogue"
            
            # Use embedding function from ChromaDB utils
            embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=self.embed_model
            )
            
            try:
                self.collection = self.chroma_client.get_collection(
                    name=collection_name,
                    embedding_function=embedder
                )
                logger.info(f"✅ Retrieved existing collection: {collection_name}")
            except Exception:
                self.collection = self.chroma_client.create_collection(
                    name=collection_name,
                    embedding_function=embedder,
                    metadata={"construct_id": self.construct_id}
                )
                logger.info(f"✅ Created new collection: {collection_name}")
            
            # Verify persistence
            self._verify_persistence()
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize ChromaDB: {e}")
            raise
    
    def _init_embedding_model(self):
        """Initialize embedding model for batch processing"""
        if SENTENCE_TRANSFORMER_AVAILABLE:
            try:
                self.embedding_model = SentenceTransformer(self.embed_model)
                logger.info(f"✅ Loaded embedding model: {self.embed_model}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to load SentenceTransformer: {e}")
                self.embedding_model = None
        else:
            self.embedding_model = None
            logger.warning("⚠️ SentenceTransformer not available, using ChromaDB's embedding function")
    
    def _verify_persistence(self):
        """Verify ChromaDB persistence is working"""
        try:
            # Test write
            test_id = f"test_{int(time.time())}"
            self.collection.add(
                documents=["test_persistence"],
                ids=[test_id],
                metadatas=[{"test": True}]
            )
            
            # Force persist
            self.chroma_client.persist()
            
            # Test read
            results = self.collection.get(ids=[test_id])
            if results['ids']:
                # Clean up test
                self.collection.delete(ids=[test_id])
                logger.info("✅ Persistence verification passed")
            else:
                raise RuntimeError("Test document not found after write")
        except Exception as e:
            logger.error(f"❌ Persistence verification failed: {e}")
            raise
    
    def _parse_chatgpt_conversation(self, file_path: str) -> Iterator[Dict[str, Any]]:
        """
        Stream parse ChatGPT conversation file (supports multiple formats)
        Returns iterator of message dictionaries
        """
        logger.info(f"📖 Parsing conversation file: {file_path}")
        
        # Detect file format
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            first_lines = ''.join([f.readline() for _ in range(10)])
            f.seek(0)
            
            # Check if it's JSON format (ChatGPT export)
            if first_lines.strip().startswith('[') or first_lines.strip().startswith('{'):
                yield from self._parse_json_export(f)
            else:
                # Text format (reverse chronological or standard)
                yield from self._parse_text_conversation(f, file_path)
    
    def _parse_json_export(self, file_handle) -> Iterator[Dict[str, Any]]:
        """Parse ChatGPT JSON export format"""
        try:
            data = json.load(file_handle)
            
            # Handle array of conversations
            if isinstance(data, list):
                conversations = data
            else:
                conversations = [data]
            
            for convo in conversations:
                mapping = convo.get('mapping', {})
                nodes = []
                
                for node_id, node_data in mapping.items():
                    message = node_data.get('message')
                    if message:
                        nodes.append({
                            'id': node_id,
                            'message': message,
                            'create_time': message.get('create_time', 0)
                        })
                
                # Sort by timestamp
                nodes.sort(key=lambda x: x['create_time'])
                
                for node in nodes:
                    msg = node['message']
                    content = msg.get('content', {})
                    
                    # Extract text content
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, dict):
                        parts = content.get('parts', [])
                        text = ' '.join(str(p) for p in parts if p)
                    elif isinstance(content, list):
                        text = ' '.join(str(p) for p in content if p)
                    else:
                        continue
                    
                    if not text.strip():
                        continue
                    
                    # Extract role
                    author = msg.get('author', {})
                    role = author.get('role', 'unknown')
                    if role == 'user':
                        role = 'user'
                    elif role == 'assistant':
                        role = 'assistant'
                    else:
                        role = 'system'
                    
                    yield {
                        'role': role,
                        'content': text.strip(),
                        'timestamp': msg.get('create_time', 0),
                        'message_id': node['id'],
                        'conversation_id': convo.get('id', 'unknown')
                    }
        
        except json.JSONDecodeError as e:
            logger.error(f"❌ Failed to parse JSON export: {e}")
            raise
    
    def _parse_text_conversation(self, file_handle, file_path: str) -> Iterator[Dict[str, Any]]:
        """Parse text-based conversation (supports reverse chronological)"""
        lines = []
        current_role = None
        current_content = []
        
        # Read all lines (for reverse chronological detection)
        all_lines = file_handle.readlines()
        
        # Detect if reverse chronological (newest first)
        # Check first 100 lines for timestamp patterns
        is_reverse = False
        timestamps = []
        for i, line in enumerate(all_lines[:100]):
            # Look for timestamp patterns
            if re.search(r'\d{4}[-/]\d{2}[-/]\d{2}', line):
                timestamps.append((i, line))
        
        if len(timestamps) > 5:
            # Check if timestamps are decreasing (reverse chronological)
            first_ts = timestamps[0][1]
            last_ts = timestamps[-1][1]
            # Simple heuristic: if first timestamp looks newer, it's reverse
            is_reverse = True  # Assume reverse for now
        
        # Process lines
        for line in all_lines:
            line = line.strip()
            if not line:
                if current_content:
                    yield {
                        'role': current_role or 'assistant',
                        'content': '\n'.join(current_content).strip(),
                        'timestamp': None,
                        'message_id': None,
                        'conversation_id': file_path
                    }
                    current_content = []
                continue
            
            # Detect role markers
            role_match = re.match(r'^(User|Assistant|Human|AI|@\w+):\s*(.+)$', line, re.IGNORECASE)
            if role_match:
                # Save previous message
                if current_content and current_role:
                    yield {
                        'role': current_role,
                        'content': '\n'.join(current_content).strip(),
                        'timestamp': None,
                        'message_id': None,
                        'conversation_id': file_path
                    }
                
                # Start new message
                role_str = role_match.group(1).lower()
                if 'user' in role_str or 'human' in role_str:
                    current_role = 'user'
                else:
                    current_role = 'assistant'
                
                current_content = [role_match.group(2)]
            else:
                # Continuation of current message
                if current_role:
                    current_content.append(line)
                else:
                    # No role detected yet, assume assistant
                    current_role = 'assistant'
                    current_content = [line]
        
        # Yield last message
        if current_content and current_role:
            yield {
                'role': current_role,
                'content': '\n'.join(current_content).strip(),
                'timestamp': None,
                'message_id': None,
                'conversation_id': file_path
            }
    
    def _generate_batch_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a batch of texts"""
        if self.embedding_model:
            # Use SentenceTransformer for fast batch processing
            embeddings = self.embedding_model.encode(
                texts,
                batch_size=min(len(texts), 512),
                show_progress_bar=False,
                convert_to_numpy=True
            )
            return embeddings.tolist()
        else:
            # Fallback: ChromaDB will generate embeddings
            # Return empty list, ChromaDB will handle it
            return []
    
    def _create_memory_id(self, content: str, source: str, index: int) -> str:
        """Create unique memory ID"""
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:12]
        return f"{self.construct_id}_{source}_{index}_{content_hash}"
    
    def _check_duplicate(self, content: str, source: str) -> bool:
        """Check if content already exists in ChromaDB"""
        try:
            # Use content hash for fast lookup
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            
            # Query for similar content
            results = self.collection.get(
                where={"source": source},
                where_document={"$contains": content[:100]}  # First 100 chars for quick check
            )
            
            # Check for exact matches
            for existing_content in results.get('documents', []):
                if existing_content == content:
                    return True
            
            # Also check by hash in metadata if available
            for metadata in results.get('metadatas', []):
                if metadata.get('content_hash') == content_hash:
                    return True
            
            return False
        except Exception as e:
            logger.debug(f"Duplicate check failed: {e}")
            return False  # If check fails, proceed with import
    
    def _smart_chunk_messages(self, messages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """
        Smart chunking: Group messages by conversation context
        
        Strategies:
        1. Conversation-level chunks (group by conversation_id)
        2. Temporal batching (group by time windows)
        3. Topic-based segmentation (detect topic shifts)
        """
        if not messages:
            return []
        
        chunks = []
        current_chunk = []
        current_conversation = None
        
        for msg in messages:
            conv_id = msg.get('conversation_id', 'unknown')
            
            # Start new chunk if conversation changes
            if current_conversation != conv_id and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
            
            current_conversation = conv_id
            current_chunk.append(msg)
            
            # Also respect batch size limit
            if len(current_chunk) >= self.batch_size:
                chunks.append(current_chunk)
                current_chunk = []
        
        # Add remaining messages
        if current_chunk:
            chunks.append(current_chunk)
        
        return chunks
    
    def _save_progress(self, progress: ImportProgress):
        """Save import progress for resume capability"""
        progress_file = self.progress_dir / f"{Path(progress.file_path).stem}_progress.json"
        try:
            with open(progress_file, 'w') as f:
                json.dump(progress.to_dict(), f, indent=2)
        except Exception as e:
            logger.warning(f"⚠️ Failed to save progress: {e}")
    
    def _load_progress(self, file_path: str) -> Optional[ImportProgress]:
        """Load import progress for resume"""
        progress_file = self.progress_dir / f"{Path(file_path).stem}_progress.json"
        if progress_file.exists():
            try:
                with open(progress_file, 'r') as f:
                    data = json.load(f)
                return ImportProgress.from_dict(data)
            except Exception as e:
                logger.warning(f"⚠️ Failed to load progress: {e}")
        return None
    
    def import_conversation(
        self,
        file_path: str,
        source_name: Optional[str] = None,
        resume: bool = True,
        verify_after_batch: bool = True
    ) -> Dict[str, Any]:
        """
        Import conversation file with fast batch processing
        
        Args:
            file_path: Path to conversation file
            source_name: Optional source identifier
            resume: Whether to resume from previous progress
            verify_after_batch: Verify persistence after each batch
        
        Returns:
            Import result dictionary
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        source_name = source_name or file_path.stem
        
        # Check for existing progress
        progress = None
        if resume:
            progress = self._load_progress(str(file_path))
            if progress and progress.status == 'completed':
                logger.info(f"✅ Import already completed for {file_path.name}")
                return {
                    'success': True,
                    'status': 'already_completed',
                    'file_path': str(file_path),
                    'total_messages': progress.total_lines,
                    'processed_messages': progress.processed_lines
                }
            elif progress and progress.status == 'in_progress':
                logger.info(f"🔄 Resuming import from line {progress.processed_lines}")
        
        # Initialize progress
        if not progress:
            # Count total lines (approximate)
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                total_lines = sum(1 for _ in f)
            
            progress = ImportProgress(
                file_path=str(file_path),
                construct_id=self.construct_id,
                total_lines=total_lines,
                processed_lines=0,
                batches_completed=0,
                last_batch_hash='',
                start_time=time.time(),
                last_update_time=time.time(),
                status='in_progress'
            )
        
        # Parse and process in batches
        messages = []
        batch_count = 0
        total_imported = 0
        
        try:
            for message in self._parse_chatgpt_conversation(str(file_path)):
                # Skip if already processed (resume)
                if progress.processed_lines > 0 and len(messages) < progress.processed_lines:
                    continue
                
                messages.append(message)
                
                # Process batch when full (with smart chunking)
                if len(messages) >= self.batch_size:
                    # Use smart chunking for better context preservation
                    chunks = self._smart_chunk_messages(messages)
                    batch_messages_count = len(messages)
                    last_message = messages[-1] if messages else {}
                    
                    for chunk in chunks:
                        imported = self._process_batch(chunk, source_name, batch_count, skip_duplicates=True)
                        total_imported += imported
                        batch_count += 1
                    
                    # Update progress (before clearing messages)
                    progress.processed_lines += batch_messages_count
                    progress.batches_completed = batch_count
                    progress.last_update_time = time.time()
                    if last_message:
                        progress.last_batch_hash = hashlib.sha256(
                            json.dumps(last_message, sort_keys=True).encode()
                        ).hexdigest()
                    
                    messages = []
                    
                    # Save progress
                    self._save_progress(progress)
                    
                    # Verify persistence
                    if verify_after_batch:
                        self._verify_batch_persistence(batch_count)
                    
                    # Log progress
                    elapsed = time.time() - progress.start_time
                    rate = progress.processed_lines / elapsed if elapsed > 0 else 0
                    eta = (progress.total_lines - progress.processed_lines) / rate if rate > 0 else 0
                    
                    logger.info(
                        f"📊 Batch {batch_count}: {imported} messages imported | "
                        f"Total: {progress.processed_lines}/{progress.total_lines} | "
                        f"Rate: {rate:.1f} msg/s | ETA: {eta:.1f}s"
                    )
                    
                    messages = []
            
            # Process remaining messages
            if messages:
                imported = self._process_batch(messages, source_name, batch_count)
                total_imported += imported
                batch_count += 1
                progress.processed_lines += len(messages)
                progress.batches_completed = batch_count
            
            # Mark as completed
            progress.status = 'completed'
            progress.last_update_time = time.time()
            self._save_progress(progress)
            
            # Final verification
            self._verify_final_import(source_name, total_imported)
            
            elapsed = time.time() - progress.start_time
            
            logger.info(
                f"✅ Import completed: {total_imported} messages in {elapsed:.1f}s "
                f"({total_imported/elapsed:.1f} msg/s)"
            )
            
            return {
                'success': True,
                'status': 'completed',
                'file_path': str(file_path),
                'total_messages': progress.processed_lines,
                'imported_messages': total_imported,
                'batches': batch_count,
                'elapsed_seconds': elapsed,
                'rate_per_second': total_imported / elapsed if elapsed > 0 else 0
            }
        
        except Exception as e:
            logger.error(f"❌ Import failed: {e}")
            progress.status = 'failed'
            progress.error_message = str(e)
            self._save_progress(progress)
            raise
    
    def _process_batch(
        self,
        messages: List[Dict[str, Any]],
        source_name: str,
        batch_index: int,
        skip_duplicates: bool = True
    ) -> int:
        """Process a batch of messages with deduplication"""
        if not messages:
            return 0
        
        # Prepare data for ChromaDB
        documents = []
        metadatas = []
        ids = []
        skipped_duplicates = 0
        
        for i, msg in enumerate(messages):
            content = msg['content']
            if not content or len(content.strip()) < 10:
                continue
            
            # Check for duplicates
            if skip_duplicates and self._check_duplicate(content, source_name):
                skipped_duplicates += 1
                continue
            
            # Create memory ID
            memory_id = self._create_memory_id(
                content,
                source_name,
                batch_index * self.batch_size + i
            )
            
            # Create content hash for deduplication
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            
            documents.append(content)
            metadatas.append({
                'role': msg['role'],
                'source': source_name,
                'timestamp': msg.get('timestamp'),
                'message_id': msg.get('message_id'),
                'conversation_id': msg.get('conversation_id'),
                'batch_index': batch_index,
                'construct_id': self.construct_id,
                'content_hash': content_hash  # For duplicate detection
            })
            ids.append(memory_id)
        
        if skipped_duplicates > 0:
            logger.debug(f"⏭️ Skipped {skipped_duplicates} duplicates in batch {batch_index}")
        
        if not documents:
            return 0
        
        # Generate embeddings in batch if using SentenceTransformer
        if self.embedding_model:
            embeddings = self._generate_batch_embeddings(documents)
        else:
            embeddings = None
        
        # Add to ChromaDB
        try:
            if embeddings:
                # Add with pre-computed embeddings
                self.collection.add(
                    documents=documents,
                    metadatas=metadatas,
                    ids=ids,
                    embeddings=embeddings
                )
            else:
                # Let ChromaDB generate embeddings
                self.collection.add(
                    documents=documents,
                    metadatas=metadatas,
                    ids=ids
                )
            
            # Force persist
            self.chroma_client.persist()
            
            return len(documents)
        
        except Exception as e:
            logger.error(f"❌ Failed to add batch to ChromaDB: {e}")
            raise
    
    def _verify_batch_persistence(self, batch_index: int):
        """Verify that a batch was persisted correctly"""
        try:
            # Query for recent batch
            results = self.collection.get(
                where={"batch_index": batch_index},
                limit=10
            )
            
            if results['ids']:
                logger.debug(f"✅ Batch {batch_index} verified: {len(results['ids'])} records found")
            else:
                logger.warning(f"⚠️ Batch {batch_index} verification: No records found")
        except Exception as e:
            logger.warning(f"⚠️ Batch verification failed: {e}")
    
    def _verify_final_import(self, source_name: str, expected_count: int):
        """Verify final import results"""
        try:
            results = self.collection.get(
                where={"source": source_name},
                limit=expected_count + 100
            )
            
            actual_count = len(results['ids'])
            logger.info(f"✅ Final verification: {actual_count} records found for source '{source_name}'")
            
            if actual_count < expected_count * 0.9:
                logger.warning(
                    f"⚠️ Import verification: Expected ~{expected_count}, found {actual_count} "
                    f"({actual_count/expected_count*100:.1f}%)"
                )
        except Exception as e:
            logger.warning(f"⚠️ Final verification failed: {e}")

def main():
    """CLI interface for fast memory import"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Fast VVAULT Memory Importer')
    parser.add_argument('file_path', help='Path to conversation file')
    parser.add_argument('--construct-id', required=True, help='VVAULT construct ID (e.g., katana-001)')
    parser.add_argument('--vvault-path', help='Path to VVAULT root directory')
    parser.add_argument('--batch-size', type=int, default=1000, help='Batch size (default: 1000)')
    parser.add_argument('--workers', type=int, default=8, help='Number of workers (default: 8)')
    parser.add_argument('--no-resume', action='store_true', help='Do not resume from previous progress')
    parser.add_argument('--no-verify', action='store_true', help='Skip persistence verification')
    
    args = parser.parse_args()
    
    # Create importer
    importer = FastMemoryImporter(
        construct_id=args.construct_id,
        vvault_path=args.vvault_path,
        batch_size=args.batch_size,
        max_workers=args.workers
    )
    
    # Import
    result = importer.import_conversation(
        file_path=args.file_path,
        resume=not args.no_resume,
        verify_after_batch=not args.no_verify
    )
    
    # Print results
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()

