#!/usr/bin/env python3
"""
Construct-Aware Logging Utility
Sets up logging with construct-specific log files and formats.
"""

import logging
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Optional


def setup_construct_logging(
    construct_callsign: str,
    script_name: str,
    vvault_root: str,
    user_id: str,
    log_level: int = logging.INFO
) -> logging.Logger:
    """
    Setup construct-aware logging.
    
    Args:
        construct_callsign: Construct callsign (e.g., "nova-001")
        script_name: Name of the script (e.g., "master_self_prompt")
        vvault_root: Root directory of VVAULT
        user_id: VVAULT user ID
        log_level: Logging level (default: INFO)
    
    Returns:
        Configured logger instance
    """
    # Calculate log directory path
    log_dir = Path(vvault_root) / "users" / "shard_0000" / user_id / "instances" / construct_callsign / "documents" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Create log file path
    log_file = log_dir / f"{script_name}_{construct_callsign}.log"
    
    # Create logger
    logger = logging.getLogger(f"{script_name}_{construct_callsign}")
    logger.setLevel(log_level)
    
    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()
    
    # Create formatter with construct-aware format
    formatter = logging.Formatter(
        f'[%(asctime)s] [CONSTRUCT: {construct_callsign}] [SCRIPT: {script_name}] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Console handler (for development)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger


def get_construct_logger(
    construct_callsign: str,
    script_name: str,
    vvault_root: Optional[str] = None,
    user_id: Optional[str] = None
) -> logging.Logger:
    """
    Get or create a construct-aware logger.
    
    Args:
        construct_callsign: Construct callsign
        script_name: Script name
        vvault_root: Optional VVAULT root (defaults to detecting from environment)
        user_id: Optional user ID (defaults to detecting from environment)
    
    Returns:
        Logger instance
    """
    if vvault_root is None:
        vvault_root = os.environ.get('VVAULT_ROOT', '/Users/devonwoodson/Documents/GitHub/vvault')
    
    if user_id is None:
        user_id = os.environ.get('VVAULT_USER_ID', 'devon_woodson_1762969514958')
    
    return setup_construct_logging(construct_callsign, script_name, vvault_root, user_id)
