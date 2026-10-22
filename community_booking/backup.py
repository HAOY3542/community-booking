"""Back up only this candidate's synthetic database; never overwrite a database."""
import argparse
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def snapshot(source: Path, destination: Path):
    if not source.is_file() or source.is_symlink() or destination.exists():
        raise ValueError("Source must exist and destination must be a new file")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("Backup integrity check failed")
            return dst.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
        finally:
            src.close()
            dst.close()
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description="Create a consistent backup of the local candidate database")
    parser.add_argument("--restore-check", action="store_true", help="Also restore to a new verification copy; never replace the live file")
    args = parser.parse_args()
    runtime = Path(__file__).resolve().parent.parent / "runtime"
    if runtime.is_symlink():
        raise ValueError("Refusing a symlinked runtime directory")
    destination = runtime / "backups" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite3")
    count = snapshot(runtime / "demo.sqlite3", destination)
    print("Synthetic database backup verified; booking count:", count)
    if args.restore_check:
        restored = destination.with_name(destination.stem + "-restore-check.sqlite3")
        if snapshot(destination, restored) != count:
            raise ValueError("Restored count differs")
        print("Restored verification copy passed integrity and booking-count checks. Live database was not replaced.")


if __name__ == "__main__":
    main()
