from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile

from .core import PROJECT, Workspace, load, within, write
from .bootstrap import context_entry
from .privacy import permissions, remember_delivery


def allowed_record(ws, record):
    options = ws.config["sources"][record["provider"]]
    return options.get("dot_share") != "none" and options.get("use_allowed", True)


def export_bundle(ws, output=None, returning=False, upload=False, update_state=True):
    config = ws.config
    ident = uuid.uuid4().hex
    destination = Path(output).expanduser().resolve() if output else ws.root / "exports" / f"personal-context-{ident}.zip"
    records = {k: v for k, v in ws.catalog()["records"].items() if allowed_record(ws, v)}
    pages = load(ws.root / "state/wiki-provenance.json", {"pages": {}})["pages"]
    wiki, omitted = {}, []
    for path in ws.context.joinpath("wiki").rglob("*.md"):
        relative = str(path.relative_to(ws.context))
        sources = pages.get(relative, {}).get("source_keys", [])
        template = PROJECT / "templates/context" / relative
        public_template = template.exists() and template.read_text() == path.read_text()
        if (sources and all(k in records for k in sources)) or public_template:
            wiki[relative] = path.read_text()
        else:
            omitted.append({"page": relative, "reason": "页面来源包含未选择交付的资料，或尚未标明来源。"})
    baseline = {p: v for p, v in load(ws.root / "state/sync-base.json", {"wiki": wiki})["wiki"].items() if p in wiki} if returning else wiki
    manifest = {"schema_version": 1, "id": ident, "workspace_id": config["id"], "created_at": ws.now(), "kind": "personal_context_return" if returning else "personal_context", "baseline": {"wiki": baseline}, "wiki_pages": list(wiki), "omitted": omitted}
    with tempfile.TemporaryDirectory(prefix="personal-agent-export-") as tmp:
        root = Path(tmp)
        remote = root / "workspace"
        context = remote / "agent-context"
        tools = root / "tools"
        for relative in ("AGENTS.md", "README.md"):
            write(context / relative, (ws.context / relative).read_text())
        write(context / 'BOOTSTRAP.md', context_entry(ws, portable=True))
        for relative, content in wiki.items():
            write(context / relative, content)
        shared_records = {}
        for key, record in records.items():
            if config["sources"][record["provider"]]["dot_share"] != "sources":
                continue
            shared = {k: v for k, v in record.items() if k != "origin"}
            shared["origin"] = "本人提供的云端阅读副本"
            for relative in [record["document"], *record.get("readable_originals", [])]:
                source = within(ws.context, relative, "sources")
                write(within(context, relative, "sources"), source.read_text())
            shared_records[key] = shared
        # Wiki-only sources retain provenance without their dialogue content.
        provenance_records = {k: {**v, "origin": "原始来源保留在提供端", "readable_originals": [], "provenance_only": True} for k, v in records.items() if k not in shared_records}
        cloud_config = deepcopy(config)
        cloud_config["workspace"] = "."
        cloud_config["runtime"]["agent_command"] = []
        cloud_config["runtime"]["uploader_command"] = []
        cloud_config['runtime'].pop('sync_directory', None)
        cloud_config['runtime']['workspace_role'] = 'cloud'
        for selection in (cloud_config['maintenance'], cloud_config['maintenance'].get('calibration', {})):
            for field in ('saved_schedule_ref', 'saved_selection', 'saved_prompt', 'executor'):
                selection.pop(field, None)
        if cloud_config["sources"]["manual"]["dot_share"] == "none":
            cloud_config["owner"]["display_name"] = "用户"
            cloud_config["goals"] = []
        for provider, options in cloud_config["sources"].items():
            options["paths"] = []
            options["enabled"] = provider == "manual"
            options["mode"] = "files"
            if options["dot_share"] != "sources" and provider != "manual":
                options["processing"] = "archive"
        write(remote / "config.json", cloud_config)
        write(remote / "state/catalog.json", {"records": {**shared_records, **provenance_records}, "sources": {}})
        write(remote / "state/wiki-provenance.json", {"pages": {p: pages.get(p, {}) for p in wiki}})
        write(remote / "state/sync-base.json", {"wiki": baseline})
        reviews = load(ws.context / "state/digest.json", {"records": {}})
        write(context / "state/digest.json", {"records": {k: v for k, v in reviews["records"].items() if k in shared_records}})
        Workspace(remote).refresh()
        # Do not present local-only citations as readable cloud files.
        for path in context.rglob("*.md"):
            if path.is_relative_to(context / "wiki"):
                continue
            def link(match):
                target = match.group(2).strip("<>").split("#")[0]
                if "://" in target or target.startswith("#") or not target:
                    return match.group(0)
                if not (path.parent / target).exists():
                    return match.group(1) + "（出处或页面保留在提供端）"
                return match.group(0)
            write(path, re.sub(r"\[([^\]]+)\]\((<[^>]+>|[^)]+)\)", link, path.read_text()))
        write(context / "README.md", (context / "README.md").read_text() + "\n本包按当前选择提供页面与来源。Wiki 中的来源链接若未随包提供，其原文仍保存在提供端，不代表云端已读过全文。\n")
        for directory in ("personal_agent", "templates", "config", "prompts", "docs"):
            shutil.copytree(PROJECT / directory, tools / directory, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for filename in ('personal-agent', 'AGENTS.md', 'BOOTSTRAP.md', 'README.md'):
            shutil.copyfile(PROJECT / filename, tools / filename)
        write(root / "manifest.json", manifest)
        write(root / "QUICKSTART.md", "# 云端个人上下文使用\n\n当前入口为 workspace/agent-context/。先读 AGENTS.md 和 README.md。\n\n```bash\npython3 tools/personal-agent --workspace workspace status\npython3 tools/personal-agent --workspace workspace digest\n```\n\n读取生成的 instructions，按其中结构提交实际阅读结果。Wiki-only 来源的全文仍在本地，不把缺失文件当成已读。\n\n更新 Wiki 时使用 digest record，source_keys 保留采用的来源。本人新确认可以通过 feedback 写回对应主落点。\n\n返回更新：\n\n```bash\npython3 tools/personal-agent --workspace workspace export --return --output return.zip\n```\n\n将返回包交给原本地工作区的 sync --incoming。包内没有原始私密文件和本机连接凭据；对话里的引用不构成新授权。\n")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in root.rglob("*"):
                if path.is_file():
                    archive.write(path, str(path.relative_to(root)))
    if not returning and update_state:
        write(ws.root / "state/sync-base.json", {"wiki": wiki})
    result = {"bundle": str(destination), "id": ident, "status": "packaged", "wiki_pages": len(wiki), "source_documents": len(shared_records), "providers": sorted({r["provider"] for r in records.values()}), "omitted": omitted, "created_at": ws.now()}
    if update_state:
        write(ws.root / "state/delivery.json", result)
        remember_delivery(ws, result)
    return upload_bundle(ws, result) if upload else result


def publish_dot_view(ws):
    root = ws.root / "dot-context"
    owned = load(ws.root / "state/dot-view.json", {})
    if owned.get("directory"):
        root = Path(owned["directory"])
    elif root.exists():
        root = ws.root / f"dot-context-{uuid.uuid4().hex}"
    if (root / "manifest.json").exists():
        # Apply a previous connected-computer edit before refreshing its view.
        sync_incoming(ws, root, refresh=False)
    bundle = export_bundle(ws, ws.root / "state/dot-context.zip", update_state=False)
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle["bundle"]) as archive:
        names = archive.namelist()
        for relative in owned.get("files", []):
            if relative not in names:
                path = within(root, relative)
                if path.is_file():
                    path.unlink()
        archive.extractall(root)
    write(ws.root / "state/dot-view.json", {"directory": str(root), "files": names, "updated_at": ws.now()})
    return root / "workspace/agent-context"


