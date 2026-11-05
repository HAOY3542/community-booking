"""Validate source inventory, text heuristics and SHA256SUMS; optionally stage a NEW copy."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_PARTS = {"runtime", ".venv", ".git", "__pycache__", "backups", "node_modules", ".env"}
LOCAL_OUTPUTS = {"runtime", ".venv", ".git", "__pycache__", "backups", "node_modules", "coverage", "output", ".playwright-cli"}
PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "service-key": re.compile(r"\b(?:sk-proj-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"),
    "credential-url": re.compile(r"https?://[^\s/@]+:[^\s/@]+@"),
    "webhook-secret": re.compile(r"https://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[A-Za-z0-9-]{16,}"),
}


def regular_file(root, name):
    path = root / name
    return path.is_file() and not any(p.is_symlink() for p in (path, *path.parents))


def inventory(root):
    if (root / ".git").exists():
        # Include tracked ignored files and new nonignored files. Never trust .gitignore alone.
        result = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others",
                                 "--exclude-standard", "-z"], check=True, capture_output=True)
        return set(result.stdout.decode("utf-8").rstrip("\0").split("\0")) - {""}
    files = set()
    def walk(directory):
        for path in directory.iterdir():
            if path.name in LOCAL_OUTPUTS and not path.is_symlink():
                continue
            if path.is_symlink() or not path.is_dir():
                files.add(path.relative_to(root).as_posix())
            else:
                walk(path)
    walk(root)
    return files


def inspect(root=None, *, verify_checksums=True):
    root = Path(root or ROOT).resolve()
    if not regular_file(root, "RELEASE_ALLOWLIST.txt"):
        raise ValueError("Allowlist must be a regular non-symlinked file")
    files = [line.strip() for line in (root / "RELEASE_ALLOWLIST.txt").read_text().splitlines()
             if line.strip() and not line.startswith("#")]
    if len(files) != len(set(files)) or not files:
        raise ValueError("Empty or duplicate allowlist entries")
    failures = []
    for name in files:
        path, parts = root / name, Path(name).parts
        if (Path(name).is_absolute() or ".." in parts or Path(name).as_posix() != name
                or name == "SHA256SUMS" or any(part in FORBIDDEN_PARTS or part.startswith(".env") for part in parts)
                or path.suffix in {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".pyc"}):
            failures.append({"path": name, "category": "forbidden-path"})
            continue
        if not regular_file(root, name):
            failures.append({"path": name, "category": "missing-or-symlink"})
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeError:
            failures.append({"path": name, "category": "non-text-source"})
            continue
        for category, pattern in PATTERNS.items():
            if pattern.search(content):
                failures.append({"path": name, "category": category})
    for name in sorted(inventory(root) - set(files) - {"SHA256SUMS"}):
        failures.append({"path": name, "category": "outside-allowlist"})
    if verify_checksums:
        if not regular_file(root, "SHA256SUMS"):
            failures.append({"path": "SHA256SUMS", "category": "missing-or-symlink"})
        else:
            recorded = {}
            for line in (root / "SHA256SUMS").read_text().splitlines():
                match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
                if not match or match[2] in recorded:
                    failures.append({"path": "SHA256SUMS", "category": "invalid-checksum-entry"})
                    continue
                recorded[match[2]] = match[1]
            if set(recorded) != set(files):
                failures.append({"path": "SHA256SUMS", "category": "checksum-inventory-mismatch"})
            # Only read already validated allowlist paths, never paths supplied just by the manifest.
            if not failures:
                for name in files:
                    if hashlib.sha256((root / name).read_bytes()).hexdigest() != recorded[name]:
                        failures.append({"path": name, "category": "checksum-mismatch"})
    return files, failures


def checksums(root, files):
    return "".join(hashlib.sha256((root / name).read_bytes()).hexdigest() + "  " + name + "\n" for name in files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--stage", type=Path, help="New local destination; existing paths are refused")
    group.add_argument("--refresh-checksums", action="store_true", help="Explicitly refresh after reviewing source changes")
    args = parser.parse_args()
    files, failures = inspect(verify_checksums=not args.refresh_checksums)
    print(json.dumps({"allowlisted_files": len(files), "findings": failures,
                      "scope": "inventory, SHA256 and heuristic text/path checks; not a complete security or ownership audit"}, ensure_ascii=False))
    if failures:
        raise SystemExit(1)
    if args.refresh_checksums:
        manifest = ROOT / "SHA256SUMS"
        if manifest.is_symlink():
            raise ValueError("Refusing a symlinked checksum manifest")
        manifest.write_text(checksums(ROOT, files))
        print("Refreshed SHA256SUMS. Review its diff and run this checker again.")
    if args.stage:
        target = args.stage.absolute()
        if target.exists() or target.is_symlink() or not target.parent.is_dir():
            raise ValueError("Destination must be a new directory under an existing parent")
        target = target.resolve()
        if target == ROOT or ROOT in target.parents:
            raise ValueError("Stage destination must be outside the source directory")
        target.mkdir(mode=0o700)
        for name in [*files, "SHA256SUMS"]:
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        _, findings = inspect(target)
        if findings:
            raise ValueError("Staged copy failed verification")
        print(f"Staged {len(files)} allowlisted files and verified SHA256SUMS into a new LOCAL directory.")


if __name__ == "__main__":
    main()
