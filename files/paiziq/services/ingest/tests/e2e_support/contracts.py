"""Shared executed-response checks for I2 and the live endpoint report."""
from urllib.parse import quote

from e2e_support.scenario import POLICY_DOCUMENT, TRANSACTIONS


def dashboard_checks(report):
    env = report['environment']['id']
    org = report['org']['id']
    policy = report['policy']['id']
    review = next(row for row in report['transactions'] if row['key'] == 't2')
    return [
        ('GET', '/health', None, 'health'),
        ('GET', '/v1/orgs?limit=200', None, 'orgs'),
        ('GET', f'/v1/orgs/{org}/environments?limit=200', None, 'environments'),
        ('GET', f'/v1/agents?env_id={env}&limit=200', None, 'agents'),
        ('GET', f'/v1/payments?env_id={env}&sort=created_desc&limit=8', None, 'payments'),
        ('GET', f"/v1/payments/{review['payment_id']}", None, 'payment'),
        ('GET', f"/v1/decisions?payment_id={review['payment_id']}", None, 'decisions'),
        ('GET', f"/v1/decisions/{review['decision_id']}", None, 'decision'),
        ('GET', f"/v1/traces/{review['request_id']}", None, 'trace-miss'),
        ('GET', f"/v1/traces/{review['payment_id']}", None, 'trace-miss'),
        ('GET', f"/v1/search/events?q={quote(chr(34) + review['request_id'] + chr(34))}&limit=10", None, 'search'),
        ('GET', f"/v1/traces/{review['trace_id']}", None, 'trace'),
        ('GET', f'/v1/reviews?state=open&env_id={env}', None, 'reviews'),
        ('GET', f"/v1/reviews/{review['review_id']}", None, 'review'),
        ('GET', '/v1/reviews/identity', None, 'identity'),
        ('GET', f'/v1/policies?env_id={env}', None, 'policies'),
        ('GET', f'/v1/policies/{policy}', None, 'policy'),
        ('GET', f'/v1/policies/{policy}/versions', None, 'versions'),
        ('POST', '/v1/policies/simulate', {
            'document': POLICY_DOCUMENT,
            'payment': {'merchant': review['merchant'], 'amount': review['amount'],
                        'currency': 'USD', 'intent_description': review['intent']},
        }, 'simulation'),
        ('GET', f'/v1/metrics/summary?env_id={env}', None, 'summary'),
        ('GET', f'/v1/metrics/timeseries?env_id={env}&metric=payments.total&interval=1h', None, 'timeseries'),
        ('GET', '/v1/audit-logs?limit=5', None, 'audit'),
        ('GET', '/v1/notifications', None, 'notifications'),
        ('GET', f"/v1/webhook-deliveries?env_id={env}&payment_id={review['payment_id']}&limit=200", None, 'deliveries'),
    ]


def fields(value, *names):
    assert isinstance(value, dict), f'expected object, received {type(value).__name__}'
    assert set(names) <= value.keys(), f'missing fields: {set(names) - value.keys()}'


