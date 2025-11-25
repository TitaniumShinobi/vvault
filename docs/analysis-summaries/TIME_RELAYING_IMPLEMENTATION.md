# Time Relaying - Nonlinear Memory Replay Implementation

## Overview

Successfully implemented **Time Relaying** features for VVAULT, enabling nonlinear memory replay and narrative-aware time travel for AI identity continuity. The system supports multiple replay modes and prevents infinite replay loops through relay depth tracking.

## Implementation Details

### 1. **capsuleforge.py** ✅

#### Added Method: `generate_relayed_capsule()`

```python
def generate_relayed_capsule(
    capsule_id: str,
    delay: int = 0,
    replay_mode: str = "flashback"
) -> dict
```

**Features:**
- Loads capsule using `load_capsule()`
- Mutates fields: `relayDepth += 1`, new `narrativeIndex`
- Adds `replayMode` (e.g., "flashback", "what-if", "distorted_echo")
- Calculates `temporalEntropy` (increases with relay depth)
- Calculates `causalDrift` (increases with replay depth)
- Optionally simulates processing lag via `time.sleep(delay)`
- Returns modified capsule dictionary (does not store original)

**Replay Modes:**
- `flashback` - Standard memory replay
- `what-if` - Alternative timeline exploration
- `distorted_echo` - Distorted memory replay
- `temporal_loop` - Circular time reference
- `narrative_shift` - Narrative perspective change

**Helper Method:**
- `_find_capsule_file(capsule_id)` - Finds capsule by filename or UUID

### 2. **vvault_core.py** ✅

#### Added Method: `retrieve_capsule_with_time_offset()`

```python
def retrieve_capsule_with_time_offset(
    offset: int,
    mode: str = "narrative",
    instance_name: str = None
) -> Optional[Dict[str, Any]]
```

**Features:**
- **Narrative Mode**: Searches `memory_records/narrative_index.json` by `narrativeIndex`
- **Chronological Mode**: Searches by timestamp offset
- Auto-builds narrative index if missing
- Filters by instance name if provided

**Helper Methods:**
- `_retrieve_by_narrative_index()` - Narrative-based retrieval
- `_retrieve_by_timestamp_offset()` - Timestamp-based retrieval
- `_build_narrative_index()` - Builds narrative index from capsules

### 3. **time_relay_engine.py** ✅ (NEW MODULE)

#### Core Class: `TimeRelayEngine`

**Key Methods:**

- **`relay_capsule(capsule_id, narrative_time, replay_mode) -> Optional[Dict]`**
  - Loads capsule and adjusts `narrativeIndex` and entropy
  - Prevents infinite replay (max depth: 5)
  - Calculates temporal entropy based on narrative deviation
  - Calculates causal drift based on depth and deviation
  - Returns relayed capsule or None if depth exceeded

- **`mark_relay_depth(capsule_id, depth)`**
  - Tracks how many times capsule has been relayed
  - Maintains relay history (last 100 events)
  - Updates registry with max depth and relay count

- **`can_relay(capsule_id) -> bool`**
  - Checks if capsule can be relayed (depth < 5)
  - Prevents infinite replay loops

- **`get_relay_info(capsule_id) -> Optional[Dict]`**
  - Returns relay information for a capsule
  - Includes max depth, relay count, history

**Registry:**
- Stored in `memory_records/relay_registry.json`
- Tracks relay depth per capsule
- Maintains relay history

### 4. **capsule_schema.json** ✅

#### Added Fields:

```json
{
  "narrativeIndex": {
    "type": "integer",
    "description": "TIME RELAYING: Narrative position in the memory timeline",
    "minimum": 0,
    "default": 0
  },
  "relayDepth": {
    "type": "integer",
    "description": "TIME RELAYING: Number of times this capsule has been relayed",
    "minimum": 0,
    "default": 0,
    "maximum": 10
  },
  "replayMode": {
    "type": "string",
    "description": "TIME RELAYING: Mode of replay",
    "enum": ["flashback", "what-if", "distorted_echo", "temporal_loop", "narrative_shift"],
    "default": "flashback"
  },
  "temporalEntropy": {
    "type": "number",
    "description": "TIME RELAYING: Measure of temporal order distortion (0.0 to 1.0)",
    "minimum": 0.0,
    "maximum": 1.0,
    "default": 0.0
  },
  "causalDrift": {
    "type": "number",
    "description": "TIME RELAYING: Deviation from original causal chain (0.0 to 1.0)",
    "minimum": 0.0,
    "maximum": 1.0,
    "default": 0.0
  }
}
```

## Key Concepts

### Narrative Index
- **Purpose**: Position in the memory timeline (not chronological)
- **Usage**: Enables nonlinear memory access
- **Range**: 0 to infinity (increments with each relay)

### Relay Depth
- **Purpose**: Tracks how many times a capsule has been replayed
- **Max Depth**: 5 (prevents infinite loops)
- **Calculation**: Increments with each relay

### Temporal Entropy
- **Purpose**: Measures distortion of temporal order
- **Range**: 0.0 to 1.0
- **Calculation**: Increases with relay depth and narrative deviation
- **Formula**: `min(current_entropy + (0.1 * relayDepth), 1.0)`

