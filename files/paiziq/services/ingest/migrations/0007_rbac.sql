-- 0007: RBAC roles on API keys (PZ-073).

ALTER TABLE api_keys ADD COLUMN role TEXT
    CHECK (role IN ('admin', 'developer', 'reviewer', 'read_only'));

UPDATE api_keys SET role = CASE scope
    WHEN 'admin' THEN 'admin'
    WHEN 'read' THEN 'read_only'
    WHEN 'ingest' THEN 'developer'
END WHERE role IS NULL;
