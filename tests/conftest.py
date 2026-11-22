import pytest

from itd_bot.rate_limits import RateLimitConfig, RateLimiter


@pytest.fixture(autouse=True)
def isolate_legacy_rate_limits(request, monkeypatch):
    """Keep transport tests independent of wall-clock pacing and shared clients."""
    if request.module.__name__.endswith("test_request_pacing"):
        return
    monkeypatch.setattr(
        "itd_bot.client.default_limiter",
        lambda origin: RateLimiter(RateLimitConfig(enabled=False, wait=False)),
    )
