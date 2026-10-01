"""Deprecated. Superseded by backend/keystore.py.

This module read keys from HF_API_KEY_1..19 env vars, which cannot be edited
while the app runs. KeyStore replaced it and is the single source of truth for
HF key rotation. Kept only so old imports fail with a clear message instead of
an obscure ImportError.
"""

raise ImportError(
    "backend.api_roller is gone. Use backend.keystore.KeyStore: it reads "
    "data/keys.json on every call, so keys added or disabled in the UI take "
    "effect without restarting the app."
)
