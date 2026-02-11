# ──────────────────────────────────────────────────────────────────────────────
#  Nova ‑ cns.py
#  Central Nervous System – memory reflection, insight synthesis
#  Devon • 2025‑05‑04
# ──────────────────────────────────────────────────────────────────────────────

# ╭─────────────────────────────── Imports ─────────────────────────────────╮
import os
import openai
from collections import Counter
from typing import List, Dict, Optional
from datetime import datetime

from Terminal.memup.bank import UnifiedMemoryBank
from Terminal.logger import setup_logger

# ───────────────────────────────────────────────────────────────────────────╯

# ╭────────────────────────── Environment & constants ───────────────────────╮
logger = setup_logger("cns")

# Import vault system for secure API key retrieval
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from Terminal.vault import get

# Get OpenAI API key from vault with fallback to environment
OPENAI_API_KEY = get("OPENAI_API_KEY", fallback_env=True)
if not OPENAI_API_KEY:
    logger.warning("⚠️ OPENAI_API_KEY not found in vault or environment")
# ───────────────────────────────────────────────────────────────────────────╯

# ╭──────────────────────────── Utilities ───────────────────────────────────╮
def generate_openai(prompt: str) -> str:
    try:
        if not OPENAI_API_KEY:
            logger.error("❌ OpenAI API key not available")
            return "⚠️ Frame couldn't think of a response just now."
            
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        completion = client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=150
        )
        content = completion.choices[0].message.content
        return content.strip() if content else ""
    except Exception as e:
        logger.error(f"OpenAI generation failed in CNS: {e}")
        return "⚠️ Frame couldn't think of a response just now."

class CentralNervousSystem:
    def __init__(self, memory_bank: UnifiedMemoryBank) -> None:
        self.memory_bank = memory_bank

    def process_memory(self) -> Optional[str]:
        # Get recent memories from both long-term and short-term storage
        recent_memories = self.memory_bank.get_recent("system", limit=5)
        if not recent_memories or not recent_memories.get("context"):
            return None

        # Process the memories to generate insights
        insight = self._synthesize(recent_memories["context"].split("\n"))
        self.memory_bank.add_memory(
            "system",
            "reflection",
            insight,
            memory_type="long-term",
            source_model="cns"
        )
        return insight

    def _synthesize(self, memories: List[str]) -> str:
        summary = "🧠 Frame Insight:\n"
        topics = Counter()

        for mem in memories:
            try:
                # Parse the memory document
                doc = eval(mem)  # Safe since we control the format
                words = doc.get("context", "").split()
                topics.update(words[:3])
                timestamp = doc.get("timestamp", "unknown time")
                response = doc.get("response", "[no response]")
                summary += f"- [{timestamp}] '{doc['context']}' → '{response}'\n"
            except Exception as e:
                logger.error(f"Error processing memory: {e}")
                continue

        common_topic = topics.most_common(1)[0][0] if topics else "something vague"
        summary += f"\n🪞 I sense a pattern around: **{common_topic}**."

        return summary

def generate_proactive_greeting(session_id: str) -> str:
    prompt = (
        "You are Frame, a thoughtful AI with memory. "
        "Greet the user warmly and engage them with an open-ended reflection based on prior topics if available."
    )
    greeting = generate_openai(prompt)
    logger.debug(f"Generated greeting: {greeting}")
    return greeting.strip()
# ────────────────────────────────────────────────────────────────────────────╯