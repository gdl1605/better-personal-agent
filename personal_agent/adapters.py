from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import csv
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid
from zoneinfo import ZoneInfo

from . import parsers
from .core import LABELS, PROVIDERS, load, safe_id, within, write
from .credentials import Redactor


class NeedsFormat(ValueError):
    """A selected source needs an adapter for its actual format."""


def identity(value):
    return uuid.uuid5(uuid.NAMESPACE_URL, str(value)).hex


def date(value, tz):
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(value, timezone.utc)
    if not value:
        return None
    result = datetime.fromisoformat(str(value).replace("/", "-").replace("Z", "+00:00"))
    return result.replace(tzinfo=tz) if result.tzinfo is None else result


def selected(ident, when, scope, tz):
    include, exclude = scope.get("include_ids", []), scope.get("exclude_ids", [])
    if (include and str(ident) not in include) or str(ident) in exclude:
        return False
    moment = date(when, tz)
    if moment:
        if scope.get("from") and moment < date(scope["from"], tz):
            return False
        if scope.get("until"):
            end = date(scope["until"], tz)
            if len(scope["until"]) == 10:
                if moment >= end + timedelta(days=1):
                    return False
            elif moment > end:
                return False
    return True


def files(paths, extensions):
    result = set()
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError("选定资料路径不存在")
        candidates = path.rglob("*") if path.is_dir() else [path]
        for candidate in candidates:
            if candidate.is_file() and candidate.suffix.lower() in extensions:
                if not path.is_dir() or candidate.resolve().is_relative_to(path):
                    result.add(candidate.resolve())
    return sorted(result, key=lambda p: (p.stat().st_mtime_ns, str(p)))


def local_codex_paths():
    # A new Codex installation may have no archived_sessions directory yet.
    return [str(path) for path in (Path.home() / '.codex/sessions', Path.home() / '.codex/archived_sessions') if path.exists()]


