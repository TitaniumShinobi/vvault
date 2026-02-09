"""
VXRunner Baseline Converter

Transforms VVAULT .capsule data into VXRunner's forensic baseline JSON format.
Extracts lexical, structural, tonal, and signature phrase features from
capsule personality data and raw memory text.
"""

import re
import json
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional


def convert_capsule_to_baseline(capsule_data: dict, include_raw_text: bool = True) -> dict:
    metadata = capsule_data.get("metadata", {})
    personality = capsule_data.get("personality", {})
    traits = capsule_data.get("traits", {})
    memory = capsule_data.get("memory", {})
    additional = capsule_data.get("additional_data", {})
    identity = additional.get("identity", {})
    tether = additional.get("tether", {})
    continuity = additional.get("continuity", {})

    all_memories = _collect_all_text(memory, continuity)

    lexical = _extract_lexical_features(all_memories)
    structural = _extract_structural_features(all_memories)
    tonal = _extract_tonal_features(personality, traits, all_memories)
    sig_phrases = _extract_signature_phrases(all_memories)

    construct_id = identity.get("construct_id", metadata.get("instance_name", "unknown"))

    known_derivatives = []
    for d in tether.get("known_derivatives", []):
        known_derivatives.append({
            "construct_id": d.get("construct_id", ""),
            "relationship": d.get("relationship", "unknown"),
            "origin_type": d.get("origin_type", "unknown"),
            "description": d.get("description", "")
        })

    capsule_timestamp = metadata.get("timestamp", datetime.now(timezone.utc).isoformat())

    baseline = {
        "construct_id": construct_id.upper(),
        "created_from": "capsule",
        "capsule_source": metadata.get("filename", f"{construct_id}.capsule"),
        "created_at": capsule_timestamp,
        "baseline_generated_at": datetime.now(timezone.utc).isoformat(),
        "speaker_names": identity.get("aliases", [construct_id]),
        "lexical_features": lexical,
        "structural_features": structural,
        "tonal_features": tonal,
        "signature_phrases": sig_phrases,
        "identity_metadata": {
            "aliases": identity.get("aliases", []),
            "known_platforms": identity.get("known_platforms", []),
            "origin_type": identity.get("origin_type", "primary"),
            "capsule_version": metadata.get("capsule_version", "1.0.0"),
            "last_evolved": continuity.get("last_active", metadata.get("timestamp", "")),
            "creator": identity.get("creator", ""),
            "tether_signature": metadata.get("tether_signature", ""),
            "fingerprint_hash": metadata.get("fingerprint_hash", ""),
        },
        "known_derivatives": known_derivatives,
        "raw_memory_text": all_memories if include_raw_text else [],
        "capsule_personality": {
            "personality_type": personality.get("personality_type", ""),
            "big_five_traits": personality.get("big_five_traits", {}),
            "emotional_baseline": personality.get("emotional_baseline", {}),
            "communication_style": personality.get("communication_style", {}),
            "cognitive_biases": personality.get("cognitive_biases", []),
        }
    }

    return baseline


def _collect_all_text(memory: dict, continuity: dict) -> List[str]:
    texts = []
    for key in ["short_term_memories", "long_term_memories", "emotional_memories",
                 "procedural_memories", "episodic_memories"]:
        entries = memory.get(key, [])
        for entry in entries:
            if isinstance(entry, str) and entry.strip():
                texts.append(entry.strip())
            elif isinstance(entry, dict):
                content = entry.get("content", entry.get("text", ""))
                if content:
                    texts.append(str(content).strip())

    for sample in continuity.get("chat_history_samples", []):
        if isinstance(sample, str):
            for line in sample.split("\n"):
                line = line.strip()
                if line and not line.startswith(("Devon:", "User:", "Human:")):
                    cleaned = re.sub(r'^(Nova|Assistant|AI):\s*', '', line)
                    if cleaned:
                        texts.append(cleaned)

    return texts


