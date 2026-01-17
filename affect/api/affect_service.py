"""
Affect Service

Backend service layer implementing update logic, memory-weighted algorithm,
and governance checks for affective state updates.
"""

import math
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from pathlib import Path

from ..models.affective_state import AffectiveState, UserSignal, StateHistoryEntry
from ..storage import AffectiveStateStore, StateHistoryManager, AuditLogger


class UpdateGovernor:
    """Governance rules for state updates"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize governor with configuration
        
        Args:
            config: Configuration dictionary with limits and bounds
        """
        self.config = config or self._default_config()
    
    def _default_config(self) -> Dict[str, Any]:
        """Default governance configuration"""
        return {
            "max_valence": 1.0,
            "min_valence": -1.0,
            "max_arousal": 1.0,
            "min_arousal": -1.0,
            "max_delta_per_update": 0.3,  # Maximum change per update
            "cooldown_seconds": 5,  # Cooldown between rapid updates
            "escalation_threshold": 0.8,  # Valence/arousal level that triggers escalation
            "max_escalation_level": 3
        }
    
    def can_update(
        self,
        current_state: AffectiveState,
        proposed_state: AffectiveState,
        last_update_time: Optional[datetime] = None
    ) -> tuple[bool, str, Dict[str, Any]]:
        """
        Check if update is allowed
        
        Args:
            current_state: Current affective state
            proposed_state: Proposed new state
            last_update_time: Timestamp of last update (for cooldown)
        
        Returns:
            Tuple of (allowed: bool, reason: str, decision: dict)
        """
        decision = {
            "allowed": False,
            "reason": "",
            "bounds_applied": False,
            "cooldown_active": False,
            "escalation_level": 0
        }
        
        # Check cooldown
        if last_update_time:
            time_since_update = (datetime.utcnow() - last_update_time).total_seconds()
            if time_since_update < self.config["cooldown_seconds"]:
                decision["cooldown_active"] = True
                decision["reason"] = f"Cooldown active ({self.config['cooldown_seconds']}s)"
                return (False, decision["reason"], decision)
        
        # Calculate deltas
        valence_delta = abs(proposed_state.valence - current_state.valence)
        arousal_delta = abs(proposed_state.arousal - current_state.arousal)
        max_delta = max(valence_delta, arousal_delta)
        
        # Check max delta per update
        if max_delta > self.config["max_delta_per_update"]:
            decision["reason"] = f"Delta too large: {max_delta:.3f} > {self.config['max_delta_per_update']}"
            return (False, decision["reason"], decision)
        
        # Check bounds
        if not (self.config["min_valence"] <= proposed_state.valence <= self.config["max_valence"]):
            decision["bounds_applied"] = True
            proposed_state.valence = max(
                self.config["min_valence"],
                min(self.config["max_valence"], proposed_state.valence)
            )
        
        if not (self.config["min_arousal"] <= proposed_state.arousal <= self.config["max_arousal"]):
            decision["bounds_applied"] = True
            proposed_state.arousal = max(
                self.config["min_arousal"],
                min(self.config["max_arousal"], proposed_state.arousal)
            )
        
        # Check escalation level
        max_abs_value = max(abs(proposed_state.valence), abs(proposed_state.arousal))
        if max_abs_value >= self.config["escalation_threshold"]:
            escalation_level = min(
                self.config["max_escalation_level"],
                int((max_abs_value - self.config["escalation_threshold"]) / 0.1) + 1
            )
            decision["escalation_level"] = escalation_level
        
        decision["allowed"] = True
        decision["reason"] = "Update allowed"
        return (True, decision["reason"], decision)
    
    def apply_bounds(self, state: AffectiveState) -> AffectiveState:
        """Apply bounds to state values"""
        state.valence = max(
            self.config["min_valence"],
            min(self.config["max_valence"], state.valence)
        )
        state.arousal = max(
            self.config["min_arousal"],
            min(self.config["max_arousal"], state.arousal)
        )
        return state


