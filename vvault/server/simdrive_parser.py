"""
SimDrive Parser — Cognitive Blueprint and Continuity Injection Engine
=====================================================================

Parses, classifies, and manages SimDrive files within a construct's
`instances/{construct}/simDrive/` folder. SimDrive files represent:

- **Blueprints**: Behavioral templates and cognitive model definitions
- **Overlays**: Config patches that modify construct behavior at runtime
- **Hooks**: Continuity injection points linking capsule memory to live sessions
- **Injections**: Memory payloads derived from memup capsules for replay

Usage:
    parser = SimDriveParser(construct_id)
    classified = parser.classify_file(filename, content)
    injection = parser.capsule_to_injection(capsule_data)
"""

import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

logger = logging.getLogger('vvault.simdrive')

SIMDRIVE_FILE_TYPES = {
    'blueprint': {
        'patterns': ['blueprint', 'cognitive_model', 'behavior_template', 'persona'],
        'extensions': ['.json', '.yaml', '.yml'],
        'description': 'Behavioral template or cognitive model definition',
    },
    'overlay': {
        'patterns': ['overlay', 'patch', 'config_override', 'runtime_config'],
        'extensions': ['.json', '.yaml', '.yml'],
        'description': 'Runtime config patch that modifies construct behavior',
    },
    'hook': {
        'patterns': ['hook', 'continuity_hook', 'injection_point', 'anchor'],
        'extensions': ['.json', '.txt'],
        'description': 'Continuity injection point linking capsule memory',
    },
    'injection': {
        'patterns': ['injection', 'memory_inject', 'continuity_injection', 'replay'],
        'extensions': ['.json'],
        'description': 'Memory payload from memup capsule for construct replay',
    },
    'manifest': {
        'patterns': ['manifest', 'index', 'registry'],
        'extensions': ['.json'],
        'description': 'SimDrive file registry and version tracking',
    },
}


