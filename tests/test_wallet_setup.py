import base64
import os
import tempfile
import unittest

from tempo_client import restore_wallet_credentials


class WalletCredentialRestoreTests(unittest.TestCase):
    def test_current_store_replaces_stale_copy(self):
        with tempfile.TemporaryDirectory() as wallet_dir:
            store_path = os.path.join(wallet_dir, "store.json")
            with open(store_path, "wb") as store_file:
                store_file.write(b"stale")

            restored = restore_wallet_credentials(
                wallet_dir=wallet_dir,
                store_b64=base64.b64encode(b'{"fresh":true}').decode(),
            )

            self.assertEqual(restored, store_path)
            with open(store_path, "rb") as store_file:
                self.assertEqual(store_file.read(), b'{"fresh":true}')
            self.assertEqual(os.stat(store_path).st_mode & 0o777, 0o600)

    def test_empty_store_is_not_restored(self):
        with tempfile.TemporaryDirectory() as wallet_dir:
            restored = restore_wallet_credentials(
                wallet_dir=wallet_dir,
                store_b64="",
            )

            self.assertIsNone(restored)
            self.assertEqual(os.listdir(wallet_dir), [])


if __name__ == "__main__":
    unittest.main()
