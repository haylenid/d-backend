"""Progres Klien - Haylen Swimming Club (app ke-6 d-backend).

Port apa adanya dari backend mandiri (zip haylen-backend: 1 file FastAPI +
psycopg mentah). Didaftarkan sebagai sub-aplikasi di app/main.py dengan
prefix /api/haylen-notes/v1; FE (Vercel) memanggil prefix itu.

Sub-aplikasi (bukan router biasa) dipilih sengaja: FE membaca j.error untuk
pesan kesalahan, jadi exception handler {"error": ...} plus middleware
(pembatas 1 MB, security header) harus tetap seperti aslinya.

Penyesuaian integrasi (hanya ini yang diubah dari aslinya):
- Tanpa StaticFiles (FE disajikan Vercel, bukan dari backend ini).
- Tanpa load_dotenv: env dibaca dari app.core.config.settings (env d-backend).
- DATABASE_URL d-backend berdialek SQLAlchemy (postgresql+psycopg://) ->
  dipangkas ke DSN psycopg polos.
- Pool memakai search_path=haylen_notes (skema sendiri; tabel generik
  users/clients/meta/login_fails tidak bentrok dengan app lain).
- SECRET token = settings.JWT_SECRET (env d-backend), tanpa fallback meta.
- Owner bootstrap dari HAYLEN_NOTES_OWNER_* (settings); ID owner sama
  dengan app lain ("owner"), akun coach dibuat ulang di app ini.
  Penyimpangan dari aslinya: password default "owner123" mengikuti
  konvensi seed app lain, dan boot tidak pernah gagal karena password
  owner belum terisi (di app mandiri, boot menolak jalan; di backend
  gabungan, satu app tidak boleh menjatuhkan kelima app lain).
- Path endpoint tanpa awalan /api (prefix dipasang saat mount di main.py).
- init_db membuat skema haylen_notes bila belum ada.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time
from contextlib import contextmanager
from typing import Any

import psycopg
from fastapi import Body, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from starlette.exceptions import HTTPException

from app.core.config import settings

WINDOW = 15 * 60  # detik, batas waktu penguncian login
TOKEN_DAYS = 30
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
SCHEMA_NAME = "haylen_notes"


def _psycopg_dsn(url: str) -> str:
    """Pangkas sufiks dialek SQLAlchemy (+psycopg/+psycopg2) dari DSN."""
    if "://" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme.split('+', 1)[0]}://{rest}"
    return url


pool = ConnectionPool(
    _psycopg_dsn(settings.DATABASE_URL),
    min_size=1, max_size=int(os.environ.get("DB_POOL_MAX", "10")),
    kwargs={"row_factory": dict_row, "options": f"-c search_path={SCHEMA_NAME}"},
    open=False,
)
SECRET = settings.JWT_SECRET


def err(status: int, msg: str):
    return HTTPException(status_code=status, detail=msg)


@contextmanager
def db():
    with pool.connection() as conn:
        yield conn


# ---------------------------------------------------------------- kata sandi & token
def hash_pw(s: str) -> str:
    salt = os.urandom(16)
    return salt.hex() + ":" + hashlib.scrypt(s.encode(), salt=salt, n=16384, r=8, p=1, dklen=32).hex()


def check_pw(s: str, stored: str) -> bool:
    try:
        salt, h = stored.split(":")
        calc = hashlib.scrypt(s.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32).hex()
        return hmac.compare_digest(calc, h)
    except Exception:
        return False


DUMMY = hash_pw("dummy")


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(p: dict) -> str:
    body = b64(json.dumps(p, separators=(",", ":")).encode())
    return body + "." + b64(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())


def verify(token: str):
    try:
        body, sig = token.split(".")
        ok = b64(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, ok):
            return None
        p = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return p if p.get("exp", 0) > time.time() else None
    except Exception:
        return None


def new_token(uid: str, pass_hash: str) -> str:
    return sign({"id": uid, "exp": time.time() + TOKEN_DAYS * 86400, "pv": pass_hash[-12:]})


# ---------------------------------------------------------------- util
def s(v: Any, mx: int) -> str:
    return str("" if v is None else v).strip()[:mx]


def pub(u: dict) -> dict:
    return {"id": u["id"], "name": u["name"], "role": u["role"], "wa": u["wa"] or "", "email": u["email"] or ""}


def valid_secret(v: Any) -> bool:
    return isinstance(v, str) and 4 <= len(v) <= 64


def valid_email(v: str) -> bool:
    return not v or bool(EMAIL_RE.match(v))


def bump(conn):
    conn.execute("UPDATE meta SET v=(v::bigint+1)::text WHERE k='rev'")


def limited(conn, key: str, mx: int) -> bool:
    r = conn.execute("SELECT n,t FROM login_fails WHERE k=%s", (key,)).fetchone()
    return bool(r and r["n"] >= mx and time.time() - r["t"] < WINDOW)


def fail(conn, key: str):
    now = time.time()
    conn.execute(
        """INSERT INTO login_fails(k,n,t) VALUES(%s,1,%s)
           ON CONFLICT (k) DO UPDATE SET
             n = CASE WHEN %s - login_fails.t > %s THEN 1 ELSE login_fails.n + 1 END, t = %s""",
        (key, now, now, WINDOW, now),
    )
    conn.execute("DELETE FROM login_fails WHERE t < %s", (now - WINDOW,))


# ---------------------------------------------------------------- database
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY, name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('owner','coach')),
  wa TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
  active BOOLEAN NOT NULL DEFAULT TRUE, pass TEXT NOT NULL, created DATE NOT NULL DEFAULT CURRENT_DATE);
CREATE UNIQUE INDEX IF NOT EXISTS users_name_ci ON users (lower(name));
CREATE TABLE IF NOT EXISTS clients(
  id TEXT PRIMARY KEY, coach_id TEXT NOT NULL DEFAULT '', data JSONB NOT NULL,
  updated TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS clients_coach ON clients(coach_id);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_fails(k TEXT PRIMARY KEY, n INTEGER NOT NULL, t DOUBLE PRECISION NOT NULL);
INSERT INTO meta(k,v) VALUES('rev','1') ON CONFLICT DO NOTHING;
"""


