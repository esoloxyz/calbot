import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

from tempo_client import TempoClient, restore_wallet_credentials


ROOT = Path(__file__).resolve().parents[1]

P256_PRIVATE_KEY = "0x" + ("0" * 63) + "1"
P256_PUBLIC_KEY = (
    "0x04"
    "6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296"
    "4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5"
)
P256_ACCESS_ADDRESS = "0xd3a9f047ad43d7e2e4e7e491f1fe2e657a2651b6"
P256_JWK = {
    "kty": "EC",
    "crv": "P-256",
    "x": "axfR8uEsQkf4vOblY6RA8ncDfYEt6zOg9KE5RdiYwpY",
    "y": "T-NC4v4af5uO5-tKfA-eFivOM1drMV7Oy7ZAaDe_UfU",
    "d": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAE",
}


def valid_store_bytes():
    return b"""{
      "tempo-cli.store": {
        "state": {
          "accounts": [{"address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],
          "accessKeys": [{
            "address": "0x4cdadb819a7fc083b72ada08288a96424f34c8a0",
            "access": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "chainId": 4217,
            "keyType": "secp256k1",
            "privateKey": "0x2c04876ec5dd00c9bd039e389e809027140b1c149658b5c1a293fa4feced2e93",
            "limits": []
          }],
          "activeAccount": 0,
          "chainId": 4217
        },
        "version": 0
      }
    }"""


