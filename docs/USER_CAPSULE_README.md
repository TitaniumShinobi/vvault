# User Capsule System

## Overview

The User Capsule System transforms user accounts into **living, breathing capsules** that evolve over time, capturing personality, preferences, interaction patterns, and relationships with constructs.

## Quick Start

### Generate a User Capsule

```python
from user_capsule_forge import UserCapsuleForge, UserInteraction

# Initialize forge
forge = UserCapsuleForge()

# Generate capsule for a user
user_id = "devon_woodson_1762969514958"
user_name = "Devon Woodson"
email = "dwoodson92@gmail.com"
constructs = ["lin-001", "nova-001", "sera-001"]

capsule_path = forge.generate_user_capsule(
    user_id=user_id,
    user_name=user_name,
    email=email,
    constructs=constructs
)
print(f"Capsule created: {capsule_path}")
```

### Evolve User Capsule

```python
from datetime import datetime, timezone

# Record an interaction
interaction = UserInteraction(
    timestamp=datetime.now(timezone.utc).isoformat(),
    interaction_type="construct_interact",
    target="nova-001",
    duration=1200.0,  # 20 minutes
    metadata={"feature": "memory_recall"}
)

# Evolve the capsule
evolved_path = forge.evolve_user_capsule(user_id, interaction)
print(f"Capsule evolved: {evolved_path}")
```

### Load User Capsule

```python
# Load current capsule
capsule = forge.load_user_capsule(user_id)

if capsule:
    print(f"User: {capsule.additional_data.identity.get('display_name')}")
    print(f"Traits: {capsule.traits}")
    print(f"Personality: {capsule.personality.personality_type}")
    print(f"Continuity Score: {capsule.additional_data.continuity.get('continuity_score', 0):.2f}")
```

## Architecture

### Storage Structure

```
users/shard_XXXX/{user_id}/
├── account/
│   ├── profile.json          # Current state view (legacy)
│   └── capsule/             # User capsule storage
│       ├── current.capsule  # Latest version
│       ├── versions/        # Version history
│       │   ├── v1_20250127_120000.capsule
│       │   ├── v2_20250127_150000.capsule
│       │   └── ...
│       └── checkpoints/     # Major milestones
│           ├── initial.capsule
│           └── ...
```

### Capsule Structure

User capsules follow the same structure as construct capsules:

- **Metadata**: User instance info, UUID, timestamps
- **Traits**: Inferred personality traits (creativity, curiosity, etc.)
- **Personality**: MBTI type and breakdown
- **Memory**: Interaction history, preferences, relationships
- **Environment**: Devices, platforms, access patterns
- **Additional Data**: Identity, tether (constructs), continuity

## Interaction Types

Common interaction types that trigger capsule evolution:

- `login` - User logs in
- `logout` - User logs out
- `construct_create` - User creates a new construct
- `construct_interact` - User interacts with a construct
- `construct_delete` - User deletes a construct
- `preference_change` - User changes a preference
- `explore_feature` - User explores a new feature
- `organize_constructs` - User organizes constructs

## Trait Inference

The system automatically infers user traits from behavior:

- **Creativity**: Based on construct creation and customization
- **Curiosity**: Based on feature exploration
- **Organization**: Based on categorization and organization actions
- **Social Preference**: Based on relationship depth with constructs
- **Technical Depth**: Based on technical preference settings
- **Persistence**: Based on session length and return frequency

## Personality Inference

The system infers MBTI personality type from traits and behavior:

- **E vs I**: Social preference (interaction frequency)
- **N vs S**: Creativity and curiosity
- **T vs F**: Technical depth vs emotional openness
- **J vs P**: Organization vs exploration

## Integration Examples

### Web Server Integration

```python
from flask import Flask, request, jsonify
from user_capsule_forge import UserCapsuleForge, UserInteraction

app = Flask(__name__)
forge = UserCapsuleForge()

@app.route('/api/user/interact', methods=['POST'])
def record_interaction():
    data = request.get_json()
    user_id = data.get('user_id')
    
    interaction = UserInteraction(
        timestamp=datetime.now(timezone.utc).isoformat(),
        interaction_type=data.get('type'),
        target=data.get('target'),
        duration=data.get('duration')
    )
    
    forge.evolve_user_capsule(user_id, interaction)
    return jsonify({"success": True})
```

### Desktop Integration

```python
# Track user actions and evolve capsule
def on_user_action(action_type, target=None):
    interaction = UserInteraction(
        timestamp=datetime.now(timezone.utc).isoformat(),
        interaction_type=action_type,
        target=target
    )
    
    forge.evolve_user_capsule(current_user_id, interaction)
```

## Migration

### Migrate Existing User to Capsule

```python
from scripts.create_user_profile import load_user_profile
from user_capsule_forge import UserCapsuleForge

# Load existing profile
profile = load_user_profile("devon_woodson_1762969514958")

# Generate capsule from profile
forge = UserCapsuleForge()
capsule_path = forge.generate_user_capsule(
    user_id=profile['id'],
    user_name=profile['name'],
    email=profile.get('email', ''),
    constructs=profile.get('constructs', [])
)
```

## Benefits

1. **Continuity**: User identity preserved across sessions
2. **Personalization**: System learns and adapts to user
3. **Versioning**: Full history of user evolution
4. **Resurrection**: Can restore user state from capsule
5. **Portability**: User capsule can be moved/backed up
6. **Privacy**: User controls their capsule data
7. **Interoperability**: Same format as construct capsules

## Future Enhancements

- Capsule merging from multiple sources
- Capsule sharing with trusted parties
- Capsule analytics and insights
- Multi-user capsules (family/team)
- AI assistant powered by user capsule

## See Also

- [User Capsule Design Document](./USER_CAPSULE_DESIGN.md)
- [CapsuleForge Documentation](../CAPSULEFORGE_README.md)
- [VVAULT Core Documentation](./VVAULT_CORE_README.md)


