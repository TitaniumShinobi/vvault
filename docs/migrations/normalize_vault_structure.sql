-- VVAULT Directory Structure Normalization Migration
-- Run this in Supabase SQL Editor to fix file organization
-- 
-- This migration:
--   1. Updates file paths to use proper user directory structure
--   2. Marks system files (instances/, vvault/, .DS_Store) as is_system=true
--   3. Associates user files with their user_id
--
-- PREREQUISITE: Run add_vault_multitenancy.sql first!

-- ============================================================
-- STEP 1: Get Devon's user_id (update the variable below)
-- Run this query first to get the user_id:
-- SELECT id, email, name FROM users WHERE email LIKE '%woodson%' OR name LIKE '%Devon%';
-- ============================================================

-- Replace this with Devon's actual user ID from the query above
-- DO $$
-- DECLARE
--     devon_user_id UUID := '<DEVON_USER_ID>';
--     devon_user_slug TEXT := 'devon_woodson_1762969514958';
-- BEGIN

-- ============================================================
-- STEP 2: Mark system files (should NOT appear in user vault)
-- ============================================================

UPDATE public.vault_files
SET is_system = TRUE,
    user_id = NULL
WHERE filename LIKE 'instances/%'
   OR filename LIKE 'vvault/instances/%'
   OR filename LIKE 'vvault/config/%'
   OR filename LIKE 'vvault/system/%'
   OR filename LIKE 'config/%'
   OR filename LIKE '.DS_Store'
   OR filename LIKE '%/.DS_Store';

-- ============================================================
-- STEP 3: Normalize Katana transcripts to proper user path
-- Move from root to user's instances folder
-- ============================================================

-- Example: Move chatgpt transcripts for katana-001
-- UPDATE public.vault_files
-- SET filename = REPLACE(
--     filename,
--     'instances/katana-001/chatgpt/',
--     'vvault/users/shard_0000/devon_woodson_1762969514958/instances/katana-001/chatgpt/'
-- ),
-- user_id = devon_user_id,
-- is_system = FALSE
-- WHERE filename LIKE 'instances/katana-001/chatgpt/%'
--   AND user_id IS NULL;

-- ============================================================
-- STEP 4: Normalize all user construct files
-- ============================================================

-- Move katana-001 files to Devon's user space
-- UPDATE public.vault_files
-- SET filename = 'vvault/users/shard_0000/' || devon_user_slug || '/instances/katana-001/' || 
--     SUBSTRING(filename FROM 'instances/katana-001/(.*)'),
-- user_id = devon_user_id,
-- is_system = FALSE
-- WHERE filename LIKE 'instances/katana-001/%'
--   AND NOT filename LIKE 'vvault/users/%';

-- Move zen-001 files to Devon's user space
-- UPDATE public.vault_files  
-- SET filename = 'vvault/users/shard_0000/' || devon_user_slug || '/instances/zen-001/' ||
--     SUBSTRING(filename FROM 'instances/zen-001/(.*)'),
-- user_id = devon_user_id,
-- is_system = FALSE
-- WHERE filename LIKE 'instances/zen-001/%'
--   AND NOT filename LIKE 'vvault/users/%';

-- ============================================================
-- STEP 5: Create default user folders if needed
-- (These would be created on first upload, but we can seed them)
-- ============================================================

-- INSERT INTO public.vault_files (filename, user_id, is_system, file_type, metadata)
-- VALUES 
--   ('vvault/users/shard_0000/' || devon_user_slug || '/account/.keep', devon_user_id, FALSE, 'text', '{"type":"folder_marker"}'),
--   ('vvault/users/shard_0000/' || devon_user_slug || '/library/documents/.keep', devon_user_id, FALSE, 'text', '{"type":"folder_marker"}'),
--   ('vvault/users/shard_0000/' || devon_user_slug || '/library/media/.keep', devon_user_id, FALSE, 'text', '{"type":"folder_marker"}')
-- ON CONFLICT DO NOTHING;

-- END $$;

-- ============================================================
-- VERIFICATION QUERIES
-- ============================================================

-- Check system vs user files:
-- SELECT 
--   CASE 
--     WHEN is_system THEN 'system'
--     WHEN user_id IS NOT NULL THEN 'user-owned'
--     ELSE 'unassigned'
--   END as ownership,
--   COUNT(*),
--   ARRAY_AGG(DISTINCT SPLIT_PART(filename, '/', 1)) as root_folders
-- FROM vault_files 
-- GROUP BY 1;

-- Check Devon's files are properly organized:
-- SELECT filename, is_system, user_id IS NOT NULL as has_owner
-- FROM vault_files
-- WHERE filename LIKE 'vvault/users/shard_0000/devon%'
-- ORDER BY filename;

-- Check for any remaining root-level construct files (should be 0 for non-system):
-- SELECT filename
-- FROM vault_files
-- WHERE filename LIKE 'instances/%'
--   AND is_system = FALSE;
