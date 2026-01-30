-- VVAULT Service API Tables
-- Migration for FXShinobi/Chatty integration
-- Run this in Supabase SQL Editor

-- Strategy Configs Table
-- Stores strategy configurations for services like FXShinobi
CREATE TABLE IF NOT EXISTS strategy_configs (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    service VARCHAR(100) NOT NULL,
    strategy_id VARCHAR(100) NOT NULL,
    params JSONB NOT NULL DEFAULT '{}',
    symbols TEXT[] DEFAULT '{}',
    risk_limits JSONB DEFAULT '{"max_position_size": 0.1, "max_daily_loss": 0.02}',
    enabled BOOLEAN DEFAULT true,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(service, strategy_id)
);

-- Service Credentials Table
-- Stores encrypted credentials for external services
CREATE TABLE IF NOT EXISTS service_credentials (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    key VARCHAR(255) NOT NULL,
    service VARCHAR(100) NOT NULL,
    encrypted_value TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(key, service)
);

-- Enable Row Level Security
ALTER TABLE strategy_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE service_credentials ENABLE ROW LEVEL SECURITY;

-- Policy: Service role can read/write all
CREATE POLICY "Service role full access to strategy_configs" ON strategy_configs
    FOR ALL USING (auth.role() = 'service_role');

CREATE POLICY "Service role full access to service_credentials" ON service_credentials
    FOR ALL USING (auth.role() = 'service_role');

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_strategy_configs_service ON strategy_configs(service);
CREATE INDEX IF NOT EXISTS idx_service_credentials_key ON service_credentials(key);
CREATE INDEX IF NOT EXISTS idx_service_credentials_service ON service_credentials(service);

-- Insert default FXShinobi config
INSERT INTO strategy_configs (service, strategy_id, params, symbols, risk_limits, enabled)
VALUES (
    'fxshinobi',
    'default',
    '{"timeframe": "1H", "strategy_type": "momentum", "lookback_periods": 20}',
    ARRAY['EUR_USD', 'GBP_USD', 'USD_JPY'],
    '{"max_position_size": 0.1, "max_daily_loss": 0.02, "max_trades_per_day": 10}',
    true
)
ON CONFLICT (service, strategy_id) DO NOTHING;