class SimDriveParser:

    def __init__(self, construct_id: str):
        self.construct_id = construct_id
        self.base_path = f'instances/{construct_id}/simDrive'

    def classify_file(self, filename: str, content: str = '') -> Dict[str, Any]:
        lower_fn = filename.lower()
        basename = lower_fn.split('/')[-1] if '/' in lower_fn else lower_fn

        file_type = 'unknown'
        for ftype, spec in SIMDRIVE_FILE_TYPES.items():
            if any(pat in basename for pat in spec['patterns']):
                file_type = ftype
                break

        if file_type == 'unknown':
            if basename.endswith('.json'):
                file_type = 'blueprint'
            elif basename.endswith(('.yaml', '.yml')):
                file_type = 'overlay'
            elif basename.endswith('.txt'):
                file_type = 'hook'

        parsed_data = None
        parse_error = None
        if content:
            try:
                if basename.endswith('.json'):
                    parsed_data = json.loads(content)
                else:
                    parsed_data = {'raw_content': content[:2000]}
            except (json.JSONDecodeError, ValueError) as e:
                parse_error = str(e)
                parsed_data = {'raw_content': content[:2000]}

        return {
            'filename': filename,
            'simdrive_type': file_type,
            'description': SIMDRIVE_FILE_TYPES.get(file_type, {}).get('description', 'Unknown SimDrive file'),
            'parsed': parsed_data,
            'parse_error': parse_error,
            'version': self._extract_version(parsed_data),
            'targets': self._extract_targets(parsed_data),
        }

    def _extract_version(self, data: Optional[Dict]) -> str:
        if not data or not isinstance(data, dict):
            return '1.0.0'
        return data.get('version', data.get('schema_version', '1.0.0'))

    def _extract_targets(self, data: Optional[Dict]) -> List[str]:
        if not data or not isinstance(data, dict):
            return [self.construct_id]
        targets = data.get('targets', data.get('applies_to', []))
        if isinstance(targets, str):
            targets = [targets]
        if not targets:
            targets = [self.construct_id]
        return targets

    def capsule_to_injection(self, capsule_data: Dict[str, Any],
                              max_sessions: int = 50) -> Dict[str, Any]:
        sessions = capsule_data.get('sessions', [])
        summary = capsule_data.get('summary', {})

        recent_sessions = sessions[-max_sessions:] if len(sessions) > max_sessions else sessions

        injection_sessions = []
        for session in recent_sessions:
            injection_sessions.append({
                'entry_id': session.get('entry_id', ''),
                'estimated_date': session.get('estimated_date', ''),
                'vibe': session.get('vibe', 'neutral'),
                'topics': session.get('topics', []),
                'continuity_hooks': session.get('continuity_hooks', []),
                'exchange_count': session.get('exchange_count', 0),
                'source': session.get('source', 'unknown'),
                'first_exchange': session.get('first_exchange', ''),
                'last_exchange': session.get('last_exchange', ''),
            })

        continuity_hooks = []
        seen_hooks = set()
        for session in recent_sessions:
            for hook in session.get('continuity_hooks', []):
                hook_key = hook if isinstance(hook, str) else json.dumps(hook, sort_keys=True)
                if hook_key not in seen_hooks and len(continuity_hooks) < 30:
                    continuity_hooks.append(hook)
                    seen_hooks.add(hook_key)

        topic_freq = {}
        vibe_dist = {}
        for session in recent_sessions:
            for topic in session.get('topics', []):
                topic_freq[topic] = topic_freq.get(topic, 0) + 1
            vibe = session.get('vibe', 'neutral')
            vibe_dist[vibe] = vibe_dist.get(vibe, 0) + 1

        top_topics = sorted(topic_freq.items(), key=lambda x: -x[1])[:20]

        now = datetime.now(timezone.utc).isoformat()

        injection = {
            'schema': 'simdrive_injection',
            'version': '1.0.0',
            'construct_id': self.construct_id,
            'generated_at': now,
            'source': 'memup_capsule',
            'source_capsule_version': capsule_data.get('capsule_version', 'unknown'),
            'profile': {
                'total_sessions': summary.get('total_sessions', len(sessions)),
                'injected_sessions': len(injection_sessions),
                'date_range': summary.get('date_range', {}),
                'dominant_vibe': max(vibe_dist, key=vibe_dist.get) if vibe_dist else 'neutral',
                'vibe_distribution': vibe_dist,
                'top_topics': [{'topic': t, 'frequency': f} for t, f in top_topics],
                'sources': summary.get('sources', []),
            },
            'continuity_hooks': continuity_hooks,
            'sessions': injection_sessions,
            'injection_metadata': {
                'max_sessions': max_sessions,
                'truncated': len(sessions) > max_sessions,
                'original_session_count': len(sessions),
            },
        }

        return injection

    def build_manifest(self, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        entries = []

        for f in files:
            classified = self.classify_file(f.get('filename', ''), f.get('content', ''))
            entries.append({
                'filename': f.get('filename', ''),
                'simdrive_type': classified['simdrive_type'],
                'version': classified['version'],
                'file_id': f.get('id'),
                'sha256': f.get('sha256', ''),
                'updated_at': f.get('updated_at', f.get('created_at', now)),
            })

        type_counts = {}
        for e in entries:
            t = e['simdrive_type']
            type_counts[t] = type_counts.get(t, 0) + 1

        return {
            'schema': 'simdrive_manifest',
            'version': '1.0.0',
            'construct_id': self.construct_id,
            'generated_at': now,
            'total_files': len(entries),
            'type_distribution': type_counts,
            'files': entries,
        }

    def validate_injection(self, injection_data: Dict[str, Any]) -> Dict[str, Any]:
        errors = []
        warnings = []

        if injection_data.get('schema') != 'simdrive_injection':
            errors.append('Missing or invalid schema field (expected "simdrive_injection")')
        if not injection_data.get('construct_id'):
            errors.append('Missing construct_id')
        if injection_data.get('construct_id') != self.construct_id:
            errors.append(f'construct_id mismatch: expected {self.construct_id}')

        sessions = injection_data.get('sessions', [])
        if not sessions:
            warnings.append('No sessions in injection payload')

        hooks = injection_data.get('continuity_hooks', [])
        if not hooks:
            warnings.append('No continuity hooks — injection may not anchor properly')

        for i, session in enumerate(sessions):
            if not session.get('entry_id'):
                warnings.append(f'Session {i} missing entry_id')
            if not session.get('topics'):
                warnings.append(f'Session {i} has no topics')

        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'session_count': len(sessions),
            'hook_count': len(hooks),
        }
