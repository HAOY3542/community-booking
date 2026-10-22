import json
import secrets
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.testclient import TestClient

from community_booking.app import create_app
from community_booking.backup import snapshot
from community_booking.config import Settings, SiteConfig
from community_booking.database import Database
from community_booking.security import digest, hash_password, read_ticket
from community_booking.service import BookingInput, CheckinInput, Service

ROOT = Path(__file__).resolve().parent.parent
ORIGIN = "http://127.0.0.1:8765"


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_password = secrets.token_urlsafe(24)
        cls.verifier_password = secrets.token_urlsafe(24)
        cls.accounts = {
            "demo-admin": {"role": "admin", "password_hash": hash_password(cls.admin_password)},
            "demo-verifier": {"role": "verifier", "password_hash": hash_password(cls.verifier_password)},
        }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="community-booking-test-")
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2030, 1, 10, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.settings = Settings(Path(self.temp.name) / "test.sqlite3", secrets.token_bytes(32), ORIGIN,
                                 SiteConfig.model_validate_json((ROOT / "config.example.json").read_text()),
                                 self.accounts, lambda: self.now)
        self.app = create_app(self.settings)
        self.service = self.app.state.service
        self.client = self.new_client()

    def new_client(self):
        client = TestClient(self.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
        self.addCleanup(client.close)
        return client

    def payload(self, **changes):
        p = dict(facility="table-games", date="2030-01-11", start_minute=600, duration=60,
                 party=2, alias="合成访客 A", guardian_confirmed=False,
                 agreement_accepted=True, agreement_version="demo-1")
        return {**p, **changes}

    def booking(self, **changes):
        response = self.client.post("/api/bookings", json=self.payload(**changes))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def owner(self, booking):
        return {"booking_id": booking["booking"]["id"], "management_secret": booking["management_secret"]}

    def login(self, role="admin", client=None):
        client = client or self.client
        r = client.post("/api/auth/login", json={"username": "demo-"+role,
                        "password": self.admin_password if role == "admin" else self.verifier_password})
        self.assertEqual(r.status_code, 200, r.text)
        return r

    def checkin_payload(self, booking, **changes):
        return {"ticket": booking["ticket"], "arrived": 1, "guardian_present": False,
                "request_id": secrets.token_urlsafe(18), **changes}

    def at_checkin(self):
        self.now = self.now.replace(day=11, hour=9, minute=45)

    def test_create_and_owner_recovery_separate_from_public_ticket(self):
        b = self.booking()
        self.assertGreaterEqual(len(b["management_secret"]), 40)
        r = self.client.post("/api/bookings/lookup", json=self.owner(b))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["booking"]["alias"], "合成访客 A")
        public = self.client.post("/api/ticket", json={"ticket": b["ticket"]})
        self.assertEqual(public.status_code, 200)
        for forbidden in ["alias", "management_secret", "management_hash"]:
            self.assertNotIn(forbidden, public.text)

    def test_phone_only_lookup_is_rejected(self):
        self.booking()
        r = self.client.post("/api/bookings/lookup", json={"phone": "synthetic-number"})
        self.assertEqual(r.status_code, 422)
        self.assertNotIn("synthetic-number", r.text)

    def test_wrong_owner_unknown_id_and_ticket_cannot_manage(self):
        b = self.booking()
        bad = {**self.owner(b), "management_secret": secrets.token_urlsafe(32)}
        r = self.client.post("/api/bookings/lookup", json=bad)
        q = self.client.post("/api/bookings/lookup", json={**bad, "booking_id": "unknown"})
        self.assertEqual((r.status_code, r.json()), (q.status_code, q.json()))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client.post("/api/bookings/cancel", json={"ticket": b["ticket"]}).status_code, 422)

    def test_missing_or_short_signing_key_refuses_startup(self):
        for key in [b"", b"short", None]:
            with self.subTest(key_present=bool(key)), self.assertRaises(ValueError):
                replace(self.settings, signing_key=key)

    def test_public_origin_is_restricted_to_loopback(self):
        for url in ["http://0.0.0.0:8765", "https://example.invalid:443", "http://127.0.0.1:8765/path", "http://user@localhost:8765"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                replace(self.settings, base_url=url)

    def test_tampered_expired_and_cross_instance_ticket_rejected(self):
        b = self.booking()
        for ticket in [b["ticket"][:-1]+("0" if b["ticket"][-1] != "0" else "1"), "x"*30]:
            self.assertEqual(self.client.post("/api/ticket", json={"ticket": ticket}).status_code, 404)
        with self.assertRaises(HTTPException):
            read_ticket(secrets.token_bytes(32), b["ticket"], int(self.now.timestamp()))
        self.now += timedelta(days=3)
        self.assertEqual(self.client.post("/api/ticket", json={"ticket": b["ticket"]}).status_code, 404)

    def test_qr_is_svg_and_contains_no_personal_or_owner_fields(self):
        b = self.booking()
        r = self.client.post("/api/ticket/qr", json={"ticket": b["ticket"]})
        self.assertEqual(r.status_code, 200)
        self.assertIn("image/svg+xml", r.headers["content-type"])
        self.assertIn(b"<svg", r.content)
        self.assertNotIn(b["management_secret"].encode(), r.content)
        self.assertNotIn("合成访客".encode(), r.content)

    def test_shared_resource_capacity_and_overlap(self):
        self.booking(party=3)
        r = self.client.post("/api/bookings", json=self.payload(facility="conversation", start_minute=630, party=2))
        self.assertEqual(r.status_code, 409)
        self.booking(facility="conversation", start_minute=660, party=4)
        self.booking(facility="reading", party=6)

    def test_concurrent_last_capacity_only_one_wins(self):
        p = BookingInput(**self.payload(party=4))
        def reserve(_):
            try:
                self.service.create(p)
                return 201
            except HTTPException as e:
                return e.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(reserve, range(2))), [201, 409])
        with self.service.db.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0], 1)

    def test_cancel_releases_capacity_and_revokes_qr_idempotently(self):
        b = self.booking(party=4)
        for _ in range(2):
            self.assertEqual(self.client.post("/api/bookings/cancel", json=self.owner(b)).status_code, 200)
        self.assertEqual(self.client.post("/api/ticket", json={"ticket": b["ticket"]}).status_code, 404)
        self.booking(party=4)

    def test_late_cancel_request_does_not_release_capacity(self):
        b = self.booking(party=4)
        self.at_checkin()
        self.assertEqual(self.client.post("/api/bookings/cancel", json=self.owner(b)).status_code, 409)
        p = {**self.owner(b), "reason": "合成临时安排变更"}
        for _ in range(2):
            self.assertEqual(self.client.post("/api/bookings/cancel-request", json=p).json()["status"], "pending")
        self.assertEqual(self.client.post("/api/bookings", json=self.payload(party=1)).status_code, 409)
        with self.service.db.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit WHERE kind='cancel_requested'").fetchone()[0], 1)

    def test_validation_rejects_past_misaligned_invalid_or_unaccepted_booking(self):
        for change in [{"date":"2030-01-09"}, {"date":"2030-03-01"}, {"date":"2030-02-30"},
                       {"start_minute":601}, {"duration":45}, {"party":5}, {"facility":"unknown"},
                       {"agreement_accepted":False}, {"agreement_version":"old"}, {"alias":"   "},
                       {"date":"2030-01-10", "start_minute":480}, {"party":True}]:
            with self.subTest(change=change):
                self.assertIn(self.client.post("/api/bookings", json=self.payload(**change)).status_code, [400,409,422])

    def test_guardian_required_at_booking_and_each_checkin(self):
        self.assertEqual(self.client.post("/api/bookings", json=self.payload(facility="family")).status_code, 400)
        b = self.booking(facility="family", guardian_confirmed=True)
        self.at_checkin(); self.login("verifier")
        p = self.checkin_payload(b)
        self.assertEqual(self.client.post("/api/staff/checkin", json=p).status_code, 400)
        self.assertEqual(self.client.post("/api/staff/checkin", json={**p, "guardian_present":True}).status_code, 200)

    def test_authentication_role_separation_and_cookie_flags(self):
        self.assertEqual(self.client.get("/api/admin/bookings?date=2030-01-11").status_code, 401)
        r = self.login("verifier")
        self.assertIn("HttpOnly", r.headers["set-cookie"])
        self.assertIn("SameSite=strict", r.headers["set-cookie"])
        self.assertEqual(self.client.get("/api/admin/audit").status_code, 403)
        self.assertEqual(self.client.post("/api/admin/capacity", json={"resource":"shared-room", "date":"2030-01-11",
                         "start_minute":600,"duration":60,"capacity":0,"reason":"合成关闭"}).status_code, 403)
        b = self.booking()
        self.assertEqual(self.client.post("/api/staff/inspect", json={"ticket":b["ticket"]}).status_code, 200)

    def test_checkin_window_partial_duplicate_conflicting_key_and_upper_bound(self):
        b = self.booking(); self.login("verifier")
        p = self.checkin_payload(b)
        self.assertEqual(self.client.post("/api/staff/checkin", json=p).status_code, 409)
        self.at_checkin()
        self.login("verifier")  # Advancing one day intentionally expires the previous four-hour session.
        r = self.client.post("/api/staff/checkin", json=p)
        self.assertEqual(r.status_code, 200)
        repeated = self.client.post("/api/staff/checkin", json=p)
        self.assertTrue(repeated.json()["duplicate"])
        self.assertEqual(repeated.json()["checked_in"], 1)
        self.assertEqual(self.client.post("/api/staff/checkin", json={**p,"arrived":2}).status_code, 409)
        self.assertEqual(self.client.post("/api/staff/checkin", json=self.checkin_payload(b)).json()["checked_in"], 2)
        self.assertEqual(self.client.post("/api/staff/checkin", json=self.checkin_payload(b)).status_code, 409)
        self.assertEqual(self.client.post("/api/bookings/cancel", json=self.owner(b)).status_code, 409)

    def test_concurrent_checkin_never_overcounts(self):
        b = self.booking(party=1); self.at_checkin()
        def confirm(_):
            try:
                self.service.checkin(CheckinInput(**self.checkin_payload(b)), "demo-verifier")
                return 200
            except HTTPException as e:
                return e.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(confirm, range(2))), [200,409])

    def test_admin_undo_audited_and_original_request_cannot_reapply(self):
        b = self.booking(); self.at_checkin(); self.login("verifier")
        p = self.checkin_payload(b)
        result = self.client.post("/api/staff/checkin", json=p).json()
        undo = {"checkin_id":result["checkin_id"],"reason":"合成误操作纠正"}
        self.assertEqual(self.client.post("/api/admin/checkin/undo", json=undo).status_code, 403)
        self.login()
        self.assertEqual(self.client.post("/api/admin/checkin/undo", json=undo).status_code, 200)
        self.assertTrue(self.client.post("/api/admin/checkin/undo", json=undo).json()["duplicate"])
        self.assertEqual(self.client.post("/api/bookings/lookup", json=self.owner(b)).json()["booking"]["checked_in"], 0)
        self.assertEqual(self.client.post("/api/staff/checkin", json=p).status_code, 409)
        events = self.client.get("/api/admin/audit").json()["events"]
        self.assertTrue(any(e["kind"] == "checkin_undone" for e in events))

    def test_admin_cancel_requires_reason_and_cannot_discard_arrivals(self):
        b = self.booking(); self.at_checkin(); self.login()
        p = {"booking_id":b["booking"]["id"], "reason":"合成管理员取消"}
        self.assertEqual(self.client.post("/api/admin/bookings/cancel", json={**p,"reason":" "}).status_code, 422)
        event = self.client.post("/api/staff/checkin", json=self.checkin_payload(b)).json()
        self.assertEqual(self.client.post("/api/admin/bookings/cancel", json=p).status_code, 409)
        self.client.post("/api/admin/checkin/undo", json={"checkin_id":event["checkin_id"],"reason":"合成纠正"})
        self.assertEqual(self.client.post("/api/admin/bookings/cancel", json=p).status_code, 200)

    def test_admin_rotation_invalidates_old_ticket_owner_can_get_new(self):
        b = self.booking(); self.login()
        r = self.client.post("/api/admin/ticket/rotate", json={"booking_id":b["booking"]["id"],"reason":"合成凭证遗失"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.post("/api/ticket", json={"ticket":b["ticket"]}).status_code, 404)
        new = self.client.post("/api/bookings/lookup", json=self.owner(b)).json()
        self.assertNotEqual(new["ticket"], b["ticket"])
        self.assertEqual(self.client.post("/api/ticket", json={"ticket":new["ticket"]}).status_code, 200)

    def test_capacity_override_respects_existing_and_closes_all_shared_facilities(self):
        b = self.booking(); self.login()
        p = {"resource":"shared-room","date":"2030-01-11","start_minute":600,"duration":60,"capacity":1,"reason":"合成调整"}
        self.assertEqual(self.client.post("/api/admin/capacity", json=p).status_code, 409)
        self.assertEqual(self.client.post("/api/admin/capacity", json={**p,"capacity":2}).status_code, 200)
        self.client.post("/api/bookings/cancel", json=self.owner(b))
        self.assertEqual(self.client.post("/api/admin/capacity", json={**p,"capacity":0}).status_code, 200)
        self.assertEqual(self.client.post("/api/bookings", json=self.payload(facility="conversation")).status_code, 409)

    def test_csrf_host_json_and_unknown_field_guards(self):
        self.assertEqual(self.client.get("/api/config", headers={"Host":"attacker.invalid"}).status_code, 400)
        self.assertEqual(self.client.post("/api/bookings", json=self.payload(), headers={"Origin":"https://attacker.invalid"}).status_code, 403)
        self.assertEqual(self.client.post("/api/bookings", json=self.payload(), headers={"Origin":""}).status_code, 403)
        self.assertEqual(self.client.post("/api/bookings", content="x").status_code, 415)
        self.assertEqual(self.client.post("/api/bookings", json={**self.payload(),"phone":"not accepted"}).status_code, 422)
        self.assertEqual(self.client.post("/api/bookings", content=b'"'+b'x'*17000+b'"',headers={"Content-Type":"application/json"}).status_code, 413)

    def test_password_failures_limited_validation_does_not_echo_password(self):
        p = {"username":"demo-admin", "password":secrets.token_urlsafe(24)}
        for _ in range(10):
            self.assertEqual(self.client.post("/api/auth/login", json=p).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json=p).status_code, 429)
        invalid = p["password"]*10
        r = self.client.post("/api/auth/login", json={**p,"password":invalid})
        self.assertEqual(r.status_code, 422)
        self.assertNotIn(p["password"], r.text)

    def test_logout_expiry_restart_and_recovery_persistence(self):
        b = self.booking(); self.login()
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)
        self.client.post("/api/auth/logout", json={})
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.login(); self.now += timedelta(hours=5)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.login()
        restarted = create_app(self.settings)
        with TestClient(restarted, base_url=ORIGIN, headers={"Origin":ORIGIN}) as client:
            client.cookies.update(self.client.cookies)
            self.assertEqual(client.get("/api/auth/me").status_code, 401)
            self.assertEqual(client.post("/api/bookings/lookup", json=self.owner(b)).status_code, 200)

    def test_secrets_are_hashed_and_never_written_to_audit(self):
        b = self.booking(); self.login()
        raw_cookie = self.client.cookies.get("cb_session")
        with self.service.db.connect() as db:
            self.assertEqual(db.execute("SELECT management_hash FROM bookings").fetchone()[0], digest(b["management_secret"]))
            self.assertEqual(db.execute("SELECT token_hash FROM sessions").fetchone()[0], digest(raw_cookie))
            records = str(db.execute("SELECT * FROM audit").fetchall())
            for secret in [b["management_secret"], raw_cookie, self.admin_password]:
                self.assertNotIn(secret, records)

    def test_backup_restore_integrity_owner_recovery_and_no_overwrite(self):
        b = self.booking()
        backup = Path(self.temp.name) / "backup.sqlite3"
        self.assertEqual(snapshot(self.settings.database, backup), 1)
        with self.assertRaises(ValueError):
            snapshot(self.settings.database, backup)
        restored = Service(replace(self.settings, database=backup))
        from community_booking.service import OwnerInput
        self.assertEqual(restored.lookup(OwnerInput(**self.owner(b)))["booking"]["id"], b["booking"]["id"])

    def test_unrelated_database_refused_without_content_changes(self):
        path = Path(self.temp.name) / "unrelated.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE unrelated(value TEXT)")
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            Database(path)
        self.assertEqual(path.read_bytes(), before)

    def test_response_headers_static_assets_and_runtime_not_exposed(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.headers["referrer-policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", r.headers["content-security-policy"])
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        for path in ["/runtime/demo-access.txt", "/runtime/demo.sqlite3", "/docs", "/openapi.json"]:
            self.assertEqual(self.client.get(path).status_code, 404)


if __name__ == "__main__":
    unittest.main()
