"""
frame Context Management System
Consolidated per-channel conversation context tracker with persistence capabilities.
"""

from datetime import datetime, timezone
from collections import defaultdict
from typing import Dict, Any, Optional, List
import logging
import random

logger = logging.getLogger(__name__)

# Import bank but handle missing remember_context gracefully
try:
    from . import bank

    def remember_context(chan_id: str, ctx: Dict[str, Any]) -> None:
        """Optional context persistence - no-op if not implemented"""
        if hasattr(bank, 'remember_context'):
            bank.remember_context(chan_id, ctx)
except ImportError:

    def remember_context(chan_id: str, ctx: Dict[str, Any]) -> None:
        """No-op fallback"""
        pass


class ContextTracker:
    """
    Comprehensive context tracker for per-channel conversation state.
    Combines lightweight RAM storage with optional persistence.
    """
    # Cooldowns and limits
    greeting_cooldown_s = 300  # 5 min
    max_message_history = 10
    mention_frequency = 3      # mention user every N turns
    
    def __init__(self):
        self._ctx: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "last_user_name": None,
            "last_user_message_ts": None,
            "last_reply_ts": None,
            "last_greet_ts": None,
            "last_topic": None,
            "turn_counter": 0,
            "user_mention_count": 0,
            "message_history": [],
        })
        self._greetings_by_part = {
            "late_night": [
                "Up late? I'm here.",
                "It's a quiet hour—what's on your mind?",
                "Midnight crew check-in. What do you need?",
            ],
            "morning": [
                "Good morning.",
                "Morning—what's first today?",
                "Let's set the tone for the day.",
            ],
            "afternoon": [
                "Good afternoon.",
                "Afternoon—what's next?",
                "Back at it—where were we?",
            ],
            "evening": [
                "Good evening.",
                "Evening—want to wrap or push?",
                "I'm here for the night shift.",
            ],
            "late_evening": [
                "Late run—how can I help?",
                "Long day—what do you want to finish?",
                "Night mode engaged. What's the target?",
            ],
            "any": [
                "Hello.",
                "Hey—what's on your mind?",
                "I'm here. What do you need?",
            ],
        }
    
    # --- Public API Methods ---
    
    def seen_user(self, chan_id: str, user_name: Optional[str], msg_text: str) -> None:
        """
        Update context when user sends a message.
        
        Args:
            chan_id: Channel identifier
            user_name: User's display name
            msg_text: Message content
        """
        ctx = self._ctx[chan_id]
        ctx["last_user_name"] = user_name or ctx["last_user_name"]
        ctx["last_user_message_ts"] = datetime.now(timezone.utc)
        ctx["last_topic"] = self._summarise(msg_text)
        ctx["turn_counter"] = (ctx["turn_counter"] or 0) + 1
        
        # Add to message history
        ctx["message_history"].append(msg_text)
        if len(ctx["message_history"]) > self.max_message_history:
            ctx["message_history"] = ctx["message_history"][-self.max_message_history:]
        
        remember_context(chan_id, ctx)
        logger.debug(f"Updated context for channel {chan_id}, user: {user_name}")
    
    def _daypart(self, dt: Optional[datetime] = None) -> str:
        """Return one of: late_night, morning, afternoon, evening, late_evening."""
        if dt is None:
            dt = datetime.now(timezone.utc)
        # Convert to local time for hour calculation
        local_dt = dt.astimezone()
        h = local_dt.hour
        if 0 <= h < 5:    return "late_night"
        if 5 <= h < 12:   return "morning"
        if 12 <= h < 18:  return "afternoon"
        if 18 <= h < 22:  return "evening"
        return "late_evening"

    def _time_aware_greeting(self, dt: Optional[datetime] = None) -> str:
        part = self._daypart(dt)
        pool = self._greetings_by_part.get(part) or self._greetings_by_part["any"]
        return random.choice(pool)
    
    def should_greet(self, chan_id: str, now: Optional[datetime] = None) -> bool:
        """
        Determine if the construct should include a greeting.
        
        Returns:
            True if greeting should be included (respects cooldown and first turn)
        """
        ctx = self._ctx[chan_id]
        now = now or datetime.now(timezone.utc)
        # Always greet on first visible turn
        if ctx.get("turn_counter", 0) <= 0:
            return True
        # Cooldown check
        lg = ctx.get("last_greet_ts")
        if not lg:
            return True
        elapsed = (now - lg).total_seconds()
        return elapsed >= self.greeting_cooldown_s
    
    def mark_replied(self, chan_id: str) -> None:
        """
        Update context when the construct sends a reply.
        
        Args:
            chan_id: Channel identifier
        """
        self._ctx[chan_id]["last_reply_ts"] = datetime.now(timezone.utc)
        remember_context(chan_id, self._ctx[chan_id])
    
    def get_name(self, chan_id: str) -> Optional[str]:
        """
        Get the user's name for this channel.
        
        Args:
            chan_id: Channel identifier
            
        Returns:
            User's display name or None
        """
        return self._ctx[chan_id]["last_user_name"]
    
    def can_mention_user(self, chan_id: str) -> bool:
        """
        Determine if the construct should mention the user by name.
        
        Args:
            chan_id: Channel identifier
            
        Returns:
            True if user should be mentioned
        """
        ctx = self._ctx[chan_id]
        mention_count = ctx.get("user_mention_count", 0)
        return mention_count == 0 or mention_count % self.mention_frequency == 0
    
    def increment_mention_count(self, chan_id: str) -> None:
        """
        Increment the user mention counter.
        
        Args:
            chan_id: Channel identifier
        """
        ctx = self._ctx[chan_id]
        ctx["user_mention_count"] = ctx.get("user_mention_count", 0) + 1
        remember_context(chan_id, ctx)
    
    def get_greeting(self, chan_id: str, now: Optional[datetime] = None) -> str:
        """
        Return a time-aware greeting. May include user's name based on policy.
        Also updates last_greet_ts when emitted.
        """
        now = now or datetime.now(timezone.utc)
        if not self.should_greet(chan_id, now=now):
            return ""
        
        ctx = self._ctx[chan_id]
        name = ctx.get("last_user_name")
        base = self._time_aware_greeting(now)
        if name and self.can_mention_user(chan_id):
            base = f"{base} {name}."
        
        # mark greet IMMEDIATELY to prevent duplicate greetings
        ctx["last_greet_ts"] = now
        remember_context(chan_id, ctx)
        return base
    
    def update_topic(self, chan_id: str, topic: str) -> None:
        """
        Update the current conversation topic.
        
        Args:
            chan_id: Channel identifier
            topic: Topic description
        """
        self._ctx[chan_id]["last_topic"] = topic
        remember_context(chan_id, self._ctx[chan_id])
    
    def get_message_history(self, chan_id: str) -> List[str]:
        """
        Get recent message history for a channel.
        
        Args:
            chan_id: Channel identifier
            
        Returns:
            List of recent messages (copy to prevent modification)
        """
        return self._ctx[chan_id].get("message_history", []).copy()
    
    def get_topic(self, chan_id: str) -> Optional[str]:
        """
        Get the current conversation topic.
        
        Args:
            chan_id: Channel identifier
            
        Returns:
            Current topic or None
        """
        return self._ctx[chan_id].get("last_topic")
    
    def get_turn_counter(self, chan_id: str) -> int:
        """
        Get the conversation turn counter.
        
        Args:
            chan_id: Channel identifier
            
        Returns:
            Number of turns in conversation
        """
        return self._ctx[chan_id].get("turn_counter", 0)
    
    def get_context_summary(self, chan_id: str) -> Dict[str, Any]:
        """
        Get a comprehensive context summary for debugging.
        
        Args:
            chan_id: Channel identifier
            
        Returns:
            Dictionary with context information
        """
        ctx = self._ctx[chan_id]
        return {
            "last_user_name": ctx.get("last_user_name"),
            "should_greet": self.should_greet(chan_id),
            "can_mention_user": self.can_mention_user(chan_id),
            "user_mention_count": ctx.get("user_mention_count", 0),
            "message_history_length": len(ctx.get("message_history", [])),
            "last_topic": ctx.get("last_topic"),
            "turn_counter": ctx.get("turn_counter", 0),
            "last_user_message_ts": ctx.get("last_user_message_ts"),
            "last_reply_ts": ctx.get("last_reply_ts"),
        }
    
    def clear_context(self, chan_id: str) -> None:
        """
        Clear all context for a channel (useful for testing or cleanup).
        
        Args:
            chan_id: Channel identifier
        """
        if chan_id in self._ctx:
            del self._ctx[chan_id]
            logger.debug(f"Cleared context for channel {chan_id}")
    
    def get_all_channels(self) -> List[str]:
        """
        Get list of all channels with active context.
        
        Returns:
            List of channel IDs
        """
        return list(self._ctx.keys())
    
    # --- Internal Methods ---
    
    def _summarise(self, txt: str) -> str:
        """
        Create a minimalist topic summary from text.
        
        Args:
            txt: Input text
            
        Returns:
            Summarized topic string
        """
        return txt[:120].strip().lower()


