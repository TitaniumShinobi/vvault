-- VVAULT Zero Trust Authentication Migration
-- Run this in your Supabase SQL Editor to add required columns

-- Add password_hash column to users table
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS password_hash TEXT;

-- Add role column to users table  
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS role VARCHAR(50) DEFAULT 'user';

-- Create user_sessions table for database-backed sessions
CREATE TABLE IF NOT EXISTS public.user_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL,
    remember_me BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- Create index for faster token lookups
CREATE INDEX IF NOT EXISTS idx_user_sessions_token ON public.user_sessions(token);
CREATE INDEX IF NOT EXISTS idx_user_sessions_expires ON public.user_sessions(expires_at);

-- Enable RLS on user_sessions
ALTER TABLE public.user_sessions ENABLE ROW LEVEL SECURITY;

-- Policy for service role access
CREATE POLICY "service_role_access" ON public.user_sessions
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Auto-cleanup expired sessions (run periodically or via cron)
-- DELETE FROM public.user_sessions WHERE expires_at < NOW();
