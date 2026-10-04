-- Preserve prior authorization snapshots; new evaluations record frozen history.
ALTER TABLE decision_contexts ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}';
