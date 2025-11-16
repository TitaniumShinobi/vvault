# Capsule Recovery Investigation

## Status: 6 Capsules Missing

Based on the investigation, here's what we found:

## What We Know

1. **Capsules Directory**: `/vvault/capsules/` exists but is **empty** (only contains README.md)
2. **Git History**: Capsules are **gitignored** (`.gitignore` line 185), so they were never tracked
3. **Documentation References**: Docs mention these capsules existed:
   - `nova-001.capsule` (6.5KB)
   - `aurora-001.capsule` (3.5KB)
   - Plus 4 more (total of 6 missing)

## Possible Locations to Check

### 1. **Time Machine / System Backups**
```bash
# Check Time Machine backups
tmutil listbackups
# Or check if there's a backup of the capsules directory
```

### 2. **Archive Directory** (mentioned in docs)
The docs mention `capsules/archive/` - check if it exists:
```bash
ls -la /Users/devonwoodson/Documents/GitHub/vvault/capsules/archive/
```

### 3. **Index Files** (might contain capsule metadata)
- `/vvault/indexes/Nova_index.json` - Contains Nova index data
- Check if indexes reference capsule file paths

### 4. **Memory Records** (capsules might have been migrated)
- `/vvault/memory_records/` - Check if capsules were converted to memory records

### 5. **Other VVAULT Directories**
- Check if there's a `VVAULT (macos)` directory elsewhere
- Check parent directories for backups

### 6. **Cleanup Scripts**
The cleanup scripts (`cleanup_capsules.py`) mention archiving capsules - check if they were moved to an archive location.

## Recovery Steps

### Step 1: Check All Possible Locations
```bash
# Search entire system for .capsule files
find ~ -name "*.capsule" 2>/dev/null

# Check Time Machine
tmutil listbackups

# Check if capsules were moved to construct directories
find /Users/devonwoodson/Documents/GitHub/vvault -name "*.capsule" -o -name "*capsule*.json"
```

### Step 2: Check Index Files
The index files might contain references to capsule locations or even capsule data.

### Step 3: Reconstruct from Memory Records
If capsules were migrated to memory records, we might be able to reconstruct them.

### Step 4: Check Git Stash
```bash
cd /Users/devonwoodson/Documents/GitHub/vvault
git stash list
git fsck --lost-found
```

## Expected Capsule Files

Based on documentation and code references, we're looking for:
1. `nova-001.capsule`
2. `aurora-001.capsule`
3. `katana-001.capsule` (or `katana-002.capsule`)
4. `sera-001.capsule`
5. `monday-001.capsule`
6. One more (possibly `chatty-001.capsule` or another construct)

## Next Steps

1. **Search system-wide** for `.capsule` files
2. **Check Time Machine** backups if available
3. **Review index files** for capsule metadata
4. **Check memory_records** for migrated data
5. **Reconstruct capsules** from available data if needed

## Note

Since capsules are gitignored, they were never committed to git. This means:
- They can't be recovered from git history
- They might be in local backups or Time Machine
- They might have been moved/archived by cleanup scripts
- They might have been migrated to memory records format

