# 🏺 VVAULT Core - Capsule Storage and Retrieval Management

## Overview

VVAULT Core is a comprehensive module for managing storage and retrieval of `.capsule` files by AI instance name, including version history, tagging support, and integrity validation. It provides structured storage with automatic indexing and robust error handling.

**Author:** Devon Allen Woodson  
**Date:** 2025-01-27  
**Version:** 1.0.0

---

## 🎯 Features

### ✅ **Core Functionality**
- **Structured Storage:** Organizes capsules by instance name in dedicated directories
- **Automatic Versioning:** Timestamp-based versioning with UUID tracking
- **Tagging System:** Add, remove, and filter capsules by tags
- **Integrity Validation:** SHA-256 fingerprint verification for all capsules
- **Index Management:** Lightweight JSON indexes for fast retrieval
- **Error Handling:** Comprehensive error handling and recovery

### ✅ **Advanced Capabilities**
- **Multi-version Support:** Retrieve latest or specific versions
- **Tag-based Filtering:** Filter capsules by tags
- **Instance Management:** List and manage multiple AI instances
- **Metadata Tracking:** File sizes, timestamps, and capsule information
- **Convenience Functions:** Easy-to-use wrapper functions
- **Robust Error Handling:** Graceful degradation and detailed error messages

---

## 📁 File Structure

```
VVAULT (macos)/
├── vvault_core.py              # Main VVAULT Core module
├── test_vvault_core.py         # Test script
├── VVAULT_CORE_README.md       # This documentation
├── capsules/                   # Stored capsule files
│   └── Nova/                  # Instance-specific directories
│       ├── Nova_2025-08-05T02-21-54-994781-00-00.capsule
│       └── Nova_2025-08-05T02-21-55-004075-00-00.capsule
└── indexes/                    # Instance indexes
    └── Nova_index.json        # Index for Nova instance
```

---

## 🚀 Quick Start

### **Basic Usage**

```python
from vvault_core import VVAULTCore, store_capsule, retrieve_capsule

# Store a capsule
capsule_data = {...}  # From CapsuleForge
stored_path = store_capsule(capsule_data)

# Retrieve latest capsule
result = retrieve_capsule("Nova")
if result.success:
    print(f"Retrieved: {result.metadata.instance_name}")
    print(f"UUID: {result.metadata.uuid}")
    print(f"Tags: {result.metadata.tags}")
```

### **Advanced Usage**

```python
from vvault_core import VVAULTCore

core = VVAULTCore()

# Store capsule with automatic indexing
stored_path = core.store_capsule(capsule_data)

# Add tags to capsules
core.add_tag("Nova", "uuid-of-capsule", "post-mirror-break")

# Retrieve by tag
result = core.retrieve_capsule("Nova", tag="post-mirror-break")

# List all capsules for an instance
capsules = core.list_capsules("Nova")

# Get instance information
info = core.get_instance_info("Nova")
```

---

## 📊 Data Structures

### **CapsuleMetadata**
```python
@dataclass
class CapsuleMetadata:
    instance_name: str           # AI instance name
    uuid: str                   # Unique identifier
    timestamp: str              # Creation timestamp
    filename: str               # Capsule filename
    fingerprint_hash: str       # SHA-256 integrity hash
    tags: List[str]            # Associated tags
    capsule_version: str        # Capsule format version
    generator: str              # Generator (CapsuleForge)
    vault_source: str          # Source (VVAULT)
    file_size: int             # File size in bytes
    created_at: str            # When stored
    updated_at: str            # Last updated
```

### **InstanceIndex**
```python
@dataclass
class InstanceIndex:
    instance_name: str                    # AI instance name
    capsules: Dict[str, CapsuleMetadata] # UUID -> metadata
    tags: Dict[str, List[str]]           # Tag -> list of UUIDs
    latest_uuid: Optional[str]           # Most recent capsule
    created_at: str                      # Index creation time
    updated_at: str                      # Last update time
```

### **RetrievalResult**
```python
@dataclass
class RetrievalResult:
    success: bool                        # Operation success
    capsule_data: Optional[Dict]        # Retrieved capsule data
    metadata: Optional[CapsuleMetadata] # Capsule metadata
    error_message: Optional[str]        # Error description
    integrity_valid: bool               # Integrity validation result
```