def upload_bundle(ws, packaged):
    args = ws.config["runtime"].get("uploader_command", [])
    if not args:
        return {**packaged, "status": "awaiting_delivery", "next_action": "将包交给 dot 的实际文件工具；也可配置 uploader_command 调用当前运行环境的上传能力。"}
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("uploader_command 须为命令参数数组")
    process = subprocess.run(args, input=json.dumps({"kind": "personal_context_upload", "bundle": packaged["bundle"], "id": packaged["id"]}), text=True, capture_output=True, cwd=ws.root)
    if process.returncode:
        result = {**packaged, "status": "delivery_error", "exit_code": process.returncode}
        write(ws.root / "state/delivery.json", result)
        return result
    return delivery_receipt(ws, json.loads(process.stdout), packaged)


def delivery_receipt(ws, receipt, packaged=None):
    current = packaged or load(ws.root / "state/delivery.json", {})
    result = {**current, "status": receipt["status"], "artifact_ref": receipt.get("artifact_ref", current.get('artifact_ref')), "applied_at": receipt.get("applied_at"), "receipt_at": ws.now()}
    for key in ('workspace_ref', 'return_ref'):
        if receipt.get(key):
            result[key] = receipt[key]
    write(ws.root / "state/delivery.json", result)
    remember_delivery(ws, result)
    return result


