#!/usr/bin/env python3
"""
Aviator - Scout Advisor for Navigator
Provides aerial reconnaissance of file structures within a user's VVAULT directory.
Gives navigator a high-level view while navigator handles ground-level file operations.

Role: Scout Advisor
- Scans directories and provides folder snapshots
- Auto-tags files based on content/type/location
- Advises navigator on what to explore
- Maintains directory awareness for constructs
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from collections import defaultdict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [AVIATOR] %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class Aviator:
    """
    Scout advisor that provides aerial view of file structures.
    Works in tandem with Navigator (ground-level file helper).
    """
    
    def __init__(self, vvault_root: str, user_id: str, construct_id: str):
        self.vvault_root = Path(vvault_root)
        self.user_id = user_id
        self.construct_id = construct_id
        self.user_vault_path = self.vvault_root / user_id
        self.tags_cache: Dict[str, List[str]] = {}
        self.last_scan: Optional[datetime] = None
        
    def scan_directory(self, relative_path: str = "") -> Dict[str, Any]:
        """
        Provide aerial snapshot of a directory.
        Returns folder structure, file counts, and type breakdown.
        """
        target_path = self.user_vault_path / relative_path
        
        if not target_path.exists():
            logger.warning(f"Directory not found: {target_path}")
            return {"error": "Directory not found", "path": str(target_path)}
        
        snapshot = {
            "path": str(relative_path) or "/",
            "scanned_at": datetime.now().isoformat(),
            "construct_id": self.construct_id,
            "folders": [],
            "files": [],
            "summary": {
                "total_folders": 0,
                "total_files": 0,
                "file_types": defaultdict(int),
                "size_bytes": 0
            }
        }
        
        try:
            for entry in target_path.iterdir():
                if entry.name.startswith('.'):
                    continue
                    
                if entry.is_dir():
                    snapshot["folders"].append({
                        "name": entry.name,
                        "child_count": len(list(entry.iterdir())) if entry.exists() else 0
                    })
                    snapshot["summary"]["total_folders"] += 1
                else:
                    ext = entry.suffix.lower() or "(no extension)"
                    size = entry.stat().st_size if entry.exists() else 0
                    snapshot["files"].append({
                        "name": entry.name,
                        "extension": ext,
                        "size_bytes": size
                    })
                    snapshot["summary"]["total_files"] += 1
                    snapshot["summary"]["file_types"][ext] += 1
                    snapshot["summary"]["size_bytes"] += size
                    
            snapshot["summary"]["file_types"] = dict(snapshot["summary"]["file_types"])
            self.last_scan = datetime.now()
            
        except PermissionError:
            logger.error(f"Permission denied: {target_path}")
            snapshot["error"] = "Permission denied"
            
        return snapshot
    
    def auto_tag_file(self, file_path: str) -> List[str]:
        """
        Automatically generate tags for a file based on:
        - Location in directory structure
        - File extension/type
        - Filename patterns
        """
        path = Path(file_path)
        tags = []
        
        ext = path.suffix.lower()
        ext_tags = {
            ".md": ["markdown", "documentation"],
            ".py": ["python", "code"],
            ".js": ["javascript", "code"],
            ".ts": ["typescript", "code"],
            ".json": ["data", "config"],
            ".yaml": ["data", "config"],
            ".yml": ["data", "config"],
            ".capsule": ["identity", "capsule", "soulgem"],
            ".txt": ["text"],
            ".log": ["logs", "debug"],
            ".png": ["media", "image"],
            ".jpg": ["media", "image"],
            ".jpeg": ["media", "image"],
            ".gif": ["media", "image"],
            ".mp3": ["media", "audio"],
            ".wav": ["media", "audio"],
            ".mp4": ["media", "video"],
        }
        tags.extend(ext_tags.get(ext, ["other"]))
        
        parts = path.parts
        if "identity" in parts:
            tags.append("identity")
        if "chatty" in parts:
            tags.append("chatty")
            tags.append("conversation")
        if "capsules" in parts:
            tags.append("capsule")
        if "instances" in parts:
            tags.append("construct-instance")
        if "library" in parts:
            tags.append("library")
        if "media" in parts:
            tags.append("media")
        if "logs" in parts:
            tags.append("logs")
            
        name_lower = path.name.lower()
        if "chat_with_" in name_lower:
            tags.append("transcript")
        if "prompt" in name_lower:
            tags.append("prompt")
        if "config" in name_lower:
            tags.append("config")
        if "test" in name_lower:
            tags.append("test")
            
        tags = list(set(tags))
        self.tags_cache[file_path] = tags
        
        return tags
    
    def get_directory_tree(self, relative_path: str = "", max_depth: int = 3) -> Dict[str, Any]:
        """
        Generate a tree view of directory structure up to max_depth.
        Useful for giving navigator orientation.
        """
        target_path = self.user_vault_path / relative_path
        
        def build_tree(path: Path, current_depth: int) -> Dict[str, Any]:
            if current_depth > max_depth or not path.exists():
                return None
                
            node = {
                "name": path.name or str(path),
                "type": "folder" if path.is_dir() else "file"
            }
            
            if path.is_dir():
                children = []
                try:
                    for child in sorted(path.iterdir()):
                        if child.name.startswith('.'):
                            continue
                        child_node = build_tree(child, current_depth + 1)
                        if child_node:
                            children.append(child_node)
                except PermissionError:
                    pass
                node["children"] = children
            else:
                node["tags"] = self.auto_tag_file(str(path))
                
            return node
            
        return build_tree(target_path, 0)
    
    def advise_navigator(self, query: str) -> Dict[str, Any]:
        """
        Provide navigation advice based on a query.
        Tells navigator where to look for specific content.
        """
        advice = {
            "query": query,
            "suggested_paths": [],
            "reasoning": []
        }
        
        query_lower = query.lower()
        
        if "transcript" in query_lower or "chat" in query_lower or "conversation" in query_lower:
            advice["suggested_paths"].append(f"instances/{self.construct_id}/chatty/")
            advice["reasoning"].append("Transcripts are stored in construct's chatty folder")
            
        if "identity" in query_lower or "prompt" in query_lower or "capsule" in query_lower:
            advice["suggested_paths"].append(f"instances/{self.construct_id}/identity/")
            advice["reasoning"].append("Identity files are in construct's identity folder")
            
        if "media" in query_lower or "image" in query_lower or "audio" in query_lower:
            advice["suggested_paths"].append("library/media/")
            advice["reasoning"].append("Media files are stored in library/media")
            
        if "log" in query_lower or "debug" in query_lower:
            advice["suggested_paths"].append(f"instances/{self.construct_id}/logs/")
            advice["reasoning"].append("Logs are in construct's logs folder")
            
        if not advice["suggested_paths"]:
            advice["suggested_paths"].append("")
            advice["reasoning"].append("No specific match - suggesting root scan")
            
        return advice
    
    def get_construct_overview(self) -> Dict[str, Any]:
        """
        Get a complete overview of the construct's presence in VVAULT.
        """
        construct_path = f"instances/{self.construct_id}"
        
        overview = {
            "construct_id": self.construct_id,
            "user_id": self.user_id,
            "vault_path": str(self.user_vault_path),
            "construct_path": construct_path,
            "scan_time": datetime.now().isoformat(),
            "structure": self.get_directory_tree(construct_path, max_depth=2),
            "quick_stats": {}
        }
        
        scan = self.scan_directory(construct_path)
        if "error" not in scan:
            overview["quick_stats"] = scan["summary"]
            
        return overview


def create_aviator(vvault_root: str, user_id: str, construct_id: str) -> Aviator:
    """Factory function to create an Aviator instance."""
    return Aviator(vvault_root, user_id, construct_id)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) >= 4:
        vvault_root = sys.argv[1]
        user_id = sys.argv[2]
        construct_id = sys.argv[3]
    else:
        vvault_root = os.environ.get("VVAULT_ROOT", ".")
        user_id = os.environ.get("VVAULT_USER_ID", "devon_woodson_1762969514958")
        construct_id = os.environ.get("CONSTRUCT_ID", "zen-001")
    
    aviator = create_aviator(vvault_root, user_id, construct_id)
    
    print("=== Aviator Scout Report ===")
    overview = aviator.get_construct_overview()
    print(json.dumps(overview, indent=2, default=str))
