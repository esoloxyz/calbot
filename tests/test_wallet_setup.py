import base64
import os
import tempfile
import unittest

from tempo_client import restore_wallet_credentials


class WalletCredentialRestoreTests(unittest.TestCase):
    def test_current_store_replaces_stale_copy_and_wins_over_legacy_keys(self):
        with tempfile.TemporaryDirectory() as wallet_dir:
            store_path = os.path.join(wallet_dir, "store.json")
            with open(store_path, "wb") as store_file:
                store_file.write(b"stale")

            restored = restore_wallet_credentials(
                wallet_dir=wallet_dir,
                store_b64=base64.b64encode(b'{"fresh":true}').decode(),
                legacy_keys_b64=base64.b64encode(b"legacy").decode(),
            )

            self.assertEqual(restored, store_path)
            with open(store_path, "rb") as store_file:
                self.assertEqual(store_file.read(), b'{"fresh":true}')
            self.assertEqual(os.stat(store_path).st_mode & 0o777, 0o600)
            self.assertFalse(os.path.exists(os.path.join(wallet_dir, "keys.toml")))

    def test_legacy_keys_remain_supported_as_a_fallback(self):
        with tempfile.TemporaryDirectory() as wallet_dir:
            restored = restore_wallet_credentials(
                wallet_dir=wallet_dir,
                store_b64="",
                legacy_keys_b64=base64.b64encode(b"legacy").decode(),
            )

            keys_path = os.path.join(wallet_dir, "keys.toml")
            self.assertEqual(restored, keys_path)
            with open(keys_path, "rb") as keys_file:
                self.assertEqual(keys_file.read(), b"legacy")
            self.assertEqual(os.stat(keys_path).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