def init_db():
    with db() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(7311)")  # aman bila dijalankan beberapa worker
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA_NAME}"')
        conn.execute(SCHEMA)
        pw = settings.HAYLEN_NOTES_OWNER_PASSWORD or ""
        owner = conn.execute("SELECT * FROM users WHERE role='owner'").fetchone()
        if not owner:
            if len(pw) < 8:
                # Sengaja TIDAK raise seperti aslinya: di backend gabungan,
                # satu app yang belum punya password owner tidak boleh
                # menjatuhkan kelima app lain. Owner dibuat saat
                # HAYLEN_NOTES_OWNER_PASSWORD valid (min 8 karakter) terisi.
                print("PERINGATAN: owner haylen_notes belum dibuat (HAYLEN_NOTES_OWNER_PASSWORD minimal 8 karakter).")
            else:
                conn.execute(
                    "INSERT INTO users(id,name,role,pass) VALUES(%s,%s,'owner',%s)",
                    (settings.HAYLEN_NOTES_OWNER_ID.lower(), settings.HAYLEN_NOTES_OWNER_NAME, hash_pw(pw)),
                )
                print("Akun owner haylen_notes dibuat.")
        elif len(pw) >= 8 and not check_pw(pw, owner["pass"]):
            conn.execute("UPDATE users SET pass=%s WHERE id=%s", (hash_pw(pw), owner["id"]))
            print("Kata sandi owner haylen_notes diperbarui dari HAYLEN_NOTES_OWNER_PASSWORD.")


def open_pool():
    """Buka pool + buat skema/tabel/owner.

    Dipanggil dari lifespan utama d-backend (app/main.py), BUKAN dari
    lifespan sub-aplikasi: lifespan aplikasi yang di-mount tidak dijalankan
    otomatis oleh Starlette, sehingga pool tidak pernah terbuka bila hanya
    mengandalkan lifespan di sini (terbukti saat uji lokal pertama).
    """
    pool.open(wait=True, timeout=30)
    init_db()


def close_pool():
    pool.close()


