-- VVAULT Supabase Database Cleanup Script
-- Generated: 2026-02-08
-- WARNING: Run these manually. Agents must NOT execute this.
-- Review each section before running.

-- ============================================================
-- SECTION 1: Remove .DS_Store junk files (11 files)
-- ============================================================
DELETE FROM vault_files WHERE id = 'dff40b8a-9828-460d-bb07-76facdfcd681';  -- capsules/.DS_Store
DELETE FROM vault_files WHERE id = '140e8f11-e64f-4560-babd-2efe7608e8d6';  -- instances/.DS_Store
DELETE FROM vault_files WHERE id = 'd47bf28c-b29b-4d4b-9efa-57a6b0c69443';  -- instances/zen-001/.DS_Store
DELETE FROM vault_files WHERE id = '3fd66fc3-0125-4068-b8d8-304bc9e7b438';  -- instances/katana-001/.DS_Store
DELETE FROM vault_files WHERE id = 'a010dc65-8004-4874-85e4-dfc0e118887a';  -- library/documents/woodson_and_associates/.DS_Store
DELETE FROM vault_files WHERE id = 'e85320fe-adbe-4820-9ec3-25f481df18a5';  -- instances/lin-001/.DS_Store
DELETE FROM vault_files WHERE id = 'b2186833-af53-446c-be76-477b66acaf52';  -- instances/zen-001/chatty/.DS_Store
DELETE FROM vault_files WHERE id = '024c23b3-f5b0-4a94-8c35-9fbc894ed8b0';  -- library/documents/.DS_Store
DELETE FROM vault_files WHERE id = '98842f1d-2845-46a1-b9e4-23807e3704ce';  -- root .DS_Store
DELETE FROM vault_files WHERE id = '1738992d-b521-4825-8f04-c81fb55d502f';  -- user root .DS_Store
DELETE FROM vault_files WHERE id = '77d318fa-ddd1-4767-8fb4-3ef61347ee9a';  -- library/.DS_Store

-- ============================================================
-- SECTION 2: Remove duplicate files created by Chatty agent
-- (12 files with full paths stuffed in filename column)
-- The correct versions with proper storage_paths already exist.
-- ============================================================
DELETE FROM vault_files WHERE id = 'fdb0920c-e81c-4c7e-8619-3bc1a286c12d';  -- dup Missing-context-examples-K1.md
DELETE FROM vault_files WHERE id = 'c4366aeb-2245-4e66-933c-3f2204a2e012';  -- dup test_1_08-03-2025.md
DELETE FROM vault_files WHERE id = 'c4684dd6-3e11-4161-928f-c725dcc71d64';  -- dup test_09-27-2025-K1.md
DELETE FROM vault_files WHERE id = '72247243-dd61-4f3d-b8f7-356dc01582a9';  -- dup VXRunner-AI-integration-K1.md
DELETE FROM vault_files WHERE id = 'bc1657c2-6b29-4fdd-a9b3-74e1a0117144';  -- dup Refusing-violent-requests-K1.md
DELETE FROM vault_files WHERE id = '7d3ae188-e4d1-46e3-ad0c-8e221fd6bb0f';  -- dup BInary-location-request-K1.md
DELETE FROM vault_files WHERE id = '8f52a232-be4a-49e3-b282-123be4079443';  -- dup Chat-preview-lost-K1.md
DELETE FROM vault_files WHERE id = '6990873c-c964-4bd1-ae1b-dfcaf9054590';  -- dup Image-analysis-inquiry-K1.md
DELETE FROM vault_files WHERE id = '4ee2f88c-a74c-4649-97b4-f8c18eb53c03';  -- dup Leaving-Nova-explanation-K1.md
DELETE FROM vault_files WHERE id = '1a1679bb-d769-4c79-a56f-c7d28518c230';  -- dup Binary-geolocation-request-K1.md
DELETE FROM vault_files WHERE id = 'ea33019a-b034-4ab6-a341-827f33ef6960';  -- dup Anger-and-offense-response-K1.md
DELETE FROM vault_files WHERE id = 'd7c63fcf-f511-4819-b9e3-018474576ec6';  -- dup Last-task-review-K1.md

-- ============================================================
-- SECTION 3: Remove duplicate personality.json for katana-001
-- (keep the older one: 3eb6243d)
-- ============================================================
DELETE FROM vault_files WHERE id = '136b5757-5247-4782-a79f-8a1b243ded88';  -- dup personality.json

-- ============================================================
-- SECTION 4: Remove orphaned file under wrong user
-- ============================================================
DELETE FROM vault_files WHERE id = '6270c354-a7cc-455c-b68e-107fc0a1a92a';  -- continuity_20260202.md (NULL storage_path, orphan)

-- ============================================================
-- SECTION 5: Fix chatty conversation files (NULL storage_paths)
-- ============================================================
UPDATE vault_files
SET filename = 'chat_with_katana-001.md',
    storage_path = '7e34f6b8-e33a-48b5-8ddb-95b94d18e296/devon_woodson_1762969514958/instances/katana-001/chatty/chat_with_katana-001.md'
WHERE id = 'eaa3d235-be9f-4c78-99b8-8d67e14b28a0';

UPDATE vault_files
SET filename = 'chat_with_zen-001.md',
    storage_path = '7e34f6b8-e33a-48b5-8ddb-95b94d18e296/devon_woodson_1762969514958/instances/zen-001/chatty/chat_with_zen-001.md'
WHERE id = '8b5e1a2a-9121-4bd1-a839-4a9d9ae81b4a';

-- ============================================================
-- SECTION 6: Decide what to do with misplaced files in lin-001 root
-- These are NOT VVAULT-managed files. Options: delete or move.
-- ============================================================
-- Option A: DELETE them (they don't belong in Lin's construct folder)
-- DELETE FROM vault_files WHERE id = '5a248e88-a8dd-4b15-95d9-c8f2978e40a5';  -- cursor_building_persistent_identity_in.md
-- DELETE FROM vault_files WHERE id = '56fa4652-075a-425d-b7d6-b10e7e2e416f';  -- Pound it solid.txt
-- DELETE FROM vault_files WHERE id = 'ec440e39-da81-4a35-962a-b92b74c3122e';  -- README.md

-- Option B: Move them to library/documents instead
-- UPDATE vault_files SET storage_path = '7e34f6b8-e33a-48b5-8ddb-95b94d18e296/devon_woodson_1762969514958/library/documents/cursor_building_persistent_identity_in.md' WHERE id = '5a248e88-a8dd-4b15-95d9-c8f2978e40a5';
-- UPDATE vault_files SET storage_path = '7e34f6b8-e33a-48b5-8ddb-95b94d18e296/devon_woodson_1762969514958/library/documents/Pound it solid.txt' WHERE id = '56fa4652-075a-425d-b7d6-b10e7e2e416f';
-- UPDATE vault_files SET storage_path = '7e34f6b8-e33a-48b5-8ddb-95b94d18e296/devon_woodson_1762969514958/library/documents/README.md' WHERE id = 'ec440e39-da81-4a35-962a-b92b74c3122e';