---

## 🔧 API Reference

### **VVAULTCore Class**

#### **Constructor**
```python
VVAULTCore(vault_path: str = None)
```
- `vault_path`: Path to VVAULT directory (defaults to module directory)

#### **Methods**

##### **store_capsule()**
```python
def store_capsule(self, capsule_data: Dict[str, Any]) -> str
```
Stores a capsule with automatic versioning and indexing.

**Parameters:**
- `capsule_data`: Capsule data from CapsuleForge

**Returns:** Path to the stored capsule file

**Features:**
- Validates capsule structure
- Creates instance directory if needed
- Generates timestamped filename
- Updates instance index
- Tracks file size and metadata

##### **retrieve_capsule()**
```python
def retrieve_capsule(
    self, 
    instance_name: str, 
    version: str = 'latest', 
    tag: str = None,
    uuid: str = None
) -> RetrievalResult
```
Retrieves a capsule by instance name with optional filtering.

**Parameters:**
- `instance_name`: Name of the AI instance
- `version`: Version to retrieve ('latest' or specific UUID)
- `tag`: Filter by tag
- `uuid`: Specific UUID to retrieve

**Returns:** RetrievalResult with capsule data and metadata

**Features:**
- Multiple retrieval methods (latest, by tag, by UUID)
- Integrity validation
- Error handling for missing instances/tags

##### **add_tag()**
```python
def add_tag(self, instance_name: str, uuid: str, tag: str) -> bool
```
Adds a tag to a specific capsule.

**Parameters:**
- `instance_name`: Name of the AI instance
- `uuid`: UUID of the capsule to tag
- `tag`: Tag to add

**Returns:** True if successful, False otherwise

##### **remove_tag()**
```python
def remove_tag(self, instance_name: str, uuid: str, tag: str) -> bool
```
Removes a tag from a specific capsule.

**Parameters:**
- `instance_name`: Name of the AI instance
- `uuid`: UUID of the capsule
- `tag`: Tag to remove

**Returns:** True if successful, False otherwise

##### **list_capsules()**
```python
def list_capsules(self, instance_name: str, tag: str = None) -> List[Dict[str, Any]]
```
Lists all capsules for an instance with optional tag filtering.

**Parameters:**
- `instance_name`: Name of the AI instance
- `tag`: Optional tag filter

**Returns:** List of capsule metadata dictionaries

##### **get_instance_info()**
```python
def get_instance_info(self, instance_name: str) -> Optional[Dict[str, Any]]
```
Gets information about an instance including capsule count and tags.

**Parameters:**
- `instance_name`: Name of the AI instance

**Returns:** Dictionary with instance information

##### **list_instances()**
```python
def list_instances(self) -> List[str]
```
Lists all AI instances in the vault.

**Returns:** List of instance names

##### **delete_capsule()**
```python
def delete_capsule(self, instance_name: str, uuid: str) -> bool
```
Deletes a specific capsule.

**Parameters:**
- `instance_name`: Name of the AI instance
- `uuid`: UUID of the capsule to delete

**Returns:** True if successful, False otherwise

### **Convenience Functions**

#### **store_capsule()**
```python
def store_capsule(capsule_data: Dict[str, Any]) -> str
```
Convenience function to store a capsule.

#### **retrieve_capsule()**
```python
def retrieve_capsule(
    instance_name: str, 
    version: str = 'latest', 
    tag: str = None,
    uuid: str = None
) -> RetrievalResult
```
Convenience function to retrieve a capsule.

#### **add_tag()**
```python
def add_tag(instance_name: str, uuid: str, tag: str) -> bool
```
Convenience function to add a tag to a capsule.

#### **list_capsules()**
```python
def list_capsules(instance_name: str, tag: str = None) -> List[Dict[str, Any]]
```
Convenience function to list capsules for an instance.

---

## 🧪 Testing

### **Run Tests**
```bash
cd "VVAULT (macos)"
python3 test_vvault_core.py
```

