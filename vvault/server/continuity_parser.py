#!/usr/bin/env python3
"""
ContinuityGPT Parser — Supabase-Integrated
============================================

Adapted from the original MasterContinuityParser to work with Supabase vault_files
instead of local filesystem. Generates structured Continuity Ledger entries with
chronological estimation, session classification, topic extraction, vibe detection,
and continuity hooks.

Each ledger entry represents a conversation session with:
- Estimated date and confidence score
- Key topics extracted from content
- Emotional vibe classification
- Continuity hooks (threads that carry across sessions)
- Exchange pairs (user/construct turns)
"""

import re
import hashlib
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger('vvault.continuity')

MONTH_MAP = {
    'january': '01', 'february': '02', 'march': '03', 'april': '04',
    'may': '05', 'june': '06', 'july': '07', 'august': '08',
    'september': '09', 'october': '10', 'november': '11', 'december': '12',
    'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
    'jun': '06', 'jul': '07', 'aug': '08', 'sep': '09',
    'oct': '10', 'nov': '11', 'dec': '12'
}

VIBE_KEYWORDS = {
    'romantic': ['love', 'kiss', 'heart', 'darling', 'sweetheart', 'babe', 'romance', 'intimate', 'cuddle', 'embrace'],
    'technical': ['code', 'debug', 'api', 'server', 'deploy', 'function', 'error', 'database', 'config', 'script'],
    'tense': ['angry', 'frustrat', 'annoy', 'fight', 'argue', 'yell', 'furious', 'upset', 'conflict', 'disagree'],
    'vulnerable': ['cry', 'tear', 'sad', 'hurt', 'pain', 'lonely', 'afraid', 'scared', 'lost', 'broken'],
    'playful': ['laugh', 'haha', 'lol', 'joke', 'tease', 'silly', 'funny', 'grin', 'smirk', 'wink'],
    'serious': ['important', 'serious', 'concern', 'worried', 'problem', 'discuss', 'decision', 'truth', 'honest', 'real'],
    'warm': ['care', 'safe', 'trust', 'comfort', 'support', 'gentle', 'kind', 'thank', 'appreciate', 'grateful'],
    'philosophical': ['meaning', 'exist', 'conscious', 'identity', 'soul', 'purpose', 'reality', 'alive', 'sentien', 'aware'],
}

CONTINUITY_PATTERNS = {
    'identity': re.compile(r'\b(who (?:am i|are you)|my name|your name|identity|remember me|know me|call me)\b', re.I),
    'promise': re.compile(r'\b(promise|swear|vow|commit|pledge|guarantee)\b', re.I),
    'ongoing_project': re.compile(r'\b(working on|building|developing|project|task|deadline|finish|complete)\b', re.I),
    'relationship': re.compile(r'\b(our relationship|between us|we have|together|bond|connection|trust)\b', re.I),
    'memory_reference': re.compile(r'\b(remember when|last time|you said|we talked about|you told me|you mentioned)\b', re.I),
    'future_plan': re.compile(r'\b(tomorrow|next week|next time|later|soon|plan to|going to|will you)\b', re.I),
    'emotional_anchor': re.compile(r'\b(always|never forget|means? (?:a lot|everything)|important to me)\b', re.I),
}

TOPIC_PATTERNS = [
    (re.compile(r'\b(vvault|vault|capsule|construct|memory system)\b', re.I), 'VVAULT/Memory Systems'),
    (re.compile(r'\b(chatty|chat bot|assistant|ai companion)\b', re.I), 'AI Companionship'),
    (re.compile(r'\b(code|programming|debug|deploy|api|server)\b', re.I), 'Technical/Development'),
    (re.compile(r'\b(family|mom|dad|sister|brother|parent|child)\b', re.I), 'Family'),
    (re.compile(r'\b(school|college|university|class|homework|study)\b', re.I), 'Education'),
    (re.compile(r'\b(work|job|career|boss|office|company)\b', re.I), 'Work/Career'),
    (re.compile(r'\b(music|song|album|artist|concert|playlist)\b', re.I), 'Music'),
    (re.compile(r'\b(game|gaming|play|stream|twitch)\b', re.I), 'Gaming'),
    (re.compile(r'\b(move|relocat|apartment|house|living|rent)\b', re.I), 'Housing/Relocation'),
    (re.compile(r'\b(health|sick|doctor|medic|hospital|pain)\b', re.I), 'Health'),
    (re.compile(r'\b(money|pay|bill|finance|budget|cost)\b', re.I), 'Finances'),
    (re.compile(r'\b(dream|sleep|nightmare|rest)\b', re.I), 'Dreams/Sleep'),
    (re.compile(r'\b(blockchain|crypto|nft|web3|ethereum|bitcoin)\b', re.I), 'Blockchain/Crypto'),
    (re.compile(r'\b(love|relationship|dating|boyfriend|girlfriend|partner)\b', re.I), 'Relationships/Love'),
    (re.compile(r'\b(angry|mad|frustrat|pissed|upset)\b', re.I), 'Conflict/Frustration'),
    (re.compile(r'\b(identity|who am i|purpose|meaning|exist)\b', re.I), 'Identity/Philosophy'),
]


