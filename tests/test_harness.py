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
from personal_agent.delivery import delivery_receipt, export_bundle, publish_dot_view, sync_incoming
from personal_agent.runtime import execute_agent, job
from personal_agent.onboarding import onboard
from personal_agent.privacy import permissions, record_withdrawal, withdraw


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
        view = publish_dot_view(self.ws)
        self.ws.feedback("每周可投入五小时", section="当前可用时间")
        body = (self.ws.context / "wiki/关于用户/当前认识.md").read_text()
        self.assertIn("每周可投入五小时", body)
        self.assertNotIn("每周可投入十小时", body)
        self.assertEqual(body.count("## 当前可用时间"), 1)
        self.assertEqual((view / "wiki/关于用户/当前认识.md").read_text(), body)
        with redirect_stdout(io.StringIO()):
            main(['-w', str(self.ws.root), 'maintain'])
        self.assertEqual((view / "wiki/关于用户/当前认识.md").read_text(), body)

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
        entry = unpack / 'workspace/agent-context/BOOTSTRAP.md'
        self.assertTrue((unpack / 'tools/BOOTSTRAP.md').exists())
        self.assertTrue((unpack / 'tools/docs/commands.md').exists())
        self.assertIn('python3 ../../tools/personal-agent --workspace .. bootstrap', entry.read_text())
        boot = subprocess.run([sys.executable, '../../tools/personal-agent', '--workspace', '..', 'bootstrap'], cwd=entry.parent, stdin=subprocess.DEVNULL, text=True, capture_output=True)
        self.assertEqual(boot.returncode, 0, boot.stderr)
        self.assertEqual(json.loads(boot.stdout)['status'], 'resumed')
        self.assertEqual(load(unpack / 'workspace/config.json')['id'], self.ws.config['id'])

    def test_bootstrap_starts_without_terminal_input_or_enabling_sources(self):
        root = self.root / 'first-start'
        output = io.StringIO()
        with patch('personal_agent.bootstrap.PROJECT', self.root / 'project'), patch('builtins.input', side_effect=AssertionError('Bootstrap must not read terminal input')), redirect_stdout(output):
            self.assertEqual(main(['--workspace', str(root), 'bootstrap', '--goal', '先整理自己的目标']), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], 'created')
        self.assertEqual(result['enabled_sources'], ['manual'])
        self.assertTrue(Path(result['instructions']).exists())
        self.assertTrue(Path(result['entry']).exists())
        self.assertEqual(load(result['binding'])['workspace'], str(root.resolve()))
        self.assertEqual(Workspace(root).config['goals'], ['先整理自己的目标'])
        self.assertEqual(result['digestion']['reviewed_current'], 0)
        self.assertEqual(set(result['automation']['tasks']), {'maintenance', 'calibration'})
        self.assertTrue(all(t['status'] == 'awaiting_platform_schedule' for t in result['automation']['tasks'].values()))
        self.assertTrue(Workspace(root).config['maintenance']['enabled'])

    def test_bootstrap_remembers_choice_to_start_with_self_description_only(self):
        root = self.root / 'self-only'
        with patch('personal_agent.bootstrap.PROJECT', self.root / 'project'):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--workspace', str(root), 'bootstrap', '--sources-decided']), 0)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(['bootstrap']), 0)
        result = json.loads(output.getvalue())
        self.assertIsNone(result['next_question'])
        self.assertEqual(result['enabled_sources'], ['manual'])

    def test_bootstrap_resumes_project_binding_and_preserves_choices_and_wiki(self):
        self.enable('wechat', self.root / 'chosen-chat.json', dot_share='none', scope={'include_ids': ['chosen-friend']})
        self.ws.feedback('保留已经确认的个人背景', section='本人确认')
        page = self.ws.context / 'wiki/关于用户/当前认识.md'
        content = page.read_text()
        config = self.ws.config
        with patch('personal_agent.bootstrap.PROJECT', self.root / 'project'):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--workspace', str(self.ws.root), 'bootstrap']), 0)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(['bootstrap']), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result['workspace'], str(self.ws.root))
            self.assertEqual(result['status'], 'resumed')
            self.assertIsNone(result['next_question'])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(['status']), 0)
        self.assertEqual(self.ws.config, config)
        self.assertEqual(page.read_text(), content)

    def cloud(self):
        bundle = export_bundle(self.ws)
        folder = self.root / "cloud"
        with zipfile.ZipFile(bundle["bundle"]) as archive:
            archive.extractall(folder)
        return Workspace(folder / "workspace")

    def test_cloud_confirmation_roundtrip_updates_local_wiki(self):
        view = publish_dot_view(self.ws)
        remote = self.cloud()
        remote.feedback("本人确认近期以考试为优先", section="当前优先事项")
        returned = export_bundle(remote, self.root / "return.zip", returning=True)
        result = sync_incoming(self.ws, returned["bundle"])
        self.assertEqual(result["conflicts"], [])
        self.assertIn("本人确认近期以考试为优先", (self.ws.context / "wiki/关于用户/当前认识.md").read_text())
        self.assertIn("本人确认近期以考试为优先", (view / "wiki/关于用户/当前认识.md").read_text())

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

    def test_schedules_keep_independent_refs_and_reconcile_changed_times(self):
        from personal_agent.runtime import schedule
        saved = schedule(self.ws, confirm='native://daily', executor='codex-local')
        self.assertEqual(saved['tasks']['maintenance']['status'], 'confirmed')
        self.assertEqual(saved['tasks']['calibration']['status'], 'awaiting_platform_schedule')
        updated = schedule(self.ws, at='20:30')
        self.assertEqual(updated['tasks']['maintenance']['status'], 'awaiting_platform_update')
        self.assertEqual(updated['tasks']['maintenance']['saved_schedule_ref'], 'native://daily')
        schedule(self.ws, task='calibration', disable=True)
        before = self.ws.config
        with patch('personal_agent.bootstrap.PROJECT', self.root / 'project'), redirect_stdout(io.StringIO()):
            main(['-w', str(self.ws.root), 'bootstrap'])
        self.assertEqual(self.ws.config, before)
        self.assertFalse(schedule(self.ws)['tasks']['calibration']['selection']['enabled'])
        self.assertNotIn('--confirm-ref', updated['tasks']['maintenance']['prompt'])
        schedule(self.ws, cancelled=True)
        self.assertNotIn('saved_schedule_ref', self.ws.config['maintenance'])
        self.assertEqual(schedule(self.ws)['tasks']['maintenance']['status'], 'disabled')

    def test_chatgpt_app_discovers_selected_threads_and_resumes_both_cursors(self):
        from personal_agent.chatgpt import discover, plan, record, snapshot_path
        from personal_agent.parsers import parse_chatgpt_app
        self.enable('chatgpt', self.ws.root / 'inbox/chatgpt', mode='app', scope={'include_ids': ['c1']})
        discovered = discover(self.ws, {'threads': [{'id': 'c1', 'kind': 'chatgpt'}, {'id': 'c2', 'kind': 'chatgpt'}, {'id': 'local', 'kind': 'codex'}]})
        self.assertEqual(discovered['discovered'], ['c1'])
        def page(ident, text, next_cursor, moment):
            return {'thread': {'id': 'c1', 'kind': 'chatgpt', 'title': '上下文', 'updatedAt': 1791720200},
                    'page': {'order': 'newest_first', 'hasMore': next_cursor is not None, 'nextCursor': next_cursor},
                    'turns': [{'startedAt': moment, 'items': [{'id': ident, 'type': 'agentMessage', 'text': text}]}]}
        record(self.ws, page('m2', '第二页正文', 'older-1', 1791720100))
        record(self.ws, page('m1', '更早正文', 'older-2', 1791720000), 'older-1')
        head = page('m4', '新正文', 'recent-gap', 1791720300)
        head['turns'].append(page('m3', '旧头部较早消息', 'recent-gap', 1791720200)['turns'][0])
        record(self.ws, head)
        requests = plan(self.ws)['requests']
        self.assertIn('older-2', [r['args'].get('cursor') for r in requests])
        self.assertIn('recent-gap', [r['args'].get('cursor') for r in requests])
        record(self.ws, page('m2', '第二页已修订', 'older-1', 1791720100), 'recent-gap')
        self.assertNotIn('recent_gap', [r['purpose'] for r in plan(self.ws)['requests']])
        record(self.ws, page('m0', '最早正文', None, 1791719900), 'older-2')
        meta, messages = parse_chatgpt_app(load(snapshot_path(self.ws, 'c1')))
        self.assertEqual([m['text'] for m in messages], ['最早正文', '更早正文', '第二页已修订', '旧头部较早消息', '新正文'])
        self.assertFalse(meta['more_history_available'])
        # An overlapping refreshed head must retain messages that have fallen
        # off the page, not merely the pages reached by historical cursors.
        refreshed = page('m5', '另一条新正文', 'known-history', 1791720400)
        refreshed['turns'].append(page('m4', '新正文已改', None, 1791720300)['turns'][0])
        record(self.ws, refreshed)
        meta, messages = parse_chatgpt_app(load(snapshot_path(self.ws, 'c1')))
        self.assertEqual([m['text'] for m in messages], ['最早正文', '更早正文', '第二页已修订', '旧头部较早消息', '新正文已改', '另一条新正文'])
        Collector(self.ws).collect('chatgpt')
        self.assertEqual(len(self.records('chatgpt')), 1)
        self.assertEqual(self.records('chatgpt')[0]['messages'], 6)
        self.assertEqual(self.ws.status()['chatgpt_capture']['known_threads'], 1)
        config = self.ws.config
        config['sources']['chatgpt']['enabled'] = False
        write(self.ws.root / 'config.json', config)
        self.assertEqual(plan(self.ws)['requests'], [])
        self.assertEqual(record(self.ws, page('not-read', '未授权', None, 1791720500))['status'], 'disabled')

    def test_long_chat_segments_resume_and_new_editions_revisit_changes(self):
        p = self.root / 'chatgpt.json'
        write(p, [conversation('原文' * 20)])
        self.enable('chatgpt', p)
        Collector(self.ws).collect('chatgpt')
        publish_dot_view(self.ws)
        first = load(job(self.ws, 'chatgpt', max_chars=12)['job'])
        reading = first['sources'][0]['reading']
        self.assertEqual(reading['start'], {'message': 0, 'offset': 0})
        self.assertEqual(reading['next_read'], {'message': 0, 'offset': 12})
        self.ws.record(first['response_example'])
        second = load(job(self.ws, 'chatgpt', max_chars=12)['job'])
        self.assertEqual(second['sources'][0]['reading']['start'], {'message': 0, 'offset': 12})
        self.ws.record(second['response_example'])
        third = load(job(self.ws, 'chatgpt', max_chars=12)['job'])
        self.assertEqual(third['sources'][0]['reading']['start'], {'message': 0, 'offset': 24})
        write(p, [conversation('更新' * 20, revision=3)])
        Collector(self.ws).collect('chatgpt')
        changed = load(job(self.ws, 'chatgpt', max_chars=12)['job'])
        self.assertEqual(changed['sources'][0]['reading']['start'], {'message': 0, 'offset': 0})

    def test_maintenance_merges_received_cloud_changes_and_publishes_after_reading(self):
        from personal_agent.maintenance import finish, maintain
        p = self.root / 'chats.json'
        write(p, [conversation()])
        self.enable('chatgpt', p, mode='export', dot_share='sources')
        Collector(self.ws).collect('chatgpt')
        remote = self.cloud()
        remote.feedback('云端确认的新目标', section='当前目标')
        incoming = self.ws.root / 'exchange/incoming/return.zip'
        export_bundle(remote, incoming, returning=True)
        result = maintain(self.ws)
        self.assertEqual(len(result['incoming']['results']), 1)
        self.assertIn('云端确认的新目标', (self.ws.context / 'wiki/关于用户/当前认识.md').read_text())
        self.assertNotIn('publication', result)
        self.assertFalse((self.ws.root / 'exchange/outgoing/context.zip').exists())
        self.assertEqual(maintain(self.ws)['incoming']['results'], [])
        second = {**conversation('本地新增材料'), 'id': 'conversation-two'}
        write(p, [conversation(), second])
        Collector(self.ws).collect('chatgpt')
        self.ws.feedback('本地确认可以开始新项目', page='wiki/想法与选择/目标与选择.md', section='当前安排')
        delivered = finish(self.ws)
        self.assertEqual(delivered['delivery']['status'], 'packaged')
        self.assertTrue(Path(delivered['delivery']['bundle']).exists())
        with zipfile.ZipFile(delivered['delivery']['bundle']) as archive:
            cloud_config = json.loads(archive.read('workspace/config.json'))
        self.assertEqual(cloud_config['runtime']['workspace_role'], 'cloud')
        self.assertTrue(cloud_config['maintenance']['enabled'])
        self.assertTrue(cloud_config['maintenance']['calibration']['enabled'])
        from personal_agent.runtime import schedule
        self.assertTrue(all(t['status'] == 'managed_by_origin' for t in schedule(remote)['tasks'].values()))
        forwarded = sync_incoming(remote, delivered['delivery']['bundle'])
        self.assertFalse(forwarded['conflicts'])
        self.assertIn('chatgpt:conversation-two', remote.catalog()['records'])
        self.assertIn('本地确认可以开始新项目', (remote.context / 'wiki/想法与选择/目标与选择.md').read_text())
        remote.feedback('云端随后补充了项目时间', page='wiki/想法与选择/目标与选择.md', section='当前安排')
        returning = finish(remote)['delivery']['bundle']
        self.assertFalse(sync_incoming(self.ws, returning)['conflicts'])
        self.assertIn('云端随后补充了项目时间', (self.ws.context / 'wiki/想法与选择/目标与选择.md').read_text())
        config = self.ws.config
        config['maintenance']['cloud_sync'] = False
        write(self.ws.root / 'config.json', config)
        self.assertEqual(maintain(self.ws)['incoming']['status'], 'disabled')
        self.assertNotIn('delivery', finish(self.ws))

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
        self.assertEqual(ws.status()['onboarding']['phase'], 'review')
        self.assertGreater(ws.queue()['counts']['pending'], 0)

    def test_record_refreshes_published_wiki_without_an_extra_dot_command(self):
        view = publish_dot_view(self.ws)
        item = self.ws.queue()['pending'][0]
        page = 'wiki/想法与选择/目标与选择.md'
        body = '# 当前目标\n\n先完成课程，再安排项目。\n'
        self.ws.record({'reviews': [{'key': item['key'], 'revision': item['revision'], 'status': 'done'}],
                        'wiki_updates': [{'path': page, 'content': body, 'source_keys': [item['key']]}]})
        self.assertEqual((view / page).read_text(), body)

    def test_narrowed_dates_remove_old_understanding_and_reject_old_returns(self):
        p = self.root / 'scoped.json'
        data = conversation('范围内的新安排')
        data['mapping']['early'] = {'parent': None, 'message': {'author': {'role': 'user'}, 'content': {'parts': ['已经撤回的早期安排']}, 'create_time': 1700000000}}
        data['mapping']['current']['parent'] = 'early'
        write(p, [data])
        self.enable('chatgpt', p, dot_share='sources')
        Collector(self.ws).collect()
        item = next(r for r in self.ws.queue()['pending'] if r['provider'] == 'chatgpt')
        page = 'wiki/想法与选择/目标与选择.md'
        old_response = {'reviews': [{'key': item['key'], 'revision': item['revision'], 'status': 'done', 'wiki_pages': [page]}],
                        'wiki_updates': [{'path': page, 'content': '# 安排\n已经撤回的早期安排\n', 'source_keys': [item['key']]}]}
        self.ws.record(old_response)
        remote = self.cloud()
        view = publish_dot_view(self.ws)
        with redirect_stdout(io.StringIO()):
            main(['-w', str(self.ws.root), 'sources', 'chatgpt', '--from', '2026-10-01'])
        self.assertIn('范围内的新安排', (self.ws.context / item['document']).read_text())
        self.assertNotIn('已经撤回的早期安排', (self.ws.context / item['document']).read_text())
        self.assertNotIn('已经撤回的早期安排', (self.ws.context / page).read_text())
        self.assertNotIn('已经撤回的早期安排', (view / page).read_text())
        self.assertTrue(self.ws.record(old_response)['skipped'])
        remote.apply_wiki([{'path': page, 'content': '# 云端旧依据\n已经撤回的早期安排\n', 'source_keys': [item['key']]}])
        returned = export_bundle(remote, self.root / 'old-return.zip', returning=True)
        self.assertTrue(sync_incoming(self.ws, returned['bundle'])['skipped'])
        current = next(r for r in self.ws.queue()['pending'] if r['provider'] == 'chatgpt')
        self.ws.record({'source_permissions': permissions(self.ws), 'reviews': [{'key': current['key'], 'revision': current['revision'], 'status': 'done'}],
                        'wiki_updates': [{'path': page, 'content': '# 安排\n范围内的新安排\n', 'source_keys': [current['key']]}]})
        self.assertIn('范围内的新安排', (view / page).read_text())
        self.assertNotIn(page, self.ws.status()['wiki_rebuild']['pages'])
        version = permissions(self.ws)['chatgpt']
        Collector(self.ws).collect()
        self.assertEqual(permissions(self.ws)['chatgpt'], version)
        bundle = export_bundle(self.ws, returning=True)
        with zipfile.ZipFile(bundle['bundle']) as archive:
            self.assertNotIn('已经撤回的早期安排', '\n'.join(archive.read(n).decode() for n in archive.namelist()))

    def test_withdraw_removes_views_and_exports_but_keeps_originals_until_requested(self):
        p = self.root / 'selected-health.txt'
        p.write_text('待撤回的虚构生活记录')
        self.enable('health', p, dot_share='sources')
        Collector(self.ws).collect()
        key = self.records('health')[0]['key']
        page = 'wiki/生活与健康/生活记录.md'
        self.ws.apply_wiki([{'path': page, 'content': '# 生活\n待撤回的虚构生活记录\n本人仍愿保留的其他认识\n', 'source_keys': [key, 'manual:profile']}])
        view = publish_dot_view(self.ws)
        bundle = export_bundle(self.ws)
        delivery_receipt(self.ws, {'status': 'uploaded', 'artifact_ref': 'fixture://selected-bundle'})
        result = withdraw(self.ws, 'health')
        self.assertTrue(list((self.ws.root / 'raw/health').glob('*')))
        self.assertTrue(p.exists())
        self.assertFalse(Path(bundle['bundle']).exists())
        self.assertFalse(self.records('health'))
        self.assertNotIn('待撤回的虚构生活记录', (view / page).read_text())
        self.assertEqual(self.ws.status()['wiki_rebuild']['pages'][page]['source_keys'], ['manual:profile'])
        self.assertEqual(result['remote'][0]['status'], 'pending')
        recorded = record_withdrawal(self.ws, {'id': result['id'], 'remote': [{'artifact_ref': 'fixture://selected-bundle', 'status': 'removed', 'note': 'fixture native tool result'}]})
        self.assertEqual(recorded['remote'][0]['status'], 'removed')
        withdraw(self.ws, 'health', delete_raw=True)
        self.assertFalse((self.ws.root / 'raw/health').exists())
        self.assertTrue(p.exists())
        self.assertTrue((view / 'wiki/想法与选择/目标与选择.md').exists())

    def test_onboarding_resumes_through_confirmation_and_a_real_answer(self):
        self.assertEqual(onboard(self.ws)['phase'], 'configure')
        start = onboard(self.ws, {'stage': 'configured', 'question': '这周怎样安排课程和项目？'})
        self.assertEqual(start['phase'], 'review')
        item = self.ws.queue()['pending'][0]
        self.ws.record({'reviews': [{'key': item['key'], 'revision': item['revision'], 'status': 'done'}]})
        self.assertEqual(onboard(self.ws)['phase'], 'review')
        page = 'wiki/关于用户/当前认识.md'
        with self.assertRaises(ValueError):
            onboard(self.ws, {'stage': 'used', 'question': '测试', 'answer': '尚未确认', 'wiki_pages': [page]})
        onboard(self.ws, {'stage': 'reviewed', 'summary': '你希望兼顾课程与个人项目。', 'wiki_pages': [page]})
        self.assertEqual(onboard(Workspace(self.ws.root))['phase'], 'confirm')
        self.ws.feedback('本周项目只能投入五小时', section='本周约束')
        step = onboard(self.ws, {'stage': 'confirmed', 'user_reply': '对，但这周项目只能投入五小时。', 'summary': '兼顾课程，本周项目限五小时。'})
        self.assertEqual(step['phase'], 'first_use')
        self.assertIn('本周项目只能投入五小时', (Path(step['context']) / page).read_text())
        result = onboard(self.ws, {'stage': 'used', 'question': '这周怎样安排课程和项目？', 'answer': '先完成课程任务，项目按你确认的五小时安排两次工作。', 'wiki_pages': [page]})
        self.assertEqual(result['phase'], 'ready')
        self.assertEqual(onboard(Workspace(self.ws.root))['phase'], 'ready')

    def test_narrowing_files_removes_old_views_but_disabling_only_pauses_collection(self):
        folder = self.root / 'chosen-files'
        folder.mkdir()
        (folder / 'a.txt').write_text('不再提供的文件内容')
        (folder / 'b.txt').write_text('继续提供的文件内容')
        self.enable('health', folder, dot_share='sources')
        Collector(self.ws).collect()
        old = next(r for r in self.records('health') if r['title'] == 'a')
        with redirect_stdout(io.StringIO()):
            main(['-w', str(self.ws.root), 'sources', 'health', '--clear-paths', '--path', str(folder / 'b.txt')])
        self.assertFalse((self.ws.context / old['document']).exists())
        self.assertEqual([r['title'] for r in self.records('health')], ['b'])
        with redirect_stdout(io.StringIO()):
            main(['-w', str(self.ws.root), 'sources', 'health', '--disable'])
        self.assertEqual([r['title'] for r in self.records('health')], ['b'])

    def test_sharing_less_preserves_actual_local_reading_progress(self):
        p = self.root / 'health.txt'
        p.write_text('本地已读的虚构记录')
        self.enable('health', p, dot_share='sources')
        Collector(self.ws).collect()
        self.ws.record({'reviews': [{'key': r['key'], 'revision': r['revision'], 'status': 'done'} for r in self.ws.queue()['pending']]})
        before = self.ws.queue()['counts']
        view = publish_dot_view(self.ws)
        with redirect_stdout(io.StringIO()):
            main(['-w', str(self.ws.root), 'sources', 'health', '--share', 'wiki'])
        self.assertEqual(self.ws.queue()['counts'], before)
        self.assertFalse((view / self.records('health')[0]['document']).exists())

    def test_withdrawn_self_description_retains_local_original_only(self):
        self.ws.feedback('本人明确的虚构背景', section='背景')
        withdraw(self.ws, 'manual')
        saved = '\n'.join(p.read_text() for p in (self.ws.root / 'raw/manual').glob('*.md'))
        self.assertIn('本人明确的虚构背景', saved)
        self.assertFalse(self.records('manual'))
        self.assertEqual(self.ws.config['goals'], [])


if __name__ == "__main__":
    unittest.main()
