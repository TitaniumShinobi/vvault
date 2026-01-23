"""
Transcript Ingester - Transcript → Capsule Ingestion Pipeline
Parses markdown transcripts and creates/updates VVAULT capsules.
"""

import logging
import json
import hashlib
from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime
import re

logger = logging.getLogger(__name__)


class TranscriptIngester:
    """
    Ingests transcript files and converts them to VVAULT memory entries.
    Tracks ingestion state to only process new messages.
    """
    
    def __init__(self, construct_id: str, vvault_root: Optional[str] = None):
        self.construct_id = construct_id
        self.vvault_root = Path(vvault_root) if vvault_root else Path(__file__).parent.parent.parent
        self.instances_dir = self.vvault_root / "instances"
        
        self._last_ingested_hash = None
        self._last_ingested_count = 0
        self._ingestion_state_file = None
        
    def _find_construct_path(self) -> Optional[Path]:
        """Find the construct's directory in instances/"""
        if not self.instances_dir.exists():
            return None
        
        for shard in self.instances_dir.iterdir():
            if shard.is_dir() and shard.name.startswith("shard_"):
                construct_path = shard / self.construct_id
                if construct_path.exists():
                    return construct_path
        return None
    
    def _find_transcript(self) -> Optional[Path]:
        """Find the main transcript file for this construct."""
        construct_path = self._find_construct_path()
        if not construct_path:
            return None
        
        chatty_dir = construct_path / "chatty"
        if chatty_dir.exists():
            transcript = chatty_dir / f"chat_with_{self.construct_id}.md"
            if transcript.exists():
                return transcript
        
        chatgpt_dir = construct_path / "chatgpt"
        if chatgpt_dir.exists():
            for f in chatgpt_dir.glob("*.md"):
                return f
        
        return None
    
    def _load_ingestion_state(self) -> Dict[str, Any]:
        """Load the ingestion state from disk."""
        construct_path = self._find_construct_path()
        if not construct_path:
            return {"last_hash": None, "last_count": 0, "entries": []}
        
        state_file = construct_path / ".ingestion_state.json"
        self._ingestion_state_file = state_file
        
        if state_file.exists():
            try:
                return json.loads(state_file.read_text())
            except:
                pass
        
        return {"last_hash": None, "last_count": 0, "entries": []}
    
    def _save_ingestion_state(self, state: Dict[str, Any]):
        """Save the ingestion state to disk."""
        if self._ingestion_state_file:
            try:
                self._ingestion_state_file.write_text(json.dumps(state, indent=2))
            except Exception as e:
                logger.error(f"Failed to save ingestion state: {e}")
    
    def _parse_transcript(self, content: str) -> List[Dict[str, Any]]:
        """Parse markdown transcript into memory entries."""
        entries = []
        
        message_pattern = re.compile(
            r'\*\*(\d+:\d+:\d+ [AP]M \w+) - (\w+)\*\* \[([^\]]+)\]: (.+?)(?=\n\n\*\*|\n\n## |\Z)',
            re.DOTALL
        )
        
        construct_names = ["aurora", "zen", "lin", "katana", "nova", "synth"]
        
        for match in message_pattern.finditer(content):
            time_str, speaker, iso_timestamp, message_content = match.groups()
            
            role = "assistant" if speaker.lower() in construct_names else "user"
            
            entry_hash = hashlib.sha256(f"{iso_timestamp}:{speaker}:{message_content[:50]}".encode()).hexdigest()[:16]
            
            entries.append({
                "id": entry_hash,
                "role": role,
                "speaker": speaker,
                "content": message_content.strip(),
                "timestamp": iso_timestamp,
                "time_display": time_str,
                "metadata": {
                    "source": "transcript",
                    "construct_id": self.construct_id
                }
            })
        
        return entries
    
    def ingest_new_messages(self) -> Dict[str, Any]:
        """
        Ingest new messages from transcript.
        Only processes messages not already ingested.
        """
        result = {
            "count": 0,
            "entries": [],
            "transcript_path": None,
            "error": None
        }
        
        transcript_path = self._find_transcript()
        if not transcript_path:
            logger.debug(f"[{self.construct_id}] No transcript found")
            return result
        
        result["transcript_path"] = str(transcript_path)
        
        try:
            content = transcript_path.read_text()
        except Exception as e:
            result["error"] = str(e)
            return result
        
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        state = self._load_ingestion_state()
        
        if content_hash == state.get("last_hash"):
            logger.debug(f"[{self.construct_id}] No new content")
            return result
        
        all_entries = self._parse_transcript(content)
        
        known_ids = set(e.get("id") for e in state.get("entries", []))
        new_entries = [e for e in all_entries if e["id"] not in known_ids]
        
        result["count"] = len(new_entries)
        result["entries"] = new_entries
        
        state["last_hash"] = content_hash
        state["last_count"] = len(all_entries)
        state["entries"] = all_entries[-1000:]
        state["last_ingested"] = datetime.now().isoformat()
        self._save_ingestion_state(state)
        
        logger.info(f"[{self.construct_id}] Ingested {len(new_entries)} new messages")
        return result
    
    def update_capsule(self) -> Dict[str, Any]:
        """
        Update the construct's capsule with latest memory data.
        Creates emotional tags, anchors, and metadata.
        """
        result = {"success": False, "capsule_path": None, "error": None}
        
        construct_path = self._find_construct_path()
        if not construct_path:
            result["error"] = "Construct path not found"
            return result
        
        state = self._load_ingestion_state()
        entries = state.get("entries", [])
        
        if not entries:
            result["error"] = "No entries to capsule"
            return result
        
        capsule_data = {
            "construct_id": self.construct_id,
            "version": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "created_at": datetime.now().isoformat(),
            "message_count": len(entries),
            "memory_summary": {
                "total_messages": len(entries),
                "user_messages": len([e for e in entries if e["role"] == "user"]),
                "assistant_messages": len([e for e in entries if e["role"] == "assistant"]),
                "date_range": {
                    "first": entries[0]["timestamp"] if entries else None,
                    "last": entries[-1]["timestamp"] if entries else None
                }
            },
            "emotional_anchors": self._extract_emotional_anchors(entries),
            "key_topics": self._extract_key_topics(entries),
            "continuity_hooks": self._extract_continuity_hooks(entries)
        }
        
        identity_dir = construct_path / "identity"
        identity_dir.mkdir(exist_ok=True)
        capsule_path = identity_dir / f"{self.construct_id}.capsule.json"
        
        try:
            capsule_path.write_text(json.dumps(capsule_data, indent=2))
            result["success"] = True
            result["capsule_path"] = str(capsule_path)
            logger.info(f"[{self.construct_id}] Capsule updated: {capsule_path}")
        except Exception as e:
            result["error"] = str(e)
        
        return result
    
    def _extract_emotional_anchors(self, entries: List[Dict]) -> List[Dict]:
        """Extract emotional anchors from message content."""
        anchors = []
        emotional_keywords = {
            "joy": ["love", "happy", "excited", "amazing", "wonderful"],
            "concern": ["worried", "concerned", "afraid", "anxious"],
            "trust": ["trust", "believe", "confident", "rely"],
            "gratitude": ["thank", "grateful", "appreciate"]
        }
        
        for entry in entries[-100:]:
            content_lower = entry.get("content", "").lower()
            for emotion, keywords in emotional_keywords.items():
                if any(kw in content_lower for kw in keywords):
                    anchors.append({
                        "emotion": emotion,
                        "timestamp": entry.get("timestamp"),
                        "speaker": entry.get("speaker"),
                        "snippet": entry.get("content", "")[:100]
                    })
                    break
        
        return anchors[-20:]
    
    def _extract_key_topics(self, entries: List[Dict]) -> List[str]:
        """Extract key topics from messages."""
        topics = set()
        topic_patterns = [
            r'\b(project|code|implementation|feature|bug|issue)\b',
            r'\b(memory|identity|construct|capsule|continuity)\b',
            r'\b(api|database|server|frontend|backend)\b'
        ]
        
        for entry in entries[-50:]:
            content = entry.get("content", "")
            for pattern in topic_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                topics.update(m.lower() for m in matches)
        
        return list(topics)[:10]
    
    def _extract_continuity_hooks(self, entries: List[Dict]) -> List[Dict]:
        """Extract continuity hooks - shared experiences and named references."""
        hooks = []
        
        name_pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b'
        
        for entry in entries[-50:]:
            content = entry.get("content", "")
            names = re.findall(name_pattern, content)
            
            for name in names:
                if name.lower() not in ["the", "this", "that", "what", "when", "where"]:
                    hooks.append({
                        "type": "name_reference",
                        "value": name,
                        "timestamp": entry.get("timestamp")
                    })
        
        seen = set()
        unique_hooks = []
        for hook in hooks:
            key = f"{hook['type']}:{hook['value']}"
            if key not in seen:
                seen.add(key)
                unique_hooks.append(hook)
        
        return unique_hooks[:15]
