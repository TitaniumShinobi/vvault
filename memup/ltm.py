#!/usr/bin/env python

import os
import logging
from datetime import datetime
import json
import re
import shutil
from tqdm import tqdm
from typing import List, Dict, Tuple, Optional, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import time
from .chroma_config import (
    get_core_memory_collection,
    get_terminal_context_collection,
    get_web_interactions_collection,
    get_persona_dialogue_collection
)

# Add the parent directory to Python path to import logger
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logger import setup_logger

# --- Configuration ---
frame_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
LOGS_FOLDER = os.path.join(frame_ROOT, "Vault", "Logs")
os.makedirs(LOGS_FOLDER, exist_ok=True)

logger = setup_logger("memory_long_import", os.path.join(LOGS_FOLDER, "memory_long.log"))

CHAT_FOLDER = os.path.join(frame_ROOT, "Vault", "ChatGPT")
BACKUP_FOLDER = os.path.join(frame_ROOT, "Vault", "backup")
os.makedirs(BACKUP_FOLDER, exist_ok=True)

# Configurable collection mapping
COLLECTION_MAPPING = {
    "terminal": "terminal_context",
    "web": "web_interactions",
    "api": "web_interactions",
    "chat": "persona_dialogue",
    "dialogue": "persona_dialogue"
}

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(frame_ROOT, 'logs', 'memory_import.log')),
        logging.StreamHandler()
    ]
)

# --- UnifiedMemoryBank with Deduplication ---
class UnifiedMemoryBank:
    def __init__(self):
        self.collections = {
            "core_memory": get_core_memory_collection(),
            "terminal_context": get_terminal_context_collection(),
            "web_interactions": get_web_interactions_collection(),
            "persona_dialogue": get_persona_dialogue_collection()
        }
        self.validate_connection()

    def validate_connection(self):
        """Validate database connection and collections"""
        try:
            for name, collection in self.collections.items():
                # Test query to validate connection
                collection.get(limit=1)
            logging.debug("Database connection and collections validated successfully")
        except Exception as e:
            logging.error(f"Database connection validation failed: {str(e)}")
            raise

    def add_memory_with_retry(self, content: str, metadata: Dict, collection: str = "core_memory", retries: int = 3) -> bool:
        """Add memory with retry mechanism"""
        for attempt in range(retries):
            try:
                if self.add_memory(content, metadata, collection):
                    return True
                if attempt < retries - 1:
                    logging.warning(f"Retrying memory addition (attempt {attempt + 1}/{retries})...")
                    time.sleep(1)  # Wait before retrying
            except Exception as e:
                logging.error(f"Attempt {attempt + 1} failed: {str(e)}")
                if attempt < retries - 1:
                    time.sleep(1)
                continue
        logging.error(f"Failed to add memory after {retries} attempts: {content[:50]}")
        return False

    def add_memory(self, content: str, metadata: Dict, collection: str = "core_memory", timestamp: Optional[str] = None) -> bool:
        """Add memory with content-based deduplication"""
        if collection not in self.collections:
            logging.error(f"Collection {collection} not found")
            return False
        if not timestamp:
            timestamp = datetime.now().isoformat()
        try:
            # Generate ID from hash of content + source
            memory_id = f"mem_{hash(content + metadata.get('source', '')) & 0xffffffff}"
            
            # Log the query parameters for debugging
            logging.debug(f"Adding memory to {collection} with ID {memory_id}")
            logging.debug(f"Content preview: {content[:50]}...")
            logging.debug(f"Metadata: {metadata}")
            
            # Check for existing memory with same content in this collection
            existing = self.collections[collection].get(
                where={"source": metadata["source"]},
                where_document={"$contains": content}
            )
            if existing['ids']:
                logging.debug(f"Skipping duplicate in {collection}: {content[:50]}...")
                return False
                
            self.collections[collection].add(
                documents=[content],
                metadatas=[metadata],
                ids=[memory_id]
            )
            logging.info(f"Memory added to {collection}: {content[:50]}...")
            return True
        except Exception as e:
            logging.error(f"Failed to add memory: {str(e)} | Content: {content[:50]} | Metadata: {metadata}")
            return False

