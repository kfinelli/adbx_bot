"""
Unit tests for webui/auth.py.

Pure-function coverage of password hashing and session-cookie signing — no ASGI
app, TestClient, httpx, or discord imports (so this runs in CI alongside the rest).
"""

import time

from webui.auth import (
    consume_token,
    hash_password,
    issue_token,
    sign_session,
    verify_password,
    verify_session,
)

KEY = b"unit-test-signing-key"
OTHER_KEY = b"a-different-key"


# --- password hashing ------------------------------------------------------

def test_password_round_trip():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored)


def test_password_rejects_wrong():
    stored = hash_password("hunter2")
    assert not verify_password("hunter3", stored)
    assert not verify_password("", stored)


def test_password_unique_salt():
    # Same password hashed twice should differ (random salt) but both verify.
    a = hash_password("same")
    b = hash_password("same")
    assert a != b
    assert verify_password("same", a)
    assert verify_password("same", b)


def test_password_malformed_stored():
    assert not verify_password("anything", "")
    assert not verify_password("anything", "no-dollar-sign")
    assert not verify_password("anything", "nothex$nothex")


# --- session cookie signing ------------------------------------------------

def test_session_valid():
    token = sign_session(int(time.time()) + 3600, key=KEY)
    assert verify_session(token, key=KEY)


def test_session_expired():
    token = sign_session(int(time.time()) - 10, key=KEY)
    assert not verify_session(token, key=KEY)


def test_session_wrong_key():
    token = sign_session(int(time.time()) + 3600, key=KEY)
    assert not verify_session(token, key=OTHER_KEY)


def test_session_tampered_payload():
    token = sign_session(int(time.time()) + 3600, key=KEY)
    payload_b64, sig_b64 = token.split(".", 1)
    # Re-sign nothing; just swap the payload for a far-future expiry, keep old sig.
    forged = sign_session(int(time.time()) + 999999, key=KEY).split(".", 1)[0]
    assert not verify_session(f"{forged}.{sig_b64}", key=KEY)


def test_session_tampered_signature():
    token = sign_session(int(time.time()) + 3600, key=KEY)
    payload_b64, sig_b64 = token.split(".", 1)
    flipped = ("A" if sig_b64[0] != "A" else "B") + sig_b64[1:]
    assert not verify_session(f"{payload_b64}.{flipped}", key=KEY)


def test_session_garbage():
    assert not verify_session("", key=KEY)
    assert not verify_session("no-dot", key=KEY)
    assert not verify_session("not.base64!!", key=KEY)


# --- magic-link tokens -----------------------------------------------------

def test_token_single_use():
    token = issue_token()
    assert consume_token(token)        # first use succeeds
    assert not consume_token(token)    # second use fails (consumed)


def test_token_expired():
    token = issue_token(ttl_seconds=-1)
    assert not consume_token(token)


def test_token_unknown_and_empty():
    assert not consume_token("never-issued")
    assert not consume_token("")


def test_tokens_are_unique():
    assert issue_token() != issue_token()
