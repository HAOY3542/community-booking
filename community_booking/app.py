import io
import secrets
from pathlib import Path
from urllib.parse import urlsplit

import qrcode
import qrcode.image.svg
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .database import audit, row_one
from .security import RateLimiter, digest, hash_password, verify_password
from .service import (AdminBookingInput, BookingInput, CancelRequest, CapacityInput, CheckinInput,
                      Input, OwnerInput, Service, TicketInput, UndoInput)
from pydantic import Field


class LoginInput(Input):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=128)


def create_app(settings):
    service = Service(settings)
    limiter = RateLimiter()
    app = FastAPI(title="Community booking local candidate", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    origin = settings.base_url
    expected_host = urlsplit(origin).netloc
    dummy_password_hash = hash_password(secrets.token_urlsafe(32))

    @app.middleware("http")
    async def guards(request: Request, call_next):
        if request.headers.get("host") != expected_host:
            return JSONResponse({"detail": "只接受配置的本机 Host"}, status_code=400)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("origin") != origin or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "请求来源不受信任"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "只接受 JSON 请求"}, status_code=415)
            body = await request.body()
            if len(body) > 16384:
                return JSONResponse({"detail": "请求过大"}, status_code=413)
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        })
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default response includes raw inputs; never echo credentials.
        return JSONResponse({"detail": "请求字段格式不正确", "fields": [list(e["loc"]) for e in exc.errors()]}, status_code=422)

    def limited(request, name, count=30, window=900):
        limiter.check(f"{name}:{request.client.host if request.client else 'local'}", count, window)

    def staff(request, roles=("admin", "verifier")):
        token = request.cookies.get("cb_session", "")
        with service.db.connect() as db:
            row = row_one(db, "SELECT * FROM sessions WHERE token_hash=? AND expires>?", (digest(token), int(settings.now().timestamp())))
        if not row or row["username"] not in settings.accounts or settings.accounts[row["username"]]["role"] != row["role"]:
            raise HTTPException(401, "请先登录工作人员账户")
        if row["role"] not in roles:
            raise HTTPException(403, "当前角色无此权限")
        return row

    @app.get("/api/config")
    def config():
        return {**settings.site.model_dump(), "today": settings.now().date().isoformat(), "now": settings.now().isoformat(), "local_demo": True}

    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": "local-review"}

    @app.get("/api/availability")
    def availability(facility: str = Query(max_length=40), date: str = Query(max_length=10), duration: int = Query(ge=15, le=360)):
        return service.availability(facility, date, duration)

    @app.post("/api/bookings", status_code=201)
    def create(payload: BookingInput, request: Request):
        limited(request, "create", 20, 60)
        return service.create(payload)

    @app.post("/api/bookings/lookup")
    def lookup(payload: OwnerInput, request: Request):
        limited(request, "owner")
        return service.lookup(payload)

    @app.post("/api/bookings/cancel")
    def cancel(payload: OwnerInput, request: Request):
        limited(request, "cancel")
        return service.cancel(payload)

    @app.post("/api/bookings/cancel-request")
    def cancel_request(payload: CancelRequest, request: Request):
        limited(request, "cancel-request")
        return service.request_cancel(payload)

    @app.post("/api/ticket")
    def ticket(payload: TicketInput, request: Request):
        limited(request, "ticket", 120)
        return service.inspect(payload)

    @app.post("/api/ticket/qr")
    def qr(payload: TicketInput, request: Request):
        limited(request, "qr", 60)
        service.inspect(payload)
        picture = qrcode.make(origin + "/ticket#" + payload.ticket, image_factory=qrcode.image.svg.SvgPathImage, box_size=5, border=4)
        stream = io.BytesIO()
        picture.save(stream)
        return Response(stream.getvalue(), media_type="image/svg+xml")

    @app.post("/api/auth/login")
    def login(payload: LoginInput, request: Request, response: Response):
        limited(request, "login", 10)
        account = settings.accounts.get(payload.username)
        valid = verify_password(payload.password, account["password_hash"] if account else dummy_password_hash)
        if not account or not valid:
            raise HTTPException(401, "账号或密码不正确")
        token = secrets.token_urlsafe(32)
        expires = int(settings.now().timestamp()) + 4 * 60 * 60
        with service.db.connect(write=True) as db:
            db.execute("DELETE FROM sessions WHERE expires<=?", (int(settings.now().timestamp()),))
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (digest(token), payload.username, account["role"], expires))
            audit(db, settings, "staff_login", payload.username)
        response.set_cookie("cb_session", token, httponly=True, samesite="strict", secure=False, path="/", max_age=14400)
        return {"username": payload.username, "role": account["role"]}

    @app.get("/api/auth/me")
    def me(request: Request):
        account = staff(request)
        return {"username": account["username"], "role": account["role"]}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        with service.db.connect(write=True) as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(request.cookies.get("cb_session", "")),))
        response.delete_cookie("cb_session", path="/")
        return {"logged_out": True}

    @app.post("/api/staff/inspect")
    def inspect(payload: TicketInput, request: Request):
        staff(request)
        return service.inspect(payload, staff=True)

    @app.post("/api/staff/checkin")
    def checkin(payload: CheckinInput, request: Request):
        actor = staff(request)
        return service.checkin(payload, actor["username"])

    @app.get("/api/admin/bookings")
    def bookings(request: Request, date: str = Query(max_length=10)):
        staff(request, ("admin",))
        return service.admin_bookings(date)

    @app.get("/api/admin/audit")
    def audit_log(request: Request):
        staff(request, ("admin",))
        return service.audit_log()

    @app.post("/api/admin/checkin/undo")
    def undo(payload: UndoInput, request: Request):
        return service.undo(payload, staff(request, ("admin",))["username"])

    @app.post("/api/admin/bookings/cancel")
    def admin_cancel(payload: AdminBookingInput, request: Request):
        return service.admin_cancel(payload, staff(request, ("admin",))["username"])

    @app.post("/api/admin/ticket/rotate")
    def rotate(payload: AdminBookingInput, request: Request):
        return service.revoke_ticket(payload, staff(request, ("admin",))["username"])

    @app.post("/api/admin/capacity")
    def capacity(payload: CapacityInput, request: Request):
        return service.set_capacity(payload, staff(request, ("admin",))["username"])

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    @app.get("/ticket")
    def index():
        return FileResponse(static / "index.html")

    return app
