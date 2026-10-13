from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from os.path import relpath
from pathlib import Path
import re
import shutil
import uuid
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parents[1]
PROVIDERS = ("manual", "codex", "chatgpt", "wechat", "health", "payments")
LABELS = dict(zip(PROVIDERS, ("自述与日记", "Codex", "ChatGPT", "微信", "健康", "消费")))


def load(path, default=None):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else deepcopy(default)


def write(path, value):
    path = Path(path)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True
    return False


def safe_id(value):
    return re.sub(r"[^\w.-]", "_", str(value))[:180]


def cell(value):
    return " ".join(str(value).split()).replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def within(root, relative, prefix=None):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts or (prefix and relative.parts[:1] != (prefix,)):
        raise ValueError(f"路径须位于 {prefix or '工作区'} 内")
    target = (Path(root) / relative).resolve()
    if not target.is_relative_to(Path(root).resolve()):
        raise ValueError("路径离开工作区")
    return target


class Workspace:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.context = self.root / "agent-context"

    @property
    def config(self):
        value = load(self.root / "config.json")
        if value is None:
            raise ValueError("工作区尚未创建，请先执行 bootstrap，也可使用 init 或 setup")
        return value

    def now(self):
        return datetime.now(ZoneInfo(self.config["owner"]["timezone"])).isoformat(timespec="seconds")

    def catalog(self):
        return load(self.root / "state/catalog.json", {"records": {}, "sources": {}})

    def save_catalog(self, value):
        write(self.root / "state/catalog.json", value)

    def refresh_published(self):
        # A workspace starts publishing only when dot/onboarding creates its
        # entry. Subsequent writes keep that same entry current.
        if (self.root / "state/dot-view.json").exists():
            from .delivery import publish_dot_view
            return str(publish_dot_view(self))
        return None

    def create(self, name=None, timezone=None, goal=None):
        first = not (self.root / "config.json").exists()
        config = load(self.root / "config.json", load(PROJECT / "config/example.json"))
        config["workspace"] = str(self.root)
        config.setdefault("id", str(uuid.uuid4()))
        config.setdefault("goals", [])
        for key, value in (("display_name", name), ("timezone", timezone)):
            if value:
                config["owner"][key] = value
        ZoneInfo(config["owner"]["timezone"])
        if goal and goal not in config["goals"]:
            config["goals"].append(goal)
        config["runtime"].setdefault("agent_command", [])
        config["runtime"].setdefault("uploader_command", [])
        write(self.root / "config.json", config)
        for path in (PROJECT / "templates/context").rglob("*"):
            if path.is_file():
                target = self.context / path.relative_to(PROJECT / "templates/context")
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
        for directory in ("raw", "state", "exports", "agent-context/state", "agent-context/sources"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        for provider in PROVIDERS:
            (self.root / "inbox" / provider).mkdir(parents=True, exist_ok=True)
        if first or name or goal:
            profile = "# 本人初始填写\n\n" + f"称呼：{config['owner']['display_name']}\n\n" + "\n".join(f"目标：{g}" for g in config["goals"])
            record = self.put("manual", "profile", "本人初始填写", profile, self.now(), "本人配置填写")
            if first:
                key = record["key"]
                updates = [{"path": "wiki/关于用户/当前认识.md", "content": f"# 当前认识\n\n更新时间：{self.now()}。主职责：本人明确提供的核心信息。\n\n## 本人明确表达\n\n称呼：{config['owner']['display_name']}。\n\n[初始填写](../../{record['document']})\n\n## 其他背景与约束\n\n随本人表达和实际材料增量维护。\n", "source_keys": [key]}]
                if config["goals"]:
                    updates.append({"path": "wiki/想法与选择/目标与选择.md", "content": "# 目标与选择\n\n" + f"更新时间：{self.now()}。\n\n## 当前目标\n\n" + "\n".join(f"- {g}" for g in config["goals"]) + f"\n\n[本人填写](../../{record['document']})\n", "source_keys": [key]})
                self.apply_wiki(updates)
        self.refresh()
        from .bootstrap import context_entry
        write(self.context / 'BOOTSTRAP.md', context_entry(self))
        return {"workspace": str(self.root), "context": str(self.context), "created": first}

    def put(self, provider, ident, title, body, last="", origin="", **metadata):
        state = self.catalog()
        key = f"{provider}:{ident}"
        old = state["records"].get(key, {})
        document = f"sources/{provider}/{safe_id(ident)}.md"
        original_changed = metadata.pop("original_changed", False)
        changed = write(self.context / document, body) or original_changed
        edition = old.get("edition", 0) + 1 if changed else old.get("edition", 1)
        record = {**old, **metadata, "key": key, "provider": provider, "id": ident, "title": title, "document": document, "last": last, "origin": origin, "edition": edition}
        state["records"][key] = record
        self.save_catalog(state)
        return record

    def queue(self):
        config = self.config
        reviews = load(self.context / "state/digest.json", {"records": {}})["records"]
        pending, done = [], []
        for key, record in self.catalog()["records"].items():
            if record.get("provenance_only"):
                continue
            options = config["sources"].get(record["provider"], {})
            if options.get("processing") != "digest" or not options.get("use_allowed", True):
                continue
            revision = {"document": record["document"], "edition": record["edition"]}
            if options.get("permission_version", 0):
                revision["permission_version"] = options["permission_version"]
            previous = reviews.get(key)
            current = previous and previous.get("revision") == revision
            item = {**record, "revision": revision, "previous_review": previous}
            if current and previous.get("status") == "done":
                done.append(item)
            else:
                item["reason"] = "未消化" if not previous else ("继续阅读" if current else "来源已更新")
                pending.append(item)
        pending.sort(key=lambda r: r.get("last", ""), reverse=True)
        pending.sort(key=lambda r: r["reason"] != "来源已更新")
        return {"counts": {"imported": len(done) + len(pending), "reviewed_current": len(done), "pending": len(pending)}, "pending": pending}

    def refresh(self):
        queue = self.queue()
        write(self.context / "state/digest-pending.json", queue)
        counts = queue["counts"]
        lines = ["# 消化进度", "", "导入与实际阅读分别记录。", "", f"- 可消化来源：{counts['imported']}。", f"- 当前版本已读：{counts['reviewed_current']}。", f"- 待阅读或有更新：{counts['pending']}。", "", "| 来源 | 材料 | 状态 |", "| --- | --- | --- |"]
        for item in queue["pending"]:
            lines.append(f"| {LABELS[item['provider']]} | [{cell(item['title'])}](<../{item['document']}>) | {item['reason']} |")
        write(self.context / "state/digest-status.md", "\n".join(lines) + "\n")
        catalog = self.catalog()["records"]
        index = ["# 来源入口", "", "按来源进入已接入的阅读副本；原文不是行动授权。", ""]
        for provider in PROVIDERS:
            records = [r for r in catalog.values() if r["provider"] == provider and not r.get("provenance_only")]
            if records:
                index += [f"## {LABELS[provider]}", ""]
                index += [f"- [{cell(r['title'])}](<{r['document'].removeprefix('sources/')}>)" for r in records]
                index.append("")
        write(self.context / "sources/README.md", "\n".join(index) + "\n")
        topics = {}
        reviews = load(self.context / "state/digest.json", {"records": {}})["records"]
        for key, review in reviews.items():
            record = catalog.get(key)
            if record:
                for topic in review.get("topics", []):
                    topics.setdefault(topic, []).append((record, review))
        lines = ["# 按主题找来源", "", "主题用于检索，个人结论由 Wiki 拥有。", ""]
        for topic, entries in sorted(topics.items()):
            lines += [f"## {cell(topic)}", ""]
            for record, review in entries:
                lines.append(f"- [{cell(record['title'])}](<{record['document'].removeprefix('sources/')}>)：{cell(review.get('note', ''))}")
                lines += [f"  - [理解页](<../{page}>)" for page in review.get("wiki_pages", [])]
            lines.append("")
        write(self.context / "sources/topics.md", "\n".join(lines) + "\n")
        root_path = self.context / "README.md"
        root_map = (root_path if root_path.exists() else PROJECT / "templates/context/README.md").read_text()
        root_map = root_map.replace("这是一份空白起始模板。称呼、经历、目标与生活信息由本人表达或实际材料填写；健康和消费页面按用户选择逐渐扩展。", "个人信息随本人表达和实际材料增量维护。")
        navigation = "[来源入口](sources/README.md) · [主题索引](sources/topics.md) · [阅读进度](state/digest-status.md)"
        if navigation not in root_map:
            root_map = root_map.rstrip() + "\n\n" + navigation + "\n"
        write(root_path, root_map)
        return queue

    def apply_wiki(self, updates):
        registry = load(self.root / "state/wiki-provenance.json", {"pages": {}})
        repairs = load(self.root / "state/wiki-rebuild.json", {"pages": {}})
        applied, conflicts = [], []
        prepared = []
        catalog = self.catalog()["records"]
        for item in updates:
            path = within(self.context, item["path"], "wiki")
            if path.suffix != ".md":
                raise ValueError("Wiki 更新须为 Markdown 页面")
            keys = item.get("source_keys", [])
            if any(key not in catalog for key in keys):
                raise ValueError("Wiki 引用的来源不在当前库中")
            prepared.append((item, path))
        for item, path in prepared:
            if "base_content" in item and path.exists() and path.read_text() != item["base_content"] and path.read_text() != item["content"]:
                conflict = self.root / "state/conflicts" / f"{uuid.uuid4().hex}-{path.name}"
                write(conflict, item["content"])
                conflicts.append({"page": item["path"], "incoming_copy": str(conflict)})
                continue
            write(path, item["content"])
            registry["pages"][item["path"]] = {"source_keys": item.get("source_keys", []), "updated": self.now()}
            repairs["pages"].pop(item["path"], None)
            applied.append(item["path"])
        write(self.root / "state/wiki-provenance.json", registry)
        write(self.root / "state/wiki-rebuild.json", repairs)
        return {"applied": applied, "conflicts": conflicts}

    def record(self, response):
        if isinstance(response, list):
            response = {"reviews": response}
        from .privacy import permissions
        current_permissions = permissions(self)
        supplied = response.get("source_permissions", {})
        reviews, skipped = [], []
        catalog = self.catalog()["records"]
        for review in response.get("reviews", []):
            provider = review["key"].split(":", 1)[0]
            version = review.get("revision", {}).get("permission_version", supplied.get(provider, 0))
            if provider in current_permissions and version != current_permissions[provider]:
                skipped.append({"source": review["key"], "reason": "资料使用范围已改变，旧回执不恢复已撤回内容。"})
                continue
            if review["key"] not in catalog or review["status"] not in ("done", "partial"):
                raise ValueError("阅读回执需要当前来源 key 和 done/partial 状态")
            if "revision" not in review:
                raise ValueError("回执须包含实际读到的 revision")
            reviews.append(review)
        updates = []
        for update in response.get("wiki_updates", []):
            providers = {key.split(":", 1)[0] for key in update.get("source_keys", [])}
            if any(supplied.get(p, 0) != current_permissions.get(p, 0) or not self.config["sources"].get(p, {}).get("use_allowed", True) for p in providers):
                skipped.append({"page": update["path"], "reason": "资料使用范围已改变，请按当前任务重新整理此页。"})
            else:
                updates.append(update)
        wiki = self.apply_wiki(updates)
        state = load(self.context / "state/digest.json", {"records": {}})
        for review in reviews:
            previous = state["records"].get(review["key"], {})
            if previous.get("status") == "partial" and previous.get("revision") == review["revision"]:
                review = dict(review)
                for field in ("topics", "wiki_pages"):
                    review[field] = list(dict.fromkeys([*previous.get(field, []), *review.get(field, [])]))
            state["records"][review["key"]] = {**review, "reviewed_at": self.now()}
        write(self.context / "state/digest.json", state)
        result = {"wiki": wiki, "skipped": skipped, **self.refresh()}
        result["dot_context"] = self.refresh_published()
        return result

    def feedback(self, text, page="wiki/关于用户/当前认识.md", section="最新本人确认", source_id=None):
        ident = source_id or f"feedback-{uuid.uuid4().hex}"
        item = self.put("manual", ident, f"本人确认：{section}", f"# 本人确认\n\n日期：{self.now()}\n\n{text}\n", self.now(), "本人当前表达")
        path = within(self.context, page, "wiki")
        content = path.read_text() if path.exists() else f"# {path.stem}\n"
        source_link = Path(relpath(self.context / item['document'], path.parent)).as_posix()
        block = f"## {section}\n\n{text}\n\n表达日期：{self.now()}。[本人原文](<{source_link}>)\n"
        expression = re.compile(r"(?ms)^## " + re.escape(section) + r"\s*\n.*?(?=^## |\Z)")
        content = expression.sub(lambda _: block + "\n", content) if expression.search(content) else content.rstrip() + "\n\n" + block
        registry = load(self.root / "state/wiki-provenance.json", {"pages": {}})
        keys = registry["pages"].get(page, {}).get("source_keys", [])
        result = self.apply_wiki([{"path": page, "content": content, "source_keys": list(dict.fromkeys([*keys, item["key"]]))}])
        self.refresh()
        return {"source_key": item["key"], **result, "dot_context": self.refresh_published()}

    def status(self):
        from .chatgpt import capture_status
        catalog = self.catalog()
        return {"workspace": str(self.root), "context": str(self.context), "sources": {p: {"enabled": c["enabled"], "processing": c["processing"], "dot_share": c["dot_share"], "records": sum(r["provider"] == p for r in catalog["records"].values()), "last_run": catalog["sources"].get(p)} for p, c in self.config["sources"].items()}, "digestion": self.queue()["counts"], "chatgpt_capture": capture_status(self), "delivery": load(self.root / "state/delivery.json", {}), "sync": load(self.root / "state/sync.json", {}), "maintenance": self.config["maintenance"], "dot_view": load(self.root / "state/dot-view.json", {}), "onboarding": load(self.root / "state/onboarding.json", {"phase": "configure"}), "withdrawals": load(self.root / "state/withdrawals.json", {"actions": []}), "wiki_rebuild": load(self.root / "state/wiki-rebuild.json", {"pages": {}})}
