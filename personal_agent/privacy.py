"""Apply changes to the user's source permissions to existing material."""
from __future__ import annotations

from pathlib import Path
import json
import shutil
import uuid
import zipfile

from .core import PROJECT, load, within, write


def permissions(ws):
    return {p: c.get("permission_version", 0) for p, c in ws.config["sources"].items()}


def narrower(ws, provider, before, after):
    from .adapters import date
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    old, new = before.get("scope", {}), after.get("scope", {})
    includes = set(new.get("include_ids", []))
    if includes and (not old.get("include_ids") or not set(old["include_ids"]) <= includes):
        return True
    if set(new.get("exclude_ids", [])) - set(old.get("exclude_ids", [])):
        return True
    if new.get("main_conversations_only", True) and not old.get("main_conversations_only", True):
        return True
    tz = ZoneInfo(ws.config["owner"]["timezone"])
    for field in ("from", "until"):
        def boundary(scope):
            value = scope.get(field)
            moment = date(value, tz)
            return moment + timedelta(days=1) if moment and field == "until" and len(value) == 10 else moment
        a, b = boundary(old), boundary(new)
        if b and (not a or (b > a if field == "from" else b < a)):
            return True
    def paths(options):
        if options.get("paths"):
            return set(options["paths"])
        if provider == "codex" and options.get("mode") == "local":
            return {str(Path.home() / ".codex/sessions"), str(Path.home() / ".codex/archived_sessions")}
        return {str(ws.root / "inbox" / provider)}
    return not paths(before) <= paths(after)


def remember_delivery(ws, item):
    history = load(ws.root / "state/deliveries.json", {"items": {}})
    if item.get("id"):
        history["items"][item["id"]] = {**history["items"].get(item["id"], {}), **item}
        write(ws.root / "state/deliveries.json", history)


def retire_deliveries(ws, provider):
    """Remove owned local exports and identify actual remote refs for dot."""
    history = load(ws.root / "state/deliveries.json", {"items": {}})
    recent = load(ws.root / "state/delivery.json", {})
    if recent.get("id") and recent["id"] not in history["items"]:
        history["items"][recent["id"]] = recent
    # Older releases only registered the most recent export.
    paths = {str(p) for p in (ws.root / "exports").glob("*.zip")}
    paths.update(v["bundle"] for v in history["items"].values() if v.get("bundle"))
    removed, remote = [], []
    for filename in sorted(paths):
        path = Path(filename)
        if not path.is_file() or not zipfile.is_zipfile(path):
            continue
        with zipfile.ZipFile(path) as archive:
            if "manifest.json" not in archive.namelist():
                continue
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("workspace_id") != ws.config["id"]:
                continue
            records = json.loads(archive.read("workspace/state/catalog.json"))["records"]
            if not any(r["provider"] == provider for r in records.values()):
                continue
        item = history["items"].setdefault(manifest["id"], {"id": manifest["id"], "bundle": filename})
        item["providers"] = sorted({r["provider"] for r in records.values()})
        item["status"] = "withdrawn"
        path.unlink()
        removed.append(filename)
    for item in history["items"].values():
        if provider not in item.get("providers", []):
            continue
        item["status"] = "withdrawn"
        if item.get("artifact_ref") and not item.get("remote_removed_at"):
            remote.append({"artifact_ref": item["artifact_ref"], "status": "pending", "action": "删除旧交付副本；按新范围重新交付。已解包文件和据此保存的认识也需停止使用。"})
    write(ws.root / "state/deliveries.json", history)
    if recent.get("id") in history["items"]:
        write(ws.root / "state/delivery.json", history["items"][recent["id"]])
    return removed, remote


