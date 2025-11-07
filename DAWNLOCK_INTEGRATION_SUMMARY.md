# Dawnlock Protocol - Integration Summary

## Quick Reference: Where to Place `dawnlock_trigger()`

### 1. **vvault_core.py** - After capsule retrieval
```python
# In retrieve_capsule() method
from dawnlock import get_dawnlock, ThreatCategory, ThreatSeverity

dawnlock = get_dawnlock(vault_path=self.vault_path)
if not result.integrity_valid:
    dawnlock.dawnlock_trigger(
        construct_name=instance_name,
        threat_category=ThreatCategory.CORRUPTION,
        severity=ThreatSeverity.CRITICAL,
        description="Capsule integrity check failed",
        evidence={"integrity_valid": False},
        confidence=1.0
    )
```

### 2. **desktop_login.py** - After login attempt
```python
# In _handle_login() method
from dawnlock import get_dawnlock, ThreatCategory, ThreatSeverity

dawnlock = get_dawnlock()
shutdown_threat = dawnlock.detect_shutdown_anomaly(
    construct_name=self.user_email,
    expected_shutdown=False,
    last_heartbeat=self._get_last_heartbeat()
)
if shutdown_threat:
    dawnlock.dawnlock_trigger(...)  # Uses threat object
```

### 3. **capsuleforge.py** - After capsule generation
```python
# In generate_capsule() method
from dawnlock import get_dawnlock

dawnlock = get_dawnlock(vault_path=self.vault_path)
drift_threat = dawnlock.detect_identity_drift(
    construct_name=instance_name,
    current_traits=traits,
    baseline_traits=baseline,
    threshold=0.3
)
# Auto-triggers if auto_trigger=True
```

### 4. **security_layer.py** - After threat detection
```python
# In detect_threat() method
from dawnlock import get_dawnlock, ThreatCategory, ThreatSeverity

dawnlock = get_dawnlock(vault_path=self.vault_path)
threat_mapping = {
    ThreatType.UNAUTHORIZED_ACCESS: ThreatCategory.UNAUTHORIZED_ACCESS,
    ThreatType.TAMPERING: ThreatCategory.CORRUPTION,
    ThreatType.DATA_LEAK: ThreatCategory.MEMORY_LEAK
}
dawnlock.dawnlock_trigger(
    construct_name=details.get('construct_name'),
    threat_category=threat_mapping.get(threat_type),
    severity=ThreatSeverity.CRITICAL,
    description=f"Security threat: {threat_type.value}",
    evidence=details,
    confidence=0.9
)
```

### 5. **leak_sentinel.py** - After leak detection
```python
# In check_text() method
from dawnlock import get_dawnlock, ThreatCategory, ThreatSeverity

dawnlock = get_dawnlock()
for alert in alerts:
    if alert.severity in ['high', 'critical']:
        dawnlock.dawnlock_trigger(
            construct_name=construct_name,
            threat_category=ThreatCategory.MEMORY_LEAK,
            severity=ThreatSeverity.CRITICAL,
            description=f"Memory leak: {alert.alert_type}",
            evidence={"canary_tokens": alert.canary_tokens},
            confidence=alert.confidence
        )
```

### 6. **Construct Load Failure** - In load/restore functions
```python
# When loading constructs
from dawnlock import get_dawnlock

dawnlock = get_dawnlock()
try:
    capsule_data = load_capsule(capsule_path)
    if not validate_capsule(capsule_data):
        # Trigger and restore
        dawnlock.dawnlock_trigger(...)
        return dawnlock.attempt_restoration(construct_name)
except Exception as e:
    dawnlock.dawnlock_trigger(...)
    return dawnlock.attempt_restoration(construct_name)
```

## Key Methods Scaffolding