class WalletCredentialRestoreTests(unittest.TestCase):
    def test_current_store_replaces_stale_copy(self):
        with tempfile.TemporaryDirectory() as wallet_dir:
            store_path = os.path.join(wallet_dir, "store.json")
            with open(store_path, "wb") as store_file:
                store_file.write(b"stale")

            restored = restore_wallet_credentials(
                wallet_dir=wallet_dir,
                store_b64=base64.b64encode(valid_store_bytes()).decode(),
            )

            self.assertEqual(restored, store_path)
            with open(store_path, "rb") as store_file:
                self.assertEqual(store_file.read(), valid_store_bytes())
            self.assertEqual(os.stat(store_path).st_mode & 0o777, 0o600)

    def test_empty_store_is_not_restored(self):
        with tempfile.TemporaryDirectory() as wallet_dir:
            restored = restore_wallet_credentials(
                wallet_dir=wallet_dir,
                store_b64="",
            )

            self.assertIsNone(restored)
            self.assertEqual(os.listdir(wallet_dir), [])

    def test_malformed_secret_cannot_destroy_an_existing_store(self):
        with tempfile.TemporaryDirectory() as wallet_dir:
            store_path = os.path.join(wallet_dir, "store.json")
            with open(store_path, "wb") as store_file:
                store_file.write(b'{"still_valid":true}')

            with self.assertRaisesRegex(ValueError, "base64"):
                restore_wallet_credentials(
                    wallet_dir=wallet_dir,
                    store_b64="!!!!",
                )

            with open(store_path, "rb") as store_file:
                self.assertEqual(store_file.read(), b'{"still_valid":true}')

    def test_decoded_store_must_be_a_json_object(self):
        with tempfile.TemporaryDirectory() as wallet_dir:
            encoded = base64.b64encode(b"[]").decode()

            with self.assertRaisesRegex(ValueError, "JSON object"):
                restore_wallet_credentials(wallet_dir, encoded)

            self.assertEqual(os.listdir(wallet_dir), [])

    def test_empty_json_object_is_not_accepted_as_a_wallet(self):
        with tempfile.TemporaryDirectory() as wallet_dir:
            encoded = base64.b64encode(b"{}").decode()

            with self.assertRaisesRegex(ValueError, "tempo-cli.store"):
                restore_wallet_credentials(wallet_dir, encoded)

            self.assertEqual(os.listdir(wallet_dir), [])

    def test_wrapped_base64_secret_is_accepted_after_whitespace_normalization(self):
        with tempfile.TemporaryDirectory() as wallet_dir:
            encoded = base64.b64encode(valid_store_bytes()).decode()
            wrapped = "\n".join(
                encoded[index : index + 76] for index in range(0, len(encoded), 76)
            )

            restored = restore_wallet_credentials(wallet_dir, wrapped + "\n")

            self.assertEqual(restored, os.path.join(wallet_dir, "store.json"))

    def test_active_account_must_be_in_range(self):
        payload = json.loads(valid_store_bytes())
        payload["tempo-cli.store"]["state"]["activeAccount"] = 9
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()

        with tempfile.TemporaryDirectory() as wallet_dir:
            with self.assertRaisesRegex(ValueError, "activeAccount"):
                restore_wallet_credentials(wallet_dir, encoded)

    def test_active_account_index_cannot_be_remapped_by_filtering_invalid_entries(self):
        payload = json.loads(valid_store_bytes())
        state = payload["tempo-cli.store"]["state"]
        state["accounts"] = [
            {"label": "invalid"},
            {"address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        ]
        state["activeAccount"] = 0
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()

        with tempfile.TemporaryDirectory() as wallet_dir:
            with self.assertRaisesRegex(ValueError, "active account"):
                restore_wallet_credentials(wallet_dir, encoded)

    def test_access_key_requires_usable_signer_material(self):
        payload = json.loads(valid_store_bytes())
        del payload["tempo-cli.store"]["state"]["accessKeys"][0]["privateKey"]
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()

        with tempfile.TemporaryDirectory() as wallet_dir:
            with self.assertRaisesRegex(ValueError, "signing"):
                restore_wallet_credentials(wallet_dir, encoded)

    def test_access_key_requires_supported_explicit_key_type(self):
        for key_type in (None, "ed25519"):
            payload = json.loads(valid_store_bytes())
            key = payload["tempo-cli.store"]["state"]["accessKeys"][0]
            if key_type is None:
                del key["keyType"]
            else:
                key["keyType"] = key_type
            encoded = base64.b64encode(json.dumps(payload).encode()).decode()

            with (
                self.subTest(key_type=key_type),
                tempfile.TemporaryDirectory() as wallet_dir,
            ):
                with self.assertRaisesRegex(ValueError, "signing"):
                    restore_wallet_credentials(wallet_dir, encoded)

    def test_access_key_address_must_match_private_key(self):
        payload = json.loads(valid_store_bytes())
        payload["tempo-cli.store"]["state"]["accessKeys"][0]["address"] = (
            "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        )
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()

        with tempfile.TemporaryDirectory() as wallet_dir:
            with self.assertRaisesRegex(ValueError, "matching its stored address"):
                restore_wallet_credentials(wallet_dir, encoded)

    def test_real_p256_private_key_wallet_is_accepted(self):
        payload = json.loads(valid_store_bytes())
        key = payload["tempo-cli.store"]["state"]["accessKeys"][0]
        key.update(
            {
                "address": P256_ACCESS_ADDRESS,
                "keyType": "p256",
                "privateKey": P256_PRIVATE_KEY,
            }
        )
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()

        with tempfile.TemporaryDirectory() as wallet_dir:
            restored = restore_wallet_credentials(wallet_dir, encoded)

        self.assertEqual(restored, os.path.join(wallet_dir, "store.json"))

    def test_real_managed_p256_wallet_is_accepted(self):
        payload = json.loads(valid_store_bytes())
        key = payload["tempo-cli.store"]["state"]["accessKeys"][0]
        key.pop("privateKey")
        key.update(
            {
                "address": P256_ACCESS_ADDRESS,
                "keyType": "p256",
                "publicKey": P256_PUBLIC_KEY,
                "handle": {"kind": "webcrypto-p256", "jwk": P256_JWK},
            }
        )
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()

        with tempfile.TemporaryDirectory() as wallet_dir:
            restored = restore_wallet_credentials(wallet_dir, encoded)

        self.assertEqual(restored, os.path.join(wallet_dir, "store.json"))

    def test_wallet_restore_accepts_mainnet_only(self):
        payload = json.loads(valid_store_bytes())
        state = payload["tempo-cli.store"]["state"]
        state["chainId"] = 42431
        state["accessKeys"][0]["chainId"] = 42431
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()

        with tempfile.TemporaryDirectory() as wallet_dir:
            with self.assertRaisesRegex(ValueError, "mainnet chainId 4217"):
                restore_wallet_credentials(wallet_dir, encoded)

    def test_fake_managed_p256_handle_is_rejected(self):
        payload = json.loads(valid_store_bytes())
        key = payload["tempo-cli.store"]["state"]["accessKeys"][0]
        key.pop("privateKey")
        key["keyType"] = "p256"
        key["handle"] = {"id": "fake"}
        key["publicKey"] = "0xdeadbeef"
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()

        with tempfile.TemporaryDirectory() as wallet_dir:
            with self.assertRaisesRegex(ValueError, "signing"):
                restore_wallet_credentials(wallet_dir, encoded)

    def test_expired_access_key_is_not_startup_ready(self):
        payload = json.loads(valid_store_bytes())
        payload["tempo-cli.store"]["state"]["accessKeys"][0]["expiry"] = 1
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()

        with tempfile.TemporaryDirectory() as wallet_dir:
            with self.assertRaisesRegex(ValueError, "nonexpired"):
                restore_wallet_credentials(wallet_dir, encoded)

    def test_garbage_addresses_and_empty_managed_handle_are_rejected(self):
        payload = json.loads(valid_store_bytes())
        state = payload["tempo-cli.store"]["state"]
        state["accounts"][0]["address"] = "garbage"
        state["accessKeys"][0] = {
            "address": "also-garbage",
            "access": "garbage",
            "chainId": 4217,
            "handle": {},
            "publicKey": "x",
        }
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()

        with tempfile.TemporaryDirectory() as wallet_dir:
            with self.assertRaisesRegex(ValueError, "active account"):
                restore_wallet_credentials(wallet_dir, encoded)

    def test_tempo_home_does_not_relocate_wallet_cli_credentials(self):
        client = TempoClient(
            bin_path="/opt/calbot/bin/tempo",
            tempo_home="/opt/calbot/tempo-home",
        )

        self.assertEqual(client.bin, "/opt/calbot/bin/tempo")
        self.assertEqual(client.tempo_home, "/opt/calbot/tempo-home")
        self.assertEqual(
            client.store_path,
            os.path.join(os.path.expanduser("~"), ".tempo", "wallet", "store.json"),
        )

    def test_startup_delegates_wallet_restore_to_tempo_client(self):
        startup = (ROOT / "start.sh").read_text()

        self.assertNotIn("base64 -d", startup)
        self.assertIn("TEMPO_WALLET_STORE_B64:-", startup)
        self.assertIn("exec python bot.py", startup)


if __name__ == "__main__":
    unittest.main()
