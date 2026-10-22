"""Booking/check-in core adapted from the allowlisted source; see PROVENANCE.md."""
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .database import Database, audit, row_one, rows_all
from .security import digest, read_ticket, ticket_for


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def nonblank(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("blank values are not accepted")
        return value


class BookingInput(Input):
    facility: str = Field(min_length=1, max_length=40)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    start_minute: int = Field(ge=0, lt=1440)
    duration: int = Field(ge=15, le=360)
    party: int = Field(ge=1, le=100)
    alias: str = Field(min_length=1, max_length=60)
    guardian_confirmed: bool = False
    agreement_accepted: bool
    agreement_version: str = Field(max_length=40)


class OwnerInput(Input):
    booking_id: str = Field(min_length=1, max_length=40)
    management_secret: str = Field(min_length=32, max_length=100)


class CancelRequest(OwnerInput):
    reason: str = Field(min_length=1, max_length=200)


class TicketInput(Input):
    ticket: str = Field(min_length=20, max_length=500)


class CheckinInput(TicketInput):
    arrived: int = Field(ge=1, le=100)
    guardian_present: bool = False
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{16,80}$")


class UndoInput(Input):
    checkin_id: str = Field(min_length=1, max_length=40)
    reason: str = Field(min_length=1, max_length=200)


class AdminBookingInput(Input):
    booking_id: str = Field(min_length=1, max_length=40)
    reason: str = Field(min_length=1, max_length=200)


class CapacityInput(Input):
    resource: str = Field(min_length=1, max_length=40)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    start_minute: int = Field(ge=0, lt=1440)
    duration: int = Field(ge=15, le=1440)
    capacity: int = Field(ge=0, le=100)
    reason: str = Field(min_length=1, max_length=200)


class Service:
    def __init__(self, settings):
        self.s = settings
        self.db = Database(settings.database)
        self.facilities = {f.id: f for f in settings.site.facilities}
        # One-process candidate: restart revokes all demo staff sessions, preserving bookings.
        with self.db.connect(write=True) as db:
            db.execute("DELETE FROM sessions")

    def day(self, value):
        try:
            return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=ZoneInfo(self.s.site.timezone))
        except ValueError:
            raise HTTPException(400, "日期格式不正确") from None

    def date_window(self, value):
        day = self.day(value)
        today = self.s.now().date()
        if not today <= day.date() <= today + timedelta(days=self.s.site.booking_days):
            raise HTTPException(400, "日期不在开放预约范围内")
        return day

    def segments(self, start, duration):
        site = self.s.site
        if (start < site.open_minute or start + duration > site.close_minute
                or start % site.slot_minutes or duration % site.slot_minutes or duration <= 0):
            raise HTTPException(400, "时段不在开放时间内或未对齐预约间隔")
        return list(range(start, start + duration, site.slot_minutes))

    def facility(self, key):
        if key not in self.facilities:
            raise HTTPException(400, "设施不存在")
        return self.facilities[key]

    def validate(self, p):
        f = self.facility(p.facility)
        day = self.date_window(p.date)
        if p.duration not in f.durations or p.party > f.max_party:
            raise HTTPException(400, "时长或人数超出设施规则")
        slots = self.segments(p.start_minute, p.duration)
        if day + timedelta(minutes=p.start_minute) < self.s.now():
            raise HTTPException(400, "不能预约已经开始的时段")
        return f, slots

    def reserved(self, db, resource, day, minute):
        return db.execute("""SELECT COALESCE(SUM(s.party),0) FROM slots s
            JOIN bookings b ON b.id=s.booking_id
            WHERE s.resource=? AND s.booking_date=? AND s.start_minute=? AND b.status='active'""",
            (resource, day, minute)).fetchone()[0]

    def capacity(self, db, resource, day, minute):
        row = db.execute("SELECT capacity FROM overrides WHERE resource=? AND booking_date=? AND start_minute=?",
                         (resource, day, minute)).fetchone()
        return row[0] if row else self.s.site.resources[resource]

    def availability(self, facility, date, duration):
        f = self.facility(facility)
        day = self.date_window(date)
        if duration not in f.durations:
            raise HTTPException(400, "时长不符合设施规则")
        result = []
        with self.db.connect() as db:
            for start in range(self.s.site.open_minute, self.s.site.close_minute - duration + 1, self.s.site.slot_minutes):
                if day + timedelta(minutes=start) < self.s.now():
                    continue
                available = min(self.capacity(db, f.resource, date, m) - self.reserved(db, f.resource, date, m)
                                for m in self.segments(start, duration))
                result.append({"start_minute": start, "remaining": max(0, min(available, f.max_party))})
        return {"slots": result}

    def create(self, p):
        f, slots = self.validate(p)
        if not p.agreement_accepted or p.agreement_version != self.s.site.agreement_version:
            raise HTTPException(409, "请阅读并同意当前版本的演示提示")
        if not p.alias.strip():
            raise HTTPException(400, "请填写合成称呼")
        if f.guardian_required and not p.guardian_confirmed:
            raise HTTPException(400, "该设施需要确认成年监护人陪同")
        booking_id, secret = str(uuid.uuid4()), secrets.token_urlsafe(32)
        with self.db.connect(write=True) as db:
            for m in slots:
                if self.reserved(db, f.resource, p.date, m) + p.party > self.capacity(db, f.resource, p.date, m):
                    raise HTTPException(409, "所选时段容量不足，请重新选择")
            db.execute("""INSERT INTO bookings(id,management_hash,alias,facility,resource,booking_date,
                start_minute,duration,party,guardian_required,status,agreement_version,agreement_hash,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,'active',?,?,?)""",
                (booking_id, digest(secret), p.alias.strip(), f.id, f.resource, p.date, p.start_minute,
                 p.duration, p.party, int(f.guardian_required), p.agreement_version,
                 digest(self.s.site.agreement_text), self.s.now().isoformat()))
            for m in slots:
                db.execute("INSERT INTO slots VALUES(?,?,?,?,?)", (booking_id, f.resource, p.date, m, p.party))
            audit(db, self.s, "booking_created", "customer", booking_id)
            row = row_one(db, "SELECT * FROM bookings WHERE id=?", (booking_id,))
        return {"booking": self.view(row), "management_secret": secret,
                "ticket": self.ticket(row), "warning": "保管凭证只返回一次。请下载到安全位置；不支持手机号找回。"}

    def bounds(self, row):
        start = self.day(row["booking_date"]) + timedelta(minutes=row["start_minute"])
        return start, start + timedelta(minutes=row["duration"])

    def ticket(self, row):
        expires = int((self.bounds(row)[1] + timedelta(hours=24)).timestamp())
        return ticket_for(self.s.signing_key, row["id"], row["ticket_version"], expires)

    def view(self, row, private=True):
        keys = ["id", "facility", "booking_date", "start_minute", "duration", "party", "status", "checked_in", "guardian_required"]
        result = {k: row[k] for k in keys}
        result["facility_name"] = self.facilities.get(row["facility"]).name if row["facility"] in self.facilities else row["facility"]
        if private:
            result.update(alias=row["alias"], cancel_requested=bool(row["cancel_requested"]), agreement_version=row["agreement_version"])
        return result

    def owner_row(self, db, p):
        row = row_one(db, "SELECT * FROM bookings WHERE id=?", (p.booking_id,))
        expected = row["management_hash"] if row else "0" * 64
        valid = hmac.compare_digest(digest(p.management_secret), expected)
        if not valid or not row:
            raise HTTPException(404, "预约或保管凭证不正确")
        return row

    def lookup(self, p):
        with self.db.connect() as db:
            row = self.owner_row(db, p)
        return {"booking": self.view(row), "ticket": self.ticket(row) if row["status"] == "active" else None}

    def ticket_row(self, db, ticket):
        booking_id, version, _ = read_ticket(self.s.signing_key, ticket, int(self.s.now().timestamp()))
        row = row_one(db, "SELECT * FROM bookings WHERE id=?", (booking_id,))
        if not row or row["status"] != "active" or row["ticket_version"] != version:
            raise HTTPException(404, "凭证无效或已过期")
        return row

    def inspect(self, p, staff=False):
        with self.db.connect() as db:
            row = self.ticket_row(db, p.ticket)
        start, end = self.bounds(row)
        within = start - timedelta(minutes=self.s.site.checkin_early_minutes) <= self.s.now() < end
        return {"booking": self.view(row, private=staff), "can_checkin": within and row["checked_in"] < row["party"]}

    def cancel(self, p):
        with self.db.connect(write=True) as db:
            row = self.owner_row(db, p)
            if row["status"] == "cancelled":
                return {"status": "cancelled"}
            if row["checked_in"]:
                raise HTTPException(409, "已有核验记录，不能自行取消")
            if self.bounds(row)[0] - self.s.now() < timedelta(minutes=self.s.site.cancel_notice_minutes):
                raise HTTPException(409, "已超过自行取消时限，可提交管理员处理申请")
            db.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (row["id"],))
            audit(db, self.s, "customer_cancelled", "customer", row["id"])
        return {"status": "cancelled"}

    def request_cancel(self, p):
        with self.db.connect(write=True) as db:
            row = self.owner_row(db, p)
            if row["status"] != "active":
                raise HTTPException(409, "预约已取消")
            if not row["cancel_requested"]:
                db.execute("UPDATE bookings SET cancel_requested=1 WHERE id=?", (row["id"],))
                audit(db, self.s, "cancel_requested", "customer", row["id"], reason=p.reason)
        return {"status": "pending", "message": "申请已记录，尚未取消，容量尚未释放"}

    def checkin(self, p, actor):
        payload_hash = digest(json.dumps(p.model_dump(), sort_keys=True))
        with self.db.connect(write=True) as db:
            row = self.ticket_row(db, p.ticket)
            existing = row_one(db, "SELECT * FROM checkins WHERE request_id=?", (p.request_id,))
            if existing:
                if existing["payload_hash"] != payload_hash or existing["actor"] != actor or existing["undone_at"]:
                    raise HTTPException(409, "请求编号已使用或对应核验已撤销")
                return {"checkin_id": existing["id"], "checked_in": existing["total"], "duplicate": True}
            start, end = self.bounds(row)
            if not start - timedelta(minutes=self.s.site.checkin_early_minutes) <= self.s.now() < end:
                raise HTTPException(409, "不在允许核验的时间窗口内")
            if row["guardian_required"] and not p.guardian_present:
                raise HTTPException(400, "请现场确认成年监护人在场")
            total = row["checked_in"] + p.arrived
            if total > row["party"]:
                raise HTTPException(409, "累计核验人数不能超过预约人数")
            checkin_id = str(uuid.uuid4())
            db.execute("UPDATE bookings SET checked_in=? WHERE id=?", (total, row["id"]))
            db.execute("INSERT INTO checkins VALUES(?,?,?,?,?,?,?,?,NULL)",
                       (checkin_id, row["id"], p.request_id, payload_hash, p.arrived, total, actor, self.s.now().isoformat()))
            audit(db, self.s, "checkin", actor, row["id"], checkin_id=checkin_id, added=p.arrived)
        return {"checkin_id": checkin_id, "checked_in": total, "duplicate": False}

    def undo(self, p, actor):
        with self.db.connect(write=True) as db:
            event = row_one(db, "SELECT * FROM checkins WHERE id=?", (p.checkin_id,))
            if not event:
                raise HTTPException(404, "核验记录不存在")
            if event["undone_at"]:
                return {"undone": True, "duplicate": True}
            db.execute("UPDATE bookings SET checked_in=checked_in-? WHERE id=?", (event["added"], event["booking_id"]))
            db.execute("UPDATE checkins SET undone_at=? WHERE id=?", (self.s.now().isoformat(), event["id"]))
            audit(db, self.s, "checkin_undone", actor, event["booking_id"], checkin_id=event["id"], reason=p.reason)
        return {"undone": True, "duplicate": False}

    def admin_cancel(self, p, actor):
        with self.db.connect(write=True) as db:
            row = row_one(db, "SELECT * FROM bookings WHERE id=?", (p.booking_id,))
            if not row:
                raise HTTPException(404, "预约不存在")
            if row["checked_in"]:
                raise HTTPException(409, "请先纠正到场记录，再取消预约")
            if row["status"] != "cancelled":
                db.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (row["id"],))
                audit(db, self.s, "admin_cancelled", actor, row["id"], reason=p.reason)
        return {"status": "cancelled"}

    def revoke_ticket(self, p, actor):
        with self.db.connect(write=True) as db:
            row = row_one(db, "SELECT * FROM bookings WHERE id=? AND status='active'", (p.booking_id,))
            if not row:
                raise HTTPException(404, "有效预约不存在")
            db.execute("UPDATE bookings SET ticket_version=ticket_version+1 WHERE id=?", (row["id"],))
            audit(db, self.s, "ticket_rotated", actor, row["id"], reason=p.reason)
        return {"rotated": True, "message": "旧二维码已失效，预约人可凭保管凭证取得新二维码"}

    def set_capacity(self, p, actor):
        if p.resource not in self.s.site.resources:
            raise HTTPException(400, "资源不存在")
        self.date_window(p.date)
        slots = self.segments(p.start_minute, p.duration)
        with self.db.connect(write=True) as db:
            if any(self.reserved(db, p.resource, p.date, m) > p.capacity for m in slots):
                raise HTTPException(409, "新容量不能低于已预约人数")
            for m in slots:
                db.execute("INSERT INTO overrides VALUES(?,?,?,?) ON CONFLICT(resource,booking_date,start_minute) DO UPDATE SET capacity=excluded.capacity",
                           (p.resource, p.date, m, p.capacity))
            audit(db, self.s, "capacity_changed", actor, resource=p.resource, date=p.date,
                  start=p.start_minute, duration=p.duration, capacity=p.capacity, reason=p.reason)
        return {"updated_slots": len(slots)}

    def admin_bookings(self, date):
        self.day(date)
        with self.db.connect() as db:
            rows = rows_all(db, "SELECT * FROM bookings WHERE booking_date=? ORDER BY start_minute,id LIMIT 500", (date,))
        return {"bookings": [self.view(r) for r in rows], "limit": 500}

    def audit_log(self):
        with self.db.connect() as db:
            return {"events": rows_all(db, "SELECT * FROM audit ORDER BY id DESC LIMIT 200")}