memory_bank = UnifiedMemoryBank()

def get_collection_for_file(file_path: str) -> str:
    """Determine collection based on configurable mapping"""
    filename = os.path.basename(file_path).lower()
    for keyword, collection in COLLECTION_MAPPING.items():
        if keyword in filename:
            return collection
    return "core_memory"  # default

def get_all_chat_files() -> List[str]:
    """Scan for chat files following frameOS boundary rules"""
    chat_files = []
    for root, dirs, files in os.walk(CHAT_FOLDER):
        if any(x in root for x in ["Foundations", "backup", "system"]):
            continue
        for file in files:
            if file.endswith(".txt"):
                full_path = os.path.join(root, file)
                try:
                    if os.path.commonpath([frame_ROOT, full_path]) == frame_ROOT:
                        chat_files.append(full_path)
                    else:
                        logging.warning(f"File outside frameRoot: {full_path}")
                except ValueError:
                    logging.warning(f"Path traversal attempt detected: {full_path}")
    return chat_files

def extract_date_from_content(content: str) -> List[Tuple[datetime, str]]:
    """Extract dates with frameOS-compliant logging"""
    dates_found = []
    patterns = [
        (r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]', '%Y-%m-%d %H:%M:%S'),
        (r'frameOS_Log_(\d{8})_', '%Y%m%d'),
        (r'Session_Start: (\d{4}-\d{2}-\d{2})', '%Y-%m-%d'),
        (r'(\d{4}/\d{2}/\d{2})', '%Y/%m/%d'),
        (r'(\d{4}-\d{2}-\d{2})', '%Y-%m-%d'),
        (r'(\d{2}/\d{2}/\d{4})', '%m/%d/%Y'),
        (r'(\d{2}-\d{2}-\d{4})', '%m-%d-%Y'),
        (r'Date: (\d{4}-\d{2}-\d{2})', '%Y-%m-%d'),
        (r'Created: (\d{4}-\d{2}-\d{2})', '%Y-%m-%d'),
        (r'Timestamp: (\d{4}-\d{2}-\d{2})', '%Y-%m-%d')
    ]
    
    for pattern, date_format in patterns:
        matches = re.finditer(pattern, content)
        for match in matches:
            try:
                date_str = match.group(1)
                date = datetime.strptime(date_str, date_format)
                context = content[max(0, match.start()-50):match.end()+50]
                dates_found.append((date, context))
                logging.debug(f"Found date {date} in context: {context[:100]}...")
            except ValueError as e:
                logging.debug(f"Failed to parse date '{date_str}' with format '{date_format}': {str(e)}")
                continue
    
    if not dates_found:
        logging.debug(f"No dates found in content: {content[:200]}...")
    
    return dates_found

def determine_chat_order(chat_files: List[str]) -> List[Tuple[str, datetime, str]]:
    """Order chats chronologically with frameOS compliance checks"""
    chat_dates = []
    for file_path in chat_files:
        path_date = None
        path_parts = os.path.relpath(file_path, CHAT_FOLDER).split(os.sep)
        if len(path_parts) >= 3 and all(x.isdigit() for x in path_parts[:3]):
            year, month, day = map(int, path_parts[:3])
            try:
                path_date = datetime(year, month, day)
                logging.debug(f"Using path date {path_date} for {file_path}")
            except ValueError:
                pass
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        content_dates = extract_date_from_content(content)
        if content_dates:
            best_date = max(dt for dt, _ in content_dates)
            confidence = "high"
        elif path_date:
            best_date = path_date
            confidence = "medium"
        else:
            best_date = datetime.fromtimestamp(os.path.getctime(file_path))
            confidence = "low"
            logging.warning(f"Using fallback date for {file_path}")
        chat_dates.append((file_path, best_date, confidence))
    return sorted(chat_dates, key=lambda x: x[1])

