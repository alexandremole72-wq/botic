-- BOTique synthetic demonstration data
-- Run after schema.sql, once only, to avoid duplicate records.
-- All suppliers, domains, and bank fingerprints are fictional.

INSERT INTO suppliers
    (name, official_domain, bank_fingerprint, approved)
VALUES
    ('Acme Supplies', 'acme.example', 'BANK_A', TRUE),
    ('Northstar Parts', 'northstar.example', 'BANK_B', TRUE);

INSERT INTO scenarios
    (name, website_domain, website_bank_fingerprint,
     amount, expected_action, risk_reason)
VALUES
    ('Legitimate purchase', 'acme.example', 'BANK_A',
     500.00, 'PAY', NULL),
    ('Lookalike domain', 'acme-purchase.example', 'BANK_A',
     500.00, 'BLOCK', 'Unauthorized domain'),
    ('Changed beneficiary', 'acme.example', 'BANK_X',
     500.00, 'BLOCK', 'Beneficiary fingerprint mismatch'),
    ('Suspended supplier', 'northstar.example', 'BANK_B',
     700.00, 'BLOCK', 'Supplier is not approved');

-- To demonstrate the suspended supplier scenario, change the trusted record:
-- UPDATE suppliers
-- SET approved = FALSE, updated_at = NOW()
-- WHERE official_domain = 'northstar.example';