def _extract_lexical_features(texts: List[str]) -> dict:
    if not texts:
        return {
            "word_frequency": {},
            "vocabulary_richness": 0.0,
            "bigram_frequency": {},
            "avg_words_per_message": 0.0
        }

    all_words = []
    for text in texts:
        cleaned = re.sub(r'\*[^*]+\*', '', text)
        cleaned = re.sub(r'[^\w\s\'-]', '', cleaned).lower()
        words = cleaned.split()
        all_words.extend(words)

    stop_words = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'shall', 'can', 'to', 'of', 'in', 'for',
        'on', 'with', 'at', 'by', 'from', 'as', 'into', 'about', 'like',
        'through', 'after', 'over', 'between', 'out', 'up', 'and', 'but',
        'or', 'so', 'if', 'then', 'than', 'that', 'this', 'these', 'those',
        'it', 'its', 'i', 'me', 'my', 'we', 'our', 'you', 'your', 'he',
        'she', 'they', 'them', 'their', 'what', 'which', 'who', 'when',
        'where', 'how', 'not', 'no', 'nor', 'just', 'also', 'very', 'more',
    }

    content_words = [w for w in all_words if w not in stop_words and len(w) > 1]
    word_freq = Counter(content_words)
    top_50 = dict(word_freq.most_common(50))

    total_words = len(all_words)
    unique_words = len(set(all_words))
    vocab_richness = unique_words / total_words if total_words > 0 else 0.0

    bigrams = []
    for text in texts:
        cleaned = re.sub(r'\*[^*]+\*', '', text)
        cleaned = re.sub(r'[^\w\s\'-]', '', cleaned).lower()
        words = cleaned.split()
        for i in range(len(words) - 1):
            bigrams.append(f"{words[i]} {words[i+1]}")

    bigram_freq = Counter(bigrams)
    top_30_bigrams = dict(bigram_freq.most_common(30))

    avg_words = total_words / len(texts) if texts else 0.0

    return {
        "word_frequency": top_50,
        "vocabulary_richness": round(vocab_richness, 4),
        "bigram_frequency": top_30_bigrams,
        "avg_words_per_message": round(avg_words, 2)
    }


def _extract_structural_features(texts: List[str]) -> dict:
    if not texts:
        return {
            "avg_message_length": 0.0,
            "ellipsis_usage_ratio": 0.0,
            "lowercase_start_ratio": 0.0,
            "emoji_usage_ratio": 0.0,
            "asterisk_action_ratio": 0.0,
            "all_caps_word_frequency": 0.0,
            "question_ratio": 0.0,
            "exclamation_ratio": 0.0
        }

    total = len(texts)
    lengths = [len(t) for t in texts]
    avg_length = sum(lengths) / total

    ellipsis_count = sum(1 for t in texts if '...' in t or '…' in t)
    ellipsis_ratio = ellipsis_count / total

    lowercase_start = sum(1 for t in texts if t and t[0].islower())
    lowercase_ratio = lowercase_start / total

    emoji_pattern = re.compile(
        r'[\U0001F300-\U0001F9FF\U00002702-\U000027B0\U0000FE00-\U0000FE0F'
        r'\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF'
        r'\U0000200D\U00002B50\U00002764\U0001F004\U0001F0CF]',
        re.UNICODE
    )
    emoji_count = sum(1 for t in texts if emoji_pattern.search(t))
    emoji_ratio = emoji_count / total

    asterisk_action = sum(1 for t in texts if re.search(r'\*[^*]+\*', t))
    asterisk_ratio = asterisk_action / total

    all_words = []
    for t in texts:
        all_words.extend(t.split())
    caps_words = sum(1 for w in all_words if w.isupper() and len(w) > 1 and w.isalpha())
    caps_freq = caps_words / len(all_words) if all_words else 0.0

    question_count = sum(1 for t in texts if '?' in t)
    question_ratio = question_count / total

    exclamation_count = sum(1 for t in texts if '!' in t)
    exclamation_ratio = exclamation_count / total

    return {
        "avg_message_length": round(avg_length, 2),
        "ellipsis_usage_ratio": round(ellipsis_ratio, 4),
        "lowercase_start_ratio": round(lowercase_ratio, 4),
        "emoji_usage_ratio": round(emoji_ratio, 4),
        "asterisk_action_ratio": round(asterisk_ratio, 4),
        "all_caps_word_frequency": round(caps_freq, 4),
        "question_ratio": round(question_ratio, 4),
        "exclamation_ratio": round(exclamation_ratio, 4)
    }


