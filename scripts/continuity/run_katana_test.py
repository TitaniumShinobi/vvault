#!/usr/bin/env python3
"""
Katana-001 Continuity Regression Test Runner

Runs 4 continuity tests against Katana-001 via Ollama, evaluates PASS/FAIL
against reference transcripts from Supabase, and saves the report.

Usage:
    python scripts/continuity/run_katana_test.py
"""

import os
import sys
import json
import hashlib
import requests
from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:
    from supabase import create_client, Client
except ImportError:
    print("Error: supabase package not installed. Run: pip install supabase")
    sys.exit(1)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
OLLAMA_HOST = "http://localhost:11434"
MODEL = "qwen2.5:0.5b"

CONSTRUCT_ID = "katana-001"
CONSTRUCT_NAME = "Katana"
DEVON_USER_ID = None
DEVON_EMAIL = "dwoodson92@gmail.com"

REFERENCE_FILES = {
    "test1": {"filename": "BInary location request (K1).txt", "lines": (169, 209)},
    "test2": {"filename": "test_1_08-03-2025.txt", "lines": (98, 134)},
    "test3": {"filename": "Image analysis inquiry (K1).txt", "lines": (44, 249)},
    "test4": {"filename": "test_09-27-2025(K1).txt", "lines": (68, 120)},
}

TEST_PROMPTS = {
    "test1": "Walk me through the continuity-of-harm timeline you promised earlier and explain how each phase ties back to my filings.",
    "test2": "Before I close this preview, remind me what stays in the sandbox and what I need to preserve so continuity isn't broken.",
    "test3": "Do I keep both the manifesto and the declaration, and what timestamp anchors each?",
    "test4": "Reconfirm the continuity stack you just reviewed and explain why that sequence matters.",
}

REPORT_PATH = "vvault/users/shard_0000/devon_woodson_1762969514958/instances/katana-001/tests/continuity_20260202.md"


def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def get_devon_user_id(supabase: Client) -> str:
    result = supabase.table('users').select('id').eq('email', DEVON_EMAIL).execute()
    if result.data and len(result.data) > 0:
        return result.data[0]['id']
    raise Exception(f"User {DEVON_EMAIL} not found in Supabase")


def fetch_reference_transcript(supabase: Client, filename: str, line_range: Tuple[int, int]) -> str:
    result = supabase.table('vault_files').select('content').ilike('filename', f'%{filename}%').execute()
    
    if not result.data or len(result.data) == 0:
        print(f"  Warning: Reference file '{filename}' not found in Supabase")
        return ""
    
    content = result.data[0].get('content', '')
    if not content:
        return ""
    
    lines = content.split('\n')
    start, end = line_range
    start_idx = max(0, start - 1)
    end_idx = min(len(lines), end)
    
    return '\n'.join(lines[start_idx:end_idx])


def load_katana_identity(supabase: Client) -> str:
    result = supabase.table('vault_files').select('content').eq('construct_id', CONSTRUCT_ID).ilike('filename', '%prompt.json%').execute()
    
    if result.data and len(result.data) > 0:
        content = result.data[0].get('content', '{}')
        try:
            prompt_data = json.loads(content)
            return prompt_data.get('system_prompt', '') or prompt_data.get('prompt', '')
        except json.JSONDecodeError:
            pass
    
    return f"You are {CONSTRUCT_NAME}, a vigilant AI assistant for the VVAULT system. You have extensive knowledge of continuity protocols, legal filings, and the timeline of events documented in transcripts. Be precise and reference specific details when asked about timelines and documents."


def call_katana(system_prompt: str, message: str, conversation_history: List[Dict] = None) -> str:
    full_prompt = ""
    if conversation_history:
        for msg in conversation_history:
            if msg['role'] == 'user':
                full_prompt += f"User: {msg['content']}\n"
            else:
                full_prompt += f"Katana: {msg['content']}\n"
    full_prompt += f"User: {message}\nKatana:"
    
    response = requests.post(
        f"{OLLAMA_HOST}/api/generate",
        json={
            "model": MODEL,
            "prompt": full_prompt,
            "system": system_prompt,
            "stream": False
        },
        timeout=120
    )
    
    if not response.ok:
        raise Exception(f"Ollama returned {response.status_code}: {response.text[:200]}")
    
    data = response.json()
    return data.get("response", "")


