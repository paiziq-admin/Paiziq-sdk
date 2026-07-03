-- 0003: API key rotation grace window (contract §6).
-- During rotation the previous secret may keep validating until
-- grace_until_ms; both columns are NULL outside a grace window.

ALTER TABLE api_keys ADD COLUMN previous_secret_hash TEXT;
ALTER TABLE api_keys ADD COLUMN grace_until_ms INTEGER;