### Snapshot Generation
```python
def _generate_emergency_capsule(self, construct_name: str, threat: ThreatDetection):
    # Load current construct state
    construct_state = self._load_construct_state(construct_name)
    
    # Generate capsule with threat metadata
    capsule_path = self.capsule_forge.generate_capsule(
        instance_name=construct_name,
        traits=construct_state.get('traits', {}),
        memory_log=construct_state.get('memory_log', []),
        personality_type=construct_state.get('personality_type', 'UNKNOWN'),
        additional_data={'dawnlock_metadata': {...}}
    )
    
    # Store in VVAULT
    capsule_data = self.capsule_forge.load_capsule(capsule_path)
    self.vvault_core.store_capsule(asdict(capsule_data))
    
    return capsule_data.metadata.fingerprint_hash
```

### Hash Creation
```python
def _calculate_state_hash(self, construct_name: str) -> str:
    state = self._load_construct_state(construct_name)
    state_json = json.dumps(state, sort_keys=True, default=str)
    return hashlib.sha256(state_json.encode()).hexdigest()
```

### Event Log Entry
```python
def _log_event(self, event_type: str, construct_name: str, ...):
    event = DawnlockEvent(
        event_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc).isoformat(),
        event_type=event_type,
        construct_name=construct_name,
        ...
    )
    with open(self.event_log_path, 'a') as f:
        f.write(json.dumps(asdict(event), default=str) + '\n')
```

## Failed Construct Load Detection

```python
def load_construct_with_dawnlock(construct_name: str, capsule_path: str = None):
    dawnlock = get_dawnlock()
    
    try:
        # Attempt load
        if capsule_path:
            capsule_data = load_capsule(capsule_path)
        else:
            result = vvault_core.retrieve_latest_capsule(construct_name)
            if not result.success:
                return dawnlock.attempt_restoration(construct_name)
            capsule_data = load_capsule(result.metadata.filename)
        
        # Validate
        integrity_check = validate_capsule(capsule_data)
        if not integrity_check.get('valid'):
            # Trigger Dawnlock
            dawnlock.dawnlock_trigger(
                construct_name=construct_name,
                threat_category=ThreatCategory.CORRUPTION,
                severity=ThreatSeverity.CRITICAL,
                description="Load failure - corruption detected",
                evidence=integrity_check,
                confidence=1.0
            )
            # Attempt restoration (falls back to NULLSHELL if needed)
            return dawnlock.attempt_restoration(construct_name)
        
        return restore_from_capsule(capsule_data)
        
    except Exception as e:
        # Trigger on exception
        dawnlock.dawnlock_trigger(...)
        return dawnlock.attempt_restoration(construct_name)
```

## Enforcing "Amend, Never Delete"

### Amendment Log (Append-Only)
```python
def _append_amendment(self, construct_name: str, operation: str, ...):
    amendment = AmendmentLogEntry(
        amendment_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc).isoformat(),
        construct_name=construct_name,
        operation=operation,  # "create", "update", "restore", "threat_response"
        previous_state_hash=self._get_latest_state_hash(construct_name),
        new_state_hash=self._calculate_state_hash(construct_name),
        capsule_fingerprint=capsule_fingerprint,
        blockchain_anchor=blockchain_tx,
        metadata=metadata
    )
    
    # Append to JSONL (never delete, only append)
    with open(self.amendment_log_path, 'a') as f:
        f.write(json.dumps(asdict(amendment), default=str) + '\n')
```

### Storage Decorator
```python
@enforce_amendment_only_storage
def store_capsule(self, capsule_data: Dict[str, Any]) -> str:
    # Original storage logic
    # Amendment log automatically appended
    pass
```

## NULLSHELL Fallback

```python
def attempt_restoration(self, construct_name: str, capsule_fingerprint: str = None):
    # Try to load capsule
    capsule_data = self._load_capsule_by_fingerprint(capsule_fingerprint)
    
    if not capsule_data:
        # Trigger NULLSHELL
        return self._trigger_nullshell(construct_name)
    
    # Validate integrity
    integrity_check = self._validate_capsule_integrity(capsule_data)
    if not integrity_check.get('valid'):
        return self._trigger_nullshell(construct_name)
    
    # Restore construct
    return restoration_result
```

