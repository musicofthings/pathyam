"""Password hashing and session handling. Offline; no database needed."""

from __future__ import annotations

import pytest

from pathyam_api.auth import (
    hash_password,
    normalise_email,
    verify_password,
)

# scrypt at production cost is ~100 ms per call by design, which is the point of it
# but makes a test suite crawl. These use a low N; the KDF path is identical.
_FAST = 1 << 12


def test_a_password_verifies_against_its_own_hash():
    stored = hash_password("correct horse battery", n=_FAST)
    assert verify_password("correct horse battery", stored)


def test_a_wrong_password_does_not_verify():
    stored = hash_password("correct horse battery", n=_FAST)
    assert not verify_password("Correct horse battery", stored)
    assert not verify_password("", stored)


def test_the_same_password_hashes_differently_every_time():
    """Per-password salt: identical passwords must not produce identical hashes."""
    a = hash_password("same password here", n=_FAST)
    b = hash_password("same password here", n=_FAST)
    assert a != b
    assert verify_password("same password here", a)
    assert verify_password("same password here", b)


def test_the_hash_is_self_describing_so_cost_can_be_raised_later():
    stored = hash_password("some long password", n=_FAST)
    scheme, n, r, p, salt, digest = stored.split("$")
    assert scheme == "scrypt"
    assert int(n) == _FAST and int(r) == 8 and int(p) == 1
    assert salt and digest

    # A hash written at one cost still verifies after the default is raised.
    assert verify_password("some long password", stored)


def test_the_plaintext_password_never_appears_in_the_hash():
    stored = hash_password("hunter2-hunter2-hunter2", n=_FAST)
    assert "hunter2" not in stored


def test_a_short_password_is_refused():
    with pytest.raises(ValueError, match="at least"):
        hash_password("short", n=_FAST)


@pytest.mark.parametrize("garbage", ["", "notahash", "scrypt$bad", "md5$1$2$3$4$5"])
def test_a_malformed_stored_hash_fails_closed(garbage):
    """A corrupt row must reject the login, not raise or accept."""
    assert verify_password("anything at all", garbage) is False


def test_email_is_case_folded_so_one_address_is_one_account():
    assert normalise_email("  Alice@Example.COM ") == "alice@example.com"
