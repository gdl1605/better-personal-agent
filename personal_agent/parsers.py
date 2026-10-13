"""Parsers migrated from the original personal context library; no account data."""
from __future__ import annotations
import csv, io, json, re, zipfile, subprocess
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
VERSION=3
REPLY_CHARS=600
TZ=ZoneInfo("Asia/Shanghai")
CONTEXT_PREFIXES=("# AGENTS.md instructions", "<environment_context>", "<user_instructions>", "<external_codex_apps_open_page>")
NS={"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def timestamp(value) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    return str(value or "")

def local_time(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return value or "时间未知"

def safe_id(value: str) -> str:
    return re.sub(r"[^\w.-]", "_", str(value))[:160]

def text_parts(parts: list) -> tuple[str, list[str]]:
    texts, media = [], []
    for part in parts:
        if isinstance(part, str):
            texts.append(part)
        elif isinstance(part, dict):
            if isinstance(part.get("text"), str):
                texts.append(part["text"])
            else:
                media.append(str(part.get("type") or part.get("content_type") or "非文本附件"))
    return "\n\n".join(texts).strip(), media

def signature(path: Path) -> list:
    stat = path.stat()
    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns, VERSION]

def codex_kind(meta: dict) -> str:
    source = meta.get("source")
    if meta.get("thread_source") == "guardian_review":
        return "guardian"
    if isinstance(source, dict) and "subagent" in source:
        return "guardian" if source["subagent"].get("other") == "guardian" else "subagent"
    if meta.get("thread_source") == "subagent":
        return "subagent"
    if source == "exec":
        return "exec"
    if meta.get("thread_source") == "agent_created_thread":
        return "agent_created"
    return "conversation"

def codex_header(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        event = json.loads(stream.readline())
    if event.get("type") != "session_meta":
        raise ValueError("首条记录不是 session_meta，未推测其会话身份")
    meta = event["payload"]
    source = meta.get("source")
    parent = None
    if isinstance(source, dict):
        parent = source.get("subagent", {}).get("thread_spawn", {}).get("parent_thread_id")
    thread_id = meta.get("id") or meta.get("session_id") or path.stem
    # Real local forks can retain the parent's metadata id while their rollout
    # filename has parentUUID_childUUID. Keep both files, not just the newest.
    uid = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
    variant = re.search(rf"({uid}(?:_{uid})?)$", path.stem)
    ident = variant.group(1) if variant else str(thread_id)
    if ident != str(thread_id) and not parent:
        parent = str(thread_id)
    if not parent and meta.get("session_id") != ident:
        parent = meta.get("session_id")
    return {"id": str(ident), "thread_id": str(thread_id), "kind": codex_kind(meta), "thread_source": meta.get("thread_source"),
            "parent_id": parent, "workspace": meta.get("cwd", ""), "source": str(path.resolve()),
            "created": timestamp(meta.get("timestamp")), "signature": signature(path),
            "archived": "archived_sessions" in path.parts, "present": True}

def parse_codex(path: Path) -> tuple[list[dict], dict, str]:
    messages = []
    omitted = Counter()
    last = ""
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                omitted["unparsed_json_lines"] += 1
                continue
            last = event.get("timestamp") or last
            payload = event.get("payload", {})
            if event.get("type") != "response_item" or not isinstance(payload, dict) or payload.get("type") != "message":
                continue
            role = payload.get("role")
            if role not in ("user", "assistant"):
                omitted["system_or_developer_messages"] += 1
                continue
            phase = payload.get("phase") or payload.get("channel")
            if phase in ("analysis", "commentary"):
                omitted[phase] += 1
                continue
            body, media = text_parts(payload.get("content", []))
            metadata = event.get("metadata") or {}
            explicit_reply = body.startswith("<send_user_message_question_reply>")
            # Observed on this host: real human messages can also carry
            # client_authored=False. It is transport metadata, not an author
            # identity test. Only omit explicit environment/context wrappers.
            is_context = role == "user" and not explicit_reply and body.startswith(CONTEXT_PREFIXES)
            if is_context:
                omitted["injected_or_environment_context"] += 1
                continue
            if not body and not media:
                continue
            messages.append({"role": role, "phase": phase, "text": body, "media": media,
                             "time": timestamp(event.get("timestamp")), "line": line_number,
                             "message_id": payload.get("id"), "client_authored": metadata.get("client_authored")})
    return messages, dict(omitted), timestamp(last)

def message_view(msg: dict) -> str:
    body = msg["text"] or "（无文本正文）"
    if msg["role"] != "assistant":
        return body
    body = re.sub(r"```.*?```", "（代码块见原件）", body, flags=re.S)
    if len(body) > REPLY_CHARS:
        body = body[:REPLY_CHARS].rstrip() + "\n\n（摘录到此，完整回复见原始指针。）"
    return "_助手回复摘录；机械截取，不是重新概括的结论。_\n\n" + body

def parse_chatgpt(conversation: dict) -> list[dict]:
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        raise ValueError("不支持该会话结构：缺少 mapping；保留原文件等待按实际格式适配")
    current = conversation.get("current_node")
    path, seen = [], set()
    while current in mapping and current not in seen:
        seen.add(current)
        path.append(current)
        current = mapping[current].get("parent")
    current_path = list(reversed(path))
    alternatives = [key for key in mapping if key not in seen]
    alternatives.sort(key=lambda k: str((mapping[k].get("message") or {}).get("create_time") or ""))
    messages = []
    for node_id in current_path + alternatives:
        node = mapping[node_id]
        msg = node.get("message") or {}
        role = (msg.get("author") or {}).get("role")
        if role not in ("user", "assistant") or msg.get("channel") == "analysis":
            continue
        if msg.get("recipient") not in (None, "all"):
            continue
        if (msg.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
            continue
        content = msg.get("content") or {}
        body, media = text_parts(content.get("parts", []))
        if not body and isinstance(content.get("text"), str):
            body = content["text"]
        if not body and not media:
            continue
        branch = "当前分支" if node_id in seen else ("其他分支" if path else "分支未指定")
        messages.append({"role": role, "text": body, "media": media, "node_id": str(node_id),
                         "message_id": msg.get("id"), "parent": node.get("parent"),
                         "time": timestamp(msg.get("create_time")), "branch": branch})
    return messages

def parse_chatgpt_app(snapshot: dict) -> tuple[dict, list[dict]]:
    """Read saved app-tool responses without pretending they are account exports."""
    pages = snapshot.get("pages") or []
    if not pages or pages[0].get("thread", {}).get("kind") != "chatgpt":
        raise ValueError("App 快照需要包含 ChatGPT read_thread 的 pages 原始返回")
    thread = pages[0]["thread"]
    messages, seen = [], set()
    for page_number in reversed(range(len(pages))):
        page = pages[page_number]
        turns = page.get("turns", [])
        order = range(len(turns))
        if page.get("page", {}).get("order") == "newest_first":
            order = reversed(order)
        for turn_number in order:
            turn = turns[turn_number]
            for item_number, item in enumerate(turn.get("items", [])):
                role = {"userMessage": "user", "agentMessage": "assistant"}.get(item.get("type"))
                if not role:
                    continue
                pointer = f"/pages/{page_number}/turns/{turn_number}/items/{item_number}"
                ident = str(item.get("id") or pointer)
                if ident in seen:
                    continue
                seen.add(ident)
                body, media = text_parts(item.get("content", [])) if role == "user" else (item.get("text") or "", [])
                if not body and not media:
                    continue
                messages.append({"role": role, "text": body, "media": media, "node_id": ident,
                                 "message_id": ident, "parent": None, "pointer": pointer,
                                 "time": timestamp(turn.get("startedAt")), "branch": "App 返回的对话",
                                 "truncated": bool(item.get("truncated") or item.get("textTruncated"))})
    revision = thread.get("updatedAt") or thread.get("createdAt") or 0
    record = {"id": str(thread["id"]), "title": str(thread.get("title") or thread["id"]),
              "source_kind": "app_snapshot", "pointer": "/pages", "member": None,
              "created": timestamp(thread.get("createdAt")), "last": timestamp(revision),
              "revision": revision, "captured_at": snapshot.get("captured_at"),
              "more_history_available": pages[-1].get("page", {}).get("hasMore"),
              "truncated_messages": sum(m["truncated"] for m in messages)}
    return record, messages

def chatgpt_payloads(path: Path):
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if re.fullmatch(r"conversations(?:[-_]\d+)?\.json", Path(n).name)]
            if not names:
                raise ValueError("ZIP 内没有 conversations.json 或 conversations-N.json；未猜测 HTML 格式")
            for name in sorted(names):
                with archive.open(name) as stream:
                    yield name, json.load(stream)
    else:
        yield None, json.loads(path.read_text(encoding="utf-8"))

def clean(value):
    return str(value).strip().lstrip("\ufeff`").strip()

def parse_csv(data, source, member=None):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("gb18030")
    reader = csv.reader(io.StringIO(text))
    header, platform, rows, issues = None, None, [], []
    previous_line = 0
    for values in reader:
        line = previous_line + 1
        previous_line = reader.line_num
        values = [clean(value) for value in values]
        if header is None:
            if "交易时间" in values and "交易单号" in values and "金额(元)" in values:
                platform = "wechatpay"
            elif "交易时间" in values and "交易订单号" in values and "金额" in values:
                platform = "alipay"
            else:
                continue
            header = values
            continue
        if not values or not re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}", values[0]):
            # Export introductions, blank lines and summaries aren't transactions.
            continue
        raw = dict(zip(header, values))
        ident = raw.get("交易单号" if platform == "wechatpay" else "交易订单号", "")
        if not ident:
            issues.append({"line": line, "error": "交易明细缺少来源交易单号"})
            continue
        amount = raw.get("金额(元)" if platform == "wechatpay" else "金额", "")
        try:
            number = Decimal(amount.replace("¥", "").replace("￥", "").replace(",", ""))
            if not number.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            issues.append({"line": line, "error": "交易明细金额无法读取"})
            continue
        rows.append({"platform": platform, "transaction_id": ident,
                     "time": raw["交易时间"], "amount": str(number), "currency": "CNY",
                     "direction": raw.get("收/支", ""),
                     "type": raw.get("交易类型" if platform == "wechatpay" else "交易分类", ""),
                     "counterparty": raw.get("交易对方", ""),
                     "description": raw.get("商品" if platform == "wechatpay" else "商品说明", ""),
                     "method": raw.get("支付方式" if platform == "wechatpay" else "收/付款方式", ""),
                     "status": raw.get("当前状态" if platform == "wechatpay" else "交易状态", ""),
                     "raw": raw, "source": str(source), "member": member, "line": line})
    if header is None:
        raise ValueError("未找到微信支付或支付宝个人对账 CSV 的交易表头")
    return rows, issues

def read_bill(path, password=None):
    if path.suffix.lower() == ".csv":
        rows, issues = parse_csv(path.read_bytes(), path)
        return rows, issues
    rows, issues = [], []
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not members:
            raise ValueError("ZIP 中没有个人对账 CSV；请保留原件并按实际格式接入")
        for member in members:
            items, errors = parse_csv(archive.read(member, pwd=password), path, member)
            rows.extend(items)
            issues.extend({"member": member, **error} for error in errors)
    return rows, issues

def message_key(row):
    # local_id restarts in each WeChat database shard. A migrated history can
    # return several different server messages at the same chat/local_id.
    server_id = row.get("server_id")
    if server_id is not None and str(server_id) not in ("", "0"):
        return f"server:{server_id}"
    return "local:" + json.dumps([row["local_id"], row["create_time"],
        row.get("local_type"), row.get("message_content")], ensure_ascii=False, separators=(",", ":"))

def call_cli(cli, args):
    # The installed managed runtime owns the clone and rejects target overrides,
    # including overrides naming that same clone.
    result = subprocess.run([str(cli), *args],
                            text=True, capture_output=True)
    if result.returncode:
        # CLI diagnostics can contain local identities; keep them out of task logs.
        raise RuntimeError(f"wechat-use {args[0]} 返回 {result.returncode}；请在微信工具设置中查看原因")
    return json.loads(result.stdout)

def query_all(cli, args):
    """Increase the query size until the CLI's documented LIMIT is not filled.

    This is query batching, not a processing quota or acceptance threshold.
    History has no offset cursor; timestamps alone cannot page same-second rows.
    """
    limit = 1000
    while True:
        payload = call_cli(cli, [*args, "--json", "-n", str(limit)])
        if len(payload["rows"]) < limit:
            return payload
        limit *= 2

def parse_xlsx(path):
    with zipfile.ZipFile(path) as z:
        ss=[''.join(t.text or '' for t in e.findall('.//m:t',NS)) for e in ET.fromstring(z.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in z.namelist() else []
        result=[]
        for r in ET.fromstring(z.read('xl/worksheets/sheet1.xml')).findall('.//m:row',NS):
            values={}
            for c in r.findall('m:c',NS):
                v=c.find('m:v',NS); val=v.text if v is not None else ''
                if c.attrib.get('t')=='s': val=ss[int(val)]
                elif c.attrib.get('t')=='inlineStr':val=''.join(t.text or '' for t in c.findall('.//m:t',NS))
                values[re.sub(r'\d','',c.attrib['r'])]=val
            result.append((int(r.attrib['r']),values))
        return result