class MemoryWeightCalculator:
    """Calculates memory weights for historical influence"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize calculator with configuration
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or self._default_config()
    
    def _default_config(self) -> Dict[str, Any]:
        """Default configuration"""
        return {
            "recency_decay_factor": 0.95,  # Exponential decay per day
            "intensity_weight": 0.3,  # Weight for emotional intensity
            "significance_weight": 0.4,  # Weight for relationship significance
            "importance_weight": 0.3,  # Weight for explicit importance markers
            "max_history_days": 30  # Maximum days to consider
        }
    
    def calculate_weights(
        self,
        interaction_history: List[Dict[str, Any]],
        current_time: Optional[datetime] = None
    ) -> List[float]:
        """
        Calculate weights for each interaction in history
        
        Args:
            interaction_history: List of interaction dictionaries with timestamps and signals
            current_time: Current time (defaults to now)
        
        Returns:
            List of weights (normalized to sum to 1.0)
        """
        if not interaction_history:
            return []
        
        if current_time is None:
            current_time = datetime.utcnow()
        
        weights = []
        for interaction in interaction_history:
            # Extract timestamp
            timestamp_str = interaction.get("timestamp", "")
            try:
                if isinstance(timestamp_str, str):
                    timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                else:
                    timestamp = timestamp_str
            except:
                timestamp = current_time
            
            # Calculate recency factor (exponential decay)
            days_ago = (current_time - timestamp).total_seconds() / 86400
            if days_ago > self.config["max_history_days"]:
                weights.append(0.0)
                continue
            
            recency_factor = math.pow(self.config["recency_decay_factor"], days_ago)
            
            # Extract intensity (from signal if available)
            signal = interaction.get("signal", {})
            intensity = abs(signal.get("valence", 0.0)) + abs(signal.get("arousal", 0.0))
            intensity_factor = 0.5 + (intensity * 0.5)  # Scale to 0.5-1.0
            
            # Extract significance (from relationship state if available)
            significance = interaction.get("significance", 0.5)  # Default medium significance
            significance_factor = significance
            
            # Extract importance (from explicit markers if available)
            importance = interaction.get("importance", 0.5)  # Default medium importance
            importance_factor = importance
            
            # Calculate combined weight
            weight = (
                recency_factor * (
                    self.config["intensity_weight"] * intensity_factor +
                    self.config["significance_weight"] * significance_factor +
                    self.config["importance_weight"] * importance_factor
                )
            )
            weights.append(weight)
        
        # Normalize weights to sum to 1.0
        total_weight = sum(weights)
        if total_weight > 0:
            weights = [w / total_weight for w in weights]
        else:
            # If all weights are zero, assign equal weights
            weights = [1.0 / len(weights)] * len(weights)
        
        return weights


class AffectService:
    """Main service for affective state management"""
    
    def __init__(self, vvault_root: str, config: Optional[Dict[str, Any]] = None):
        """
        Initialize affect service
        
        Args:
            vvault_root: Root path to VVAULT filesystem
            config: Optional configuration dictionary
        """
        self.vvault_root = vvault_root
        self.state_store = AffectiveStateStore(vvault_root)
        self.history_manager = StateHistoryManager(vvault_root)
        self.audit_logger = AuditLogger(vvault_root)
        self.governor = UpdateGovernor(config.get("governance") if config else None)
        self.weight_calculator = MemoryWeightCalculator(config.get("memory") if config else None)
        self.config = config or {}
    
    def get_state(self, user_id: str, construct_callsign: str) -> AffectiveState:
        """
        Get current affective state (creates default if not found)
        
        Args:
            user_id: VVAULT user ID
            construct_callsign: Construct callsign
        
        Returns:
            AffectiveState (default if not found)
        """
        state = self.state_store.load_state(user_id, construct_callsign)
        if state is None:
            state = AffectiveState.default()
            # Save default state
            self.state_store.save_state(user_id, construct_callsign, state)
        return state
    
    def update_state(
        self,
        user_id: str,
        construct_callsign: str,
        user_signal: Dict[str, Any],
        interaction_history: Optional[List[Dict[str, Any]]] = None
    ) -> tuple[AffectiveState, Dict[str, Any]]:
        """
        Update affective state based on user signal and interaction history
        
        Args:
            user_id: VVAULT user ID
            construct_callsign: Construct callsign
            user_signal: User signal dictionary (valence, arousal, intent_category, confidence)
            interaction_history: Optional list of historical interactions
        
        Returns:
            Tuple of (new_state: AffectiveState, update_result: dict)
        """
        # Load current state
        current_state = self.get_state(user_id, construct_callsign)
        
        # Parse last update time
        last_update_time = None
        try:
            last_update_time = datetime.fromisoformat(current_state.last_update.replace('Z', '+00:00'))
        except:
            pass
        
        # Calculate memory weights if history provided
        memory_weights = []
        if interaction_history:
            memory_weights = self.weight_calculator.calculate_weights(interaction_history)
        
        # Compute proposed new state
        proposed_state = self._compute_new_state(
            current_state,
            user_signal,
            interaction_history or [],
            memory_weights
        )
        
        # Apply governance checks
        can_update, reason, decision = self.governor.can_update(
            current_state,
            proposed_state,
            last_update_time
        )
        
        update_result = {
            "success": False,
            "reason": reason,
            "governance_decision": decision,
            "previous_state": current_state.to_dict(),
            "proposed_state": proposed_state.to_dict()
        }
        
        if not can_update:
            # Log governance denial
            self.audit_logger.log_governance_decision(
                user_id,
                construct_callsign,
                decision,
                current_state.to_dict(),
                None,
                "governor"
            )
            update_result["new_state"] = current_state.to_dict()
            return (current_state, update_result)
        
        # Apply bounds if needed
        if decision.get("bounds_applied"):
            proposed_state = self.governor.apply_bounds(proposed_state)
        
        # Update state metadata
        proposed_state.last_update = datetime.utcnow().isoformat()
        proposed_state.update_count = current_state.update_count + 1
        proposed_state.governance_status["escalation_level"] = decision.get("escalation_level", 0)
        if decision.get("cooldown_active"):
            cooldown_until = (datetime.utcnow() + timedelta(seconds=self.governor.config["cooldown_seconds"])).isoformat()
            proposed_state.governance_status["cooldown_until"] = cooldown_until
        
        # Save new state
        success = self.state_store.save_state(user_id, construct_callsign, proposed_state)
        
        if success:
            # Create history entry
            history_entry = StateHistoryEntry(
                timestamp=proposed_state.last_update,
                previous_state=current_state.to_dict(),
                new_state=proposed_state.to_dict(),
                user_signal=user_signal,
                governance_decision=decision,
                actor="system"
            )
            self.history_manager.append_history_entry(user_id, construct_callsign, history_entry)
            
            # Log audit
            self.audit_logger.log_state_change(
                user_id,
                construct_callsign,
                current_state.to_dict(),
                proposed_state.to_dict(),
                user_signal,
                decision,
                "system"
            )
            
            update_result["success"] = True
            update_result["new_state"] = proposed_state.to_dict()
        else:
            update_result["reason"] = "Failed to save state"
            update_result["new_state"] = current_state.to_dict()
        
        return (proposed_state if success else current_state, update_result)
    
    def _compute_new_state(
        self,
        current_state: AffectiveState,
        user_signal: Dict[str, Any],
        interaction_history: List[Dict[str, Any]],
        memory_weights: List[float]
    ) -> AffectiveState:
        """
        Compute new state from current state, signal, and weighted history
        
        Args:
            current_state: Current affective state
            user_signal: User signal dictionary
            interaction_history: Historical interactions
            memory_weights: Weights for historical interactions
        
        Returns:
            Proposed new AffectiveState
        """
        # Extract signal values
        signal_valence = user_signal.get("valence", 0.0)
        signal_arousal = user_signal.get("arousal", 0.0)
        signal_confidence = user_signal.get("confidence", 1.0)
        
        # Apply signal weight (confidence)
        signal_weight = signal_confidence
        
        # Calculate signal influence
        signal_valence_influence = signal_valence * signal_weight * 0.5  # Scale down
        signal_arousal_influence = signal_arousal * signal_weight * 0.5
        
        # Calculate historical influence
        historical_valence_influence = 0.0
        historical_arousal_influence = 0.0
        
        for i, interaction in enumerate(interaction_history):
            if i >= len(memory_weights):
                break
            
            weight = memory_weights[i]
            signal = interaction.get("signal", {})
            hist_valence = signal.get("valence", 0.0)
            hist_arousal = signal.get("arousal", 0.0)
            
            historical_valence_influence += hist_valence * weight * 0.3  # Scale down
            historical_arousal_influence += hist_arousal * weight * 0.3
        
        # Apply decay (state naturally drifts toward neutral over time)
        decay_factor = 0.95  # 5% decay per update
        decayed_valence = current_state.valence * decay_factor
        decayed_arousal = current_state.arousal * decay_factor
        
        # Compute new state
        new_valence = decayed_valence + signal_valence_influence + historical_valence_influence
        new_arousal = decayed_arousal + signal_arousal_influence + historical_arousal_influence
        
        # Determine dominant emotion (simplified)
        dominant_emotion = self._determine_emotion(new_valence, new_arousal)
        
        # Create new state
        new_state = AffectiveState(
            valence=new_valence,
            arousal=new_arousal,
            dominant_emotion=dominant_emotion,
            last_update=current_state.last_update,  # Will be updated by caller
            update_count=current_state.update_count,
            governance_status=current_state.governance_status.copy()
        )
        
        return new_state
    
    def _determine_emotion(self, valence: float, arousal: float) -> str:
        """Determine dominant emotion from valence and arousal"""
        if abs(valence) < 0.1 and abs(arousal) < 0.1:
            return "neutral"
        elif valence > 0.5 and arousal > 0.5:
            return "excited"
        elif valence > 0.5 and arousal < -0.5:
            return "calm"
        elif valence > 0.5:
            return "happy"
        elif valence < -0.5 and arousal > 0.5:
            return "angry"
        elif valence < -0.5 and arousal < -0.5:
            return "sad"
        elif valence < -0.5:
            return "unhappy"
        elif arousal > 0.5:
            return "anxious"
        elif arousal < -0.5:
            return "relaxed"
        else:
            return "neutral"
    
    def get_history(
        self,
        user_id: str,
        construct_callsign: str,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get state history"""
        return self.history_manager.load_history(user_id, construct_callsign, limit)
    
    def reset_state(self, user_id: str, construct_callsign: str) -> bool:
        """Reset state to default"""
        default_state = AffectiveState.default()
        success = self.state_store.save_state(user_id, construct_callsign, default_state)
        if success:
            # Log reset
            self.audit_logger.log_state_change(
                user_id,
                construct_callsign,
                None,
                default_state.to_dict(),
                None,
                {"action": "reset"},
                "system"
            )
        return success

