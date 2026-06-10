# Paiziq — Rules for Agents and Collaborators

STRICT. These rules bind every coding agent (Cursor, Claude Code, Codex,
or other) and every human collaborator working in this repository.
The canonical, scope-aware versions live in `.cursor/rules/` at the
workspace root; this file is the harness-agnostic summary.

## Change workflow (every change)

1. Use `python3` / `pip3` only. Drive tasks through `make` —
   run `make help` for the list of automated commands.
2. Ship code, tests, CHANGELOG entry, and tracker update **together**:
   - `CHANGELOG.md` — bullet under the target version (Keep a Changelog,
     ISO dates). No silent changes.
   - `docs/05_PROGRESS_TRACKER.md` — update item status (✅/🔄/⬜) and
     append to the verification log when a gate runs.
3. `make check` must be green before work is declared done. Never
   report completion with a red gate.
4. Semantic versioning. `sdk/pyproject.toml` and `paiziq.__version__`
   must always match.

## Coding practices (SDK core: `sdk/src/paiziq`)

- **Zero-dependency core** — stdlib-only at import time; optional deps
  are lazy-imported and exposed as pip extras.
- **Deterministic decisions** — no LLM calls or randomness in `engine/`;
  every verdict carries machine-readable reasons.
- **Observability never breaks the agent** — exporters/scrubbers/notifiers
  swallow and log their own failures.
- **Append-only audit** — audit stores never update or delete.
- **Protocol seams** — new stores/gateways/notifiers implement the
  existing protocols and accept injected clients for testability.
- Public API additions are re-exported from the top-level `paiziq`
  package and covered by a test or example.

## Documentation updates

- Numbered docs have fixed roles (scope/architecture/plan/guide/tracker);
  do not create overlapping documents.
- Doc snippets must stay runnable against the current API; update the
  docs site (`docs/site/`) in the same change when signatures change.
- Never document invented endpoints, parameters, or versions.

## Forbidden

- Secrets, real card numbers, SSNs, or live API keys anywhere in the
  repo — tests construct sensitive-looking strings at runtime.
- Rewriting CHANGELOG history, tracker verification-log rows, or
  audit-store semantics.
