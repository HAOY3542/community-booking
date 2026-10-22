"""Validate an exact source allowlist, optionally stage a NEW local review directory."""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_PARTS = {"runtime", ".venv", ".git", "__pycache__", "backups", "node_modules", ".env"}
PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "service-key": re.compile(r"\b(?:sk-proj-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"),
    "credential-url": re.compile(r"https?://[^\s/@]+:[^\s/@]+@"),
    "webhook-secret": re.compile(r"https://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[A-Za-z0-9-]{16,}"),
}


def inspect():
    files = [line.strip() for line in (ROOT / "RELEASE_ALLOWLIST.txt").read_text().splitlines()
             if line.strip() and not line.startswith("#")]
    if len(files) != len(set(files)) or not files:
        raise ValueError("Empty or duplicate allowlist entries")
    failures = []
    for name in files:
        path = ROOT / name
        parts = Path(name).parts
        if (Path(name).is_absolute() or ".." in parts or any(part in FORBIDDEN_PARTS for part in parts)
                or path.suffix in {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".pyc"}):
            failures.append({"path": name, "category": "forbidden-path"})
            continue
        if not path.is_file() or path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != ROOT.parent):
            failures.append({"path": name, "category": "missing-or-symlink"})
            continue
        content = path.read_text(encoding="utf-8")
        for category, pattern in PATTERNS.items():
            if pattern.search(content):
                failures.append({"path": name, "category": category})
    return files, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, help="New local destination; existing paths are refused")
    args = parser.parse_args()
    files, failures = inspect()
    print(json.dumps({"allowlisted_files": len(files), "findings": failures,
                      "scope": "heuristic text/path check, not a complete security or ownership audit"}, ensure_ascii=False))
    if failures:
        raise SystemExit(1)
    if args.stage:
        target = args.stage.absolute()
        if target.exists() or target.is_symlink() or not target.parent.is_dir():
            raise ValueError("Destination must be a new directory under an existing parent")
        target.mkdir(mode=0o700)
        checksums = []
        for name in files:
            source, destination = ROOT / name, target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            checksums.append(hashlib.sha256(destination.read_bytes()).hexdigest() + "  " + name)
        (target / "SHA256SUMS").write_text("\n".join(checksums)+"\n")
        print(f"Staged {len(files)} allowlisted files and SHA256SUMS into a new LOCAL review directory.")


if __name__ == "__main__":
    main()