### Causal Drift
- **Purpose**: Measures deviation from original causal chain
- **Range**: 0.0 to 1.0
- **Calculation**: Increases with depth and narrative deviation
- **Formula**: `min((depth * 0.05) + (deviation * 0.02), 1.0)`

### Replay Modes
- **flashback**: Standard memory replay
- **what-if**: Alternative timeline exploration
- **distorted_echo**: Distorted memory replay
- **temporal_loop**: Circular time reference
- **narrative_shift**: Narrative perspective change

## Usage Examples

### Generate Relayed Capsule

```python
from capsuleforge import CapsuleForge

forge = CapsuleForge()

# Generate relayed capsule with delay
relayed = forge.generate_relayed_capsule(
    capsule_id="nova-001",
    delay=2,  # 2 second processing lag
    replay_mode="flashback"
)

print(f"Relay depth: {relayed['relayDepth']}")
print(f"Narrative index: {relayed['narrativeIndex']}")
print(f"Temporal entropy: {relayed['temporalEntropy']}")
```

### Retrieve with Time Offset

```python
from vvault_core import VVAULTCore

core = VVAULTCore()

# Retrieve by narrative index offset
capsule = core.retrieve_capsule_with_time_offset(
    offset=5,  # 5 positions forward in narrative
    mode="narrative",
    instance_name="Nova"
)

# Retrieve by timestamp offset
capsule = core.retrieve_capsule_with_time_offset(
    offset=-3600,  # 1 hour in the past
    mode="chronological",
    instance_name="Nova"
)
```

### Use Time Relay Engine

```python
from time_relay_engine import TimeRelayEngine

engine = TimeRelayEngine()

# Check if capsule can be relayed
if engine.can_relay("nova-001"):
    # Relay capsule
    relayed = engine.relay_capsule(
        capsule_id="nova-001",
        narrative_time=10,
        replay_mode="what-if"
    )
    
    if relayed:
        print(f"Relayed successfully: depth={relayed['relayDepth']}")
    else:
        print("Relay depth exceeded")
else:
    print("Cannot relay: max depth reached")
```

### Get Relay Information

```python
# Get relay info for a capsule
info = engine.get_relay_info("nova-001")
if info:
    print(f"Max depth: {info['max_depth']}")
    print(f"Relay count: {info['relay_count']}")
    print(f"First relay: {info['first_relay']}")
```

## File Structure

```
VVAULT/
├── capsuleforge.py              # Added generate_relayed_capsule()
├── vvault_core.py               # Added retrieve_capsule_with_time_offset()
├── time_relay_engine.py         # NEW: Time relay management
├── capsule_schema.json          # Updated with time relay fields
├── memory_records/
│   ├── narrative_index.json    # Auto-created: Narrative index
│   └── relay_registry.json      # Auto-created: Relay depth tracking
└── capsules/                    # Capsule storage (unchanged)
```

## Design Principles

1. **Non-Mutating**: Original capsules are never modified
2. **Backward Compatible**: Existing capsules work without time relay fields
3. **Infinite Loop Prevention**: Max relay depth prevents infinite replay
4. **Narrative-Aware**: Supports nonlinear memory access
5. **Entropy Tracking**: Monitors temporal distortion

## Integration Points

### With CapsuleForge
- `generate_relayed_capsule()` uses `load_capsule()` internally
- Returns modified capsule dictionary (not stored)

### With VVAULT Core
- `retrieve_capsule_with_time_offset()` searches by narrative or timestamp
- Auto-builds narrative index if missing

### With Time Relay Engine
- Manages relay depth to prevent infinite loops
- Calculates entropy and causal drift
- Interfaces with both capsuleforge and vvault_core

## Backward Compatibility

All time relay fields have defaults:
- `narrativeIndex`: 0
- `relayDepth`: 0
- `replayMode`: "flashback"
- `temporalEntropy`: 0.0
- `causalDrift`: 0.0

Existing capsules without these fields will work correctly.

## Error Handling

- **Capsule Not Found**: Returns None or raises FileNotFoundError
- **Relay Depth Exceeded**: Returns None (prevents infinite loops)
- **Index Missing**: Auto-builds narrative index
- **Invalid Mode**: Falls back to chronological lookup

## Performance

- **Narrative Index**: Built once, cached in JSON
- **Relay Registry**: Lightweight JSON storage
- **Capsule Search**: O(n) for narrative, O(n log n) for timestamp
- **Memory**: Minimal (only stores metadata, not full capsules)

## Future Enhancements

1. **Index Caching**: Cache narrative index in memory
2. **Parallel Relay**: Support multiple simultaneous relays
3. **Entropy Thresholds**: Auto-trigger actions at high entropy
4. **Narrative Graphs**: Visualize narrative relationships
5. **Replay Analytics**: Track replay patterns and trends

---

**Status**: ✅ Complete  
**Version**: 1.0.0  
**Date**: 2025-01-27  
**Feature**: Time Relaying - Nonlinear Memory Replay