def validate(kind, status, body, report):
    assert status == 200, f'expected 200, received {status}: {body}'
    review = next(row for row in report['transactions'] if row['key'] == 't2')
    if kind == 'health':
        assert body == {'status': 'ok'}
        return
    if kind.startswith('trace'):
        fields(body, 'trace_id', 'spans')
        assert isinstance(body['spans'], list)
        if kind == 'trace-miss':
            assert body['spans'] == []
        else:
            assert body['trace_id'] == review['trace_id']
            span = next(s for s in body['spans'] if s['name'] == 'paiziq.review_payment')
            fields(span, 'span_id', 'status', 'start_ms', 'end_ms', 'events', 'attributes')
            assert any(e.get('payload', {}).get('request_id') == review['request_id'] for e in span['events'])
        return
    if kind == 'notifications':
        assert isinstance(body['notifications'], list)
        return
    fields(body, 'success', 'data', 'error')
    assert body['success'] is True and body['error'] is None
    data = body['data']
    list_fields = {
        'orgs': ('id', 'name'), 'environments': ('id', 'org_id', 'name', 'kind'),
        'agents': ('id', 'env_id', 'name', 'status'),
        'payments': ('id', 'env_id', 'agent_id', 'principal_id', 'merchant', 'amount', 'currency', 'state', 'request_id', 'created_at_ms'),
        'decisions': ('id', 'verdict', 'reasons', 'risk_flags', 'policy_version'),
        'reviews': ('id', 'payment_id', 'state', 'sla_deadline_ms', 'sla_remaining_ms', 'priority', 'payment'),
        'policies': ('id', 'name', 'active_version', 'draft_document'),
        'versions': ('policy_id', 'version', 'document', 'is_active'),
        'search': ('trace_id', 'span_id', 'name', 'payload'),
        'audit': ('id', 'actor', 'action', 'resource', 'at_ms'),
        'timeseries': ('bucket_ms', 'value'),
        'deliveries': ('id', 'event_type', 'status'),
    }
    if kind in list_fields:
        assert isinstance(data, list)
        if kind != 'deliveries':
            assert data, f'{kind} unexpectedly empty'
        for row in data:
            fields(row, *list_fields[kind])
    if kind in {'orgs', 'environments', 'agents', 'payments', 'decisions', 'reviews', 'policies', 'search', 'audit', 'deliveries'}:
        fields(body['meta'], 'total', 'limit', 'offset')
        assert isinstance(body['meta']['total'], int)
    if kind == 'payments':
        assert body['meta']['total'] == 3
        assert {p['id']: p['state'] for p in data} == {p['payment_id']: p['state'] for p in report['transactions']}
    elif kind == 'payment':
        fields(data, *list_fields['payments'], 'transitions')
        assert data['state'] == 'needs_review' and data['request_id'] == review['request_id']
        assert data['transitions'][-1]['to'] == 'needs_review'
    elif kind in {'decision', 'decisions', 'simulation'}:
        decision = data[0] if kind == 'decisions' else data
        for field in ('verdict', 'reasons', 'risk_flags'):
            assert decision[field] == review[field]
        if kind == 'simulation':
            assert data['persisted'] is False and data['policy_source'] == {'type': 'inline'}
        else:
            assert decision['policy_version'] == 1
    elif kind in {'review', 'reviews'}:
        item = data[0] if kind == 'reviews' else data
        fields(item, *list_fields['reviews'])
        assert item['id'] == review['review_id'] and item['payment_id'] == review['payment_id']
        assert item['state'] == 'open' and item['sla_deadline_ms'] > 0
    elif kind == 'identity':
        fields(data, 'reviewer_id', 'role', 'env_id', 'managed_identity')
        assert data['role'] == 'admin'
    elif kind in {'policy', 'policies'}:
        item = data[0] if kind == 'policies' else data
        assert item['id'] == report['policy']['id'] and item['active_version'] == 1
        assert item['draft_document'] == POLICY_DOCUMENT
    elif kind == 'versions':
        assert data[0]['version'] == 1 and data[0]['is_active'] is True
        assert data[0]['document'] == POLICY_DOCUMENT
    elif kind == 'search':
        assert {row['trace_id'] for row in data} == {review['trace_id']}
    elif kind == 'summary':
        fields(data, 'env_id', 'payments', 'decisions', 'risk_flags', 'open_reviews')
        assert data['payments'] == {'executed': 1, 'needs_review': 1, 'rejected': 1}
        assert data['open_reviews'] == 1
    elif kind == 'orgs':
        assert any(row['id'] == report['org']['id'] for row in data)
    elif kind == 'environments':
        assert any(row['id'] == report['environment']['id'] for row in data)


def validate_outcomes(report):
    for row, spec in zip(report['transactions'], TRANSACTIONS, strict=True):
        assert row['key'] == spec['key']
        for actual, expected in [('verdict', 'expected_verdict'), ('reasons', 'expected_reasons'), ('risk_flags', 'expected_flags')]:
            assert row[actual] == row['local_' + actual] == spec[expected]
        assert row['state'] == spec['expected_state'] and row['policy_version'] == 1
        assert bool(row['gateway_reference']) is spec['execute']