def backup_memories() -> bool:
    """Create frameOS-compliant backup"""
    try:
        if not os.path.exists(CHROMA_PATH):
            return False
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_FOLDER, f"memory_{timestamp}")
        shutil.copytree(
            CHROMA_PATH,
            backup_path,
            ignore=shutil.ignore_patterns('*.lock', '*.tmp')
        )
        manifest = {
            "backup_time": timestamp,
            "source": CHROMA_PATH,
            "destination": backup_path,
            "system": "frameOS Memory Module",
            "signature": f"backup:memory:{timestamp}"
        }
        with open(os.path.join(backup_path, "manifest.json"), 'w') as f:
            json.dump(manifest, f)
        logging.info(f"Created backup at {backup_path}")
        return True
    except Exception as e:
        logging.error(f"Backup failed: {str(e)}")
        return False

def import_chat_file(file_path: str, session_date: datetime, max_workers: int = 8) -> int:
    """Import a single chat file using thread pool for speed"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip()]
    except UnicodeDecodeError:
        logging.error(f"Encoding error in {file_path}, trying fallback encoding")
        try:
            with open(file_path, 'r', encoding='latin-1') as f:
                lines = [line.strip() for line in f if line.strip()]
        except Exception as e:
            logging.error(f"Failed to read {file_path}: {str(e)}")
            return 0
    except Exception as e:
        logging.error(f"Unexpected error reading {file_path}: {str(e)}")
        return 0

    collection = get_collection_for_file(file_path)
    source_name = os.path.basename(file_path)

    def process_line(i: int, line: str) -> bool:
        try:
            metadata = {
                "source": source_name,
                "timestamp": session_date.isoformat(),
                "line_number": i + 1,
                "module": memory_bank.collections[collection].metadata["module"],
                "command": f"import:memory:{collection}"
            }
            return memory_bank.add_memory_with_retry(line, metadata, collection)
        except Exception as e:
            logging.error(f"Failed to import line {i+1} from {file_path}: {str(e)}")
            return False

    imported_count = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_line, i, line) for i, line in enumerate(lines)]
        for future in as_completed(futures):
            if future.result():
                imported_count += 1

    return imported_count

def import_all_chats(force_rescan: bool = False) -> Dict[str, Union[int, str]]:
    """Main import function following frameOS protocols"""
    if force_rescan:
        if not backup_memories():
            logging.critical("Aborting import - backup failed")
            return {"status": "backup_failed"}
        for collection in memory_bank.collections.values():
            collection.delete(where={})
    chat_files = get_all_chat_files()
    ordered_chats = determine_chat_order(chat_files)
    results: Dict[str, Union[int, str]] = {
        "total_files": len(ordered_chats),
        "imported": 0,
        "status": "in_progress"
    }
    for file_path, session_date, confidence in tqdm(ordered_chats, desc="frameOS Memory Import"):
        try:
            imported = import_chat_file(file_path, session_date)
            # Type assertion to ensure imported is treated as int
            assert isinstance(results["imported"], int)
            results["imported"] += imported
            logging.info(f"Imported {imported} lines from {os.path.basename(file_path)}")
        except Exception as e:
            logging.error(f"Failed to import {file_path}: {str(e)}")
            continue
    memory_bank.client.persist()
    results["status"] = "completed"
    logging.info(f"Import completed. Total memories: {results['imported']}")
    return results

def verify_import(chat_files: List[str]) -> Dict[str, int]:
    """Verify imported memories exist in ChromaDB"""
    verification = {"total_files": len(chat_files), "verified": 0}
    for file_path in chat_files:
        collection = get_collection_for_file(file_path)
        source_name = os.path.basename(file_path)
        try:
            results = memory_bank.collections[collection].get(
                where={"source": source_name}
            )
            if results['ids']:
                verification["verified"] += 1
        except Exception as e:
            logging.error(f"Verification failed for {source_name}: {str(e)}")
    return verification

if __name__ == "__main__":
    logging.info("Executing command: run:memory_import:full")
    results = import_all_chats(force_rescan=True)
    chat_files = get_all_chat_files()
    verification = verify_import(chat_files)
    results["verified_files"] = verification["verified"]
    report_path = os.path.join(frame_ROOT, "logs", "memory_import_report.json")
    with open(report_path, 'w') as f:
        json.dump(results, f)
    logging.info(f"Report saved to {report_path}")
    print(f"Operation completed. Status: {results['status']}. Verified files: {results['verified_files']}")