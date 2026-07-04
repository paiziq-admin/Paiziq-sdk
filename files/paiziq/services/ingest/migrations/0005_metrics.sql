-- 0005: metrics rollups for dashboard trends (PZ-079).

CREATE TABLE IF NOT EXISTS metrics_rollups (
    env_id    TEXT NOT NULL,
    bucket_ms INTEGER NOT NULL,
    metric    TEXT NOT NULL,
    value     REAL NOT NULL,
    PRIMARY KEY (env_id, bucket_ms, metric)
);
CREATE INDEX IF NOT EXISTS idx_metrics_rollups_bucket ON metrics_rollups (bucket_ms);