### **Expected Output**
```
============================================================
🏺 VVAULT Core Test Suite
============================================================
🧪 Testing VVAULT Core...

📦 Creating test capsule...
✅ Test capsule created

💾 Testing capsule storage...
✅ Capsule stored: /path/to/capsules/Nova/Nova_2025-08-05T02-21-54-994781-00-00.capsule

📋 Testing instance listing...
✅ Found instances: ['Nova']

ℹ️ Testing instance info...
✅ Instance info: {'instance_name': 'Nova', 'total_capsules': 1, 'latest_uuid': 'f964796c-3765-4006-9b24-85c77f75c7de', 'tags': {}, 'created_at': '2025-08-05T02:21:55.002720+00:00', 'updated_at': '2025-08-05T02:21:55.002726+00:00'}

📋 Testing capsule listing...
✅ Found 1 capsules
  1. Nova - f964796c... - 0 tags

📖 Testing capsule retrieval (latest)...
✅ Retrieved capsule: Nova
   UUID: f964796c-3765-4006-9b24-85c77f75c7de
   Tags: []
   Integrity: Valid

🏷️ Testing tag addition...
✅ Tag 'test-tag' added successfully

🏷️ Testing second tag addition...
✅ Tag 'post-mirror-break' added successfully

📋 Testing capsule listing with tag filter...
✅ Found 1 capsules with 'test-tag'

📖 Testing capsule retrieval by tag...
✅ Retrieved capsule by tag: Nova
   UUID: f964796c-3765-4006-9b24-85c77f75c7de
   Tags: ['test-tag', 'post-mirror-break']

🏷️ Testing tag removal...
✅ Tag 'test-tag' removed successfully

📋 Testing capsule listing after tag removal...
✅ Found 0 capsules with 'test-tag' (should be 0)

🧪 Testing convenience functions...
✅ Capsule stored via convenience function
✅ Retrieved capsule via convenience function
✅ Tag added via convenience function
✅ Listed 2 capsules via convenience function

🧪 Testing error handling...
✅ Correctly handled non-existent instance
✅ Correctly handled non-existent tag
✅ Correctly handled non-existent capsule UUID

============================================================
🎉 All VVAULT Core tests passed!
✅ VVAULT Core is ready for production use
============================================================
```

---

## 🔍 Storage Structure

### **Directory Organization**
```
capsules/
├── Nova/                          # Instance-specific directory
│   ├── Nova_2025-08-05T02-21-54-994781-00-00.capsule
│   └── Nova_2025-08-05T02-21-55-004075-00-00.capsule
├── Aurora/                        # Another instance
│   └── Aurora_2025-08-05T02-22-00-123456-00-00.capsule
└── Monday/                        # Another instance
    └── Monday_2025-08-05T02-22-30-789012-00-00.capsule
```

### **Index Structure**
```json
{
  "instance_name": "Nova",
  "capsules": {
    "f964796c-3765-4006-9b24-85c77f75c7de": {
      "instance_name": "Nova",
      "uuid": "f964796c-3765-4006-9b24-85c77f75c7de",
      "timestamp": "2025-08-05T02:21:54.994781+00:00",
      "filename": "Nova_2025-08-05T02-21-54-994781-00-00.capsule",
      "fingerprint_hash": "381faebed60251d94e6ba7838b83952aa83e29ff188df8911f80b95576cf0d28",
      "tags": ["post-mirror-break"],
      "capsule_version": "1.0.0",
      "generator": "CapsuleForge",
      "vault_source": "VVAULT",
      "file_size": 6691,
      "created_at": "2025-08-05T02:21:55.002698+00:00",
      "updated_at": "2025-08-05T02:21:55.003878+00:00"
    }
  },
  "tags": {
    "post-mirror-break": ["f964796c-3765-4006-9b24-85c77f75c7de"]
  },
  "latest_uuid": "f964796c-3765-4006-9b24-85c77f75c7de",
  "created_at": "2025-08-05T02:21:55.002720+00:00",
  "updated_at": "2025-08-05T02:21:55.005225+00:00"
}
```

---

## 🛡️ Security & Integrity

### **Fingerprint Validation**
Each capsule includes a SHA-256 fingerprint for integrity validation:

