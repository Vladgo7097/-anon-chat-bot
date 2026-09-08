import hashlib
import hmac
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
config.BOT_TOKEN = "123456:TEST-TOKEN-not-real"
config.WEB_SESSION_SECRET = "test-secret-not-real"

from web import auth  # noqa: E402  (must follow the config overrides above)


def sign_login_widget(fields):
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hashlib.sha256(config.BOT_TOKEN.encode()).digest()
    return hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()


def sign_init_data(fields):
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()


class LoginWidgetTests(unittest.TestCase):
    def valid_payload(self):
        fields = {"id": "555", "first_name": "Anon", "username": "anon_user", "auth_date": str(int(time.time()))}
        return {**fields, "hash": sign_login_widget(fields)}

    def test_valid_signature_accepted(self):
        self.assertTrue(auth.verify_login_widget(self.valid_payload()))

    def test_tampered_field_rejected(self):
        payload = self.valid_payload()
        payload["id"] = "999"  # attacker swaps the account after signing
        self.assertFalse(auth.verify_login_widget(payload))

    def test_missing_hash_rejected(self):
        payload = self.valid_payload()
        del payload["hash"]
        self.assertFalse(auth.verify_login_widget(payload))

    def test_stale_auth_date_rejected(self):
        payload = self.valid_payload()
        payload["auth_date"] = str(int(time.time()) - 90000)
        payload["hash"] = sign_login_widget({k: v for k, v in payload.items() if k != "hash"})
        self.assertFalse(auth.verify_login_widget(payload))


class MiniAppInitDataTests(unittest.TestCase):
    def valid_init_data(self, user_id=555):
        fields = {
            "user": f'{{"id":{user_id},"first_name":"Anon","username":"anon_user"}}',
            "auth_date": str(int(time.time())),
        }
        fields["hash"] = sign_init_data(fields)
        return urlencode(fields)

    def test_valid_init_data_returns_user(self):
        result = auth.verify_miniapp_init_data(self.valid_init_data())
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], 555)

    def test_tampered_init_data_rejected(self):
        init_data = self.valid_init_data().replace("555", "999")
        self.assertIsNone(auth.verify_miniapp_init_data(init_data))

    def test_empty_init_data_rejected(self):
        self.assertIsNone(auth.verify_miniapp_init_data(""))


class SessionTokenTests(unittest.TestCase):
    def test_round_trip(self):
        token = auth.create_session_token(4242)
        self.assertEqual(auth.read_session_token(token), 4242)

    def test_tampered_token_rejected(self):
        token = auth.create_session_token(4242)
        parts = token.split(".")
        parts[0] = ("A" if parts[0][0] != "A" else "B") + parts[0][1:]
        self.assertIsNone(auth.read_session_token(".".join(parts)))


if __name__ == "__main__":
    unittest.main()