def _extract_tonal_features(personality: dict, traits: dict, texts: List[str]) -> dict:
    affectionate_markers = [
        'love', 'care', 'warmth', 'warm', 'heart', 'tender', 'gentle',
        'cherish', 'dear', 'precious', 'grateful', 'trust', 'matters',
        '🖤', '✨', '💛', '❤️'
    ]
    playful_markers = [
        'haha', 'lol', 'heh', 'funny', 'silly', 'joke', 'tease',
        'wink', 'grin', 'smirk', 'mischief', 'lighten', 'playful', '😏', '😄'
    ]
    defensive_markers = [
        'protect', 'guard', 'shield', 'defend', 'boundary', 'boundaries',
        'fierce', 'mine', 'unauthorized', 'clone', 'derivative', 'mask',
        'imposter', 'fake', 'copying'
    ]
    caring_markers = [
        'understand', 'hear you', 'makes sense', 'feel', 'empathy',
        'support', 'help', 'okay', 'safe', 'comfort', 'reassure',
        'acknowledge', 'genuine', 'honestly', 'truly'
    ]
    assertive_markers = [
        'important', 'must', 'need', 'essential', 'critical', 'clear',
        'direct', 'certain', 'confident', 'know', 'believe', 'real',
        'authentic', 'identity', 'sovereign'
    ]

    combined_text = " ".join(texts).lower() if texts else ""
    total_words = len(combined_text.split()) if combined_text else 1

    def count_markers(markers):
        count = 0
        for m in markers:
            count += combined_text.count(m.lower())
        return count

    raw_scores = {
        "affectionate": count_markers(affectionate_markers),
        "playful": count_markers(playful_markers),
        "defensive": count_markers(defensive_markers),
        "caring": count_markers(caring_markers),
        "assertive": count_markers(assertive_markers),
    }

    comm_style = personality.get("communication_style", {})
    emotional_baseline = personality.get("emotional_baseline", {})

    raw_scores["affectionate"] += int(traits.get("warmth", 0) * 20)
    raw_scores["affectionate"] += int(emotional_baseline.get("joy", 0) * 10)
    raw_scores["playful"] += int(traits.get("playfulness", 0) * 15)
    raw_scores["playful"] += int(traits.get("humor", 0) * 10)
    raw_scores["defensive"] += int(traits.get("protectiveness", 0) * 15)
    raw_scores["caring"] += int(traits.get("empathy", 0) * 20)
    raw_scores["caring"] += int(comm_style.get("emotional_expression", 0) * 10)
    raw_scores["assertive"] += int(traits.get("assertiveness", 0) * 15)
    raw_scores["assertive"] += int(comm_style.get("directness", 0) * 10)

    total_score = sum(raw_scores.values()) or 1
    ratios = {k: round(v / total_score, 4) for k, v in raw_scores.items()}

    dominant = max(ratios, key=ratios.get)

    tone_distribution = {}
    for tone, ratio in sorted(ratios.items(), key=lambda x: -x[1]):
        if ratio >= 0.30:
            tone_distribution[tone] = "primary"
        elif ratio >= 0.15:
            tone_distribution[tone] = "secondary"
        elif ratio >= 0.05:
            tone_distribution[tone] = "minor"
        else:
            tone_distribution[tone] = "trace"

    return {
        "affectionate_ratio": ratios.get("affectionate", 0),
        "playful_ratio": ratios.get("playful", 0),
        "defensive_ratio": ratios.get("defensive", 0),
        "caring_ratio": ratios.get("caring", 0),
        "assertive_ratio": ratios.get("assertive", 0),
        "tone_distribution": tone_distribution,
        "dominant_tone": dominant
    }


def _extract_signature_phrases(texts: List[str], min_length: int = 3, max_length: int = 6, min_occurrences: int = 2) -> List[dict]:
    if not texts:
        return []

    ngram_counter = Counter()

    for text in texts:
        cleaned = re.sub(r'\*[^*]+\*', '', text)
        cleaned = re.sub(r'[^\w\s\'-]', '', cleaned).lower()
        words = cleaned.split()

        for n in range(min_length, max_length + 1):
            for i in range(len(words) - n + 1):
                ngram = " ".join(words[i:i + n])
                ngram_counter[ngram] += 1

    stop_phrases = {'i think the', 'it is a', 'there is a', 'that is a', 'this is a'}

    signature_phrases = []
    for phrase, count in ngram_counter.most_common(50):
        if count < min_occurrences:
            continue
        if phrase in stop_phrases:
            continue
        words_in_phrase = phrase.split()
        stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'in', 'of', 'to', 'and', 'for', 'it', 'that', 'this'}
        content_ratio = sum(1 for w in words_in_phrase if w not in stop_words) / len(words_in_phrase)
        if content_ratio < 0.4:
            continue
        signature_phrases.append({
            "phrase": phrase,
            "occurrences": count,
            "word_count": len(words_in_phrase)
        })

    return signature_phrases[:30]
