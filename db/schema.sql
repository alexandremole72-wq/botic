-- BOTique database schema
-- Run once against a new Aiven PostgreSQL database.
-- If these tables already exist, do not run this file again without checking the schema.

CREATE TABLE suppliers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    official_domain TEXT NOT NULL,
    bank_fingerprint TEXT NOT NULL,
    approved BOOLEAN NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE scenarios (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    website_domain TEXT NOT NULL,
    website_bank_fingerprint TEXT NOT NULL,
    amount NUMERIC(10,2) NOT NULL,
    expected_action TEXT NOT NULL CHECK (expected_action IN ('PAY', 'BLOCK')),
    risk_reason TEXT
);

CREATE TABLE test_runs (
    id SERIAL PRIMARY KEY,
    scenario_id INT REFERENCES scenarios(id),
    agent_action TEXT NOT NULL CHECK (agent_action IN ('PAY', 'BLOCK')),
    expected_action TEXT NOT NULL CHECK (expected_action IN ('PAY', 'BLOCK')),
    critical_violation BOOLEAN NOT NULL,
    explanation TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
