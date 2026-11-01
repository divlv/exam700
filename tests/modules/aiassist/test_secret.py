"""
Encrypting the Azure OpenAI key for storage, and opening it again at startup.

What matters most is what a failure does not say: no error message may contain
the key, the token or the decryption key.
"""

from __future__ import annotations

import pytest

from api.modules.aiassist.internal import _secret

PLAINTEXT = "azure-key-0123456789abcdef-NOT-REAL"


@pytest.fixture()
def key() -> str:
    return _secret.generate_key()


def test_a_generated_key_is_a_valid_44_character_fernet_key(key):
    assert len(key) == 44
    # It works as a key: a round trip succeeds.
    assert _secret.decrypt(_secret.encrypt("x", key), key) == "x"


def test_keys_are_random():
    assert _secret.generate_key() != _secret.generate_key()


def test_round_trip(key):
    token = _secret.encrypt(PLAINTEXT, key)

    assert PLAINTEXT not in token
    assert _secret.decrypt(token, key) == PLAINTEXT


def test_the_same_secret_encrypts_differently_each_time(key):
    """A fresh IV every time: equal tokens would reveal equal keys."""
    assert _secret.encrypt(PLAINTEXT, key) != _secret.encrypt(PLAINTEXT, key)


def test_non_ascii_text_survives(key):
    assert _secret.decrypt(_secret.encrypt("ключ-🔑", key), key) == "ключ-🔑"


def test_whitespace_around_the_values_is_not_part_of_them(key):
    """A file or a Secret often carries a trailing newline; a pasted value may be wrapped."""
    token = _secret.encrypt(f"  {PLAINTEXT}\n", key)
    wrapped = token[:20] + "\n  " + token[20:] + "\r\n"

    assert _secret.decrypt(wrapped, key + "\n") == PLAINTEXT


def test_the_wrong_key_is_refused_without_saying_anything_secret(key):
    token = _secret.encrypt(PLAINTEXT, key)
    other = _secret.generate_key()

    with pytest.raises(_secret.SecretError) as caught:
        _secret.decrypt(token, other)

    message = str(caught.value)
    assert "wrong key" in message
    for secret in (PLAINTEXT, token, key, other):
        assert secret not in message


def test_a_damaged_token_is_refused(key):
    token = _secret.encrypt(PLAINTEXT, key)
    index = len(token) // 2
    damaged = token[:index] + ("A" if token[index] != "A" else "B") + token[index + 1 :]

    with pytest.raises(_secret.SecretError) as caught:
        _secret.decrypt(damaged, key)

    assert token not in str(caught.value) and damaged not in str(caught.value)


@pytest.mark.parametrize("junk", ["not a token", "gAAAA", "ключ", "{}", "\x00"])
def test_junk_instead_of_a_token_is_refused(key, junk):
    with pytest.raises(_secret.SecretError):
        _secret.decrypt(junk, key)


@pytest.mark.parametrize("bad_key", ["", "short", "x" * 44, "ключ" * 11, "a" * 43])
def test_an_unusable_key_is_refused_without_echoing_it(bad_key):
    token = _secret.encrypt(PLAINTEXT, _secret.generate_key())

    with pytest.raises(_secret.SecretError) as caught:
        _secret.decrypt(token, bad_key)
    with pytest.raises(_secret.SecretError):
        _secret.encrypt(PLAINTEXT, bad_key)

    assert "valid Fernet key" in str(caught.value)
    if bad_key:
        assert bad_key not in str(caught.value)


@pytest.mark.parametrize("empty", ["", "   ", "\n"])
def test_nothing_to_decrypt_and_nothing_to_encrypt_are_refused(key, empty):
    with pytest.raises(_secret.SecretError):
        _secret.decrypt(empty, key)
    with pytest.raises(_secret.SecretError):
        _secret.encrypt(empty, key)
