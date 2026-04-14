-- Migration: Create manual_signals table for Human-driven trading logic
-- Target: Platinum members and Admins

CREATE TABLE IF NOT EXISTS public.manual_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('BUY', 'SELL')),
    entry_price NUMERIC NOT NULL,
    exit_price NUMERIC,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
    entry_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    roi NUMERIC,
    comment TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for faster queries
CREATE INDEX IF NOT EXISTS idx_manual_signals_symbol ON public.manual_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_manual_signals_status ON public.manual_signals(status);

-- Enable RLS
ALTER TABLE public.manual_signals ENABLE ROW LEVEL SECURITY;

-- Simple RLS Policy (Read access for authenticated users, Admin handle the rest)
-- Note: Further filtering by 'Platinum' role is done at the API level for security.
CREATE POLICY "Enable read access for all users" ON public.manual_signals
    FOR SELECT USING (auth.role() = 'authenticated');

-- Admin bypasses RLS via service role key in the backend