# Global singleton instance
context_tracker = ContextTracker()


# --- Legacy Compatibility Functions ---
# These maintain compatibility with existing code that might use the old API

def get_context(channel_id: str) -> ContextTracker:
    """Legacy function for backward compatibility."""
    return context_tracker

def update_user_message(channel_id: str, user_name: str, message: str, timestamp: datetime) -> None:
    """Legacy function for backward compatibility."""
    context_tracker.seen_user(channel_id, user_name, message)

def update_reply(channel_id: str, timestamp: datetime) -> None:
    """Legacy function for backward compatibility."""
    context_tracker.mark_replied(channel_id)

def should_greet(channel_id: str) -> bool:
    """Legacy function for backward compatibility."""
    return context_tracker.should_greet(channel_id)

def can_mention_user(channel_id: str) -> bool:
    """Legacy function for backward compatibility."""
    return context_tracker.can_mention_user(channel_id)

def get_user_name(channel_id: str) -> Optional[str]:
    """Legacy function for backward compatibility."""
    return context_tracker.get_name(channel_id)

def increment_mention_count(channel_id: str) -> None:
    """Legacy function for backward compatibility."""
    context_tracker.increment_mention_count(channel_id)

def get_greeting(channel_id: str) -> str:
    """Legacy function for backward compatibility."""
    return context_tracker.get_greeting(channel_id)

def update_topic(channel_id: str, topic: str) -> None:
    """Legacy function for backward compatibility."""
    context_tracker.update_topic(channel_id, topic)

def get_message_history(channel_id: str) -> List[str]:
    """Legacy function for backward compatibility."""
    return context_tracker.get_message_history(channel_id)