```python
# Validate capsule integrity
result = core.retrieve_capsule("Nova")
if result.integrity_valid:
    print("✅ Capsule integrity validated")
else:
    print("❌ Capsule integrity validation failed")
```

### **Structure Validation**
Capsules are validated for required structure:

```python
# Required sections
required_sections = ['metadata', 'traits', 'personality', 'memory', 'environment']

# Required metadata fields
required_metadata = ['instance_name', 'uuid', 'timestamp', 'fingerprint_hash']
```

### **Error Handling**
Comprehensive error handling for all operations:

```python
result = core.retrieve_capsule("NonExistentInstance")
if not result.success:
    print(f"Error: {result.error_message}")
```

---

## 🔧 Configuration

### **Dependencies**
- **Required:** Python 3.7+, standard library modules
- **No external dependencies:** Works with basic Python installation
- **Integration:** Works with CapsuleForge for capsule generation

### **Directory Structure**
- **capsules/:** Stored capsule files organized by instance
- **indexes/:** JSON index files for fast retrieval
- **Automatic creation:** Directories created on-demand

### **File Naming**
- **Capsules:** `{instance_name}_{timestamp}.capsule`
- **Indexes:** `{instance_name}_index.json`
- **Timestamps:** ISO 8601 format with timezone

---

## 🚀 Advanced Features

### **Multi-version Support**
```python
# Retrieve latest version
result = core.retrieve_capsule("Nova")

# Retrieve specific version
result = core.retrieve_capsule("Nova", uuid="specific-uuid")

# Retrieve by tag
result = core.retrieve_capsule("Nova", tag="post-mirror-break")
```

### **Tag Management**
```python
# Add tags
core.add_tag("Nova", "uuid", "post-mirror-break")
core.add_tag("Nova", "uuid", "pre-break")

# Remove tags
core.remove_tag("Nova", "uuid", "test-tag")

# List by tag
capsules = core.list_capsules("Nova", tag="post-mirror-break")
```

### **Instance Management**
```python
# List all instances
instances = core.list_instances()

# Get instance information
info = core.get_instance_info("Nova")
print(f"Total capsules: {info['total_capsules']}")
print(f"Tags: {info['tags']}")
```

### **Capsule Deletion**
```python
# Delete specific capsule
success = core.delete_capsule("Nova", "uuid-to-delete")
if success:
    print("✅ Capsule deleted successfully")
```

---

## 📋 Examples

### **Example 1: Basic Storage and Retrieval**
```python
from vvault_core import store_capsule, retrieve_capsule

# Store capsule from CapsuleForge
capsule_data = {...}  # From CapsuleForge
stored_path = store_capsule(capsule_data)
print(f"Capsule stored: {stored_path}")

# Retrieve latest capsule
result = retrieve_capsule("Nova")
if result.success:
    print(f"Retrieved: {result.metadata.instance_name}")
    print(f"UUID: {result.metadata.uuid}")
    print(f"Tags: {result.metadata.tags}")
    print(f"Integrity: {'Valid' if result.integrity_valid else 'Invalid'}")
else:
    print(f"Error: {result.error_message}")
```

### **Example 2: Tag Management**
```python
from vvault_core import VVAULTCore

core = VVAULTCore()

# Store capsule
stored_path = core.store_capsule(capsule_data)

# Retrieve to get UUID
result = core.retrieve_capsule("Nova")
if result.success:
    uuid = result.metadata.uuid
    
    # Add tags
    core.add_tag("Nova", uuid, "post-mirror-break")
    core.add_tag("Nova", uuid, "stable-version")
    
    # List capsules with specific tag
    tagged_capsules = core.list_capsules("Nova", tag="post-mirror-break")
    print(f"Found {len(tagged_capsules)} capsules with 'post-mirror-break' tag")
    
    # Retrieve by tag
    result = core.retrieve_capsule("Nova", tag="post-mirror-break")
    if result.success:
        print(f"Retrieved by tag: {result.metadata.instance_name}")
```

