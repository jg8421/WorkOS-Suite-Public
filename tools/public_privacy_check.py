"""Bounded, read-only privacy gate for current public source and release ZIPs.

Findings contain relative filenames, categories and counts, never matched text.
This is a technical pattern audit, not proof that unknown personal information
is absent. Media semantics, encrypted contents and Git history are not inferred.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import zipfile


@dataclass(frozen=True)
class Limits:
    max_member_bytes: int = 128 * 1024 * 1024
    max_total_bytes: int = 768 * 1024 * 1024
    max_entries: int = 20_000
    max_depth: int = 3
    max_ratio: int = 250
    max_source_files: int = 20_000
    max_findings: int = 2_000


# General rules contain no task-specific people, institutions or projects.
# Public author attribution and machine names are not personal-data signatures.
_PATH_PATTERN = re.compile(
    r"(?i)[A-Z]:[/\\]+Users[/\\]+(?P<user>[^/\\\s\"'\x00]+)"
)
_ORGANIZATION_PATH_PATTERN = re.compile(
    r"(?i)\bOneDrive\s+-\s+(?P<organization>[A-Za-z0-9][^/\\\r\n\"';<>]{2,99})"
)
_SECRETS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----(?:\r?\n|\\n)[A-Za-z0-9+/=]{40,128}")),
    ("api_credential", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,512}\b")),
    ("github_credential", re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,255}|github_pat_[A-Za-z0-9_]{20,512})\b")),
    ("bearer_credential", re.compile(r"\bBearer[ \t]+[A-Za-z0-9_.~+/=-]{20,2048}", re.I)),
    ("jwt_credential", re.compile(r"\beyJ[A-Za-z0-9_-]{8,1024}\.[A-Za-z0-9_-]{8,2048}\.[A-Za-z0-9_-]{8,1024}\b")),
    ("cloud_credential", re.compile(r"\b(?:AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{25,255})\b")),
)
_FAKE_TOKEN = re.compile(
    r"synthetic|fixture|dummy|example|fake|sentinel|do[_-]?not[_-]?report|never[_-]?callback|not[_-]?a[_-]?real|test[_-]?(?:key|secret|token)", re.I
)
_SKIP_SOURCE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "work", "taskwork",
    "outputs", "output", "runtime", "data", "credentials", "secrets", "backups",
}
_RUNTIME_NAMES = {
    "auth.json", "password-account.json", "custom-models.json", "project-artifacts.json",
    "model-status.json", "state.json", "settings.json", "config.json", "runtime.json",
    "memory.jsonl", "events.jsonl", "tombstones.jsonl", "inbox.md", "status.json",
    "last-addr.txt", "last_serial.txt", "hardware_sensor_bridge.json", "hardware_monitor_path.txt",
    "pairing.json", "phone-pairing.json", "phone-state.json", "paired-devices.json", "wireless-adb.json",
}
_DATA_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".jsonl", ".log", ".pid", ".wav", ".mp3", ".m4a",
    ".mp4", ".aac", ".flac", ".ogg", ".wma", ".mov", ".pfx", ".p12", ".jks",
    ".keystore", ".key", ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".ppt", ".pptx", ".pdf",
}
_ARCHIVE_SUFFIXES = {".zip", ".apk", ".jar", ".whl"}
_WINDOWS_DEVICE = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.I)
_EXAMPLE_PATH = re.compile(r"(?:^|/)(?:tests?|docs?|examples?|fixtures?)(?:/|$)|(?:^|/)(?:test[_-]|readme(?:\.|$))", re.I)


class InputError(ValueError):
    """Invalid audit input; public error messages do not include host paths."""


def _linked(path: Path) -> bool:
    value = path.lstat()
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, "st_file_attributes", 0) & 0x400)


def _unsafe_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    parts = normalized.rstrip("/").split("/")
    return (
        not normalized or normalized.startswith("/") or ":" in normalized
        or any(ord(character) < 32 for character in normalized)
        or any(part in {"", ".", ".."} or part.rstrip(" .") != part or _WINDOWS_DEVICE.match(part) for part in parts)
    )


def _third_party(name: str) -> bool:
    clean = name.replace("\\", "/").lower()
    return clean.startswith("runtime/") or "/runtime/" in clean or "/node_modules/" in clean or "/vendor/" in clean


def _example(name: str) -> bool:
    return bool(_EXAMPLE_PATH.search(name.replace("\\", "/")))


def _placeholder_path_component(value: str) -> bool:
    normalized = value.strip("<>[]{}% ").lower()
    return normalized in {
        "user", "username", "user-name", "your-user-name", "your-username", "name", "profile",
        "example", "example-user", "synthetic-user", "test-user", "demo-user", "public", "default",
        "organization", "your-organization", "company", "example company", "example organization",
    }


def _obvious_fake_credential(value: str, category: str) -> bool:
    if _FAKE_TOKEN.search(value):
        return True
    if category == "api_credential":
        body = value.removeprefix("sk-").removeprefix("proj-").lower()
        # Explicit alphabet/number sequences are common redaction fixtures;
        # generic low entropy or merely residing in tests is not an exemption.
        return len(body) >= 20 and any(sequence.startswith(body) for sequence in (
            "abcdefghijklmnopqrstuvwxyz0123456789", "0123456789" * 8,
        ))
    if category == "jwt_credential":
        parts = value.split(".")
        if parts[-1].lower() in {"signature", "testsignature", "dummysignature", "fakesignature"}:
            return True
        try:
            signature = base64.urlsafe_b64decode(parts[-1] + "=" * (-len(parts[-1]) % 4))
            if signature.lower() in {b"signature", b"testsignature", b"dummysignature", b"fakesignature"}:
                return True
            header, payload = [json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))) for part in parts[:2]]
            if not isinstance(header, dict) or not isinstance(payload, dict) or not isinstance(header.get("alg"), str):
                return True
            expected = {"HS256": 32, "HS384": 48, "HS512": 64, "ES256": 64, "ES384": 96, "ES512": 132, "EdDSA": 64}
            if header["alg"] in expected:
                return len(signature) != expected[header["alg"]]
            if header["alg"].startswith(("RS", "PS")):
                return len(signature) < 128
            return False
        except (ValueError, UnicodeError):
            # A JWT-shaped redaction fixture without valid JSON is not a
            # verified credential; it remains a visible review finding.
            return True
    return False


def _data_category(name: str) -> str | None:
    clean = name.replace("\\", "/").split("!")[-1]
    leaf = clean.rsplit("/", 1)[-1].lower()
    suffix = Path(leaf).suffix
    if leaf.startswith("adbkey") or re.search(r"(?:phone[-_]?pairing[-_]?state|pairing[-_]?state)", leaf):
        return "phone_pairing_state"
    if leaf == ".env" or leaf.startswith(".env.") or leaf in _RUNTIME_NAMES or suffix in _DATA_SUFFIXES:
        return "private_runtime_or_data_asset"
    if suffix == ".pem" and not (_third_party(name) and leaf in {"cacert.pem", "cert.pem"}):
        return "key_material_file"
    if any(part.lower() in {"recordings", "录音", "credentials", "secrets"} for part in clean.split("/")):
        return "private_runtime_or_data_asset"
    return None


class _Audit:
    def __init__(self, limits: Limits):
        if any(type(getattr(limits, field)) is not int or getattr(limits, field) < 1 for field in limits.__dataclass_fields__):
            raise InputError("Audit limits must be positive integers")
        self.limits = limits
        self.findings: Counter = Counter()
        self.files_scanned = 0
        self.bytes_scanned = 0
        self.entries_checked = 0
        self.halted = False

    def add(self, name: str, category: str, severity: str = "block", count: int = 1):
        # Never allow control characters or long, attacker-controlled filenames
        # to become multiline output or a second covert content channel.
        safe_name = name if len(name) <= 700 and not any(ord(c) < 32 for c in name) else "<invalid filename>"
        for _, pattern in _SECRETS:
            safe_name = pattern.sub("<redacted filename>", safe_name)
        key = (safe_name, category, severity)
        if key not in self.findings and len(self.findings) >= self.limits.max_findings:
            self.findings[("<audit>", "finding_limit", "block")] = 1
            self.halted = True
            return
        self.findings[key] += count

    def reserve(self, name: str, amount: int) -> bool:
        if amount > self.limits.max_member_bytes:
            self.add(name, "member_size_limit")
            return False
        if self.bytes_scanned + amount > self.limits.max_total_bytes:
            self.add(name, "expansion_budget")
            self.halted = True
            return False
        self.bytes_scanned += amount
        return True

    def scan_bytes(self, name: str, raw: bytes):
        self.files_scanned += 1
        found: dict[tuple[str, str], set[str]] = {}
        # Overlap is larger than each bounded token pattern, including JWTs.
        # UTF-16 views also find paths embedded in native executable metadata.
        chunk_size, overlap = 1024 * 1024, 8192
        for offset in range(0, max(1, len(raw)), chunk_size):
            chunk = raw[max(0, offset - overlap):offset + chunk_size]
            views = [chunk.decode("utf-8", errors="ignore")]
            if b"\x00" in chunk:
                for start in (0, 1):
                    for encoding in ("utf-16le", "utf-16be"):
                        views.append(chunk[start:].decode(encoding, errors="ignore"))
            for text in views:
                rules = [
                    ("personal_user_path", _PATH_PATTERN), ("organization_sync_path", _ORGANIZATION_PATH_PATTERN),
                ]
                for category, pattern in rules + list(_SECRETS):
                    for match in pattern.finditer(text):
                        component = match.groupdict().get("user") or match.groupdict().get("organization")
                        if component and _placeholder_path_component(component):
                            continue
                        value = match.group(0)
                        severity = "block"
                        if category.endswith("credential") and _example(name) and _obvious_fake_credential(value, category):
                            severity = "needs_review"
                        if category == "private_key":
                            severity = "block"
                        found.setdefault((category, severity), set()).add(hashlib.sha256(value.encode("utf-8")).hexdigest())
        for (category, severity), hashes in found.items():
            self.add(name, category, severity, len(hashes))
        if name.lower().split("!")[-1].endswith(".json") and len(raw) <= 10 * 1024 * 1024:
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeError):
                return
            entries = value if isinstance(value, list) else value.get("repositories", []) if isinstance(value, dict) else []
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("private") is True and any(key in entry for key in ("full_name", "html_url", "repository")):
                        self.add(name, "private_catalogue_entry")

    def scan_file(self, path: Path, name: str):
        if self.halted:
            return
        try:
            if _linked(path):
                self.add(name, "source_link_or_reparse")
                return
            if not path.is_file():
                self.add(name, "source_not_regular_file")
                return
            category = _data_category(name)
            if category:
                self.add(name, category)
                return  # Do not open a runtime DB, authentication or media file.
            size = path.stat().st_size
            if not self.reserve(name, size):
                return
            with path.open("rb") as handle:
                raw = handle.read(self.limits.max_member_bytes + 1)
            if len(raw) != size:
                self.add(name, "source_changed_during_scan")
                return
            self.scan_bytes(name, raw)
            if path.suffix.lower() in _ARCHIVE_SUFFIXES:
                self.scan_zip(io.BytesIO(raw), name, depth=1)
        except (OSError, ValueError):
            self.add(name, "source_unreadable")

    def scan_zip(self, file, label: str = "", *, depth: int = 0):
        if self.halted:
            return
        if depth > self.limits.max_depth:
            self.add(label or "<package>", "archive_depth_limit")
            return
        try:
            with zipfile.ZipFile(file) as archive:
                for member in archive.infolist():
                    if self.halted:
                        break
                    self.entries_checked += 1
                    if self.entries_checked > self.limits.max_entries:
                        self.add(label or "<package>", "archive_entry_limit")
                        self.halted = True
                        break
                    name = label + "!" + member.filename if label else member.filename
                    if _unsafe_member(member.filename):
                        self.add("<invalid archive member>", "unsafe_archive_path")
                        continue
                    mode = (member.external_attr >> 16) & 0o170000
                    if mode == stat.S_IFLNK or (member.external_attr & 0x400):
                        self.add(name, "archive_link_or_reparse")
                        continue
                    if member.flag_bits & 1:
                        self.add(name, "encrypted_archive_member")
                        continue
                    if member.is_dir():
                        continue
                    category = _data_category(name)
                    if category:
                        self.add(name, category)
                        continue
                    if member.file_size / max(1, member.compress_size) > self.limits.max_ratio:
                        self.add(name, "archive_ratio_limit")
                        continue
                    if not self.reserve(name, member.file_size):
                        continue
                    with archive.open(member) as handle:
                        raw = handle.read(self.limits.max_member_bytes + 1)
                    if len(raw) != member.file_size:
                        self.add(name, "archive_member_size_mismatch")
                        continue
                    self.scan_bytes(name, raw)
                    if Path(member.filename).suffix.lower() in _ARCHIVE_SUFFIXES:
                        self.scan_zip(io.BytesIO(raw), name, depth=depth + 1)
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
            self.add(label or "<package>", "invalid_or_unreadable_archive")

    def result(self) -> dict:
        findings = [{"file": name, "category": category, "severity": severity, "count": count}
                    for (name, category, severity), count in sorted(self.findings.items())]
        blocked = sum(item["count"] for item in findings if item["severity"] == "block")
        reviewed = sum(item["count"] for item in findings if item["severity"] == "needs_review")
        return {
            "status": "fail" if blocked else "pass", "files_scanned": self.files_scanned,
            "bytes_scanned": self.bytes_scanned, "archive_entries_checked": self.entries_checked,
            "blocked_count": blocked, "review_count": reviewed, "findings": findings,
            "limitations": [
                "Pattern audit only; unknown personal information and media semantics require review.",
                "Current source and supplied archives only; Git history and excluded local runtime folders are not exported.",
                "Recognizable fake credentials in tests or documentation require review; credentials are never validated online.",
            ],
        }


def _source_candidates(root: Path) -> list[Path]:
    paths = set()
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            capture_output=True, timeout=10, check=False,
        )
        if result.returncode == 0:
            for name in result.stdout.decode("utf-8").split("\x00"):
                if name:
                    if _unsafe_member(name):
                        raise InputError("Git inventory contains an unsafe source path")
                    paths.add(root.joinpath(*name.replace("\\", "/").split("/")))
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        pass
    # Also covers a freshly initialized, not-yet-staged source snapshot. Local
    # scratch/private state is excluded, while any tracked file is still checked.
    for current, directories, files in os.walk(root, followlinks=False):
        retained = []
        for name in directories:
            if name.lower() in _SKIP_SOURCE_DIRS:
                continue
            path = Path(current) / name
            try:
                linked = _linked(path)
            except OSError:
                linked = True
            if linked:
                paths.add(path)
            else:
                retained.append(name)
        directories[:] = retained
        for name in files:
            paths.add(Path(current) / name)
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def _scan_source(audit: _Audit, source: Path):
    source = Path(os.path.abspath(source))
    if not source.is_dir():
        raise InputError("Source must be an existing directory")
    if _linked(source):
        audit.add("<source>", "source_link_or_reparse")
        return
    candidates = _source_candidates(source)
    if len(candidates) > audit.limits.max_source_files:
        audit.add("<source>", "source_file_limit")
        return
    for path in candidates:
        if audit.halted:
            break
        label = path.relative_to(source).as_posix()
        parents = list(path.parents)
        try:
            if any(_linked(parent) for parent in parents[:parents.index(source)]):
                audit.add(label, "source_link_or_reparse")
                continue
        except OSError:
            audit.add(label, "source_unreadable")
            continue
        audit.scan_file(path, label)


def _scan_package(audit: _Audit, package: Path):
    package = Path(package)
    if not package.is_file():
        raise InputError("Package must be an existing ZIP file")
    if _linked(package):
        audit.add("<package>", "source_link_or_reparse")
        return
    if package.stat().st_size > audit.limits.max_total_bytes:
        audit.add("<package>", "compressed_input_limit")
        return
    audit.scan_zip(package)


def check_source(source: str | Path, *, limits: Limits | None = None) -> dict:
    result = _Audit(limits or Limits())
    _scan_source(result, Path(source))
    return result.result()


def check_package(package: str | Path, *, limits: Limits | None = None) -> dict:
    result = _Audit(limits or Limits())
    _scan_package(result, Path(package))
    return result.result()


def audit(source: str | Path, package: str | Path | None = None, *, limits: Limits | None = None) -> dict:
    result = _Audit(limits or Limits())
    _scan_source(result, Path(source))
    if package is not None:
        _scan_package(result, Path(package))
    return result.result()


def _write_report(path: Path, report: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".privacy-report-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = audit(args.source, args.package)
        if args.report:
            _write_report(args.report, report)
    except (InputError, OSError):
        print(json.dumps({"status": "error", "category": "invalid_input_or_report", "count": 1}))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 1 if report["blocked_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