# Tanpa lifespan sendiri: open/close pool diurus lifespan utama (lihat atas).
app = FastAPI(title="Haylen Progres Klien", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def basic_guard(request: Request, call_next):
    if int(request.headers.get("content-length") or 0) > 1_000_000:
        return JSONResponse({"error": "Data terlalu besar."}, status_code=413)
    res = await call_next(request)
    res.headers["X-Content-Type-Options"] = "nosniff"
    res.headers["Referrer-Policy"] = "no-referrer"
    res.headers["X-Frame-Options"] = "DENY"
    return res


@app.exception_handler(HTTPException)
async def http_err(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail if isinstance(exc.detail, str) else "Terjadi kesalahan."}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def val_err(request: Request, exc: RequestValidationError):
    return JSONResponse({"error": "Data tidak valid."}, status_code=400)


@app.exception_handler(Exception)
async def any_err(request: Request, exc: Exception):
    print("ERROR:", repr(exc))
    return JSONResponse({"error": "Terjadi kesalahan di server."}, status_code=500)


# ---------------------------------------------------------------- autentikasi
def current_user(authorization: str = Header(default="")) -> dict:
    p = verify(authorization[7:] if authorization.startswith("Bearer ") else "")
    u = None
    if p:
        with db() as conn:
            u = conn.execute("SELECT id,name,role,wa,email,active,pass FROM users WHERE id=%s", (p.get("id"),)).fetchone()
    if not u or not u["active"] or p.get("pv") != u["pass"][-12:]:
        raise err(401, "Sesi berakhir. Silakan masuk lagi.")
    return u


def owner_only(u: dict = Depends(current_user)) -> dict:
    if u["role"] != "owner":
        raise err(403, "Hanya owner yang boleh melakukan ini.")
    return u


@app.post("/login")
def login(request: Request, b: dict = Body(None)):
    b = b or {}
    uid, secret = s(b.get("id"), 40).lower(), str(b.get("secret") or "")
    ip = request.client.host if request.client else "?"
    k1, k2 = f"{ip}|{uid}", f"id|{uid}"
    with db() as conn:
        if limited(conn, k1, 8) or limited(conn, k2, 40):
            raise err(429, "Terlalu banyak percobaan. Coba lagi 15 menit lagi.")
        u = conn.execute("SELECT * FROM users WHERE id=%s", (uid,)).fetchone()
        ok = check_pw(secret, u["pass"] if u else DUMMY)
        if not u or not u["active"] or not ok:
            fail(conn, k1)
            fail(conn, k2)
            conn.commit()
            raise err(401, "ID atau PIN salah.")
        conn.execute("DELETE FROM login_fails WHERE k=%s", (k1,))
    return {"token": new_token(u["id"], u["pass"]), "user": pub(u)}


@app.get("/me")
def me(u: dict = Depends(current_user)):
    return pub(u)


@app.patch("/me")
def update_me(b: dict = Body(None), u: dict = Depends(current_user)):
    b = b or {}
    name, email = s(b.get("name"), 80), s(b.get("email"), 120)
    if not name:
        raise err(400, "Nama tidak boleh kosong.")
    if not valid_email(email):
        raise err(400, "Format email belum benar.")
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE lower(name)=lower(%s) AND id<>%s", (name, u["id"])).fetchone():
            raise err(409, "Nama sudah dipakai anggota lain.")
        conn.execute("UPDATE users SET name=%s, wa=%s, email=%s WHERE id=%s", (name, s(b.get("wa"), 30), email, u["id"]))
        bump(conn)
    return {"ok": True}


@app.post("/me/secret")
def change_secret(b: dict = Body(None), u: dict = Depends(current_user)):
    b = b or {}
    if not check_pw(str(b.get("old") or ""), u["pass"]):
        raise err(401, "PIN lama salah.")
    if not valid_secret(b.get("secret")):
        raise err(400, "PIN baru minimal 4 karakter.")
    nh = hash_pw(b["secret"])
    with db() as conn:
        conn.execute("UPDATE users SET pass=%s WHERE id=%s", (nh, u["id"]))
    return {"ok": True, "token": new_token(u["id"], nh)}


@app.get("/rev")
def rev(u: dict = Depends(current_user)):
    with db() as conn:
        return {"rev": int(conn.execute("SELECT v FROM meta WHERE k='rev'").fetchone()["v"])}


# ---------------------------------------------------------------- anggota coach
@app.get("/coaches")
def list_coaches(u: dict = Depends(current_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT id,name,wa,email,active,created FROM users WHERE role='coach' ORDER BY lower(name)"
        ).fetchall()
    if u["role"] == "owner":
        return [{**r, "created": str(r["created"])} for r in rows]
    return [{"id": r["id"], "name": r["name"], "wa": "", "active": True} for r in rows if r["active"]]


@app.post("/coaches")
def create_coach(b: dict = Body(None), u: dict = Depends(owner_only)):
    b = b or {}
    cid, name, email = s(b.get("id"), 20).lower(), s(b.get("name"), 80), s(b.get("email"), 120)
    if not re.fullmatch(r"[a-z0-9._-]{3,20}", cid) or not name:
        raise err(400, "Isi ID (3-20 huruf/angka) dan nama.")
    if not valid_secret(b.get("secret")):
        raise err(400, "PIN minimal 4 karakter.")
    if not valid_email(email):
        raise err(400, "Format email belum benar.")
    try:
        with db() as conn:
            if conn.execute("SELECT 1 FROM users WHERE id=%s", (cid,)).fetchone():
                raise err(409, "ID sudah dipakai.")
            if conn.execute("SELECT 1 FROM users WHERE lower(name)=lower(%s)", (name,)).fetchone():
                raise err(409, "Nama sudah dipakai anggota lain.")
            conn.execute(
                "INSERT INTO users(id,name,role,wa,email,pass) VALUES(%s,%s,'coach',%s,%s,%s)",
                (cid, name, s(b.get("wa"), 30), email, hash_pw(b["secret"])),
            )
            bump(conn)
    except psycopg.errors.UniqueViolation:
        raise err(409, "ID atau nama sudah dipakai.")
    return {"ok": True}


@app.patch("/coaches/{cid}")
def update_coach(cid: str, b: dict = Body(None), u: dict = Depends(owner_only)):
    b = b or {}
    with db() as conn:
        c = conn.execute("SELECT * FROM users WHERE id=%s AND role='coach'", (cid,)).fetchone()
        if not c:
            raise err(404, "Anggota tidak ditemukan.")
        if "secret" in b:
            if not valid_secret(b["secret"]):
                raise err(400, "PIN minimal 4 karakter.")
            conn.execute("UPDATE users SET pass=%s WHERE id=%s", (hash_pw(b["secret"]), cid))
        if "active" in b:
            conn.execute("UPDATE users SET active=%s WHERE id=%s", (bool(b["active"]), cid))
        if "name" in b and s(b["name"], 80):
            try:
                conn.execute("UPDATE users SET name=%s WHERE id=%s", (s(b["name"], 80), cid))
            except psycopg.errors.UniqueViolation:
                raise err(409, "Nama sudah dipakai anggota lain.")
        if "wa" in b:
            conn.execute("UPDATE users SET wa=%s WHERE id=%s", (s(b["wa"], 30), cid))
        if "email" in b and valid_email(s(b["email"], 120)):
            conn.execute("UPDATE users SET email=%s WHERE id=%s", (s(b["email"], 120), cid))
        bump(conn)
    return {"ok": True}


@app.delete("/coaches/{cid}")
def delete_coach(cid: str, u: dict = Depends(owner_only)):
    with db() as conn:
        n = conn.execute("DELETE FROM users WHERE id=%s AND role='coach'", (cid,)).rowcount
        if not n:
            raise err(404, "Anggota tidak ditemukan.")
        bump(conn)
    return {"ok": True}


# ---------------------------------------------------------------- klien
@app.get("/clients")
def list_clients(u: dict = Depends(current_user)):
    with db() as conn:
        if u["role"] == "owner":
            rows = conn.execute("SELECT data FROM clients").fetchall()
        else:
            rows = conn.execute("SELECT data FROM clients WHERE coach_id=%s", (u["id"],)).fetchall()
    return [r["data"] for r in rows]


@app.put("/clients/{cid}")
def put_client(cid: str, d: dict = Body(None), u: dict = Depends(current_user)):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", cid) or not isinstance(d, dict) or d.get("id") != cid or not s(d.get("name"), 120):
        raise err(400, "Data klien tidak valid.")
    if d.get("sessions") is not None and (not isinstance(d["sessions"], list) or len(d["sessions"]) > 5000):
        raise err(400, "Data klien tidak valid.")
    if d.get("handovers") is not None and not isinstance(d["handovers"], list):
        raise err(400, "Data klien tidak valid.")
    with db() as conn:
        ex = conn.execute("SELECT coach_id FROM clients WHERE id=%s FOR UPDATE", (cid,)).fetchone()

        def coach(cid_, only_active=False):
            if not isinstance(cid_, str) or not cid_:
                return None
            sql = "SELECT id,name FROM users WHERE id=%s AND role='coach'" + (" AND active" if only_active else "")
            return conn.execute(sql, (cid_,)).fetchone()

        if u["role"] == "coach":
            if ex and ex["coach_id"] != u["id"]:
                raise err(403, "Klien ini bukan milik Anda.")
            if not ex or not coach(d.get("coachId"), True):
                d["coachId"] = u["id"]
        elif d.get("coachId") and not coach(d["coachId"]):
            d["coachId"] = ""
        co = coach(d.get("coachId"))
        if co:
            d["coach"] = co["name"]
        conn.execute(
            """INSERT INTO clients(id,coach_id,data,updated) VALUES(%s,%s,%s,now())
               ON CONFLICT (id) DO UPDATE SET coach_id=EXCLUDED.coach_id, data=EXCLUDED.data, updated=now()""",
            (cid, d.get("coachId") or "", Jsonb(d)),
        )
        bump(conn)
    return {"ok": True}


@app.delete("/clients/{cid}")
def delete_client(cid: str, u: dict = Depends(current_user)):
    with db() as conn:
        ex = conn.execute("SELECT coach_id FROM clients WHERE id=%s", (cid,)).fetchone()
        if not ex:
            return {"ok": True}
        if u["role"] != "owner" and ex["coach_id"] != u["id"]:
            raise err(403, "Klien ini bukan milik Anda.")
        conn.execute("DELETE FROM clients WHERE id=%s", (cid,))
        bump(conn)
    return {"ok": True}


@app.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def api_not_found(rest: str):
    raise err(404, "Tidak ditemukan.")
