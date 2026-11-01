"""
Keeping the Azure OpenAI key encrypted outside the running process.

The key is stored as a Fernet token (AES-128-CBC with an HMAC-SHA256, so a wrong
key or a damaged token is detected, never decrypted into garbage) and the
application decrypts it with a separate key when it starts.

What this does and does not buy: the encrypted value and the decryption key are
meant to live in *different* places (here, two Kubernetes Secrets), so that a
leaked manifest, copy or pasted ``get secret`` output holds only the half that is
useless alone. It does not protect against someone who can read both, or the
running process: that is the limit of any scheme in which the application has to
be able to use the key by itself.

No message raised here contains a key, a token or the plaintext.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretError(ValueError):
    """A key or token is unusable. The message is safe to log and to show."""


def _compact(value: str) -> str:
    """
    Drop all whitespace.

    A value read from a file or a Secret may carry a trailing newline, and one
    pasted into a document may have been wrapped; neither is part of the value.
    """
    return "".join(value.split())


def _fernet(key: str) -> Fernet:
    try:
        return Fernet(_compact(key))
    except (ValueError, TypeError):
        # Fernet's own message is harmless, but a key must never be chained into one.
        raise SecretError("The decryption key is not a valid Fernet key.") from None


def generate_key() -> str:
    """Create a new random decryption key (44 characters, URL-safe base64)."""
    return Fernet.generate_key().decode("ascii")


def encrypt(plaintext: str, key: str) -> str:
    """
    Encrypt ``plaintext`` with ``key`` and return the token as text.

    Raises:
        SecretError: If there is nothing to encrypt or the key is not valid.
    """
    secret = plaintext.strip()
    if not secret:
        raise SecretError("There is nothing to encrypt: the value is empty.")
    return _fernet(key).encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt(token: str, key: str) -> str:
    """
    Decrypt a token produced by :func:`encrypt`.

    Raises:
        SecretError: If the key is not valid, the token is empty or damaged, or
            the key is not the one the token was made with.
    """
    compact_token = _compact(token)
    if not compact_token:
        raise SecretError("The encrypted value is empty.")
    fernet = _fernet(key)
    try:
        plaintext = fernet.decrypt(compact_token.encode("ascii", errors="replace")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError):
        raise SecretError("The value does not decrypt with this key: wrong key or damaged value.") from None
    if not plaintext.strip():
        raise SecretError("The decrypted value is empty.")
    return plaintext.strip()
