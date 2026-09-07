"""Per-IP rate limiting: the window, and the client-identification trap."""

from __future__ import annotations

import time

import pytest

from pathyam_api.ratelimit import (
    Limit,
    RateLimiter,
    RateLimitExceeded,
    client_address,
)


def _limiter(requests=3, per_seconds=60):
    return RateLimiter(Limit(requests=requests, per_seconds=per_seconds, name="test"))


# --------------------------------------------------------------- the window ----

def test_requests_are_allowed_up_to_the_limit_then_refused():
    limiter = _limiter(requests=3)
    for _ in range(3):
        limiter.check("1.2.3.4")
    with pytest.raises(RateLimitExceeded):
        limiter.check("1.2.3.4")


def test_addresses_are_limited_independently():
    limiter = _limiter(requests=2)
    limiter.check("1.2.3.4")
    limiter.check("1.2.3.4")
    limiter.check("5.6.7.8")          # a different caller is unaffected
    with pytest.raises(RateLimitExceeded):
        limiter.check("1.2.3.4")


def test_the_window_slides_so_a_caller_recovers():
    limiter = _limiter(requests=2, per_seconds=1)
    limiter.check("1.2.3.4")
    limiter.check("1.2.3.4")
    with pytest.raises(RateLimitExceeded):
        limiter.check("1.2.3.4")

    time.sleep(1.05)
    limiter.check("1.2.3.4")          # window has moved on


def test_a_refused_attempt_does_not_extend_the_lockout():
    """Otherwise hammering the endpoint becomes a self-inflicted permanent ban."""
    limiter = _limiter(requests=2, per_seconds=1)
    limiter.check("1.2.3.4")
    limiter.check("1.2.3.4")

    for _ in range(20):               # keep hammering while over the limit
        with pytest.raises(RateLimitExceeded):
            limiter.check("1.2.3.4")

    time.sleep(1.05)
    limiter.check("1.2.3.4")          # still recovers on schedule


def test_retry_after_is_a_usable_positive_number():
    limiter = _limiter(requests=1, per_seconds=60)
    limiter.check("1.2.3.4")
    with pytest.raises(RateLimitExceeded) as excinfo:
        limiter.check("1.2.3.4")
    assert 0 < excinfo.value.retry_after <= 61


# ------------------------------------------------- client identification ----
#
# The trap: honouring X-Forwarded-For unconditionally lets a caller present a new
# address per request, which does not weaken the limiter — it removes it, while the
# metrics still show one running.


def test_forwarded_for_is_ignored_when_no_proxy_is_configured():
    assert client_address(
        peer="203.0.113.9", forwarded_for="1.1.1.1", trusted_hops=0
    ) == "203.0.113.9"


def test_a_spoofed_forwarded_for_cannot_mint_new_identities():
    limiter = _limiter(requests=2)
    for spoofed in ("1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"):
        key = client_address(peer="203.0.113.9", forwarded_for=spoofed, trusted_hops=0)
        if spoofed in ("1.1.1.1", "2.2.2.2"):
            limiter.check(key)
        else:
            with pytest.raises(RateLimitExceeded):
                limiter.check(key)


def test_with_one_trusted_proxy_the_client_is_taken_from_the_right():
    """The rightmost hops were appended by infrastructure we control."""
    # client -> proxy. The proxy appended the real peer, so the client is index -1.
    assert client_address(
        peer="10.0.0.1", forwarded_for="198.51.100.7", trusted_hops=1
    ) == "198.51.100.7"

    # A caller prepends a forged entry. With one trusted hop we still take the
    # rightmost, which the proxy wrote, not the forgery.
    assert client_address(
        peer="10.0.0.1", forwarded_for="1.1.1.1, 198.51.100.7", trusted_hops=1
    ) == "198.51.100.7"


def test_with_two_trusted_proxies_the_index_moves_left_accordingly():
    """Each proxy appends the address it received FROM, so the last hop's own
    address is the socket peer and never appears in the chain: two trusted proxies
    means two entries, "client, proxy1". Equivalent to chain[-hops], which is what
    werkzeug's ProxyFix(x_for=N) does."""
    assert client_address(
        peer="10.0.0.1",                              # proxy2, the socket peer
        forwarded_for="198.51.100.7, 10.0.0.9",       # client, then proxy1
        trusted_hops=2,
    ) == "198.51.100.7"

    # A forged entry prepended by the caller is pushed out of range by the count.
    assert client_address(
        peer="10.0.0.1",
        forwarded_for="1.1.1.1, 198.51.100.7, 10.0.0.9",
        trusted_hops=2,
    ) == "198.51.100.7"


def test_a_shorter_chain_than_configured_does_not_index_off_the_end():
    assert client_address(
        peer="10.0.0.1", forwarded_for="198.51.100.7", trusted_hops=5
    ) == "198.51.100.7"


def test_a_missing_peer_still_yields_a_key():
    assert client_address(peer=None, forwarded_for=None) == "unknown"
