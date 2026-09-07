#!/usr/bin/env python3
"""Fail-closed, read-only secret-pattern scan that never prints values or paths."""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from typing import Any, Dict, List, Optional, Tuple


SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", ".venv", "venv",
    "__pycache__", ".cache", "dist", "build", "coverage", ".next", ".nuxt",
}
MAX_DEFAULT = 2 * 1024 * 1024

FILENAME_RULES = (
    (re.compile(r"^\.env(?:\..+)?$", re.I), "environment-file", "P3"),
    (re.compile(r"^(?:id_rsa|id_dsa|id_ecdsa|id_ed25519)$", re.I), "private-key-file", "P2"),
    (re.compile(r"\.(?:p12|pfx|jks|keystore)$", re.I), "credential-container", "P2"),
    (re.compile(r"^(?:\.netrc|\.npmrc|\.pypirc)$", re.I), "credential-config", "P2"),
    (re.compile(r"(?:credentials?|service[-_]?account).*(?:\.json|\.ya?ml)$", re.I), "credential-file", "P2"),
)

CONTENT_RULES = (
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "private-key", "P1"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws-access-key-id", "P2"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "github-token", "P1"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"), "slack-token", "P1"),
    (re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"), "telegram-bot-token", "P1"),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"), "api-key-like", "P1"),
    (re.compile(
        r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)\b"
        r"\s*[:=]\s*['\"]?(?!<|\$\{|example|sample|changeme|replace|your[-_])[^\s'\"#]{8,}"
    ), "generic-secret-assignment", "P2"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find secret-like material without printing values or paths.")
    parser.add_argument("--root", required=True, help="Directory to scan")
    parser.add_argument("--json", action="store_true", help="Emit redacted JSON")
    parser.add_argument("--max-file-size", type=int, default=MAX_DEFAULT, help="Bytes; default 2097152")
    parser.add_argument(
        "--private-map",
        help="Optional new 0600 JSON file mapping file_id to path. Owner runs this manually outside AI chat.",
    )
    return parser.parse_args()


def platform_supported() -> bool:
    return (
        os.name == "posix"
        and (sys.platform == "darwin" or sys.platform.startswith("linux"))
        and hasattr(os, "O_NOFOLLOW")
        and os.open in os.supports_dir_fd
        and os.scandir in os.supports_fd
    )


def directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def open_root_without_symlinks(raw_root: str) -> Tuple[int, os.stat_result]:
    """Open every root path component relative to a held directory descriptor."""
    expanded = os.path.expanduser(raw_root)
    is_absolute = os.path.isabs(expanded)
    components = [part for part in expanded.split(os.sep) if part not in ("", ".")]
    if any(part == ".." for part in components):
        raise ValueError("parent traversal is not allowed")

    current_fd = os.open(os.sep if is_absolute else ".", directory_flags())
    try:
        for component in components:
            next_fd = os.open(component, directory_flags(), dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        opened = os.fstat(current_fd)
        if not stat.S_ISDIR(opened.st_mode):
            raise NotADirectoryError("root is not a directory")
        return current_fd, opened
    except Exception:
        os.close(current_fd)
        raise


def mount_id(fd: int) -> Optional[str]:
    """Return Linux mount ID; macOS relies on st_dev plus visited inode checks."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        with open(f"/proc/self/fdinfo/{fd}", "r", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("mnt_id:"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def same_mount(opened: os.stat_result, opened_mount: Optional[str], root_stat: os.stat_result, root_mount: Optional[str]) -> bool:
    if sys.platform.startswith("linux"):
        return opened_mount is not None and root_mount is not None and opened_mount == root_mount
    return opened.st_dev == root_stat.st_dev


def make_file_id(relative: str, scope_salt: str) -> str:
    material = f"{scope_salt}\0{relative}".encode("utf-8", errors="replace")
    return "file-" + hashlib.sha256(material).hexdigest()[:12]


def finding(kind: str, severity: str, reference: str, line: Optional[int], source: str) -> Dict[str, Any]:
    item = {
        "severity": severity,
        "kind": kind,
        "file_id": reference,
        "evidence": "[REDACTED]",
        "source": source,
    }
    if line is not None:
        item["line"] = line
    return item


def is_probably_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def read_regular_at(
    parent_fd: int,
    name: str,
    expected: os.stat_result,
    root_stat: os.stat_result,
    root_mount: Optional[str],
    max_size: int,
) -> Tuple[Optional[bytes], str]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(name, flags, dir_fd=parent_fd)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            return None, "changed"
        if not same_mount(opened, mount_id(fd), root_stat, root_mount):
            return None, "mount"
        if not stat.S_ISREG(opened.st_mode):
            return None, "non_regular"
        if opened.st_size > max_size:
            return None, "large"

        chunks = []
        total = 0
        while total <= max_size:
            chunk = os.read(fd, min(65536, max_size + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > max_size:
            return None, "large"
        return b"".join(chunks), "ok"
    finally:
        os.close(fd)


def scan(
    root_fd: int,
    root_stat: os.stat_result,
    root_mount: Optional[str],
    max_size: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, str]]:
    findings: List[Dict[str, Any]] = []
    private_paths: Dict[str, str] = {}
    scope_salt = f"{root_stat.st_dev}:{root_stat.st_ino}"
    stats = {
        "files_seen": 0,
        "files_scanned": 0,
        "skipped_excluded_dirs": 0,
        "skipped_symlink": 0,
        "skipped_binary": 0,
        "skipped_large": 0,
        "skipped_non_regular": 0,
        "skipped_mount": 0,
        "revisited_dir": 0,
        "changed_during_scan": 0,
        "unreadable": 0,
        "walk_errors": 0,
    }
    visited_dirs = {(root_mount or str(root_stat.st_dev), root_stat.st_ino)}

    def visit(directory_fd: int, parts: List[str]) -> None:
        try:
            iterator = os.scandir(directory_fd)
        except OSError:
            stats["walk_errors"] += 1
            return

        with iterator:
            for entry in iterator:
                name = entry.name
                try:
                    listed = entry.stat(follow_symlinks=False)
                except OSError:
                    stats["unreadable"] += 1
                    continue

                mode = listed.st_mode
                if stat.S_ISLNK(mode):
                    stats["skipped_symlink"] += 1
                    continue

                if stat.S_ISDIR(mode):
                    if name in SKIP_DIRS:
                        stats["skipped_excluded_dirs"] += 1
                        continue
                    try:
                        child_fd = os.open(name, directory_flags(), dir_fd=directory_fd)
                    except OSError:
                        stats["unreadable"] += 1
                        continue
                    try:
                        opened = os.fstat(child_fd)
                        if (opened.st_dev, opened.st_ino) != (listed.st_dev, listed.st_ino):
                            stats["changed_during_scan"] += 1
                            continue
                        child_mount = mount_id(child_fd)
                        if not same_mount(opened, child_mount, root_stat, root_mount):
                            stats["skipped_mount"] += 1
                            continue
                        visit_key = (child_mount or str(opened.st_dev), opened.st_ino)
                        if visit_key in visited_dirs:
                            stats["revisited_dir"] += 1
                            continue
                        visited_dirs.add(visit_key)
                        visit(child_fd, parts + [name])
                    finally:
                        os.close(child_fd)
                    continue

                stats["files_seen"] += 1
                relative = "/".join(parts + [name])
                reference = make_file_id(relative, scope_salt)

                for pattern, kind, severity in FILENAME_RULES:
                    if pattern.search(name) and not re.search(r"(?:example|sample|template)$", name, re.I):
                        findings.append(finding(kind, severity, reference, None, "filename"))
                        private_paths[reference] = relative

                try:
                    data, status = read_regular_at(directory_fd, name, listed, root_stat, root_mount, max_size)
                except OSError:
                    stats["unreadable"] += 1
                    continue

                if status == "changed":
                    stats["changed_during_scan"] += 1
                    continue
                if status == "mount":
                    stats["skipped_mount"] += 1
                    continue
                if status == "non_regular":
                    stats["skipped_non_regular"] += 1
                    continue
                if status == "large":
                    stats["skipped_large"] += 1
                    continue
                if data is None:
                    stats["unreadable"] += 1
                    continue
                if is_probably_binary(data):
                    stats["skipped_binary"] += 1
                    continue

                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    stats["skipped_binary"] += 1
                    continue

                stats["files_scanned"] += 1
                for number, line_text in enumerate(text.splitlines(), 1):
                    for pattern, kind, severity in CONTENT_RULES:
                        if pattern.search(line_text):
                            findings.append(finding(kind, severity, reference, number, "content-pattern"))
                            private_paths[reference] = relative

    visit(root_fd, [])

    unique = []
    seen = set()
    for item in findings:
        key = (item["kind"], item["file_id"], item.get("line"), item["source"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique, stats, private_paths


def write_private_map(destination: str, mapping: Dict[str, str]) -> bool:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(destination, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(mapping, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        return True
    except OSError:
        return False


def main() -> int:
    args = parse_args()
    if args.max_file_size < 1 or not platform_supported():
        print("Scan not started safely: unsupported platform or invalid size.", file=sys.stderr)
        return 2

    root_fd = None
    try:
        root_fd, opened_root = open_root_without_symlinks(args.root)
        root_mount = mount_id(root_fd)
        if sys.platform.startswith("linux") and root_mount is None:
            print("Scan not started safely: mount boundary is unavailable.", file=sys.stderr)
            return 2
        findings, stats, private_paths = scan(root_fd, opened_root, root_mount, args.max_file_size)
    except Exception as exc:
        print(f"Scan failed safely: {type(exc).__name__}", file=sys.stderr)
        return 2
    finally:
        if root_fd is not None:
            os.close(root_fd)

    incomplete_keys = (
        "skipped_symlink", "skipped_binary", "skipped_large", "skipped_non_regular",
        "skipped_mount", "revisited_dir", "changed_during_scan", "unreadable", "walk_errors",
    )
    incomplete = any(stats[key] > 0 for key in incomplete_keys)
    if stats["files_seen"] > 0 and stats["files_scanned"] == 0:
        incomplete = True

    if args.private_map:
        if not write_private_map(args.private_map, private_paths):
            print("Private map was not written safely.", file=sys.stderr)
            return 2
        print("Private file map written locally with mode 0600; do not share it with the AI.", file=sys.stderr)

    result = {
        "complete": not incomplete,
        "findings_count": len(findings),
        "findings": findings,
        "coverage": stats,
        "limitations": [
            "No path names in normal output", "No symlink targets", "No mounted filesystems",
            "No git history", "No archives", "No remote/cloud storage",
            "Excluded dependency/cache/build directories", "Pattern matches may be false positives",
        ],
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Redacted scan: {len(findings)} finding(s); {stats['files_scanned']} file(s) scanned.")
        for item in findings:
            location = f":{item['line']}" if "line" in item else ""
            print(f"{item['severity']} {item['kind']} {item['file_id']}{location} [REDACTED]")
        skipped = sum(stats[key] for key in incomplete_keys)
        print(f"Coverage limits: {skipped} object(s) skipped; git history, archives and remote storage not checked.")
        if incomplete:
            print("INCOMPLETE: skipped or changed objects exist; do not interpret this result as clean.", file=sys.stderr)

    if incomplete:
        return 2
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