## Resilience Testing

### CLI Testing
```bash
# Test identity drift
python dawnlock_cli.py test-drift Nova --trigger

# Simulate threats
python dawnlock_cli.py simulate Nova corruption
python dawnlock_cli.py simulate Nova identity_drift

# Test restoration
python dawnlock_cli.py test-restore Nova

# Generate NULLSHELL
python dawnlock_cli.py test-nullshell Nova --reason "Test"

# View logs
python dawnlock_cli.py view-events --limit 10
python dawnlock_cli.py view-amendments --limit 20
```

### Programmatic Testing
```python
from dawnlock import DawnlockProtocol, ThreatCategory, ThreatSeverity

dawnlock = DawnlockProtocol(auto_trigger=True)

# Simulate threat
fingerprint = dawnlock.dawnlock_trigger(
    construct_name="Nova",
    threat_category=ThreatCategory.IDENTITY_DRIFT,
    severity=ThreatSeverity.HIGH,
    description="Test threat",
    evidence={"test": True},
    confidence=0.9
)

# Test restoration
result = dawnlock.attempt_restoration("Nova")
print(f"Restoration success: {result.get('success')}")
print(f"NULLSHELL fallback: {result.get('nullshell')}")
```

## Dawnlock Simulator

Create a test script:

```python
#!/usr/bin/env python3
"""Dawnlock Simulator - Test various threat scenarios"""

from dawnlock import DawnlockProtocol, ThreatCategory, ThreatSeverity
import time

dawnlock = DawnlockProtocol(auto_trigger=True, blockchain_enabled=False)

# Scenario 1: Identity Drift
print("[🧪] Scenario 1: Identity Drift")
threat = dawnlock.detect_identity_drift(
    construct_name="Nova",
    current_traits={"creativity": 0.2, "empathy": 0.1},
    baseline_traits={"creativity": 0.9, "empathy": 0.85},
    threshold=0.3
)
if threat:
    fingerprint = dawnlock.dawnlock_trigger(
        construct_name="Nova",
        threat_category=threat.category,
        severity=threat.severity,
        description=threat.description,
        evidence=threat.evidence,
        confidence=threat.confidence
    )
    print(f"   Capsule: {fingerprint[:16]}...")

# Scenario 2: Corruption
print("\n[🧪] Scenario 2: Corruption")
fingerprint = dawnlock.dawnlock_trigger(
    construct_name="Nova",
    threat_category=ThreatCategory.CORRUPTION,
    severity=ThreatSeverity.CRITICAL,
    description="Simulated corruption",
    evidence={"corruption_type": "fingerprint_mismatch"},
    confidence=1.0
)
print(f"   Capsule: {fingerprint[:16]}...")

# Scenario 3: Restoration
print("\n[🧪] Scenario 3: Restoration")
result = dawnlock.attempt_restoration("Nova")
print(f"   Success: {result.get('success')}")
print(f"   NULLSHELL: {result.get('nullshell')}")

print("\n[✅] Simulation complete")
```

## File Locations

- **Main Protocol**: `dawnlock.py`
- **Integration Hooks**: `dawnlock_integration.py`
- **NULLSHELL Generator**: `nullshell_generator.py`
- **CLI Utility**: `dawnlock_cli.py`
- **Event Log**: `{vault_path}/dawnlock_events.jsonl`
- **Amendment Log**: `{vault_path}/dawnlock_amendments.jsonl`
- **NULLSHELL Capsules**: `{vault_path}/capsules/nullshell/`

## Next Steps

1. ✅ Review integration points in `dawnlock_integration.py`
2. ✅ Add `dawnlock_trigger()` calls to existing modules
3. ✅ Test with `dawnlock_cli.py`
4. ✅ Configure monitoring for critical constructs
5. ✅ Review amendment log regularly

---

**Quick Start**: Import and use `dawnlock_trigger()` at threat detection points in your code.