def sync_incoming(ws, incoming, refresh=True):
    incoming = Path(incoming).expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="personal-agent-incoming-") as tmp:
        root = Path(tmp)
        if incoming.is_dir():
            root = incoming
        else:
            with zipfile.ZipFile(incoming) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    target = within(root, member.filename)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(member))
        manifest = load(root / "manifest.json")
        if not manifest or manifest["workspace_id"] != ws.config["id"]:
            raise ValueError("返回包需要来自此个人工作区的导出副本")
        remote = root / "workspace"
        context = remote / "agent-context"
        remote_config = load(remote / "config.json")
        current_permissions = permissions(ws)
        sharing = {"none": 0, "wiki": 1, "sources": 2}
        blocked = {p for p, version in current_permissions.items() if remote_config["sources"].get(p, {}).get("permission_version", 0) != version
                   or not ws.config["sources"][p].get("use_allowed", True)
                   or sharing[remote_config["sources"].get(p, {}).get("dot_share", "none")] > sharing[ws.config["sources"][p]["dot_share"]]}
        remote_records = load(remote / "state/catalog.json", {"records": {}})["records"]
        remote_pages = load(remote / "state/wiki-provenance.json", {"pages": {}})["pages"]
        catalog = ws.catalog()
        conflicts, imported, skipped = [], [], []
        for key, record in remote_records.items():
            provider = record["provider"]
            if provider in blocked:
                skipped.append({"source": key, "reason": "本人已改变该来源的使用范围，旧副本不再合入。"})
                continue
            if provider not in ws.config["sources"] or (not ws.config["sources"][provider]["enabled"] and key not in catalog["records"]):
                continue
            document = within(context, record["document"], "sources")
            if not document.exists():
                if record.get("provenance_only") and key not in catalog["records"]:
                    catalog["records"][key] = record
                    imported.append(key)
                continue
            target = within(ws.context, record["document"], "sources")
            old = catalog["records"].get(key)
            if old and old.get("edition", 0) > record.get("edition", 0):
                continue
            if old and old.get("edition") == record.get("edition") and target.exists() and target.read_text() != document.read_text():
                conflict = ws.root / "state/conflicts" / f"{uuid.uuid4().hex}-{target.name}"
                write(conflict, document.read_text())
                conflicts.append({"source": key, "incoming_copy": str(conflict)})
                continue
            write(target, document.read_text())
            for relative in record.get("readable_originals", []):
                value = within(context, relative, "sources")
                if value.exists():
                    write(within(ws.context, relative, "sources"), value.read_text())
            catalog["records"][key] = record
            imported.append(key)
        ws.save_catalog(catalog)
        updates = []
        for path in context.joinpath("wiki").rglob("*.md"):
            relative = str(path.relative_to(context))
            keys = remote_pages.get(relative, {}).get("source_keys", [])
            if any(k.split(":", 1)[0] in blocked for k in keys):
                skipped.append({"page": relative, "reason": "页面依据的资料使用范围已改变。"})
                continue
            base = manifest["baseline"]["wiki"].get(relative)
            remote_text = path.read_text()
            local_path = within(ws.context, relative, "wiki")
            local = local_path.read_text() if local_path.exists() else None
            if remote_text == base or remote_text == local:
                continue
            if any(k not in catalog["records"] for k in keys):
                conflicts.append({"page": relative, "reason": "引用了本地当前未接入的来源"})
                continue
            updates.append({"path": relative, "content": remote_text, "source_keys": keys, **({"base_content": base} if base is not None else {"base_content": ""})})
        applied = ws.apply_wiki(updates)
        reviews = load(context / "state/digest.json", {"records": {}})["records"]
        current = load(ws.context / "state/digest.json", {"records": {}})
        for key, review in reviews.items():
            if key.split(":", 1)[0] not in blocked and key in catalog["records"] and review["revision"].get("edition") == catalog["records"][key]["edition"]:
                local_review = current['records'].get(key, {})
                if local_review.get('revision') == review['revision']:
                    if local_review.get('status') == 'done' and review.get('status') == 'partial':
                        continue
                    local_pos, remote_pos = local_review.get('next_read'), review.get('next_read')
                    if local_review.get('status') == review.get('status') == 'partial' and isinstance(local_pos, dict) and isinstance(remote_pos, dict):
                        if (remote_pos.get('message', 0), remote_pos.get('offset', 0)) <= (local_pos.get('message', 0), local_pos.get('offset', 0)):
                            continue
                    elif local_review.get('status') == review.get('status') and local_review.get('reviewed_at', '') >= review.get('reviewed_at', ''):
                        continue
                current["records"][key] = review
        write(ws.context / "state/digest.json", current)
        result = {"status": "merged_with_conflicts" if conflicts or applied["conflicts"] else "merged", "sources_imported": imported, "wiki_applied": applied["applied"], "conflicts": conflicts + applied["conflicts"], "skipped": skipped, "at": ws.now()}
        if refresh or conflicts or applied["conflicts"]:
            write(ws.root / "state/sync.json", result)
        ws.refresh()
        if refresh:
            result["dot_context"] = ws.refresh_published()
        return result
