"""
Memory Context Manager
Handles STM (Short-Term Memory) and LTM (Long-Term Memory) context building
"""

import os
import json
import logging
import re
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class MemoryEntry:
    role: str
    content: str
    timestamp: str
    speaker: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryContext:
    construct_id: str
    thread_id: str
    stm_window: List[MemoryEntry] = field(default_factory=list)
    ltm_entries: List[MemoryEntry] = field(default_factory=list)
    summaries: List[str] = field(default_factory=list)
    total_messages: int = 0


class MemoryContextBuilder:
    """Builds memory context from transcripts for LLM inference"""
    
    def __init__(self, vvault_root: Optional[str] = None, max_stm: int = 20, max_ltm: int = 10):
        self.vvault_root = Path(vvault_root) if vvault_root else Path(__file__).parent.parent.parent
        self.instances_dir = self.vvault_root / "instances"
        self.max_stm = max_stm
        self.max_ltm = max_ltm
    
    def _find_transcript(self, construct_id: str) -> Optional[Path]:
        """Find the transcript file for a construct"""
        if not self.instances_dir.exists():
            return None
        
        for shard in self.instances_dir.iterdir():
            if shard.is_dir() and shard.name.startswith("shard_"):
                construct_path = shard / construct_id
                chatty_dir = construct_path / "chatty"
                if chatty_dir.exists():
                    transcript = chatty_dir / f"chat_with_{construct_id}.md"
                    if transcript.exists():
                        return transcript
        
        return None
    
    def _parse_transcript(self, transcript_path: Path) -> List[MemoryEntry]:
        """Parse a markdown transcript into memory entries"""
        entries = []
        
        try:
            content = transcript_path.read_text()
        except Exception as e:
            logger.error(f"Error reading transcript {transcript_path}: {e}")
            return entries
        
        message_pattern = re.compile(
            r'\*\*(\d+:\d+:\d+ [AP]M \w+) - (\w+)\*\* \[([^\]]+)\]: (.+?)(?=\n\n\*\*|\n\n## |\Z)',
            re.DOTALL
        )
        
        for match in message_pattern.finditer(content):
            time_str, speaker, iso_timestamp, message_content = match.groups()
            
            role = "user" if speaker.lower() not in ["aurora", "zen", "lin", "katana", "nova", "synth"] else "assistant"
            
            entries.append(MemoryEntry(
                role=role,
                content=message_content.strip(),
                timestamp=iso_timestamp,
                speaker=speaker,
                metadata={}
            ))
        
        return entries
    
    def build_context(self, construct_id: str, thread_id: Optional[str] = None) -> MemoryContext:
        """Build memory context for a construct conversation"""
        transcript_path = self._find_transcript(construct_id)
        
        if not transcript_path:
            return MemoryContext(
                construct_id=construct_id,
                thread_id=thread_id or construct_id,
                stm_window=[],
                ltm_entries=[],
                summaries=[],
                total_messages=0
            )
        
        all_entries = self._parse_transcript(transcript_path)
        stm_window = all_entries[-self.max_stm:] if len(all_entries) > self.max_stm else all_entries
        ltm_entries = all_entries[:-self.max_stm][:self.max_ltm] if len(all_entries) > self.max_stm else []
        
        return MemoryContext(
            construct_id=construct_id,
            thread_id=thread_id or construct_id,
            stm_window=stm_window,
            ltm_entries=ltm_entries,
            summaries=[],
            total_messages=len(all_entries)
        )
    
    def format_for_llm(self, context: MemoryContext) -> List[Dict[str, str]]:
        """Format memory context as LLM message history"""
        messages = []
        
        for entry in context.stm_window:
            messages.append({
                "role": entry.role,
                "content": entry.content
            })
        
        return messages
    
    def get_recent_messages(self, construct_id: str, count: int = 10) -> List[MemoryEntry]:
        """Get the most recent messages for a construct"""
        context = self.build_context(construct_id)
        return context.stm_window[-count:] if context.stm_window else []


_default_builder: Optional[MemoryContextBuilder] = None

def get_memory_builder(vvault_root: Optional[str] = None) -> MemoryContextBuilder:
    """Get the default memory context builder singleton"""
    global _default_builder
    if _default_builder is None:
        _default_builder = MemoryContextBuilder(vvault_root)
    return _default_builder
