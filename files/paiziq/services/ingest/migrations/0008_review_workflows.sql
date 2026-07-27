-- 0008: human-review queue workflow (PZ-101).
--
-- Review resolution still uses the original append-only terminal states
-- (approved/rejected). These columns capture assignment, triage priority,
-- and non-terminal operator actions without weakening the original state
-- constraint.

ALTER TABLE reviews
ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'
CHECK (priority IN ('low', 'normal', 'high', 'urgent'));

ALTER TABLE reviews
ADD COLUMN last_action TEXT NOT NULL DEFAULT 'opened'
CHECK (
    last_action IN (
        'opened',
        'claimed',
        'released',
        'reassigned',
        'requested_info',
        'escalated',
        'approved',
        'rejected'
    )
);

ALTER TABLE reviews ADD COLUMN assigned_at_ms INTEGER;
ALTER TABLE reviews ADD COLUMN updated_at_ms INTEGER NOT NULL DEFAULT 0;

UPDATE reviews SET updated_at_ms = created_at_ms WHERE updated_at_ms = 0;

CREATE INDEX IF NOT EXISTS idx_reviews_reviewer
ON reviews (reviewer_id, state);

CREATE INDEX IF NOT EXISTS idx_reviews_priority_sla
ON reviews (state, priority, sla_deadline_ms);

CREATE INDEX IF NOT EXISTS idx_reviews_payment_state
ON reviews (payment_id, state);
