-- VVAULT Multi-Tenancy Migration
-- Run this in your Supabase SQL Editor to enable proper user isolation
-- 
-- IMPORTANT: Run this AFTER add_auth_columns.sql
-- This migration:
--   1. Adds user_id and is_system columns to vault_files
--   2. Enables Row Level Security (RLS)
--   3. Creates policies for user isolation and dev access
--
-- Before running: Note your Supabase user ID for the dev override policy

-- ============================================================
-- STEP 1: Add columns for multi-tenancy
-- ============================================================

ALTER TABLE public.vault_files
  ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES public.users(id) ON DELETE SET NULL;

ALTER TABLE public.vault_files
  ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT FALSE;

-- Create index for faster user lookups
CREATE INDEX IF NOT EXISTS idx_vault_files_user_id ON public.vault_files(user_id);
CREATE INDEX IF NOT EXISTS idx_vault_files_is_system ON public.vault_files(is_system);

-- ============================================================
-- STEP 2: Backfill existing data
-- Replace '<YOUR_USER_ID>' with your actual users.id from Supabase
-- ============================================================

-- Mark instances/ and other system folders as system-owned
UPDATE public.vault_files
SET is_system = TRUE,
    user_id = NULL
WHERE filename LIKE 'instances/%'
   OR filename LIKE 'vvault/%'
   OR filename LIKE 'config/%';

-- Assign remaining files to your dev account
-- UNCOMMENT and replace with your user ID after checking it:
-- UPDATE public.vault_files
-- SET user_id = '<YOUR_USER_ID>'
-- WHERE user_id IS NULL
--   AND is_system = FALSE;

-- ============================================================
-- STEP 3: Enable Row Level Security
-- ============================================================

ALTER TABLE public.vault_files ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- STEP 4: Create RLS Policies
-- ============================================================

-- Policy 1: Users can see their own files
CREATE POLICY "Users see their own files"
ON public.vault_files
FOR SELECT
TO authenticated
USING (user_id = auth.uid());

-- Policy 2: Users can insert their own files
CREATE POLICY "Users insert their own files"
ON public.vault_files
FOR INSERT
TO authenticated
WITH CHECK (user_id = auth.uid());

-- Policy 3: Users can update their own files
CREATE POLICY "Users update their own files"
ON public.vault_files
FOR UPDATE
TO authenticated
USING (user_id = auth.uid())
WITH CHECK (user_id = auth.uid());

-- Policy 4: Users can delete their own files
CREATE POLICY "Users delete their own files"
ON public.vault_files
FOR DELETE
TO authenticated
USING (user_id = auth.uid());

-- Policy 5: Service role has full access (for backend operations)
CREATE POLICY "Service role full access"
ON public.vault_files
FOR ALL
TO service_role
USING (TRUE)
WITH CHECK (TRUE);

-- ============================================================
-- OPTIONAL: Dev/Admin Override Policy
-- Uncomment and replace with your user ID for full access
-- ============================================================

-- CREATE POLICY "Dev full access"
-- ON public.vault_files
-- FOR ALL
-- TO authenticated
-- USING (auth.uid() = '<YOUR_USER_ID>')
-- WITH CHECK (auth.uid() = '<YOUR_USER_ID>');

-- ============================================================
-- VERIFICATION QUERIES (run these after migration)
-- ============================================================

-- Check column additions:
-- SELECT column_name, data_type FROM information_schema.columns 
-- WHERE table_name = 'vault_files' AND column_name IN ('user_id', 'is_system');

-- Check RLS is enabled:
-- SELECT relname, relrowsecurity FROM pg_class WHERE relname = 'vault_files';

-- Check policies:
-- SELECT policyname, cmd, qual FROM pg_policies WHERE tablename = 'vault_files';

-- Count files by ownership type:
-- SELECT 
--   CASE 
--     WHEN is_system THEN 'system'
--     WHEN user_id IS NOT NULL THEN 'user-owned'
--     ELSE 'unassigned'
--   END as ownership,
--   COUNT(*) 
-- FROM vault_files 
-- GROUP BY 1;
