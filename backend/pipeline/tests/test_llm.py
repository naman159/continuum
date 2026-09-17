from types import SimpleNamespace

import pytest

from pipeline import llm


class ProviderError(Exception):
    def __init__(self, status, message="provider failed", headers=None):
        super().__init__(message)
        self.status_code = status
        self.response = SimpleNamespace(headers=headers or {})


def test_rate_limit_honors_gemini_retry_info(monkeypatch):
    sleeps = []
    calls = []
    monkeypatch.setattr(llm.time, "sleep", sleeps.append)

    def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ProviderError(429, 'Gemini error: {"error":{"details":['
                                '{"@type":"type.googleapis.com/google.rpc.RetryInfo",'
                                '"retryDelay":"4.5s"}]}}')
        return "actual response"

    assert llm.call_with_retry(completion, model="gemini/test") == "actual response"
    assert sleeps == [5.5]
    assert len(calls) == 2
    assert calls[0]["num_retries"] == calls[0]["max_retries"] == 0


def test_persistent_failure_stops_after_three_retries(monkeypatch):
    sleeps = []
    calls = []
    monkeypatch.setattr(llm.time, "sleep", sleeps.append)

    def completion(**kwargs):
        calls.append(kwargs)
        raise ProviderError(429)

    with pytest.raises(ProviderError):
        llm.call_with_retry(completion)
    assert len(calls) == 4
    assert sleeps == [60.0, 60.0, 60.0]


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_errors_are_not_retried(monkeypatch, status):
    def unexpected_sleep(seconds):
        pytest.fail("permanent provider failures must not retry")

    monkeypatch.setattr(llm.time, "sleep", unexpected_sleep)

    def completion(**kwargs):
        raise ProviderError(status)

    with pytest.raises(ProviderError):
        llm.call_with_retry(completion)


def test_http_retry_after_takes_precedence():
    assert llm._retry_delay(ProviderError(429, headers={"retry-after": "7"}), 0) == 8


def test_server_failures_back_off_without_rate_limit_delay():
    assert [llm._retry_delay(ProviderError(503), attempt) for attempt in range(3)] == [1, 2, 4]
