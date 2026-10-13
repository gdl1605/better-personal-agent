import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("redact_wechat", Path(__file__).resolve().parents[1] / "scripts/redact_wechat.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RedactWeChatTests(unittest.TestCase):
    def test_snapshot_credentials_removed_and_message_identity_preserved(self):
        source = {"provider": "wechat-use.history", "result": {"rows": [{"server_id": 123, "create_time": 100, "sender_is_self": True, "aeskey": "0123456789abcdef0123456789abcdef", "display_text": "今天去训练"}]}}
        result = MODULE.Redactor().obj(source)
        row = result["result"]["rows"][0]
        self.assertEqual(row["aeskey"], MODULE.MARK)
        self.assertEqual((row["server_id"], row["create_time"], row["sender_is_self"], row["display_text"]), (123, 100, True, "今天去训练"))

    def test_xml_and_embedded_json_values_are_removed(self):
        text = '<img aeskey="0123456789abcdef0123456789abcdef" /> {"password":"unit_test_value_123"} <authkey>unit_test_auth_456</authkey>'
        result = MODULE.Redactor().text(text)
        for secret in ("0123456789abcdef0123456789abcdef", "unit_test_value_123", "unit_test_auth_456"):
            self.assertNotIn(secret, result)
        self.assertIn('<img aeskey="' + MODULE.MARK + '" />', result)

    def test_url_auth_and_private_key_are_removed(self):
        text = 'https://example.invalid/file?access_token=unit_test_access_123&size=10 postgres://user:unit_test_pw_456@localhost/db Authorization: Bearer unit_test_auth_789\n-----BEGIN PRIVATE KEY-----\nUNIT_TEST_ONLY\n-----END PRIVATE KEY-----'
        result = MODULE.Redactor().text(text)
        for secret in ("unit_test_access_123", "unit_test_pw_456", "unit_test_auth_789", "UNIT_TEST_ONLY"):
            self.assertNotIn(secret, result)
        self.assertIn("&size=10", result)
        self.assertIn("postgres://user:" + MODULE.MARK + "@localhost/db", result)

    def test_business_keys_model_tokens_and_placeholders_are_preserved(self):
        source = {"key": "order_123", "token": 1024, "api_key": "${API_KEY}", "text": "模型 token 数量 1024，密码是字段名，key 是字典的键"}
        self.assertEqual(MODULE.Redactor().obj(dict(source)), source)

    def test_escaped_xml_and_json_and_explicit_chinese_password(self):
        text = r'<img aeskey=\"0123456789abcdef0123456789abcdef\" /> {\"password\":\"unit_test_value_123\"} 密码：unit_test_pw_456'
        result = MODULE.Redactor().text(text)
        for secret in ("0123456789abcdef0123456789abcdef", "unit_test_value_123", "unit_test_pw_456"):
            self.assertNotIn(secret, result)
        self.assertEqual(MODULE.Redactor().text(result), result)

    def test_file_copies_leave_originals_and_report_without_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wechat" / "snapshot.json"
            source.parent.mkdir()
            original = json.dumps({"password": "unit_test_pw_123", "server_id": 55})
            source.write_text(original)
            summary = MODULE.run([source.parent], root / "out", root)
            self.assertEqual(source.read_text(), original)
            self.assertEqual(json.loads((root / "out/wechat/snapshot.json").read_text())["password"], MODULE.MARK)
            self.assertNotIn("unit_test_pw_123", (root / "out/redaction-report.json").read_text())
            self.assertEqual((summary["files"], summary["changed"], summary["errors"]), (1, 1, 0))


if __name__ == "__main__":
    unittest.main()
