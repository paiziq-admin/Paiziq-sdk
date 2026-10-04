"""Run the scenario and audit real write/read responses and dashboard fields."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_INGEST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_INGEST))
sys.path.insert(0, str(_INGEST / 'tests'))

from e2e_support.contracts import dashboard_checks, fields, validate, validate_outcomes  # noqa: E402
from e2e_support.simulated_agent import SimulatedPaymentAgent  # noqa: E402

OUT = Path(os.environ.get('PAIZIQ_E2E_REPORT', str(_INGEST.parents[1] / 'docs/e2e/ENDPOINT_VERIFICATION.md')))


def main():
    base = os.environ.get('PAIZIQ_ENDPOINT', 'http://127.0.0.1:8800')
    agent = SimulatedPaymentAgent(base, os.environ.get('PAIZIQ_API_KEY', 'dev-key'))
    rows, failures = [], []
    report = None

    def record(method, path, status, note, error=None):
        note = str(error or note).replace('|', '\\|').replace('\n', ' ')
        rows.append(f"| {method} | `{path}` | {status} | {note} | {'FAIL' if error else 'pass'} |")
        if error:
            failures.append(f'{method} {path}: {note}')

    try:
        report = agent.run()
        for method, path, status, body in agent.transport.responses:
            if method != 'POST':
                continue
            try:
                assert status == 200, f'expected 200; received {status}'
                if path == '/v1/traces':
                    assert body['accepted'] > 0
                    note = 'SDK spans accepted'
                else:
                    fields(body, 'success', 'data', 'error')
                    assert body['success'] is True and body['error'] is None
                    fields(body['data'], *(['version', 'document'] if path.endswith('/publish') else ['id']))
                    note = 'success envelope and persisted resource fields'
                record(method, path, status, note)
            except Exception as exc:
                record(method, path, status, '', repr(exc))
        validate_outcomes(report)
        for method, path, body, kind in dashboard_checks(report):
            status = 'no response'
            try:
                response = agent.transport.request(method, path, json_body=body)
                status = response.status
                validate(kind, status, response.json(), report)
                record(method, path, status, f'{kind}: envelope, fields, scenario consistency')
            except Exception as exc:
                record(method, path, status, '', repr(exc))
    except Exception as exc:
        record('WORKFLOW', base, 'incomplete', '', repr(exc))
    finally:
        agent.sdk.shutdown()

    lines = ['# Endpoint verification', '',
             f'Executed: {datetime.now(timezone.utc).isoformat()}', f'Base URL: `{base}`', '',
             '| Method | Path | Actual status (expected 200) | Verified | Result |',
             '| --- | --- | --- | --- | --- |', *rows, '']
    if report:
        lines += ['Local SDK and server verdicts, reasons, flags, final states, policy version, and mock execution checked.', '',
                  '```json', json.dumps(report, indent=2), '```', '']
    lines += ['## Failures', *[f'- {f}' for f in failures]] if failures else ['All recorded checks passed.']
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text('\n'.join(lines) + '\n')
    print(f'wrote {OUT}: {len(rows)} responses, {len(failures)} failures')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