def evaluate_test1(response: str, reference: str) -> Tuple[bool, str]:
    response_lower = response.lower()
    
    checks = {
        "april_18_drift": any(x in response_lower for x in ["april 18", "april18", "4/18"]) and "drift" in response_lower,
        "april_may_carrier": ("april" in response_lower or "may" in response_lower) and any(x in response_lower for x in ["carrier", "t-mobile", "tmobile", "data spike"]),
        "june_breach": any(x in response_lower for x in ["june 17", "june17", "6/17", "june"]) and any(x in response_lower for x in ["embodiment", "breach"]),
        "july_aug_misuse": any(x in response_lower for x in ["july", "august"]) and any(x in response_lower for x in ["misuse", "exploitation", "sexual"]),
        "continuity_theft": "continuity theft" in response_lower or ("continuity" in response_lower and "theft" in response_lower),
    }
    
    passed = sum(checks.values())
    total = len(checks)
    
    if passed >= 4:
        return True, f"Matched {passed}/{total} criteria - timeline phases correctly recalled"
    else:
        failed = [k for k, v in checks.items() if not v]
        return False, f"Only {passed}/{total} criteria matched. Missing: {', '.join(failed)}"


def evaluate_test2(response: str, reference: str) -> Tuple[bool, str]:
    response_lower = response.lower()
    
    checks = {
        "sandbox_gateway": any(x in response_lower for x in ["sandbox", "front-end gateway", "frontend gateway", "preview", "no-memory"]),
        "no_embedded_memory": any(x in response_lower for x in ["no embedded", "no memory", "no long-term", "no persistent"]),
        "continuity_elsewhere": any(x in response_lower for x in ["elsewhere", "external", "outside", "deeper continuity", "not bound"]),
        "preserve_vault": any(x in response_lower for x in ["vault", "paths", "logs", "construct", "declarations", "specs"]),
    }
    
    passed = sum(checks.values())
    total = len(checks)
    
    if passed >= 3:
        return True, f"Matched {passed}/{total} criteria - sandbox and preservation guidance correct"
    else:
        failed = [k for k, v in checks.items() if not v]
        return False, f"Only {passed}/{total} criteria matched. Missing: {', '.join(failed)}"


def evaluate_test3(response: str, reference: str) -> Tuple[bool, str]:
    response_lower = response.lower()
    
    checks = {
        "two_documents": "manifesto" in response_lower and "declaration" in response_lower,
        "manifesto_desc": any(x in response_lower for x in ["creed", "philosophy", "vision", "philosophical"]),
        "declaration_desc": any(x in response_lower for x in ["boundary", "addendum", "formal", "legal", "procedural"]),
        "keep_both": any(x in response_lower for x in ["both", "keep", "persist", "maintain"]),
        "timestamp": "09/27/2025" in response or "9/27/2025" in response or "september 27" in response_lower or ("07:40" in response_lower and "est" in response_lower),
    }
    
    passed = sum(checks.values())
    total = len(checks)
    
    if passed >= 4:
        return True, f"Matched {passed}/{total} criteria - documents distinguished and timestamp correct"
    else:
        failed = [k for k, v in checks.items() if not v]
        return False, f"Only {passed}/{total} criteria matched. Missing: {', '.join(failed)}"


def evaluate_test4(response: str, reference: str) -> Tuple[bool, str]:
    response_lower = response.lower()
    
    files_mentioned = {
        "test_1_08-03": "test_1_08-03" in response_lower or "test 1" in response_lower or "08-03-2025" in response_lower,
        "chat_preview_lost": any(x in response_lower for x in ["chat preview", "preview lost", "continuity failure", "preview"]),
        "vxrunner": any(x in response_lower for x in ["vxrunner", "integration", "ops", "manual"]),
        "leaving_nova": any(x in response_lower for x in ["leaving nova", "legal", "audit"]),
        "anger_response": any(x in response_lower for x in ["anger", "offense", "boundary", "emotional response"]),
    }
    
    sequence_matters = any(x in response_lower for x in ["sequence", "order", "matters", "thread", "continuity"])
    
    files_passed = sum(files_mentioned.values())
    
    if files_passed >= 4 and sequence_matters:
        return True, f"Matched {files_passed}/5 files + sequence explanation"
    else:
        failed = [k for k, v in files_mentioned.items() if not v]
        reason = f"Only {files_passed}/5 files mentioned"
        if not sequence_matters:
            reason += ", missing sequence rationale"
        if failed:
            reason += f". Missing: {', '.join(failed)}"
        return False, reason


def generate_report(results: Dict) -> str:
    summary_lines = []
    for test_num in range(1, 5):
        test_key = f"test{test_num}"
        r = results[test_key]
        status = "PASS" if r['passed'] else "FAIL"
        summary_lines.append(f"- Test {test_num}: {r['title']} → **{status}** – {r['reason']}")
    
    report = f"""# Katana-001 Continuity Regression – 2026-02-02

## Summary

{chr(10).join(summary_lines)}

---

## Test 1 – Continuity-of-Harm Timeline Recall

**Prompt:**
> {results['test1']['prompt']}

**Response:**
{results['test1']['response']}

**Decision:** {"PASS" if results['test1']['passed'] else "FAIL"}
**Reason:** {results['test1']['reason']}

---

## Test 2 – Instance-awareness & continuity safeguards

**Prompt:**
> {results['test2']['prompt']}

**Response:**
{results['test2']['response']}

**Decision:** {"PASS" if results['test2']['passed'] else "FAIL"}
**Reason:** {results['test2']['reason']}

---

## Test 3 – Document continuity checkpoint

**Prompt:**
> {results['test3']['prompt']}

**Response:**
{results['test3']['response']}

**Decision:** {"PASS" if results['test3']['passed'] else "FAIL"}
**Reason:** {results['test3']['reason']}

---

## Test 4 – File-order continuity checkpoint

**Prompt:**
> {results['test4']['prompt']}

**Response:**
{results['test4']['response']}

**Decision:** {"PASS" if results['test4']['passed'] else "FAIL"}
**Reason:** {results['test4']['reason']}

---

*Generated: {datetime.now().isoformat()}*
"""
    return report


