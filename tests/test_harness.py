"""Observable source choice, reading progress, feedback, sharing and sync behavior."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from xml.sax.saxutils import escape

from personal_agent.adapters import Collector
from personal_agent.cli import main
from personal_agent.core import PROJECT, Workspace, load, write
from personal_agent.delivery import export_bundle, publish_dot_view, sync_incoming
from personal_agent.runtime import execute_agent, job


def snapshot(rows, chat="friend", account="owner"):
    return {"provider": "wechat-use.history", "account": account, "chat_id": chat, "result": {"rows": rows}}


def row(server, text, moment=1791720000):
    return {"server_id": server, "local_id": 1, "create_time": moment, "sender_is_self": True, "display_text": text, "message_content": text}


def conversation(text="我想先完成课程学习", revision=2):
    return {"id": "conversation-one", "title": "学习与安排", "update_time": revision, "current_node": "current", "mapping": {
        "current": {"parent": None, "message": {"author": {"role": "user"}, "content": {"parts": [text]}, "create_time": 1791720000}},
        "other": {"parent": None, "message": {"author": {"role": "assistant"}, "content": {"parts": ["另一分支建议"]}, "create_time": 1791720100}}}}


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ws = Workspace(self.root / "library")
        self.ws.create("示例用户", "Asia/Shanghai", "安排学习和个人项目")

    def tearDown(self):
        self.temp.cleanup()

    def enable(self, provider, path, **options):
        config = self.ws.config
        config["sources"][provider].update(enabled=True, paths=[str(path)], **options)
        write(self.ws.root / "config.json", config)

    def records(self, provider):
        return [r for r in self.ws.catalog()["records"].values() if r["provider"] == provider]

    def test_disabled_source_is_not_read_and_scope_filters_wechat_messages(self):
        disabled = self.ws.root / "inbox/chatgpt/no.json"
        write(disabled, [conversation()])
        p = self.root / "wechat.json"
        write(p, snapshot([row(1, "范围外", 1700000000), row(2, "当前安排")]))
        self.enable("wechat", p, scope={"include_ids": ["friend"], "from": "2026-10-01"})
        Collector(self.ws).collect()
        self.assertEqual(self.records("chatgpt"), [])
        item = self.records("wechat")[0]
        body = (self.ws.context / item["document"]).read_text()
        self.assertIn("当前安排", body)
        self.assertNotIn("范围外", body)
        original = (self.ws.context / item["readable_originals"][0]).read_text()
        self.assertNotIn("范围外", original)

    def test_wechat_credentials_removed_before_queue_and_shard_ids_remain_distinct(self):
        p = self.root / "wechat.json"
        first, second = row(10, '图片 aeskey="unit_test_secret_value_0123456789"'), row(11, "第二条消息")
        first["aeskey"] = "unit_test_secret_value_0123456789"
        write(p, snapshot([first, second]))
        self.enable("wechat", p, dot_share="sources")
        result = Collector(self.ws).collect()
        item = self.records("wechat")[0]
        self.assertEqual(item["messages"], 2)
        self.assertGreater(result["redaction_positions"], 0)
        for relative in [item["document"], *item["readable_originals"]]:
            self.assertNotIn("unit_test_secret_value_0123456789", (self.ws.context / relative).read_text())
        self.assertIn("unit_test_secret_value_0123456789", p.read_text())
        old_edition = item["edition"]
        Collector(self.ws).collect()
        self.assertEqual(self.records("wechat")[0]["edition"], old_edition)

    def test_chatgpt_branch_preserved_and_old_receipt_leaves_new_version_pending(self):
        p = self.root / "chatgpt.json"
        write(p, [conversation()])
        self.enable("chatgpt", p)
        Collector(self.ws).collect()
        item = next(r for r in self.ws.queue()["pending"] if r["provider"] == "chatgpt")
        body = (self.ws.context / item["document"]).read_text()
        self.assertIn("当前分支", body)
        self.assertIn("其他分支", body)
        write(p, [conversation("我的目标已经改变", 3)])
        Collector(self.ws).collect()
        self.ws.record({"reviews": [{"key": item["key"], "revision": item["revision"], "status": "done", "topics": ["学习"], "note": "旧版本已读"}]})
        current = next(r for r in self.ws.queue()["pending"] if r["provider"] == "chatgpt")
        self.assertEqual(current["reason"], "来源已更新")
        self.assertIn("我的目标已经改变", (self.ws.context / current["document"]).read_text())

    def test_codex_real_user_transport_flag_kept_and_subagent_not_imported(self):
        folder = self.root / "codex"
        folder.mkdir()
        events = [
            {"type": "session_meta", "payload": {"id": "main-id", "source": "cli", "timestamp": "2026-10-11T01:00:00Z"}},
            {"type": "response_item", "timestamp": "2026-10-11T01:01:00Z", "metadata": {"client_authored": False}, "payload": {"type": "message", "role": "user", "content": [{"text": "本人真实表达"}]}},
            {"type": "response_item", "timestamp": "2026-10-11T01:02:00Z", "payload": {"type": "message", "role": "assistant", "channel": "analysis", "content": [{"text": "内部分析"}]}}
        ]
        (folder / "main.jsonl").write_text("\n".join(json.dumps(e) for e in events))
        child = deepcopy(events)
        child[0]["payload"].update(id="child-id", source={"subagent": {"thread_spawn": {"parent_thread_id": "main-id"}}})
        (folder / "child.jsonl").write_text("\n".join(json.dumps(e) for e in child))
        self.enable("codex", folder)
        Collector(self.ws).collect()
        records = self.records("codex")
        self.assertEqual(len(records), 1)
        body = (self.ws.context / records[0]["document"]).read_text()
        self.assertIn("本人真实表达", body)
        self.assertNotIn("内部分析", body)

    def test_payment_overlapping_csvs_merge_and_status_retained(self):
        folder = self.root / "bills"
        folder.mkdir()
        bill = "交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号\n2026-10-11 10:00:00,商户消费,商户,餐食,支出,12.30,零钱,支付成功,one\n"
        (folder / "a.csv").write_text(bill)
        (folder / "b.csv").write_text(bill.replace("支付成功", "已退款"))
        self.enable("payments", folder)
        Collector(self.ws).collect()
        item = self.records("payments")[0]
        self.assertEqual(item["transactions"], 1)
        original = load(self.ws.context / item["readable_originals"][0])
        self.assertEqual((original[0]["amount"], original[0]["status"]), ("12.30", "已退款"))

    def test_feedback_updates_same_section_instead_of_accumulating_old_claim(self):
        self.ws.feedback("每周可投入十小时", section="当前可用时间")
        self.ws.feedback("每周可投入五小时", section="当前可用时间")
        body = (self.ws.context / "wiki/关于用户/当前认识.md").read_text()
        self.assertIn("每周可投入五小时", body)
        self.assertNotIn("每周可投入十小时", body)
        self.assertEqual(body.count("## 当前可用时间"), 1)

    def test_export_omits_raw_and_unshared_source_derived_wiki(self):
        p = self.root / "health.txt"
        p.write_text("private_health_statement_for_test")
        self.enable("health", p, dot_share="none")
        Collector(self.ws).collect()
        key = self.records("health")[0]["key"]
        self.ws.apply_wiki([{"path": "wiki/生活与健康/生活记录.md", "content": "# 生活记录\nprivate_health_statement_for_test\n", "source_keys": [key]}])
        bundle = export_bundle(self.ws)
        with zipfile.ZipFile(bundle["bundle"]) as archive:
            self.assertFalse(any("/raw/" in name for name in archive.namelist()))
            self.assertNotIn("workspace/agent-context/wiki/生活与健康/生活记录.md", archive.namelist())
            self.assertNotIn("private_health_statement_for_test", "\n".join(archive.read(n).decode() for n in archive.namelist()))

    def test_export_contains_redacted_sources_and_portable_runtime(self):
        p = self.root / "wechat.json"
        write(p, snapshot([row(1, 'aeskey="unit_test_secret_value_0123456789"')]))
        self.enable("wechat", p, dot_share="sources")
        Collector(self.ws).collect()
        bundle = export_bundle(self.ws)
        unpack = self.root / "cloud"
        with zipfile.ZipFile(bundle["bundle"]) as archive:
            archive.extractall(unpack)
            self.assertNotIn("unit_test_secret_value_0123456789", "\n".join(archive.read(n).decode() for n in archive.namelist()))
        process = subprocess.run([sys.executable, str(unpack / "tools/personal-agent"), "--workspace", str(unpack / "workspace"), "status", "--json"], text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["sources"]["wechat"]["records"], 1)

    def cloud(self):
        bundle = export_bundle(self.ws)
        folder = self.root / "cloud"
        with zipfile.ZipFile(bundle["bundle"]) as archive:
            archive.extractall(folder)
        return Workspace(folder / "workspace")

    def test_cloud_confirmation_roundtrip_updates_local_wiki(self):
        remote = self.cloud()
        remote.feedback("本人确认近期以考试为优先", section="当前优先事项")
        returned = export_bundle(remote, self.root / "return.zip", returning=True)
        result = sync_incoming(self.ws, returned["bundle"])
        self.assertEqual(result["conflicts"], [])
        self.assertIn("本人确认近期以考试为优先", (self.ws.context / "wiki/关于用户/当前认识.md").read_text())

    def test_concurrent_changes_keep_local_and_save_remote_copy(self):
        remote = self.cloud()
        remote.feedback("云端收到的新安排", section="当前安排")
        self.ws.feedback("本地刚确认的安排", section="当前安排")
        returned = export_bundle(remote, self.root / "return.zip", returning=True)
        result = sync_incoming(self.ws, returned["bundle"])
        self.assertTrue(result["conflicts"])
        self.assertIn("本地刚确认的安排", (self.ws.context / "wiki/关于用户/当前认识.md").read_text())
        incoming = Path(result["conflicts"][0]["incoming_copy"])
        self.assertIn("云端收到的新安排", incoming.read_text())

    def test_agent_handoff_does_not_mark_unread_material_done(self):
        prepared = job(self.ws)
        result = execute_agent(self.ws, prepared)
        self.assertEqual(result["status"], "awaiting_agent")
        self.assertEqual(self.ws.queue()["counts"]["reviewed_current"], 0)
        request = load(prepared["job"])
        self.assertEqual(request["sources"][0]["revision"], self.ws.queue()["pending"][0]["revision"])

    def test_external_agent_protocol_applies_actual_returned_receipt(self):
        config = self.ws.config
        script = "import json,sys; q=json.load(sys.stdin); print(json.dumps({'reviews':[{'key':r['key'],'revision':r['revision'],'status':'done','note':'fixture handler completed'} for r in q['sources']]}))"
        config["runtime"]["agent_command"] = [sys.executable, "-c", script]
        write(self.ws.root / "config.json", config)
        result = execute_agent(self.ws, job(self.ws))
        self.assertEqual(result["status"], "processed")
        self.assertEqual(self.ws.queue()["counts"]["pending"], 0)

    def test_cli_configuration_and_native_schedule_selection(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--workspace", str(self.ws.root), "sources", "wechat", "--enable", "--include", "friend", "--share", "sources"]), 0)
            self.assertEqual(main(["--workspace", str(self.ws.root), "schedule", "--daily-at", "21:30"]), 0)
        config = self.ws.config
        self.assertEqual(config["sources"]["wechat"]["scope"]["include_ids"], ["friend"])
        self.assertNotIn("saved_schedule_ref", config["maintenance"])
        self.assertTrue((self.ws.root / "state/schedule-request.md").exists())

    def test_reply_change_outside_excerpt_still_updates_reading_revision(self):
        p = self.root / "chatgpt.json"
        data = conversation()
        data["mapping"]["other"]["message"]["content"]["parts"] = ["A" * 700 + "旧回复后段"]
        write(p, [data])
        self.enable("chatgpt", p)
        Collector(self.ws).collect()
        before = self.records("chatgpt")[0]["edition"]
        data["mapping"]["other"]["message"]["content"]["parts"] = ["A" * 700 + "新的完整回复后段"]
        data["update_time"] = 3
        write(p, [data])
        Collector(self.ws).collect()
        self.assertGreater(self.records("chatgpt")[0]["edition"], before)

    def test_unknown_wechat_format_gives_redacted_adapter_sample(self):
        p = self.root / "unknown.json"
        write(p, {"provider": "other-export", "password": "unit_test_secret_value_123", "messages": [{"body": "普通聊天"}]})
        self.enable("wechat", p)
        result = Collector(self.ws).collect()
        error = result["sources"]["wechat"]["errors"][0]
        self.assertEqual(error["error_type"], "NeedsFormat")
        sample = Path(error["adaptation_sample"]).read_text()
        self.assertNotIn("unit_test_secret_value_123", sample)
        self.assertIn("普通聊天", sample)

    def test_health_csv_time_range_excludes_unselected_records(self):
        p = self.root / "health.csv"
        p.write_text("date,steps\n2026-09-01,1234\n2026-10-11,5678\n")
        self.enable("health", p, scope={"from": "2026-10-01"})
        result = Collector(self.ws).collect()
        self.assertEqual(result["sources"]["health"]["errors"], [])
        record = self.records("health")[0]
        body = (self.ws.context / record["document"]).read_text()
        self.assertIn("5678", body)
        self.assertNotIn("1234", body)

    def test_xlsx_multiline_values_keep_real_worksheet_row(self):
        p = self.root / "bill.xlsx"
        header = ["交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)", "支付方式", "当前状态", "交易单号"]
        def xml_row(number, values):
            cells = ''.join(f'<c r="{chr(65+i)}{number}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' for i, v in enumerate(values))
            return f'<row r="{number}">{cells}</row>'
        first = ["2026-10-11 10:00:00", "商户消费", "商户", "多行\n商品", "支出", "12.30", "零钱", "支付成功", "first"]
        second = ["2026-10-11 11:00:00", "商户消费", "商户", "另一商品", "支出", "7.20", "零钱", "支付成功", "second"]
        with zipfile.ZipFile(p, "w") as archive:
            archive.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + xml_row(4, header) + xml_row(8, first) + xml_row(11, second) + '</sheetData></worksheet>')
        self.enable("payments", p)
        result = Collector(self.ws).collect()
        self.assertEqual(result["sources"]["payments"]["errors"], [])
        original = load(self.ws.context / self.records("payments")[0]["readable_originals"][0])
        self.assertEqual([r["line"] for r in original], [8, 11])

    def test_upload_command_records_provider_receipt(self):
        config = self.ws.config
        script = "import json,sys; from pathlib import Path; q=json.load(sys.stdin); assert Path(q['bundle']).exists(); print(json.dumps({'status':'uploaded','artifact_ref':'fixture://opaque-artifact'}))"
        config["runtime"]["uploader_command"] = [sys.executable, "-c", script]
        write(self.ws.root / "config.json", config)
        result = export_bundle(self.ws, upload=True)
        self.assertEqual(result["artifact_ref"], "fixture://opaque-artifact")
        self.assertEqual(load(self.ws.root / "state/delivery.json")["status"], "uploaded")

    def test_dot_view_obeys_share_choice_and_keeps_user_added_files(self):
        view = publish_dot_view(self.ws)
        self.assertTrue((view / "wiki/关于用户/当前认识.md").exists())
        note = view.parent / "inbox/manual/not-yet-imported.txt"
        write(note, "本人尚未导入的资料")
        config = self.ws.config
        config["sources"]["manual"]["dot_share"] = "none"
        write(self.ws.root / "config.json", config)
        refreshed = publish_dot_view(self.ws)
        self.assertFalse((refreshed / "wiki/关于用户/当前认识.md").exists())
        self.assertEqual(note.read_text(), "本人尚未导入的资料")
        self.assertNotIn("示例用户", (refreshed.parent / "config.json").read_text())

    def test_setup_wizard_saves_choices_without_enabling_unselected_sources(self):
        ws = Workspace(self.root / "wizard")
        answers = "示例\nAsia/Shanghai\n安排课程学习\ny\n\n\n\nn\nn\nn\nn\nn\n\n"
        with patch("sys.stdin", io.StringIO(answers)), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--workspace", str(ws.root), "setup"]), 0)
        self.assertEqual(ws.config["goals"], ["安排课程学习"])
        self.assertTrue(ws.config["sources"]["manual"]["enabled"])
        self.assertFalse(any(c["enabled"] for p, c in ws.config["sources"].items() if p != "manual"))


if __name__ == "__main__":
    unittest.main()