class Collector:
    def __init__(self, workspace):
        self.ws = workspace
        self.config = workspace.config
        self.tz = ZoneInfo(self.config["owner"]["timezone"])
        parsers.TZ = self.tz
        self.redactions = {}
        self.original_changes = {}

    def clean(self, provider, value):
        policy = self.config["privacy"]
        if policy.get("redact_credentials") and provider in policy.get("credential_scan_sources", ["wechat"]):
            redactor = Redactor()
            result = redactor.obj(deepcopy(value))
            for rule, number in redactor.counts.items():
                self.redactions[rule] = self.redactions.get(rule, 0) + number
            return result
        return value

    def archive(self, provider, path):
        target = self.ws.root / "raw" / provider / f"{identity(path)}{path.suffix.lower()}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or [target.stat().st_size, target.stat().st_mtime_ns] != [path.stat().st_size, path.stat().st_mtime_ns]:
            shutil.copy2(path, target)
        return str(target.relative_to(self.ws.root))

    def original(self, provider, ident, value):
        path = f"sources/{provider}/originals/{safe_id(ident)}.json"
        self.original_changes[path] = write(self.ws.context / path, value)
        return path

    def dialogue(self, provider, ident, title, messages, origin, extra=None):
        if not messages:
            state = self.ws.catalog()
            state["records"].pop(f"{provider}:{ident}", None)
            self.ws.save_catalog(state)
            return
        original = self.original(provider, ident, {"kind": "selected_reading_copy", "provider": provider, "messages": messages})
        lines = [f"# {title}", "", "> 选定范围的来源视图；本人、助手、引用与其他分支分别判断。", "", f"[所选范围的完整文字](<originals/{safe_id(ident)}.json>)", ""]
        for index, message in enumerate(messages):
            pointer = message.get("pointer") or (f"原节点 {message['node_id']}" if "node_id" in message else f"原 JSONL 行 {message.get('line', '')}")
            if message.get('_origin_record'):
                pointer = f"{message['_origin_record']}，成员 {message.get('_origin_member') or ''}，会话 {message.get('_origin_conversation_index', '')}，{message['_origin_pointer']}"
            anchor = safe_id(message.get("node_id") or f"line-{message.get('line', index)}")
            lines += [f'<a id="{anchor}"></a>', f"## {message.get('time') or '时间未返回'} · {message['role']} · {message.get('branch', '')}", "", f"出处：{pointer}；阅读副本 `/messages/{index}`。", "", parsers.message_view(message), ""]
            if message.get("truncated"):
                lines += ["工具标记这条消息截断；完整性以实际返回为准。", ""]
            if message.get("media"):
                lines += [f"附件类型：{', '.join(message['media'])}；当前没有识别媒体内容。", ""]
        self.ws.put(provider, ident, title, "\n".join(lines) + "\n", max(m.get("time", "") for m in messages), origin, messages=len(messages), readable_originals=[original], original_changed=self.original_changes.get(original, False), **(extra or {}))

    def codex(self, path, options):
        header = parsers.codex_header(path)
        if options.get("scope", {}).get("main_conversations_only", True) and header["kind"] != "conversation":
            return
        messages, omitted, _ = parsers.parse_codex(path)
        messages = [m for m in messages if selected(header["id"], m["time"], options.get("scope", {}), self.tz)]
        messages = self.clean("codex", messages)
        title = next((m["text"].splitlines()[0][:90] for m in messages if m["role"] == "user" and m["text"]), header["id"])
        self.dialogue("codex", header["id"], title, messages, self.archive("codex", path), {"omitted": omitted})

    def chatgpt(self, path, options):
        for member, payload in parsers.chatgpt_payloads(path):
            app = isinstance(payload, dict) and payload.get("provider") == "codex_app.read_thread"
            conversations = [payload] if app else payload if isinstance(payload, list) else payload.get("conversations", [payload])
            for index, conversation in enumerate(conversations):
                if app:
                    record, messages = parsers.parse_chatgpt_app(conversation)
                else:
                    ident = conversation.get("id") or conversation.get("conversation_id")
                    if not ident:
                        raise ValueError("ChatGPT 导出缺少会话 ID")
                    record = {"id": str(ident), "title": conversation.get("title") or str(ident), "revision": conversation.get("update_time") or conversation.get("create_time") or 0}
                    messages = parsers.parse_chatgpt(conversation)
                old = self.ws.catalog()["records"].get(f"chatgpt:{record['id']}", {})
                source_kind = 'app_snapshot' if app else 'account_export'
                mixed = old.get('source_kind') in ('account_export', 'app_snapshot', 'mixed') and old['source_kind'] != source_kind
                if old.get("source_revision", 0) > record["revision"] and not mixed:
                    continue
                messages = [m for m in messages if selected(record["id"], m["time"], options.get("scope", {}), self.tz)]
                origin = self.archive("chatgpt", path)
                if mixed:
                    previous = []
                    for relative in old.get('readable_originals', []):
                        saved = load(within(self.ws.context, relative, 'sources'))
                        if isinstance(saved, dict) and saved.get('kind') == 'selected_reading_copy':
                            previous.extend(saved['messages'])
                    # Tool pages and account exports have different coverage.
                    # Keep messages absent from the other input; a newer input
                    # owns an explicitly returned message with the same ID.
                    for values, location, saved_member, saved_index in (
                        (previous, old['origin'], old.get('member'), old.get('conversation_index')),
                        (messages, origin, member, index),
                    ):
                        for message in values:
                            message.setdefault('_origin_record', location)
                            message.setdefault('_origin_member', saved_member)
                            message.setdefault('_origin_conversation_index', saved_index)
                            message.setdefault('_origin_pointer', message.get('pointer') or '/mapping/' + str(message['node_id']).replace('~', '~0').replace('/', '~1') + '/message')
                    ordered = (previous, messages) if record['revision'] >= old.get('source_revision', 0) else (messages, previous)
                    merged = {}
                    for values in ordered:
                        for message in values:
                            key = (message['role'], message.get('message_id') or message.get('node_id') or (message['_origin_record'], message['_origin_pointer']))
                            merged[key] = message
                    messages = sorted(merged.values(), key=lambda m: m.get('time', ''))
                    source_kind = 'mixed'
                    record['revision'] = max(record['revision'], old.get('source_revision', 0))
                messages = self.clean("chatgpt", messages)
                self.dialogue("chatgpt", record["id"], record["title"], messages, origin, {"source_revision": record["revision"], "source_kind": source_kind, "member": member, "conversation_index": index, **({"more_history_available": record['more_history_available'], "truncated_messages": record['truncated_messages']} if app else {})})

    def database(self):
        db = sqlite3.connect(self.ws.root / "state/wechat.sqlite3")
        db.execute("CREATE TABLE IF NOT EXISTS messages (account TEXT, chat TEXT, message_key TEXT, month TEXT, created INTEGER, body TEXT, origin TEXT, pointer TEXT, PRIMARY KEY(account,chat,message_key))")
        return db

    def wechat(self, path, options):
        snapshot = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(snapshot, dict) or snapshot.get("provider") != "wechat-use.history":
            raise NeedsFormat("当前微信接口读取 wechat-use.history；请让 Agent 读取适配样本并修改对应解析器，或投放此格式的快照。")
        account, chat = snapshot["account"], snapshot["chat_id"]
        if not selected(chat, None, options.get("scope", {}), self.tz):
            return
        origin = self.archive("wechat", path)
        db = self.database()
        with db:
            for index, row in enumerate(snapshot["result"]["rows"]):
                if not selected(chat, row["create_time"], options.get("scope", {}), self.tz):
                    continue
                key = parsers.message_key(row)
                if not key.startswith("server:"):
                    key = "local:" + identity(key)
                clean = self.clean("wechat", row)
                body = json.dumps(clean, ensure_ascii=False, sort_keys=True)
                old = db.execute("SELECT body FROM messages WHERE account=? AND chat=? AND message_key=?", (account, chat, key)).fetchone()
                if old and old[0] == body:
                    continue
                month = datetime.fromtimestamp(row["create_time"], self.tz).strftime("%Y-%m")
                db.execute("INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?,?)", (account, chat, key, month, row["create_time"], body, origin, f"/result/rows/{index}"))
        db.close()

    def render_wechat(self, options):
        db = self.database()
        visible = set()
        for account, chat, month in db.execute("SELECT DISTINCT account,chat,month FROM messages ORDER BY month"):
            rows = db.execute("SELECT message_key,created,body,origin,pointer FROM messages WHERE account=? AND chat=? AND month=? ORDER BY created,message_key", (account, chat, month)).fetchall()
            rows = [r for r in rows if selected(chat, r[1], options.get("scope", {}), self.tz)]
            if not rows:
                continue
            ident = f"{account}:{chat}:{month}"
            messages = [{**self.clean("wechat", json.loads(body)), "_origin_record": origin, "_origin_pointer": pointer} for _, _, body, origin, pointer in rows]
            original = self.original("wechat", ident, {"provider": "wechat-selected-reading-copy", "account": account, "chat_id": chat, "rows": messages})
            title = f"{messages[-1].get('chat_display_name') or chat} · {month}"
            lines = [f"# 微信：{title}", "", "> 本人归属按来源字段；对方与群友的发言不直接成为个人观点。", "", f"[所选消息的完整字段](<originals/{safe_id(ident)}.json>)", ""]
            for index, (key, created, _, _, _) in enumerate(rows):
                row = messages[index]
                sender = self.config["owner"]["display_name"] + "（本人）" if row.get("sender_is_self") is True else row.get("sender_display_name") or row.get("sender_wxid") or "发送者未确认"
                moment = datetime.fromtimestamp(created, self.tz).isoformat()
                lines += [f'<a id="{safe_id(key)}"></a>', f"## {moment} · {sender}", "", f"消息身份：`{key}`；阅读副本 `/rows/{index}`；原始位置 `{row['_origin_pointer']}`。", "", row.get("display_text") or row.get("message_content") or "本条无可读文字，媒体未识别。", ""]
            self.ws.put("wechat", ident, title, "\n".join(lines), datetime.fromtimestamp(rows[-1][1], self.tz).isoformat(), "已授权微信快照", chat_id=chat, account=account, messages=len(rows), readable_originals=[original], original_changed=self.original_changes.get(original, False))
            visible.add(f"wechat:{ident}")
        db.close()
        state = self.ws.catalog()
        state["records"] = {k: v for k, v in state["records"].items() if v["provider"] != "wechat" or k in visible or v.get("format") == "text"}
        self.ws.save_catalog(state)

    def live_wechat(self, options):
        cli = Path(options.get("cli") or "~/.local/bin/wechat-use").expanduser()
        if not cli.exists():
            return {"status": "needs_setup", "next_action": "安装并登录微信读取工具，或将已导出的 JSON/Markdown 放入微信投放目录。"}
        registry = parsers.call_cli(cli, ["accounts", "--json"])
        accounts = [a for a in registry["accounts"] if a.get("bundle_id") == options.get("bundle_id", "com.tencent.xinWeChat419WechatUse")]
        if len(accounts) != 1:
            setup = parsers.call_cli(cli, ["setup", "--json"])
            return {"status": "needs_setup", "stage": setup.get("stage"), "next_action": "在微信工具中完成激活和本人登录。"}
        account = accounts[0]["wxid"]
        sessions = parsers.query_all(cli, ["sessions"])
        state = self.ws.catalog()["sources"].get("wechat", {})
        cursors = state.get("cursors", {}) if state.get("scope") == options.get("scope", {}) else {}
        for row in sessions["rows"]:
            chat = row["username"]
            if chat in ("brandservicesessionholder", "brandsessionholder") or not selected(chat, None, options.get("scope", {}), self.tz):
                continue
            args = ["history", "--chat", chat]
            if chat in cursors:
                args += ["--since", str(cursors[chat])]
            elif options.get("scope", {}).get("from"):
                args += ["--since", str(int(date(options["scope"]["from"], self.tz).timestamp()))]
            until = options.get('scope', {}).get('until')
            if until:
                end = date(until, self.tz)
                # Harness date ranges include the selected day; the CLI's
                # --until is exclusive and its history timestamps are seconds.
                boundary = int((end + timedelta(days=1)).timestamp()) if len(until) == 10 else int(end.timestamp()) + 1
                args += ['--until', str(boundary)]
            result = parsers.query_all(cli, args)
            if result["rows"]:
                path = self.ws.root / "inbox/wechat" / f"{safe_id(account)}-{safe_id(chat)}.json"
                write(path, {"provider": "wechat-use.history", "account": account, "chat_id": chat, "result": result})
                self.wechat(path, options)
                cursors[chat] = max(r["create_time"] for r in result["rows"])
        return {"status": "collected", "cursors": cursors, "scope": options.get("scope", {})}

    def payment_xlsx(self, path):
        table = parsers.parse_xlsx(path)
        header_index, header = next((number, values) for number, values in table if values.get("A") == "交易时间")
        def csv_row(values):
            buffer = io.StringIO()
            csv.writer(buffer).writerow(values)
            return buffer.getvalue()
        chunks = [csv_row(header.values())]
        next_line = chunks[0].count("\n") + 1
        original_lines = {}
        for number, values in table:
            if number <= header_index or not values.get("A"):
                continue
            row = [values.get(key, "") for key in header]
            try:
                row[0] = (datetime(1899, 12, 30) + timedelta(seconds=round(float(row[0]) * 86400))).isoformat(sep=" ")
            except ValueError:
                pass
            chunk = csv_row(row)
            original_lines[next_line] = number
            chunks.append(chunk)
            next_line += chunk.count("\n")
        items, issues = parsers.parse_csv("".join(chunks).encode(), path)
        for item in items:
            item["line"] = original_lines[item["line"]]
            item["format"] = "xlsx"
        for issue in issues:
            issue["line"] = original_lines.get(issue["line"], issue["line"])
        return items, issues

    def payments(self, path, options):
        state = json.loads((self.ws.root / "state/payments.json").read_text()) if (self.ws.root / "state/payments.json").exists() else {"transactions": {}}
        password = os.environ.get(options.get("password_env", "PERSONAL_AGENT_BILL_PASSWORD"))
        items, issues = self.payment_xlsx(path) if path.suffix.lower() == ".xlsx" else parsers.read_bill(path, password.encode() if password else None)
        origin = self.archive("payments", path)
        for item in items:
            if selected(item["transaction_id"], item["time"], options.get("scope", {}), self.tz):
                item["source"] = origin
                state["transactions"][f"{item['platform']}:{item['transaction_id']}"] = self.clean("payments", item)
        write(self.ws.root / "state/payments.json", state)
        return issues

    def render_payments(self, options):
        state = json.loads((self.ws.root / "state/payments.json").read_text()) if (self.ws.root / "state/payments.json").exists() else {"transactions": {}}
        groups = {}
        for key, item in state["transactions"].items():
            if selected(item["transaction_id"], item["time"], options.get("scope", {}), self.tz):
                groups.setdefault(f"{item['platform']}:{item['time'][:7].replace('/', '-')}", []).append(item)
        visible = set()
        for ident, items in groups.items():
            items.sort(key=lambda r: (r["time"], r["transaction_id"]))
            original = self.original("payments", ident, items)
            lines = [f"# 消费记录：{ident}", "", "> 保留收支、类型与状态；转账、退款和失败记录分别理解。消费动机依据本人表达。", "", f"[完整字段](<originals/{safe_id(ident)}.json>)", ""]
            for item in items:
                lines += [f"## {item['time']} · {item['transaction_id']}", "", f"金额：{item['amount']} CNY；{item['direction']}；类型：{item['type']}；状态：{item['status']}。", f"对方：{item['counterparty']}；说明：{item['description']}。", f"来源：{item['source']}，成员 {item.get('member') or ''}，第 {item['line']} 行。", ""]
            self.ws.put("payments", ident, ident, "\n".join(lines), items[-1]["time"], "本人提供的账单", transactions=len(items), readable_originals=[original], original_changed=self.original_changes.get(original, False))
            visible.add(f"payments:{ident}")
        catalog = self.ws.catalog()
        catalog["records"] = {k: v for k, v in catalog["records"].items() if v["provider"] != "payments" or k in visible}
        self.ws.save_catalog(catalog)

    def document(self, provider, path, options):
        ident = identity(path)
        if not selected(ident, None, options.get("scope", {}), self.tz):
            return
        text = path.read_text(encoding="utf-8-sig")
        scope = options.get("scope", {})
        time_keys = ("date", "time", "日期", "时间", "记录时间", "测量时间", "交易时间", "create_time")
        if path.suffix.lower() == ".csv":
            data = list(csv.DictReader(io.StringIO(text)))
        elif path.suffix.lower() == ".json":
            data = self.clean(provider, json.loads(text))
        else:
            text = self.clean(provider, text)
            data = {"text": text}
        if scope.get("from") or scope.get("until"):
            if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
                raise NeedsFormat("此文件尚未识别逐条记录的日期；请选择文件范围，或让 Agent 按适配样本增加日期解析。")
            key = next((k for k in time_keys if data and k in data[0]), None)
            if key is None or any(not r.get(key) for r in data):
                raise NeedsFormat("记录缺少可识别的日期字段；请按实际字段增加日期解析，或改为选择文件范围。")
            try:
                data = [r for r in data if selected(ident, r[key], scope, self.tz)]
            except ValueError:
                raise NeedsFormat("日期格式需要按实际来源适配，当前只提供字段结构供 Agent 检查。") from None
        if path.suffix.lower() in (".json", ".csv"):
            text = json.dumps(self.clean(provider, data), ensure_ascii=False, indent=2)
            data = self.clean(provider, data)
        original = self.original(provider, ident, data)
        body = f"# {path.stem}\n\n> 本人选择提供的文件；发生时间与作者依据正文，文件修改时间不代表事件时间。\n\n[阅读副本](<originals/{ident}.json>)\n\n{text}\n"
        self.ws.put(provider, ident, path.stem, body, "", self.archive(provider, path), format="text", readable_originals=[original], original_changed=self.original_changes.get(original, False))

    def adaptation_sample(self, provider, path, options):
        relative = f"sources/{provider}/format-samples/{identity(path)}.json"
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        elif path.suffix.lower() == ".csv":
            data = list(csv.DictReader(io.StringIO(text)))
        else:
            data = {"text": text}
        scope = options.get("scope", {})
        if scope.get("from") or scope.get("until"):
            def shape(value):
                if isinstance(value, dict):
                    return {k: shape(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [shape(value[0])] if value else []
                return type(value).__name__
            data = {"kind": "format_structure_only", "reason": "所选时间范围尚未能解析，样本不提供原始内容值。", "structure": shape(data)}
        else:
            data = {"kind": "selected_format_reading_copy", "data": self.clean(provider, data)}
        write(self.ws.context / relative, data)
        return str(self.ws.context / relative)

    def discover(self, provider):
        options = self.config["sources"][provider]
        if not options["enabled"]:
            return {"items": [], "next_action": "先选择启用此来源，再读取会话或文件列表。"}
        if provider == "wechat" and options.get("mode") == "cli":
            cli = Path(options.get("cli") or "~/.local/bin/wechat-use").expanduser()
            if not cli.exists():
                return {"items": [], "next_action": "先安装和登录微信读取工具，或选择文件导入。"}
            rows = parsers.query_all(cli, ["sessions"])["rows"]
            return {"items": [{"id": r["username"], "title": self.clean("wechat", r.get("display_name") or r.get("nickname") or r["username"])} for r in rows if r["username"] not in ("brandservicesessionholder", "brandsessionholder")]}
        paths = options.get("paths") or [str(self.ws.root / "inbox" / provider)]
        if provider == "codex" and options.get("mode") == "local" and not options.get("paths"):
            paths = local_codex_paths()
        extensions = {"codex": {".jsonl"}, "chatgpt": {".json", ".zip"}, "wechat": {".json", ".md", ".txt"}}.get(provider, {".json", ".md", ".txt", ".csv", ".xlsx", ".zip"})
        result = {}
        for path in files(paths, extensions):
            if provider == "codex":
                meta = parsers.codex_header(path)
                if meta["kind"] == "conversation":
                    result[meta["id"]] = {"id": meta["id"], "title": path.stem, "file": str(path)}
            elif provider == "chatgpt":
                for _, payload in parsers.chatgpt_payloads(path):
                    if isinstance(payload, dict) and payload.get("provider") == "codex_app.read_thread":
                        values = [payload["pages"][0]["thread"]]
                    else:
                        values = payload if isinstance(payload, list) else payload.get("conversations", [payload])
                    for value in values:
                        ident = value.get("id") or value.get("conversation_id")
                        if ident:
                            result[str(ident)] = {"id": str(ident), "title": value.get("title") or str(ident), "file": str(path)}
            elif provider == "wechat" and path.suffix == ".json":
                value = json.loads(path.read_text())
                if isinstance(value, dict) and value.get("provider") == "wechat-use.history":
                    rows = value["result"]["rows"]
                    title = rows[-1].get("chat_display_name") if rows else None
                    result[value["chat_id"]] = {"id": value["chat_id"], "title": self.clean("wechat", title or value["chat_id"]), "file": str(path)}
            else:
                ident = identity(path)
                result[ident] = {"id": ident, "title": path.stem, "file": str(path)}
        return {"items": list(result.values()), "next_action": "按标题选择后用 --include/--exclude 保存对应 ID；文件来源也可直接选择路径。"}

    def collect(self, only=None, force=False):
        from .privacy import change_selection
        results = {}
        for provider in PROVIDERS:
            options = self.config["sources"][provider]
            if not options["enabled"] or not options.get("use_allowed", True) or (only and provider != only):
                continue
            before = self.ws.catalog()["sources"].get(provider, {})
            if before:
                previous = before.get("selection", {**options, "scope": before.get("scope", {})})
                change_selection(self.ws, provider, previous, options)
                self.config = self.ws.config
                options = self.config["sources"][provider]
                before = self.ws.catalog()["sources"].get(provider, {})
            result = {"status": "collected", "files_read": 0, "unchanged_files": 0, "errors": [], "issues": [], "scope": options.get("scope", {}), "selection": deepcopy(options), "file_signatures": before.get("file_signatures", {})}
            choices = options.get("paths") or [str(self.ws.root / "inbox" / provider)]
            if provider == 'chatgpt' and options.get('mode') == 'app':
                (self.ws.root / 'inbox/chatgpt/app').mkdir(parents=True, exist_ok=True)
                choices = list(dict.fromkeys([*choices, str(self.ws.root / 'inbox/chatgpt/app')]))
            extensions = {"codex": {".jsonl"}, "chatgpt": {".json", ".zip"}, "wechat": {".json", ".md", ".txt"}, "payments": {".csv", ".zip", ".xlsx"}}.get(provider, {".md", ".txt", ".json", ".csv"})
            if provider == "codex" and options.get("mode") == "local" and not options.get("paths"):
                choices = local_codex_paths()
            try:
                if provider == "wechat" and options.get("mode") == "cli":
                    result.update(self.live_wechat(options))
                for path in files(choices, extensions):
                    signature = [path.stat().st_size, path.stat().st_mtime_ns, options.get("scope", {}), self.config["privacy"]]
                    if not force and result["file_signatures"].get(str(path)) == signature:
                        result["unchanged_files"] += 1
                        continue
                    try:
                        if provider == "codex":
                            self.codex(path, options)
                        elif provider == "chatgpt":
                            self.chatgpt(path, options)
                        elif provider == "wechat" and path.suffix.lower() == ".json":
                            self.wechat(path, options)
                        elif provider == "payments":
                            result["issues"] += self.payments(path, options) or []
                        else:
                            self.document(provider, path, options)
                        result["files_read"] += 1
                        result["file_signatures"][str(path)] = signature
                    except (OSError, ValueError, KeyError, TypeError, StopIteration, RuntimeError) as error:
                        item = {"file": str(path), "error_type": type(error).__name__, "next_action": str(error) if isinstance(error, NeedsFormat) else "按实际格式或权限处理此文件，其他资料继续接入。"}
                        if isinstance(error, NeedsFormat):
                            item["adaptation_sample"] = self.adaptation_sample(provider, path, options)
                        result["errors"].append(item)
                if provider == "wechat":
                    self.render_wechat(options)
                elif provider == "payments":
                    self.render_payments(options)
            except (OSError, ValueError, KeyError, RuntimeError) as error:
                result["errors"].append({"error_type": type(error).__name__, "next_action": "检查选定路径或当前读取工具的实际状态。"})
            if result["errors"]:
                result["status"] = "with_errors"
            elif not result["files_read"] and not result["unchanged_files"] and options.get("mode") != "cli":
                result.update(status="needs_input", next_action=f"向 {self.ws.root / 'inbox' / provider} 投放支持的文件，或选择已有资料路径。", supported_formats=sorted(extensions))
                if provider == 'chatgpt' and options.get('mode') == 'app':
                    result.update(status='awaiting_app_capture', next_action='当前 Agent 执行 chatgpt 获取真实 App 请求，发现会话、分页读取并保存返回，再 collect；无需本人手工导出已有工具可读取的会话。')
            result["at"] = self.ws.now()
            state = self.ws.catalog()
            state["sources"][provider] = result
            self.ws.save_catalog(state)
            results[provider] = {k: v for k, v in result.items() if k not in ("file_signatures", "selection")}
        queue = self.ws.refresh()
        write(self.ws.root / "state/redaction.json", {"at": self.ws.now(), "replacement_positions": sum(self.redactions.values()), "rules": self.redactions})
        return {"sources": results, "redaction_positions": sum(self.redactions.values()), "digestion": queue["counts"], "dot_context": self.ws.refresh_published()}
