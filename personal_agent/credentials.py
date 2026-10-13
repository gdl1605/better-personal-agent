#!/usr/bin/env python3
"""Create credential-redacted copies of selected WeChat JSON/Markdown/text files."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
from urllib.parse import unquote

MARK = "[REDACTED_CREDENTIAL]"
FIELDS = {
    "aeskey", "encryptionkey", "decryptkey", "decryptionkey", "databasekey",
    "dbkey", "sessionkey", "authkey", "cdnkey", "secretkey", "privatekey",
    "apikey", "apisecret", "clientsecret", "accesstoken", "refreshtoken",
    "idtoken", "authtoken", "bearertoken", "sessiontoken", "activationcode",
    "password", "passwd", "pwd", "authorization", "cookie", "sessioncookie",
    "密码", "口令", "激活码", "密钥", "令牌",
}
PARAMETERS = FIELDS | {
    "token", "encfilekey", "securitytoken", "signature", "xamzsignature",
    "xamzcredential", "xamzsecuritytoken",
}
NAMES = (
    r"aes_?key|encryption_?key|decrypt_?key|decryption_?key|database_?key|db_?key|"
    r"session_?key|auth_?key|cdn_?key|secret_?key|private_?key|api_?key|api_?secret|"
    r"client_?secret|access_?token|refresh_?token|id_?token|auth_?token|"
    r"bearer_?token|session_?token|activation_?code|password|passwd|pwd|"
    r"authorization|session_?cookie|cookie|密码|口令|激活码|密钥|令牌"
)
PLACEHOLDER = re.compile(
    r"^(?:\[REDACTED_CREDENTIAL\]|<[^<>]+>|\*+|x{4,}|your[_-].*|"
    r"example[_-].*|replace[_-].*|\$\{[^}]+\}|os\.(?:getenv|environ).*|"
    r"process\.env\..*|None|null|undefined)$", re.I
)
HINT = re.compile(NAMES + r"|sk-|gh[pousr]_|github_pat_|xox[baprs]-|AKIA|eyJ|Bearer|Basic|PRIVATE KEY|://|[?&]token=", re.I)
PEM = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", re.S)
PROVIDER = re.compile(r"\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|xox[baprs]-[A-Za-z0-9-]{15,}|AKIA[A-Z0-9]{16})\b")
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{10,}\b")
AUTH = re.compile(r"\b(?:Bearer|Basic)\s+(?P<value>[A-Za-z0-9._~+/=-]+)", re.I)
USERPASS = re.compile(r"\b(?:https?|ftp|postgres(?:ql)?|mysql|redis|ssh)://[^/\s:@]+:(?P<value>[^/\s@]+)@", re.I)
QUERY = re.compile(r"(?:[?&](?:amp;)?)(?P<name>[A-Za-z0-9_%.-]+)=(?P<value>[^&\s<>\"'\\)\]，。；]+)")
JSON_FIELD = re.compile(r'"(?:' + NAMES + r')"\s*:\s*(?P<value>"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?)', re.I)
ESCAPED_FIELD = re.compile(r'(?P<keyquote>\\["\x27])(?:' + NAMES + r')(?P=keyquote)\s*:\s*(?P<quote>\\["\x27])(?P<value>.*?)(?P=quote)', re.I | re.S)
QUOTED = re.compile(r"(?<!\w)(?:" + NAMES + r")\s*[:=]\s*(?P<quote>\\?[\"']|&quot;|&apos;)(?P<value>.*?)(?P=quote)", re.I | re.S)
XML_TAG = re.compile(r"<(?P<name>" + NAMES + r")(?:\s[^>]*)?>(?P<value>.*?)</(?P=name)\s*>", re.I | re.S)
BARE = re.compile(r"(?<!\w)(?!authorization\b)(?:" + NAMES + r")\s*[:=]\s*(?P<value>[A-Za-z0-9._~+/=!@#$%^*()-]+)", re.I)
CHINESE = re.compile(r"(?:密码|口令|激活码|密钥|令牌)\s*(?:[:：=]|是|为)\s*[“\"']?(?P<value>[A-Za-z0-9._~+/=!@#$%^*()-]+)")


def normalize(value: str) -> str:
    return re.sub(r"[_\-\s]", "", value).lower()


def is_value(value) -> bool:
    return isinstance(value, (str, int, float)) and not isinstance(value, bool) and bool(str(value).strip()) and MARK not in str(value) and not PLACEHOLDER.fullmatch(str(value).strip())


class Redactor:
    def __init__(self):
        self.counts = Counter()

    def replace_value(self, match, rule, json_literal=False):
        value = match.group("value")
        if json_literal:
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return match.group(0)
        if not is_value(unquote(str(value))):
            return match.group(0)
        self.counts[rule] += 1
        start, end = match.start("value") - match.start(), match.end("value") - match.start()
        replacement = json.dumps(MARK) if json_literal else MARK
        return match.group(0)[:start] + replacement + match.group(0)[end:]

    def whole(self, match, rule):
        if not is_value(match.group(0)):
            return match.group(0)
        self.counts[rule] += 1
        return MARK

    def text(self, value: str) -> str:
        if not HINT.search(value):
            return value
        value = PEM.sub(lambda m: self.whole(m, "private_key"), value)
        value = QUERY.sub(lambda m: self.replace_value(m, "url_parameter") if normalize(unquote(m.group("name"))) in PARAMETERS else m.group(0), value)
        value = USERPASS.sub(lambda m: self.replace_value(m, "url_password"), value)
        value = JSON_FIELD.sub(lambda m: self.replace_value(m, "embedded_json", True), value)
        value = ESCAPED_FIELD.sub(lambda m: self.replace_value(m, "escaped_json"), value)
        value = QUOTED.sub(lambda m: self.replace_value(m, "quoted_assignment"), value)
        value = XML_TAG.sub(lambda m: self.replace_value(m, "xml_field"), value)
        value = CHINESE.sub(lambda m: self.replace_value(m, "explicit_password"), value)
        value = BARE.sub(lambda m: self.replace_value(m, "assignment"), value)
        value = AUTH.sub(lambda m: self.replace_value(m, "authorization"), value)
        value = PROVIDER.sub(lambda m: self.whole(m, "provider_key"), value)
        return JWT.sub(lambda m: self.whole(m, "jwt"), value)

    def obj(self, value):
        if isinstance(value, dict):
            for key, item in value.items():
                if normalize(key) in FIELDS and is_value(item):
                    value[key] = MARK
                    self.counts["credential_field"] += 1
                else:
                    value[key] = self.obj(item)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                value[index] = self.obj(item)
        elif isinstance(value, str):
            return self.text(value)
        return value


def redact_file(source: Path, destination: Path):
    if source.resolve() == destination.resolve():
        raise ValueError("输出必须是独立副本，不能覆盖原件")
    redactor = Redactor()
    text = source.read_text(encoding="utf-8-sig")
    parsed = None
    if source.suffix.lower() == ".json":
        parsed = redactor.obj(json.loads(text))
        del text
    else:
        text = redactor.text(text)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not redactor.counts:
        shutil.copyfile(source, destination)
    elif parsed is not None:
        with destination.open("w", encoding="utf-8") as stream:
            json.dump(parsed, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    else:
        destination.write_text(text, encoding="utf-8")
    return dict(redactor.counts)


def run(paths, output: Path, source_root: Path | None = None):
    output = output.expanduser().resolve()
    base = source_root.expanduser().resolve() if source_root else None
    files = {}
    skipped = 0
    for raw in paths:
        path = raw.expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError("选定输入不存在")
        candidates = path.rglob("*") if path.is_dir() else [path]
        for candidate in candidates:
            if not candidate.is_file() or candidate.is_relative_to(output):
                continue
            if candidate.suffix.lower() not in (".json", ".md", ".txt"):
                skipped += 1
                continue
            relative = candidate.relative_to(base or path.parent)
            if candidate.resolve() == (output / relative).resolve():
                raise ValueError("输出目录不能覆盖选定原件")
            if relative in files and files[relative] != candidate:
                raise ValueError("输入路径重名，请用 --source-root 保留来源目录结构")
            files[relative] = candidate
    results, totals = [], Counter()
    for relative, source in sorted(files.items()):
        try:
            counts = redact_file(source, output / relative)
            totals.update(counts)
            results.append({"path": str(relative), "status": "written", "replacements": sum(counts.values()), "rules": counts})
        except (OSError, ValueError, RecursionError) as error:
            results.append({"path": str(relative), "status": "error", "error_type": type(error).__name__})
        if len(results) % 50 == 0:
            print(json.dumps({"processed": len(results), "files": len(files), "replacements": sum(totals.values())}), flush=True)
    summary = {"files": len(files), "written": sum(r["status"] == "written" for r in results), "changed": sum(r.get("replacements", 0) > 0 for r in results), "replacements": sum(totals.values()), "errors": sum(r["status"] == "error" for r in results), "skipped_other_formats": skipped, "rules": dict(totals)}
    output.mkdir(parents=True, exist_ok=True)
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "kind": "wechat_credential_redaction", "summary": summary, "files": results}
    (output / "redaction-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="Selected WeChat files or directories")
    parser.add_argument("--output", required=True, type=Path, help="Separate destination for redacted copies")
    parser.add_argument("--source-root", type=Path, help="Base for relative paths; does not expand the selected input scope")
    args = parser.parse_args()
    try:
        summary = run(args.paths, args.output, args.source_root)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{type(error).__name__}: {error}\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(1 if summary["errors"] else 0)


if __name__ == "__main__":
    main()
