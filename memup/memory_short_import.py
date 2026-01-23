#!/usr/bin/env python

import os
import json
from datetime import datetime
import shutil
from typing import Dict, Optional
import sys
from .chroma_config import get_short_term_collection

# Add the Terminal directory to Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from logger import setup_logger

# --- Paths ---
frame_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
CHATGPT_EXPORT = os.path.join(frame_ROOT, "Vault", "ChatGPT", "Initial Data Export")
CONVERSATIONS_PATH = os.path.join(CHATGPT_EXPORT, "conversations.json")
BACKUP_FOLDER = os.path.join(frame_ROOT, "Vault", "Backups")
LOGS_FOLDER = os.path.join(frame_ROOT, "Vault", "Logs")
os.makedirs(LOGS_FOLDER, exist_ok=True)

logger = setup_logger("memory_short_import", os.path.join(LOGS_FOLDER, "memory_short.log"))

# Debug logging for paths
logger.info(f"frame_ROOT: {frame_ROOT}")
logger.info(f"CHATGPT_EXPORT: {CHATGPT_EXPORT}")
logger.info("ChromaDB configured via unified chroma_config")

class ShortTermMemoryBank:
    def __init__(self):
        self.collection = get_short_term_collection()

    def add_memory(self, content: str, metadata: Dict, timestamp: Optional[str] = None) -> bool:
        """Add memory with content-based deduplication"""
        if not timestamp:
            timestamp = datetime.now().isoformat()
        try:
            memory_id = f"stm_{hash(content + metadata.get('source', '')) & 0xffffffff}"
            
            existing = self.collection.get(
                where={"source": metadata["source"]},
                where_document={"$contains": content}
            )
            
            if existing['ids']:
                logger.debug(f"Skipping duplicate: {content[:50]}...")
                return False
                
            self.collection.add(
                documents=[content],
                metadatas=[metadata],
                ids=[memory_id]
            )
            logger.info(f"Short-term memory added: {content[:50]}...")
            return True
        except Exception as e:
            logger.error(f"Failed to add memory: {str(e)}")
            return False

def process_conversation(conversation: Dict, memory_bank: ShortTermMemoryBank) -> int:
    """Process a single conversation from ChatGPT export"""
    imported_count = 0
    try:
        messages = conversation.get('mapping', {})
        for msg_id, msg_data in messages.items():
            message = msg_data.get('message', {})
            if not message:
                continue

            content = message.get('content', {}).get('parts', [''])[0]
            if not content:
                continue

            metadata = {
                "source": "chatgpt_export",
                "timestamp": message.get('create_time', datetime.now().timestamp()),
                "role": message.get('author', {}).get('role', 'unknown'),
                "conversation_id": conversation.get('id', 'unknown'),
                "module": "ChatGPT Import"
            }

            if memory_bank.add_memory(content, metadata):
                imported_count += 1

    except Exception as e:
        logger.error(f"Failed to process conversation: {str(e)}")
    
    return imported_count

def import_chatgpt_data(force_rescan: bool = False) -> Dict[str, int]:
    """Import ChatGPT export data as short-term memory"""
    results = {
        "total_files": 0,
        "imported": 0,
        "status": "failed",
        "error": None
    }

    try:
        # Add debug logging for directory structure
        logger.info("Checking directory structure:")
        logger.info(f"Initial Data Export path: {CHATGPT_EXPORT}")
        if os.path.exists(CHATGPT_EXPORT):
            logger.info("Contents of Initial Data Export:")
            for item in os.listdir(CHATGPT_EXPORT):
                item_path = os.path.join(CHATGPT_EXPORT, item)
                if os.path.isdir(item_path):
                    logger.info(f"Directory: {item}")
                    # List contents of user directory
                    for subitem in os.listdir(item_path):
                        logger.info(f"  - {subitem}")
                else:
                    logger.info(f"File: {item}")
        else:
            logger.error(f"Initial Data Export directory not found: {CHATGPT_EXPORT}")

        if force_rescan:
            backup_path = os.path.join(BACKUP_FOLDER, f"short_term_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            short_term_path = os.path.join(CHROMA_PATH, "short_term_memory")
            if os.path.exists(short_term_path):
                shutil.copytree(short_term_path, os.path.join(backup_path, "short_term_memory"))
                logger.info(f"Backup created at {backup_path}")

        memory_bank = ShortTermMemoryBank()

        # Process conversations.json
        logger.info(f"Looking for conversations.json at: {CONVERSATIONS_PATH}")
        
        if not os.path.exists(CONVERSATIONS_PATH):
            error_msg = f"conversations.json not found at {CONVERSATIONS_PATH}"
            logger.error(error_msg)
            results["error"] = error_msg
            # List contents of the directory to help debug
            if os.path.exists(CHATGPT_EXPORT):
                logger.info(f"Contents of {CHATGPT_EXPORT}:")
                for item in os.listdir(CHATGPT_EXPORT):
                    logger.info(f"  - {item}")
            else:
                logger.error(f"Export directory not found: {CHATGPT_EXPORT}")
            return results

        with open(CONVERSATIONS_PATH, 'r', encoding='utf-8') as f:
            conversations = json.load(f)
            if isinstance(conversations, list):
                results["total_files"] += 1
                logger.info(f"Found {len(conversations)} conversations to process")
                for conversation in conversations:
                    imported = process_conversation(conversation, memory_bank)
                    results["imported"] += imported
                logger.info(f"Processed conversations.json: {results['imported']} memories imported")
                results["status"] = "completed"
            else:
                error_msg = f"Unexpected format in conversations.json: {type(conversations)}"
                logger.error(error_msg)
                results["error"] = error_msg

    except Exception as e:
        error_msg = f"Failed to process conversations.json: {str(e)}"
        logger.error(error_msg)
        results["error"] = error_msg

    logger.info(f"Import completed. Total memories: {results['imported']}")
    return results

if __name__ == "__main__":
    logger.info("Starting ChatGPT export import")
    results = import_chatgpt_data(force_rescan=True)
    
    print(f"Operation completed. Status: {results['status']}")
    print(f"Total files processed: {results['total_files']}")
    print(f"Total memories imported: {results['imported']}")
    if results.get('error'):
        print(f"Error: {results['error']}") 