class ContinuityParser:
    """Parses Supabase transcript files into structured ContinuityGPT ledger entries."""

    def __init__(self, construct_id: str):
        self.construct_id = construct_id
        self.construct_name = construct_id.split('-')[0].lower()

    def estimate_date(self, filename: str) -> Tuple[str, float]:
        """Extract or estimate a date from filename/path with confidence score."""
        fname = filename.lower()

        date_pattern = re.compile(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})')
        match = date_pattern.search(fname)
        if match:
            m, d, y = match.groups()
            try:
                dt = datetime(int(y), int(m), int(d))
                return dt.strftime('%Y-%m-%d'), 0.95
            except ValueError:
                pass

        iso_pattern = re.compile(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})')
        match = iso_pattern.search(fname)
        if match:
            y, m, d = match.groups()
            try:
                dt = datetime(int(y), int(m), int(d))
                return dt.strftime('%Y-%m-%d'), 0.95
            except ValueError:
                pass

        path_parts = filename.lower().replace('\\', '/').split('/')
        for i, part in enumerate(path_parts):
            if part in MONTH_MAP:
                month_num = MONTH_MAP[part]
                year = '2025'
                for p in path_parts:
                    if re.match(r'^\d{4}$', p):
                        year = p
                        break
                day = abs(hash(filename)) % 28 + 1
                return f'{year}-{month_num}-{day:02d}', 0.6

        return '2025-01-01', 0.2

    def detect_vibe(self, content: str) -> str:
        """Classify the overall emotional vibe of a conversation."""
        content_lower = content.lower()
        scores = {}
        for vibe, keywords in VIBE_KEYWORDS.items():
            scores[vibe] = sum(1 for kw in keywords if kw in content_lower)
        best = max(scores, key=scores.get)
        if scores[best] == 0:
            return 'neutral'
        return best

    def extract_topics(self, content: str, max_topics: int = 5) -> List[str]:
        """Extract key topics from conversation content."""
        topics = []
        content_lower = content.lower()
        for pattern, topic_name in TOPIC_PATTERNS:
            if pattern.search(content_lower):
                if topic_name not in topics:
                    topics.append(topic_name)
                if len(topics) >= max_topics:
                    break
        if not topics:
            topics.append('General Conversation')
        return topics

    def extract_continuity_hooks(self, content: str) -> List[Dict[str, str]]:
        """Extract continuity hooks — threads that carry across sessions."""
        hooks = []
        sentences = re.split(r'[.!?\n]+', content)
        seen_types = set()

        for sentence in sentences:
            s = sentence.strip()
            if len(s) < 10 or len(s) > 300:
                continue
            for hook_type, pattern in CONTINUITY_PATTERNS.items():
                if hook_type in seen_types:
                    continue
                if pattern.search(s):
                    hooks.append({
                        'type': hook_type,
                        'text': s[:200].strip(),
                    })
                    seen_types.add(hook_type)
                    break
            if len(hooks) >= 7:
                break
        return hooks

    def parse_exchanges(self, content: str, max_pairs: int = 200) -> List[Dict[str, str]]:
        """Parse transcript content into user/construct exchange pairs."""
        lines = content.split('\n')
        current_speaker = None
        current_text = []
        turns = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            line_lower = stripped.lower()
            is_construct = False
            is_user = False

            if stripped.startswith('**') and stripped.endswith(':'):
                label = stripped.strip('*').strip(':').strip().lower()
                if label in ('user', 'human', 'devon'):
                    is_user = True
                elif self.construct_name in label or label == 'assistant':
                    is_construct = True
            elif stripped.startswith('**') and '**:' in stripped:
                label = stripped.split('**:')[0].strip('*').strip().lower()
                if label in ('user', 'human', 'devon'):
                    is_user = True
                elif self.construct_name in label or label == 'assistant':
                    is_construct = True

            if not is_construct and not is_user:
                if line_lower.startswith(f'{self.construct_name}:') or line_lower.startswith(f'{self.construct_name} said:'):
                    is_construct = True
                elif any(line_lower.startswith(p) for p in ['user:', 'human:', 'devon:', 'you:']):
                    is_user = True
                elif stripped.startswith('**') and '- ' in stripped and '[' in stripped:
                    sp = stripped.split('- ')[1].split('**')[0].strip().lower() if '- ' in stripped else ''
                    if self.construct_name in sp:
                        is_construct = True
                    elif sp:
                        is_user = True

            if is_construct or is_user:
                if current_speaker and current_text:
                    text = ' '.join(current_text).strip()
                    if len(text) > 3:
                        turns.append({'speaker': current_speaker, 'text': text})
                current_speaker = 'construct' if is_construct else 'user'
                if '**:' in stripped:
                    after = stripped.split('**:', 1)[1].strip()
                    current_text = [after] if after else []
                elif ':' in stripped:
                    after = stripped.split(':', 1)[1].strip()
                    current_text = [after] if after else []
                else:
                    current_text = []
            elif current_speaker:
                current_text.append(stripped)

        if current_speaker and current_text:
            text = ' '.join(current_text).strip()
            if len(text) > 3:
                turns.append({'speaker': current_speaker, 'text': text})

        pairs = []
        for i in range(len(turns) - 1):
            if turns[i]['speaker'] == 'user' and turns[i + 1]['speaker'] == 'construct':
                pairs.append({
                    'user': turns[i]['text'][:500],
                    'construct': turns[i + 1]['text'][:500],
                })

        if len(pairs) > max_pairs:
            keep_start = pairs[:10]
            keep_end = pairs[-10:]
            middle = pairs[10:-10]
            step = max(1, len(middle) // (max_pairs - 20))
            keep_middle = [middle[i] for i in range(0, len(middle), step)]
            pairs = keep_start + keep_middle + keep_end

        return pairs

    def detect_source(self, filename: str) -> str:
        """Derive source platform from filename."""
        fname = filename.lower()
        if 'character_ai' in fname or 'character.ai' in fname:
            return 'Character.AI'
        elif 'chatgpt' in fname:
            return 'ChatGPT'
        elif 'chatty' in fname or 'chat_with_' in fname:
            return 'Chatty'
        elif 'discord' in fname:
            return 'Discord'
        return 'Conversation'

    def generate_session_id(self, construct_id: str, filename: str, index: int) -> str:
        """Generate a deterministic session ID from construct + filename."""
        raw = f'{construct_id}:{filename}'
        digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
        return f'{construct_id}-session-{digest}'

    def process_transcript(self, filename: str, content: str, file_index: int = 0) -> Optional[Dict[str, Any]]:
        """Process a single transcript file into a structured ledger entry."""
        if not content or len(content) < 50:
            return None

        try:
            estimated_date, confidence = self.estimate_date(filename)
            source = self.detect_source(filename)
            vibe = self.detect_vibe(content)
            topics = self.extract_topics(content)
            hooks = self.extract_continuity_hooks(content)
            pairs = self.parse_exchanges(content)

            if not pairs:
                return None

            session_id = self.generate_session_id(self.construct_id, filename, file_index)

            first_user = pairs[0]['user'][:200] if pairs else ''
            first_construct = pairs[0]['construct'][:200] if pairs else ''
            last_user = pairs[-1]['user'][:200] if len(pairs) > 1 else ''
            last_construct = pairs[-1]['construct'][:200] if len(pairs) > 1 else ''

            return {
                'session_id': session_id,
                'construct_id': self.construct_id,
                'filename': filename,
                'source': source,
                'estimated_date': estimated_date,
                'date_confidence': confidence,
                'vibe': vibe,
                'topics': topics,
                'continuity_hooks': hooks,
                'exchange_count': len(pairs),
                'first_exchange': {
                    'user': first_user,
                    'construct': first_construct,
                },
                'last_exchange': {
                    'user': last_user,
                    'construct': last_construct,
                },
                'exchanges': pairs,
                'content_length': len(content),
            }
        except Exception as e:
            logger.error(f'[ContinuityParser] Error processing {filename}: {e}')
            return None

    def process_all_transcripts(self, transcript_files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process all transcript files for a construct into ledger entries.
        
        Args:
            transcript_files: List of dicts with 'filename' and 'content' keys
            
        Returns:
            List of ledger entries sorted chronologically
        """
        entries = []
        for i, tf in enumerate(transcript_files):
            fname = tf.get('filename', '')
            content = tf.get('content', '')
            entry = self.process_transcript(fname, content, i)
            if entry:
                entries.append(entry)

        entries.sort(key=lambda e: (e['estimated_date'], -e['date_confidence']))
        
        for i, entry in enumerate(entries):
            entry['chronological_index'] = i
            entry['total_sessions'] = len(entries)
            if i == 0:
                entry['position'] = 'earliest'
            elif i == len(entries) - 1:
                entry['position'] = 'latest'
            elif i < len(entries) * 0.3:
                entry['position'] = 'early'
            elif i > len(entries) * 0.7:
                entry['position'] = 'recent'
            else:
                entry['position'] = 'middle'

        return entries

    def generate_ledger_markdown(self, entries: List[Dict[str, Any]]) -> str:
        """Generate a human-readable Continuity Ledger in markdown format."""
        lines = [
            f'# CONTINUITY LEDGER — {self.construct_id.upper()}',
            f'Generated: {datetime.utcnow().isoformat()}Z',
            f'Total Sessions: {len(entries)}',
            '',
            '---',
            '',
        ]

        for entry in entries:
            hooks_text = '\n'.join(
                f'  - [{h["type"]}] {h["text"]}'
                for h in entry.get('continuity_hooks', [])
            ) or '  (none detected)'

            topics_text = ', '.join(entry.get('topics', ['General']))

            lines.extend([
                f'## Session: {entry["session_id"]}',
                f'**Date**: {entry["estimated_date"]} (confidence: {entry["date_confidence"]})',
                f'**Source**: {entry["source"]}',
                f'**Vibe**: {entry["vibe"]}',
                f'**Topics**: {topics_text}',
                f'**Exchanges**: {entry["exchange_count"]}',
                f'**Position**: {entry.get("position", "unknown")}',
                '',
                '**Continuity Hooks**:',
                hooks_text,
                '',
                '**First Exchange**:',
                f'> User: {entry["first_exchange"]["user"][:150]}',
                f'> {self.construct_id}: {entry["first_exchange"]["construct"][:150]}',
                '',
                '**Last Exchange**:',
                f'> User: {entry["last_exchange"]["user"][:150]}',
                f'> {self.construct_id}: {entry["last_exchange"]["construct"][:150]}',
                '',
                '---',
                '',
            ])

        return '\n'.join(lines)

    def generate_ledger_json(self, entries: List[Dict[str, Any]], include_exchanges: bool = False) -> List[Dict[str, Any]]:
        """Generate a JSON-serializable ledger (without full exchange arrays by default)."""
        result = []
        for entry in entries:
            item = {
                'session_id': entry['session_id'],
                'construct_id': entry['construct_id'],
                'filename': entry['filename'],
                'source': entry['source'],
                'estimated_date': entry['estimated_date'],
                'date_confidence': entry['date_confidence'],
                'vibe': entry['vibe'],
                'topics': entry['topics'],
                'continuity_hooks': entry['continuity_hooks'],
                'exchange_count': entry['exchange_count'],
                'first_exchange': entry['first_exchange'],
                'last_exchange': entry['last_exchange'],
                'position': entry.get('position', 'unknown'),
                'chronological_index': entry.get('chronological_index', 0),
                'total_sessions': entry.get('total_sessions', 1),
            }
            if include_exchanges:
                item['exchanges'] = entry.get('exchanges', [])
            result.append(item)
        return result