def clear_reading(ws, provider, rebuild):
    catalog = ws.catalog()
    ws_records = dict(catalog["records"])
    keys = {k for k, r in catalog["records"].items() if r["provider"] == provider and not (rebuild and provider == "manual" and not r.get("format"))}
    registry = load(ws.root / "state/wiki-provenance.json", {"pages": {}})
    repairs = load(ws.root / "state/wiki-rebuild.json", {"pages": {}})
    affected = []
    for page, entry in registry["pages"].items():
        if not keys.intersection(entry.get("source_keys", [])):
            continue
        affected.append(page)
        candidates = entry["source_keys"] if rebuild else [k for k in entry["source_keys"] if k not in keys]
        repairs["pages"][page] = {"source_keys": candidates, "reason": "本人改变了资料使用范围；依据仍获准的资料重新整理。"}
        template = PROJECT / "templates/context" / page
        write(within(ws.context, page, "wiki"), template.read_text() if template.exists() else f"# {Path(page).stem}\n\n待依据仍获准的资料重新整理。\n")
        registry["pages"][page] = {"source_keys": [], "updated": ws.now()}
    write(ws.root / "state/wiki-provenance.json", registry)
    write(ws.root / "state/wiki-rebuild.json", repairs)
    for key in keys:
        catalog["records"].pop(key)
    catalog["sources"].pop(provider, None)
    ws.save_catalog(catalog)
    # These directories are generated reading material, not user originals.
    for key in keys:
        record = ws_records[key]
        if provider == "manual" and not record.get("format"):
            document = within(ws.context, record["document"], "sources")
            if document.exists():
                write(ws.root / "raw/manual" / document.name, document.read_text())
        for relative in [record["document"], *record.get("readable_originals", [])]:
            within(ws.context, relative, "sources").unlink(missing_ok=True)
    for directory in (ws.context / "sources" / provider / "format-samples", ws.context / "state/jobs"):
        if directory.exists():
            shutil.rmtree(directory)
    for name in ({"wechat": ["wechat.sqlite3"], "payments": ["payments.json"]}.get(provider, [])):
        (ws.root / "state" / name).unlink(missing_ok=True)
    reviews = load(ws.context / "state/digest.json", {"records": {}})
    reviews["records"] = {k: v for k, v in reviews["records"].items() if k not in keys and not set(v.get("wiki_pages", [])).intersection(affected)}
    # Reread the remaining evidence of mixed pages, even if its original review
    # did not list every page it was later used in.
    for page in affected:
        for key in repairs["pages"][page]["source_keys"]:
            reviews["records"].pop(key, None)
    write(ws.context / "state/digest.json", reviews)
    baseline = load(ws.root / "state/sync-base.json", {"wiki": {}})
    for page in affected:
        baseline["wiki"].pop(page, None)
    write(ws.root / "state/sync-base.json", baseline)
    sync = load(ws.root / "state/sync.json", {})
    conflicts = []
    for conflict in sync.get("conflicts", []):
        if conflict.get("page") in affected or conflict.get("source") in keys:
            if conflict.get("incoming_copy"):
                copy = Path(conflict["incoming_copy"]).resolve()
                if copy.is_relative_to(ws.root / "state/conflicts"):
                    copy.unlink(missing_ok=True)
        else:
            conflicts.append(conflict)
    if sync:
        sync["conflicts"] = conflicts
        write(ws.root / "state/sync.json", sync)
    onboarding = load(ws.root / "state/onboarding.json", {})
    used_pages = set(onboarding.get("review", {}).get("wiki_pages", [])) | set(onboarding.get("first_use", {}).get("wiki_pages", []))
    if used_pages.intersection(affected):
        write(ws.root / "state/onboarding.json", {"phase": "review", "question": onboarding.get("question", ""), "configured_at": onboarding.get("configured_at"), "reason": "资料范围已改变，请重新展示当前认识。"})
        (ws.root / "state/onboarding-response.json").unlink(missing_ok=True)
    return {"source_keys": sorted(keys), "wiki_pages": affected}


def change_selection(ws, provider, before, after, stop_using=False, delete_raw=False):
    restricted = stop_using or narrower(ws, provider, before, after)
    shares = {"none": 0, "wiki": 1, "sources": 2}
    less_shared = shares[after["dot_share"]] < shares[before["dot_share"]]
    if not restricted and not less_shared:
        return None
    material = any(r["provider"] == provider and not (provider == "manual" and not r.get("format")) for r in ws.catalog()["records"].values())
    if not stop_using and not less_shared and not material and not before.get("permission_version"):
        return None
    config = ws.config
    if restricted:
        config["sources"][provider]["permission_version"] = before.get("permission_version", 0) + 1
    write(ws.root / "config.json", config)
    affected = clear_reading(ws, provider, rebuild=not stop_using) if restricted else {"source_keys": [], "wiki_pages": []}
    removed, remote = retire_deliveries(ws, provider)
    if delete_raw:
        for directory in (ws.root / "raw" / provider, ws.root / "inbox" / provider):
            if directory.exists():
                shutil.rmtree(directory)
    action = {"id": uuid.uuid4().hex, "provider": provider, "at": ws.now(), "kind": "stop_using" if stop_using else "scope_changed", **affected, "raw_deleted": delete_raw, "local_exports_removed": removed, "remote": remote}
    actions = load(ws.root / "state/withdrawals.json", {"actions": []})
    actions["actions"].append(action)
    write(ws.root / "state/withdrawals.json", actions)
    ws.refresh()
    return action


def withdraw(ws, provider, delete_raw=False):
    config = ws.config
    before = dict(config["sources"][provider])
    config["sources"][provider].update(enabled=False, processing="archive", dot_share="none", use_allowed=False)
    if provider == "manual":
        config["owner"]["display_name"] = "用户"
        config["goals"] = []
    write(ws.root / "config.json", config)
    result = change_selection(ws, provider, before, config["sources"][provider], stop_using=True, delete_raw=delete_raw)
    ws.refresh_published()
    return result


def record_withdrawal(ws, receipt):
    actions = load(ws.root / "state/withdrawals.json", {"actions": []})
    action = next((a for a in actions["actions"] if a["id"] == receipt["id"]), None)
    if action is None:
        raise ValueError("撤回回执需要已有请求的 id")
    for result in receipt["remote"]:
        target = next((r for r in action["remote"] if r["artifact_ref"] == result["artifact_ref"]), None)
        if target is None or result["status"] not in ("removed", "failed"):
            raise ValueError("回执须对应实际远端引用及 removed/failed 结果")
        target.update(status=result["status"], note=result.get("note", ""), receipt_at=ws.now())
        if result["status"] == "removed":
            history = load(ws.root / "state/deliveries.json", {"items": {}})
            for item in history["items"].values():
                if item.get("artifact_ref") == result["artifact_ref"]:
                    item["remote_removed_at"] = ws.now()
            write(ws.root / "state/deliveries.json", history)
    write(ws.root / "state/withdrawals.json", actions)
    return action