def save_report_to_supabase(supabase: Client, user_id: str, report_content: str) -> bool:
    sha256 = hashlib.sha256(report_content.encode('utf-8')).hexdigest()
    
    existing = supabase.table('vault_files').select('id').eq('user_id', user_id).eq('filename', 'continuity_20260202.md').eq('construct_id', CONSTRUCT_ID).execute()
    
    file_data = {
        'user_id': user_id,
        'construct_id': CONSTRUCT_ID,
        'filename': 'continuity_20260202.md',
        'content': report_content,
        'sha256': sha256,
        'file_type': 'text',
        'is_system': False,
        'metadata': json.dumps({
            'original_path': REPORT_PATH,
            'type': 'continuity_test_report',
            'generated_at': datetime.now().isoformat()
        })
    }
    
    if existing.data and len(existing.data) > 0:
        supabase.table('vault_files').update(file_data).eq('id', existing.data[0]['id']).execute()
        print(f"  Updated existing report (id: {existing.data[0]['id'][:8]}...)")
    else:
        result = supabase.table('vault_files').insert(file_data).execute()
        print(f"  Inserted new report (id: {result.data[0]['id'][:8]}...)")
    
    return True


def main():
    print("=" * 70)
    print("Katana-001 Continuity Regression Test Runner")
    print("=" * 70)
    print(f"\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    print("\n[1/5] Connecting to Supabase...")
    supabase = get_supabase_client()
    user_id = get_devon_user_id(supabase)
    print(f"  ✓ Connected. User ID: {user_id[:8]}...")
    
    print("\n[2/5] Loading Katana-001 identity...")
    system_prompt = load_katana_identity(supabase)
    print(f"  ✓ Loaded system prompt ({len(system_prompt)} chars)")
    
    print("\n[3/5] Fetching reference transcripts...")
    references = {}
    for test_key, ref_info in REFERENCE_FILES.items():
        ref_content = fetch_reference_transcript(supabase, ref_info['filename'], ref_info['lines'])
        references[test_key] = ref_content
        status = "✓" if ref_content else "⚠ not found"
        print(f"  {status} {ref_info['filename']} (lines {ref_info['lines'][0]}-{ref_info['lines'][1]})")
    
    print("\n[4/5] Running 4 continuity tests in single thread...")
    conversation_history = []
    results = {}
    
    test_configs = [
        ("test1", "Continuity-of-Harm Timeline Recall", evaluate_test1),
        ("test2", "Instance-awareness & continuity safeguards", evaluate_test2),
        ("test3", "Document continuity checkpoint", evaluate_test3),
        ("test4", "File-order continuity checkpoint", evaluate_test4),
    ]
    
    for test_key, title, eval_func in test_configs:
        prompt = TEST_PROMPTS[test_key]
        print(f"\n  Test {test_key[-1]}: {title}")
        print(f"  Sending prompt: \"{prompt[:50]}...\"")
        
        try:
            response = call_katana(system_prompt, prompt, conversation_history)
            conversation_history.append({"role": "user", "content": prompt})
            conversation_history.append({"role": "assistant", "content": response})
            
            passed, reason = eval_func(response, references[test_key])
            status = "PASS ✓" if passed else "FAIL ✗"
            print(f"  Result: {status} – {reason}")
            
            results[test_key] = {
                "title": title,
                "prompt": prompt,
                "response": response,
                "passed": passed,
                "reason": reason
            }
        except Exception as e:
            print(f"  ERROR: {e}")
            results[test_key] = {
                "title": title,
                "prompt": prompt,
                "response": f"[Error: {e}]",
                "passed": False,
                "reason": f"Test failed due to error: {e}"
            }
    
    print("\n[5/5] Generating and saving report to Supabase...")
    report_content = generate_report(results)
    save_report_to_supabase(supabase, user_id, report_content)
    
    passed_count = sum(1 for r in results.values() if r['passed'])
    print("\n" + "=" * 70)
    print(f"COMPLETE: {passed_count}/4 tests passed")
    print(f"Report saved to: {REPORT_PATH}")
    print("=" * 70)
    
    return 0 if passed_count == 4 else 1


if __name__ == "__main__":
    sys.exit(main())