### **Example 3: Instance Management**
```python
from vvault_core import VVAULTCore

core = VVAULTCore()

# List all instances
instances = core.list_instances()
print(f"Found instances: {instances}")

# Get detailed information for each instance
for instance_name in instances:
    info = core.get_instance_info(instance_name)
    if info:
        print(f"\nInstance: {info['instance_name']}")
        print(f"  Total capsules: {info['total_capsules']}")
        print(f"  Latest UUID: {info['latest_uuid']}")
        print(f"  Tags: {info['tags']}")
        print(f"  Created: {info['created_at']}")
        print(f"  Updated: {info['updated_at']}")
    
    # List all capsules for this instance
    capsules = core.list_capsules(instance_name)
    print(f"  Capsules: {len(capsules)}")
    for capsule in capsules[:3]:  # Show first 3
        print(f"    - {capsule['uuid'][:8]}... ({len(capsule['tags'])} tags)")
```

### **Example 4: Error Handling**
```python
from vvault_core import VVAULTCore

core = VVAULTCore()

# Test various error conditions
test_cases = [
    ("NonExistentInstance", None, None),
    ("Nova", None, "non-existent-tag"),
    ("Nova", "non-existent-uuid", None)
]

for instance_name, uuid, tag in test_cases:
    if uuid:
        result = core.retrieve_capsule(instance_name, uuid=uuid)
    elif tag:
        result = core.retrieve_capsule(instance_name, tag=tag)
    else:
        result = core.retrieve_capsule(instance_name)
    
    if not result.success:
        print(f"✅ Correctly handled error: {result.error_message}")
    else:
        print(f"❌ Should have failed for: {instance_name}, {uuid}, {tag}")
```

---

## 🔮 Future Enhancements

### **Planned Features**
1. **Capsule Comparison:** Diff between different capsule versions
2. **Capsule Merging:** Combine multiple capsules into unified profile
3. **Encryption Support:** Optional encryption for sensitive capsules
4. **Compression:** Automatic compression for large memory logs
5. **Version Control:** Git-like versioning for capsule evolution
6. **Real-time Monitoring:** Live capsule validation and monitoring
7. **Backup Management:** Automatic backup and restore functionality
8. **Search Capabilities:** Full-text search across capsule contents

### **Advanced Integration**
1. **CapsuleForge Integration:** Seamless integration with capsule generation
2. **CapsuleLoader Integration:** Direct integration with VXRunner loading
3. **Database Backend:** Optional database storage for large-scale deployments
4. **API Interface:** RESTful API for remote capsule management
5. **Web Interface:** Web-based capsule management interface

---

## 📞 Support

### **Troubleshooting**
- **Missing instance:** Ensure instance exists in capsules directory
- **Invalid capsule structure:** Check capsule was generated by CapsuleForge
- **Integrity validation failed:** Capsule may be corrupted or modified
- **Tag not found:** Check tag exists in instance index
- **File not found:** Check capsule file exists in instance directory

### **Logging**
VVAULT Core provides comprehensive logging:
- **INFO:** Normal operation messages
- **WARNING:** Non-critical issues (missing tags)
- **ERROR:** Critical errors that prevent operation

### **Debug Mode**
Enable debug logging for detailed operation tracking:
```python
import logging
logging.getLogger('vvault_core').setLevel(logging.DEBUG)
```

---

## 🔗 Integration with Other Modules

### **CapsuleForge Integration**
```python
from capsuleforge import generate_capsule
from vvault_core import store_capsule

# Generate capsule with CapsuleForge
capsule_path = generate_capsule("Nova", traits, memory_log, personality)

# Load and store with VVAULT Core
with open(capsule_path, 'r') as f:
    capsule_data = json.load(f)
stored_path = store_capsule(capsule_data)
```

### **CapsuleLoader Integration**
```python
from vvault_core import retrieve_capsule
from capsuleloader import restore_construct

# Retrieve capsule from VVAULT Core
result = retrieve_capsule("Nova", tag="post-mirror-break")
if result.success:
    # Restore construct with CapsuleLoader
    construct_state = restore_construct(result.capsule_data)
    print(f"Restored: {construct_state.instance_name}")
```

---

**✅ VVAULT Core is now fully operational and ready for production use!**

The system provides comprehensive capsule storage and retrieval management with version history, tagging support, and integrity validation. Each capsule is securely stored with automatic indexing and can be efficiently retrieved using multiple methods including tags, UUIDs, and version tracking. 