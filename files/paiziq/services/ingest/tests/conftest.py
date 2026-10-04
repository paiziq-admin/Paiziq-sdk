"""Isolate the process-wide test HTTP rate bucket between test cases."""

import pytest


@pytest.fixture(autouse=True)
def fresh_rate_limit_window():
    # A single case still exercises the real limiter. Test order and the total
    # number of API fixtures must not exhaust another case's request allowance.
    from app import _rate_limiter

    with _rate_limiter._lock:
        _rate_limiter._buckets.clear()
    yield
