"""Explicit local-only initialization and launcher. No source-project imports."""
import argparse
import json
import os
import secrets
from datetime import timedelta
from pathlib import Path

import uvicorn

from .app import create_app
from .config import Settings, SiteConfig
from .security import hash_password
from .service import BookingInput


ROOT = Path(__file__).resolve().parent.parent


def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value)


def prepare(port=8765):
    os.umask(0o077)
    runtime = ROOT / "runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    if runtime.is_symlink():
        raise ValueError("Refusing a symlinked runtime directory")
    identity = runtime / "demo-identity.json"
    access = runtime / "demo-access.txt"
    config = runtime / "config.json"
    database = runtime / "demo.sqlite3"
    if not identity.exists():
        if any(p.exists() for p in (access, config, database)):
            raise ValueError("Incomplete demo runtime: restore its identity, or use a new empty candidate directory")
        credentials = [("demo-admin", "admin", secrets.token_urlsafe(24)),
                       ("demo-verifier", "verifier", secrets.token_urlsafe(24))]
        material = {"signing_key": secrets.token_hex(32),
                    "accounts": {name: {"role": role, "password_hash": hash_password(password)}
                                 for name, role, password in credentials}}
        write_private(identity, json.dumps(material, indent=2))
        write_private(access, "仅供本机合成演示；不是生产账户。勿上传、分享或复用。\n\n" +
                      "\n".join(f"{name} ({role})\n{password}\n" for name, role, password in credentials))
        write_private(config, (ROOT / "config.example.json").read_text())
    for file in (identity, access, config):
        if not file.is_file() or file.is_symlink() or file.stat().st_mode & 0o077:
            raise ValueError("Demo identity/config must be regular private files (owner access only)")
    material = json.loads(identity.read_text())
    return Settings(database=database, signing_key=bytes.fromhex(material["signing_key"]),
                    base_url=f"http://127.0.0.1:{port}", site=SiteConfig.model_validate_json(config.read_text()),
                    accounts=material["accounts"])


def seed(service):
    path = ROOT / "runtime" / "synthetic-receipts.json"
    if path.exists():
        return
    with service.db.connect() as db:
        if db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]:
            raise ValueError("Seed is only allowed on an empty demo database")
    now = service.s.now()
    # Pick the next slot, including the midnight transition, for an immediately usable check-in demo.
    step = service.s.site.slot_minutes
    minute = ((now.hour * 60 + now.minute) // step + 1) * step
    day = now.date()
    if minute + step > service.s.site.close_minute:
        day += timedelta(days=1)
        minute = service.s.site.open_minute
    minute = max(minute, service.s.site.open_minute)
    facilities = service.s.site.facilities
    rows = []
    for i, f in enumerate(facilities[:3]):
        duration = min(f.durations)
        selected = day + timedelta(days=i)
        start = minute if i == 0 else service.s.site.open_minute
        if start + duration > service.s.site.close_minute:
            start = service.s.site.open_minute
            selected += timedelta(days=1)
        rows.append(service.create(BookingInput(facility=f.id, date=selected.isoformat(), start_minute=start,
                    duration=duration, party=min(2, f.max_party), alias=f"合成演示访客 {i+1}",
                    guardian_confirmed=f.guardian_required, agreement_accepted=True,
                    agreement_version=service.s.site.agreement_version)))
    write_private(path, json.dumps(rows, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Initialize/run a loopback-only synthetic booking demo")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--seed", action="store_true", help="Add up to three synthetic bookings to an empty demo")
    parser.add_argument("--init-only", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    settings = prepare(args.port)
    app = create_app(settings)
    if args.seed:
        seed(app.state.service)
    print("Local candidate ready:", settings.base_url, flush=True)
    print("Synthetic staff credentials: runtime/demo-access.txt (private; do not publish)", flush=True)
    if not args.init_only:
        # Deliberately no external-host or proxy option. Access logging is disabled to avoid URL token leaks.
        uvicorn.run(app, host="127.0.0.1", port=args.port, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
