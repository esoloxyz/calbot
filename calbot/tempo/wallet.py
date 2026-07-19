"""Tempo wallet credential decoding and cryptographic validation."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import tempfile
import time
from typing import Optional

from Crypto.Hash import keccak
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


TEMPO_MAINNET_CHAIN_ID = 4217
ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")


def _wallet_public_key(private_key: str, key_type: str) -> bytes:
    if not isinstance(private_key, str) or not re.fullmatch(
        r"0x[0-9a-fA-F]{64}", private_key
    ):
        raise ValueError("Tempo wallet access key has an invalid private key")
    curve = ec.SECP256K1() if key_type == "secp256k1" else ec.SECP256R1()
    try:
        key = ec.derive_private_key(int(private_key[2:], 16), curve)
    except ValueError as exc:
        raise ValueError("Tempo wallet access key has an invalid private key") from exc
    return key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )


def _public_key_from_hex(public_key: str, curve: ec.EllipticCurve) -> bytes:
    if not isinstance(public_key, str) or not re.fullmatch(
        r"0x04[0-9a-fA-F]{128}", public_key
    ):
        raise ValueError("Tempo wallet access key has an invalid public key")
    encoded = bytes.fromhex(public_key[2:])
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(curve, encoded)
    except ValueError as exc:
        raise ValueError("Tempo wallet access key has an invalid public key") from exc
    return encoded


def _jwk_component(value, *, label: str) -> bytes:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError(f"Tempo wallet P-256 JWK has an invalid {label}")
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Tempo wallet P-256 JWK has an invalid {label}") from exc
    if len(decoded) != 32:
        raise ValueError(f"Tempo wallet P-256 JWK has an invalid {label}")
    return decoded


def _managed_p256_public_key(key: dict) -> bytes:
    handle = key.get("handle")
    if not isinstance(handle, dict) or handle.get("kind") != "webcrypto-p256":
        raise ValueError("Tempo wallet access key has an unsupported managed handle")
    jwk = handle.get("jwk")
    if not isinstance(jwk, dict) or jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise ValueError("Tempo wallet access key has an invalid P-256 JWK")
    x = _jwk_component(jwk.get("x"), label="x coordinate")
    y = _jwk_component(jwk.get("y"), label="y coordinate")
    private_value = int.from_bytes(
        _jwk_component(jwk.get("d"), label="private key"), "big"
    )
    try:
        derived = ec.derive_private_key(private_value, ec.SECP256R1())
    except ValueError as exc:
        raise ValueError("Tempo wallet access key has an invalid P-256 JWK") from exc
    numbers = derived.public_key().public_numbers()
    if numbers.x.to_bytes(32, "big") != x or numbers.y.to_bytes(32, "big") != y:
        raise ValueError("Tempo wallet P-256 JWK public and private keys do not match")
    encoded = b"\x04" + x + y
    stored_public = _public_key_from_hex(key.get("publicKey"), ec.SECP256R1())
    if encoded != stored_public:
        raise ValueError("Tempo wallet managed key does not match its public key")
    return encoded


def _access_key_address(public_key: bytes) -> str:
    digest = keccak.new(digest_bits=256, data=public_key[1:]).digest()
    return "0x" + digest[-20:].hex()


def _usable_wallet_access_key(key: dict, active_address: str, chain_id: int) -> bool:
    if (
        not isinstance(key, dict)
        or not isinstance(key.get("address"), str)
        or not ADDRESS_PATTERN.fullmatch(key["address"])
        or not isinstance(key.get("access"), str)
        or key["access"].casefold() != active_address.casefold()
        or key.get("chainId") != chain_id
    ):
        return False
    expiry = key.get("expiry")
    if expiry is not None and (
        not isinstance(expiry, int)
        or isinstance(expiry, bool)
        or expiry <= int(time.time())
    ):
        return False
    key_type = key.get("keyType")
    if key_type not in {"secp256k1", "p256"}:
        return False
    try:
        if key.get("privateKey") is not None:
            public_key = _wallet_public_key(key["privateKey"], key_type)
        elif key_type == "p256":
            public_key = _managed_p256_public_key(key)
        else:
            return False
    except ValueError:
        return False
    return key["address"].casefold() == _access_key_address(public_key).casefold()


def _validate_wallet_store(raw: bytes) -> None:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Tempo wallet store must contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Tempo wallet store must be a JSON object")
    envelope = payload.get("tempo-cli.store")
    if not isinstance(envelope, dict):
        raise ValueError("Tempo wallet store is missing the tempo-cli.store envelope")
    state = envelope.get("state")
    if not isinstance(state, dict):
        raise ValueError("Tempo wallet store is missing its state object")
    accounts = state.get("accounts")
    active_account = state.get("activeAccount")
    if (
        not isinstance(accounts, list)
        or not isinstance(active_account, int)
        or isinstance(active_account, bool)
        or active_account < 0
        or active_account >= len(accounts)
    ):
        raise ValueError("Tempo wallet store has an invalid activeAccount")
    active_account_record = accounts[active_account]
    if (
        not isinstance(active_account_record, dict)
        or not isinstance(active_account_record.get("address"), str)
        or not ADDRESS_PATTERN.fullmatch(active_account_record["address"])
    ):
        raise ValueError("Tempo wallet store has an invalid active account")
    chain_id = state.get("chainId")
    if chain_id != TEMPO_MAINNET_CHAIN_ID or isinstance(chain_id, bool):
        raise ValueError("Tempo wallet store must use Tempo mainnet chainId 4217")
    active_address = active_account_record["address"]
    access_keys = state.get("accessKeys")
    if not isinstance(access_keys, list) or not any(
        isinstance(key, dict)
        and isinstance(key.get("address"), str)
        and bool(ADDRESS_PATTERN.fullmatch(key["address"]))
        and isinstance(key.get("access"), str)
        and key["access"].casefold() == active_address.casefold()
        and key.get("chainId") == chain_id
        for key in access_keys
    ):
        raise ValueError(
            "Tempo wallet store must contain an account and a valid access key"
        )
    if not any(
        _usable_wallet_access_key(key, active_address, chain_id) for key in access_keys
    ):
        raise ValueError(
            "Tempo wallet access key has no usable, nonexpired signing material "
            "matching its stored address"
        )


def restore_wallet_credentials(wallet_dir: str, store_b64: str) -> Optional[str]:
    """Atomically restore a validated Tempo wallet store from a secret."""
    if not store_b64:
        return None

    try:
        compact_secret = "".join(store_b64.split())
        decoded = base64.b64decode(compact_secret, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Tempo wallet store must be valid base64") from exc
    _validate_wallet_store(decoded)

    path = os.path.join(wallet_dir, "store.json")
    os.makedirs(wallet_dir, mode=0o700, exist_ok=True)
    os.chmod(wallet_dir, 0o700)
    descriptor, temporary_path = tempfile.mkstemp(prefix=".store.json.", dir=wallet_dir)
    try:
        os.fchmod(descriptor, 0o600)
        credential_file = os.fdopen(descriptor, "wb")
        descriptor = -1
        with credential_file:
            credential_file.write(decoded)
            credential_file.flush()
            os.fsync(credential_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
    return path
