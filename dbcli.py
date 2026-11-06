#!/usr/bin/env python3
"""d-backend database CLI (v3).

Jalankan dari root repo d-backend (tempat file .env berada):

    venv/bin/python dbcli.py --help
    venv/bin/python dbcli.py menu
    venv/bin/python dbcli.py status
    venv/bin/python dbcli.py users list --app haylen
    venv/bin/python dbcli.py user create --app haylen --login owner --name "Owner Haylen" --role owner
    venv/bin/python dbcli.py user create --app dbacc --login user@example.com --name "User Baru" --tier 3
    venv/bin/python dbcli.py user create --app dfin --login kasir1 --name "Kasir Satu" --tier 3
    venv/bin/python dbcli.py user passwd --app haylen --login owner
    venv/bin/python dbcli.py user set-tier --app dbacc --login user@example.com --tier 2
    venv/bin/python dbcli.py user set-active --app dfin --login kasir1 --active false
    venv/bin/python dbcli.py settings list --app haylen
    venv/bin/python dbcli.py db init --basics
    venv/bin/python dbcli.py db backup --app haylen --out /tmp/haylen.sql
    venv/bin/python dbcli.py db reset --app haylen --backup /tmp/haylen.sql

Model user per app (diambil dari model masing-masing app, bukan ditebak):
- dbn6sport, haylen (olahraga): Member, login = kolom `login`,
  hak = `role` (owner/admin/head/coach), id `u1`, `u2`, ...
- dbacc: User, login = `email` (disimpan huruf kecil), hak = `tier`
  (0 Owner, 1 Admin, 2 Staff, 3 Viewer), id UUID otomatis.
- dfin: User, login = `username` (3-64 karakter), hak = `tier`
  (0 Owner, 1-3), id integer otomatis, theme/hide_balance diisi eksplisit.
- claisrox: User, login = `username` (3-50 karakter), hak = `tier`
  (0 Owner, 1 Manager, 2 Staf Produksi & Gudang, 3 Kasir & Toko Online),
  id integer otomatis.
- Tier default user baru = 3 (paling bawah) di ketiga app tier, sama
  seperti jalur akun aplikasinya. Tier 0 hanya bisa dibuat/diubah lewat
  CLI ini secara eksplisit, bukan lewat API akun biasa.

Mode menu (v3): `venv/bin/python dbcli.py menu` membuka menu bernomor
yang memanggil fungsi perintah yang sama persis dengan mode CLI — jadi
tidak ada jalur kedua yang bisa menyimpang dari pengaman di atas.
Menu hanya mengumpulkan argumen (app, login, role/tier, dll) lalu
memanggil fungsi yang sama; kata sandi, konfirmasi hapus, dan ketik
`RESET <app>` tetap diminta oleh fungsi itu sendiri. Pilihan yang salah
atau gagal tidak keluar dari menu, kecuali memilih 0 (Keluar).

Aturan keamanan (sama seperti v1, dipertahankan di v2 dan v3):
- Kata sandi TIDAK PERNAH lewat argumen CLI, file, atau chat. Selalu
  diminta interaktif lewat getpass (tidak tampil saat diketik).
- Perintah ini TIDAK PERNAH mencetak hash kata sandi, URL database,
  atau nilai rahasia lainnya. Output hanya id/login/role/tier dan jumlah baris.
- `db reset` hanya boleh satu schema per perintah (tidak ada --app all),
  wajib `--backup <file>` atau `--no-backup` eksplisit, wajib
  `--confirm <app>` yang cocok, dan wajib mengetik `RESET <app>` secara
  interaktif. Tanpa ketiganya, tidak ada yang dihapus.
- Owner/tier-0 satu-satunya dilindungi: hapus, turun hak, atau
  nonaktifkan ditolak, kecuali pakai `--force` secara sadar.
- Perintah `settings` tetap hanya untuk dbn6sport/haylen, karena hanya
  kedua app itu yang punya tabel Setting seperti itu. Perintah
  status/init/backup/reset tetap bekerja per schema untuk kelima aplikasi.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

# Import app.main dulu: ini mendaftarkan semua model ke Base SQLAlchemy,
# sehingga init_db() benar-benar membuat tabel semua aplikasi.
import app.main  # noqa: F401  (side effect: registrasi model)
from app.core import security
from app.core.database import APP_SCHEMAS, SessionLocal, engine, init_db
from sqlalchemy import inspect, text

SPORT_APPS = {
    "dbn6sport": {"schema": "dn6sport", "module": "app.apps.dbn6sport"},
    "haylen": {"schema": "haylen", "module": "app.apps.haylen"},
}

TIER_APPS = {
    "dbacc": {"schema": "daccounting", "module": "app.apps.dbacc.models", "login_field": "email"},
    "dfin": {"schema": "dfinance", "module": "app.apps.dbfin.models", "login_field": "username"},
    "claisrox": {"schema": "dclaisrox", "module": "app.apps.claisrox.models", "login_field": "username"},
}

ALL_APPS = {
    "dbn6sport": "dn6sport",
    "haylen": "haylen",
    "dbacc": "daccounting",
    "dfin": "dfinance",
    "claisrox": "dclaisrox",
}

SPORT_ROLES = ("owner", "admin", "head", "coach")
TIER_MIN, TIER_MAX, TIER_DEFAULT = 0, 3, 3


def fail(msg: str, code: int = 1) -> "SystemExit":
    print(f"GAGAL: {msg}", file=sys.stderr)
    raise SystemExit(code)


def sport_models(app: str):
    """Kembalikan (Member, Setting, ensure_basics) untuk aplikasi olahraga."""
    if app not in SPORT_APPS:
        fail(
            f"perintah settings hanya untuk dbn6sport dan haylen; "
            f"app '{app}' tidak punya tabel Setting seperti itu."
        )
    mod = __import__(SPORT_APPS[app]["module"] + ".models", fromlist=["Member", "Setting"])
    seed = __import__(SPORT_APPS[app]["module"] + ".seed", fromlist=["ensure_basics"])
    return mod.Member, mod.Setting, seed.ensure_basics


def user_model(app: str):
    """Kembalikan (kind, Model, login_field) untuk app apa pun.

    kind = 'sport' (role) atau 'tier' (tier 0-3)."""
    if app in SPORT_APPS:
        mod = __import__(SPORT_APPS[app]["module"] + ".models", fromlist=["Member"])
        return "sport", mod.Member, "login"
    if app in TIER_APPS:
        mod = __import__(TIER_APPS[app]["module"], fromlist=["User"])
        return "tier", mod.User, TIER_APPS[app]["login_field"]
    fail(f"app tidak dikenal: {app}")


def normalize_login(app: str, login: str) -> str:
    login = (login or "").strip()
    if app == "dbacc":
        login = login.lower()
    return login


def validate_login(app: str, login: str) -> None:
    if not login:
        fail("login tidak boleh kosong.")
    if app == "dbacc" and "@" not in login:
        fail("login dbacc harus berupa email (mengandung '@').")
    if app == "dfin" and not (3 <= len(login) <= 64):
        fail("username dfin harus 3-64 karakter.")
    if app == "claisrox" and not (3 <= len(login) <= 50):
        fail("username claisrox harus 3-50 karakter.")


def ask_password() -> str:
    pw = getpass.getpass("Kata sandi: ")
    pw2 = getpass.getpass("Ulangi kata sandi: ")
    if not pw:
        fail("kata sandi kosong, tidak ada yang diubah.")
    if pw != pw2:
        fail("kata sandi tidak cocok, tidak ada yang diubah.")
    return pw


def hash_or_fail(pw: str) -> str:
    try:
        return security.hash_password(pw)
    except ValueError as e:
        fail(str(e))


def next_member_id(db, Member) -> str:
    used = {r[0] for r in db.query(Member.id).all()}
    for i in range(1, 1000):
        cand = f"u{i}"
        if cand not in used:
            return cand
    fail("tidak ada id member bebas (u1..u999 sudah dipakai).")


def is_owner(user, kind: str) -> bool:
    return user.role == "owner" if kind == "sport" else user.tier == 0


def owner_label(user, kind: str) -> str:
    return user.role if kind == "sport" else f"tier {user.tier}"


def count_owners(db, Model, kind: str) -> int:
    if kind == "sport":
        return db.query(Model).filter_by(role="owner").count()
    return db.query(Model).filter_by(tier=0).count()


def guard_sole_owner(db, Model, kind: str, user, force: bool, action: str) -> None:
    if is_owner(user, kind) and count_owners(db, Model, kind) <= 1 and not force:
        fail(f"ini owner/tier-0 satu-satunya di app ini; {action} ditolak "
             f"agar app tidak terkunci. Tambah owner/tier-0 lain dulu, "
             f"atau ulangi dengan --force bila memang disengaja.")


def find_user(db, Model, login_field: str, login: str):
    return db.query(Model).filter_by(**{login_field: login}).first()


# ---------------------------------------------------------------- status

def cmd_status(_args) -> None:
    insp = inspect(engine)
    schemas = [s for s in APP_SCHEMAS if s in insp.get_schema_names()]
    db = SessionLocal()
    try:
        for schema in APP_SCHEMAS:
            tables = insp.get_table_names(schema=schema) if schema in schemas else []
            total = 0
            print(f"{schema}: {len(tables)} tabel")
            for t in tables:
                try:
                    n = db.execute(
                        text(f'SELECT COUNT(*) FROM "{schema}"."{t}"')
                    ).scalar()
                except Exception:
                    db.rollback()
                    n = "?"
                if isinstance(n, int):
                    total += n
                print(f"  {t}: {n}")
            print(f"  total baris: {total}")
    finally:
        db.close()


# ---------------------------------------------------------------- users

def cmd_users_list(args) -> None:
    kind, Model, login_field = user_model(args.app)
    db = SessionLocal()
    try:
        if kind == "sport":
            rows = db.query(Model).order_by(Model.id).all()
            print(f"jumlah: {len(rows)}")
            for r in rows:
                print(f"{r.id} {r.login} {r.role}")
        else:
            rows = db.query(Model).order_by(Model.tier, getattr(Model, login_field)).all()
            print(f"jumlah: {len(rows)}")
            for r in rows:
                print(f"{r.id} {getattr(r, login_field)} {r.name} tier {r.tier} aktif {bool(r.is_active)}")
    finally:
        db.close()


def cmd_user_create(args) -> None:
    kind, Model, login_field = user_model(args.app)
    login = normalize_login(args.app, args.login)
    validate_login(args.app, login)
    if not args.name or not args.name.strip():
        fail("nama tidak boleh kosong.")
    db = SessionLocal()
    try:
        if find_user(db, Model, login_field, login):
            fail(f"login '{login}' sudah ada, tidak ada yang diubah.")
        # Validasi role/tier SEBELUM meminta kata sandi, supaya perintah
        # yang salah tidak membuang waktu input kata sandi.
        if kind == "sport":
            if getattr(args, "tier", None) is not None:
                fail("app olahraga pakai --role, bukan --tier.")
            if args.role not in SPORT_ROLES:
                fail(f"role harus salah satu dari: {', '.join(SPORT_ROLES)}")
        else:
            if args.role is not None:
                fail("app tier pakai --tier (0-3), bukan --role.")
            tier_early = TIER_DEFAULT if args.tier is None else args.tier
            if not (TIER_MIN <= tier_early <= TIER_MAX):
                fail("tier harus 0-3.")
        pw = ask_password()
        hashed = hash_or_fail(pw)
        if kind == "sport":
            mid = args.id or next_member_id(db, Model)
            if db.query(Model).filter_by(id=mid).first():
                fail(f"id '{mid}' sudah dipakai, tidak ada yang diubah.")
            m = Model(id=mid, name=args.name.strip(), role=args.role, login=login,
                      password_hash=hashed, email=args.email or None, wa="", loc="")
            db.add(m)
            db.commit()
            print(f"user dibuat: {m.id} {m.login} {m.role}")
        else:
            if args.role is not None:
                fail("app tier pakai --tier (0-3), bukan --role.")
            tier = TIER_DEFAULT if args.tier is None else args.tier
            if not (TIER_MIN <= tier <= TIER_MAX):
                fail("tier harus 0-3.")
            if args.app == "dbacc":
                m = Model(id=str(uuid.uuid4()), name=args.name.strip(), email=login,
                          password_hash=hashed, tier=tier, is_active=True)
            elif args.app == "dfin":
                m = Model(username=login, name=args.name.strip(),
                          password_hash=hashed, tier=tier, is_active=True,
                          theme="dark", hide_balance=False)
            else:  # claisrox
                m = Model(username=login, name=args.name.strip(),
                          password_hash=hashed, tier=tier, is_active=True)
            db.add(m)
            db.commit()
            db.refresh(m)
            print(f"user dibuat: {m.id} {login} {m.name} tier {m.tier} aktif True")
    except SystemExit:
        raise
    except Exception as e:
        db.rollback()
        fail(f"{type(e).__name__}: {e}")
    finally:
        db.close()


def cmd_user_passwd(args) -> None:
    kind, Model, login_field = user_model(args.app)
    login = normalize_login(args.app, args.login)
    db = SessionLocal()
    try:
        m = find_user(db, Model, login_field, login)
        if not m:
            fail(f"login '{login}' tidak ditemukan.")
        pw = ask_password()
        m.password_hash = hash_or_fail(pw)
        db.commit()
        print(f"kata sandi diubah: {login} {owner_label(m, kind)}")
    except SystemExit:
        raise
    except Exception as e:
        db.rollback()
        fail(f"{type(e).__name__}: {e}")
    finally:
        db.close()


def cmd_user_delete(args) -> None:
    kind, Model, login_field = user_model(args.app)
    login = normalize_login(args.app, args.login)
    db = SessionLocal()
    try:
        m = find_user(db, Model, login_field, login)
        if not m:
            fail(f"login '{login}' tidak ditemukan.")
        guard_sole_owner(db, Model, kind, m, args.force, "hapus")
        typed = normalize_login(args.app, input(f"Ketik login yang akan dihapus ({login}): "))
        if typed != login:
            fail("ketikan tidak cocok, tidak ada yang dihapus.")
        db.delete(m)
        db.commit()
        print(f"user dihapus: {login}")
    except SystemExit:
        raise
    except Exception as e:
        db.rollback()
        fail(f"{type(e).__name__}: {e}")
    finally:
        db.close()


def cmd_user_set_role(args) -> None:
    kind, Model, login_field = user_model(args.app)
    if kind != "sport":
        fail("app tier tidak punya role; pakai 'user set-tier' dengan --tier 0-3.")
    if args.role not in SPORT_ROLES:
        fail(f"role harus salah satu dari: {', '.join(SPORT_ROLES)}")
    login = normalize_login(args.app, args.login)
    db = SessionLocal()
    try:
        m = find_user(db, Model, login_field, login)
        if not m:
            fail(f"login '{login}' tidak ditemukan.")
        if m.role == "owner" and args.role != "owner":
            guard_sole_owner(db, Model, kind, m, args.force, "penurunan role")
        m.role = args.role
        db.commit()
        print(f"role diubah: {m.login} -> {m.role}")
    except SystemExit:
        raise
    except Exception as e:
        db.rollback()
        fail(f"{type(e).__name__}: {e}")
    finally:
        db.close()


def cmd_user_set_tier(args) -> None:
    kind, Model, login_field = user_model(args.app)
    if kind != "tier":
        fail("app olahraga tidak punya tier; pakai 'user set-role' dengan --role.")
    if not (TIER_MIN <= args.tier <= TIER_MAX):
        fail("tier harus 0-3.")
    login = normalize_login(args.app, args.login)
    db = SessionLocal()
    try:
        m = find_user(db, Model, login_field, login)
        if not m:
            fail(f"login '{login}' tidak ditemukan.")
        if m.tier == 0 and args.tier != 0:
            guard_sole_owner(db, Model, kind, m, args.force, "penurunan tier")
        m.tier = args.tier
        db.commit()
        print(f"tier diubah: {login} -> tier {m.tier}")
    except SystemExit:
        raise
    except Exception as e:
        db.rollback()
        fail(f"{type(e).__name__}: {e}")
    finally:
        db.close()


def cmd_user_set_active(args) -> None:
    kind, Model, login_field = user_model(args.app)
    if kind != "tier":
        fail("app olahraga tidak punya kolom is_active; pakai 'user delete' bila perlu.")
    active = {"true": True, "false": False, "1": True, "0": False,
              "yes": True, "no": False}[args.active.lower()]
    login = normalize_login(args.app, args.login)
    db = SessionLocal()
    try:
        m = find_user(db, Model, login_field, login)
        if not m:
            fail(f"login '{login}' tidak ditemukan.")
        if m.tier == 0 and not active:
            guard_sole_owner(db, Model, kind, m, args.force, "nonaktifkan")
        m.is_active = active
        db.commit()
        print(f"aktif diubah: {login} -> {bool(m.is_active)}")
    except SystemExit:
        raise
    except Exception as e:
        db.rollback()
        fail(f"{type(e).__name__}: {e}")
    finally:
        db.close()


# ---------------------------------------------------------------- settings

def cmd_settings_list(args) -> None:
    _Member, Setting, _seed = sport_models(args.app)
    db = SessionLocal()
    try:
        rows = db.query(Setting).order_by(Setting.key).all()
        for r in rows:
            print(f"{r.key}: {json.dumps(r.value, ensure_ascii=False)}")
        print(f"jumlah: {len(rows)}")
    finally:
        db.close()


def cmd_settings_get(args) -> None:
    _Member, Setting, _seed = sport_models(args.app)
    db = SessionLocal()
    try:
        r = db.query(Setting).filter_by(key=args.key).first()
        if not r:
            fail(f"setting '{args.key}' tidak ditemukan.")
        print(json.dumps(r.value, ensure_ascii=False, indent=2))
    finally:
        db.close()


def cmd_settings_set(args) -> None:
    _Member, Setting, _seed = sport_models(args.app)
    try:
        value = json.loads(args.value)
    except json.JSONDecodeError as e:
        fail(f"--value bukan JSON valid: {e}")
    db = SessionLocal()
    try:
        r = db.query(Setting).filter_by(key=args.key).first()
        if r:
            r.value = value
            action = "diubah"
        else:
            db.add(Setting(key=args.key, value=value))
            action = "dibuat"
        db.commit()
        print(f"setting {args.key} {action}")
    except SystemExit:
        raise
    except Exception as e:
        db.rollback()
        fail(f"{type(e).__name__}: {e}")
    finally:
        db.close()


# ---------------------------------------------------------------- db ops

def cmd_db_init(args) -> None:
    init_db()
    print("init selesai: schema dan tabel dibuat bila belum ada.")
    if args.basics:
        db = SessionLocal()
        try:
            for app in ("dbn6sport", "haylen"):
                _Member, _Setting, ensure_basics = sport_models(app)
                ensure_basics(db)
            db.commit()
            print("basics selesai: settings targets/acl dibuat bila belum ada.")
        except Exception as e:
            db.rollback()
            fail(f"{type(e).__name__}: {e}")
        finally:
            db.close()


def run_backup(schema: str, out: str) -> None:
    """pg_dump satu schema ke file. Kredensial hanya lewat env, tidak dicetak."""
    from app.core.config import settings

    url = urlparse(settings.DATABASE_URL)
    if url.scheme.split("+")[0] not in ("postgresql", "postgres"):
        fail("backup hanya mendukung DATABASE_URL PostgreSQL.")
    if not out:
        fail("tentukan file backup dengan --out.")
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    # Host bisa lewat hostname TCP, atau lewat query ?host=... (socket Unix).
    query_host = parse_qs(url.query).get("host", [None])[0]
    cmd = ["pg_dump", "-n", schema, "-f", out]
    if url.hostname:
        cmd += ["-h", url.hostname]
    elif query_host:
        cmd += ["-h", query_host]
    if url.port:
        cmd += ["-p", str(url.port)]
    if url.username:
        cmd += ["-U", url.username]
    if url.path and url.path != "/":
        cmd.append(url.path.lstrip("/"))
    try:
        subprocess.run(cmd, env=env, check=True)
    except FileNotFoundError:
        fail("pg_dump tidak ditemukan di mesin ini.")
    except subprocess.CalledProcessError:
        fail("pg_dump gagal; backup tidak dibuat, reset dibatalkan.")
    print(f"backup dibuat: {out}")


def cmd_db_backup(args) -> None:
    schema = ALL_APPS[args.app]
    run_backup(schema, args.out)


def cmd_db_reset(args) -> None:
    if args.app not in ALL_APPS:
        fail(f"app harus salah satu dari: {', '.join(ALL_APPS)}")
    schema = ALL_APPS[args.app]
    if args.confirm != args.app:
        fail(f"reset dibatalkan: --confirm harus persis '{args.app}'.")
    if not args.backup and not args.no_backup:
        fail("reset dibatalkan: tentukan --backup <file> atau --no-backup eksplisit.")
    if args.backup:
        run_backup(schema, args.backup)
    typed = input(f"Ketik RESET {args.app} untuk menghapus SEMUA data di schema {schema}: ").strip()
    if typed != f"RESET {args.app}":
        fail("ketikan tidak cocok, tidak ada yang dihapus.")
    insp = inspect(engine)
    tables = insp.get_table_names(schema=schema)
    if not tables:
        print(f"schema {schema} sudah kosong, tidak ada yang dihapus.")
        return
    quoted = ", ".join(f'"{schema}"."{t}"' for t in tables)
    db = SessionLocal()
    try:
        db.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
        db.commit()
        print(f"reset selesai: {len(tables)} tabel di {schema} dikosongkan.")
        if args.app in SPORT_APPS:
            _Member, _Setting, ensure_basics = sport_models(args.app)
            ensure_basics(db)
            db.commit()
            print("basics dibuat ulang: settings targets/acl.")
    except Exception as e:
        db.rollback()
        fail(f"{type(e).__name__}: {e}")
    finally:
        db.close()
    if args.create_owner:
        print("Lanjut buat owner baru dengan perintah berikut:")
        if args.app in SPORT_APPS:
            print(f"  venv/bin/python dbcli.py user create --app {args.app} "
                  f"--login <login> --name <nama> --role owner")
        else:
            print(f"  venv/bin/python dbcli.py user create --app {args.app} "
                  f"--login <login/email> --name <nama> --tier 0")


# ---------------------------------------------------------------- menu

def _menu_text(prompt: str, default: str = "") -> str:
    try:
        v = input(prompt).strip()
    except EOFError:
        print()
        raise SystemExit(0)
    return v or default


def _menu_yes(prompt: str) -> bool:
    return _menu_text(prompt).lower() in ("y", "ya", "yes", "1")


def _menu_pick_app() -> str | None:
    """Pilih app lewat nomor atau nama. Kembalikan None bila batal (0/kosong)."""
    apps = list(ALL_APPS)
    print("Pilih app:")
    for i, a in enumerate(apps, 1):
        print(f"  {i}. {a}  (schema {ALL_APPS[a]})")
    print("  0. Batal / kembali")
    v = _menu_text("Nomor atau nama app: ")
    if v in ("0", ""):
        return None
    if v.isdigit() and 1 <= int(v) <= len(apps):
        return apps[int(v) - 1]
    if v in apps:
        return v
    print("Pilihan tidak dikenal.")
    return None


def _menu_pick_role() -> str | None:
    print("Pilih role:")
    for i, r in enumerate(SPORT_ROLES, 1):
        print(f"  {i}. {r}")
    print("  0. Batal")
    v = _menu_text("Nomor atau nama role: ")
    if v in ("0", ""):
        return None
    if v.isdigit() and 1 <= int(v) <= len(SPORT_ROLES):
        return SPORT_ROLES[int(v) - 1]
    if v in SPORT_ROLES:
        return v
    print("Pilihan tidak dikenal.")
    return None


def _menu_pick_tier(default: int | None = None) -> int | None:
    """Pilih tier 0-3. Kosong = default bila ada, bila tidak = batal.

    Tier 0 selalu berarti owner, bukan batal — batal dilakukan di level
    pilih app, atau dengan mengosongkan input bila tidak ada default."""
    hint = f", kosong = {default}" if default is not None else ", kosong = batal"
    v = _menu_text(f"Tier (0-3{hint}): ")
    if v == "":
        return default
    if v.isdigit() and 0 <= int(v) <= 3:
        return int(v)
    print("Tier harus 0-3.")
    return None


def _menu_run(fn, args) -> None:
    """Jalankan satu fungsi perintah; gagalnya tidak keluar dari menu."""
    try:
        fn(args)
    except SystemExit:
        pass
    except KeyboardInterrupt:
        print()
        raise SystemExit(0)
    print()


def _menu_settings() -> None:
    app = _menu_pick_app()
    if app is None:
        return
    print("Settings (hanya sport):")
    print("  1. Daftar semua setting")
    print("  2. Tampilkan satu setting")
    print("  3. Buat/ubah satu setting")
    print("  0. Kembali")
    choice = _menu_text("Pilih: ")
    if choice == "1":
        _menu_run(cmd_settings_list, SimpleNamespace(app=app))
    elif choice == "2":
        key = _menu_text("Kunci setting: ")
        if key:
            _menu_run(cmd_settings_get, SimpleNamespace(app=app, key=key))
    elif choice == "3":
        key = _menu_text("Kunci setting: ")
        value = _menu_text("Nilai JSON: ")
        if key and value:
            _menu_run(cmd_settings_set, SimpleNamespace(app=app, key=key, value=value))
    elif choice != "0":
        print("Pilihan tidak dikenal.")


def cmd_menu(_args) -> None:
    print("d-backend database CLI — menu interaktif")
    print("Perintah CLI biasa tetap berlaku; menu ini hanya jalan pintas.")
    print("Kata sandi tetap diminta tersembunyi oleh perintahnya sendiri.")
    while True:
        print("Menu utama:")
        print("  1. Status DB")
        print("  2. Daftar user")
        print("  3. Buat user")
        print("  4. Ganti kata sandi user")
        print("  5. Ubah role (sport) / tier (app tier)")
        print("  6. Aktifkan/nonaktifkan user (app tier)")
        print("  7. Hapus user")
        print("  8. Settings (sport)")
        print("  9. Backup schema")
        print(" 10. Reset schema (DESTRUKTIF)")
        print(" 11. Init DB")
        print("  0. Keluar")
        try:
            choice = _menu_text("Pilih: ")
        except SystemExit:
            print("Keluar.")
            return
        except KeyboardInterrupt:
            print("\nKeluar.")
            return
        if choice == "0" or choice == "":
            print("Keluar.")
            return
        elif choice == "1":
            _menu_run(cmd_status, SimpleNamespace())
        elif choice == "2":
            app = _menu_pick_app()
            if app is not None:
                _menu_run(cmd_users_list, SimpleNamespace(app=app))
        elif choice == "3":
            app = _menu_pick_app()
            if app is None:
                continue
            login = _menu_text("Login (sport: login, dbacc: email, lain: username): ")
            name = _menu_text("Nama: ")
            if not login or not name:
                print("Login dan nama wajib diisi.")
                continue
            if app in SPORT_APPS:
                role = _menu_pick_role()
                if role is None:
                    continue
                email = _menu_text("Email (boleh kosong, hanya sport): ")
                mid = _menu_text("ID (kosong = otomatis u berikutnya): ")
                _menu_run(cmd_user_create, SimpleNamespace(
                    app=app, login=login, name=name, role=role, tier=None,
                    email=email, id=mid))
            else:
                tier = _menu_pick_tier(default=3)
                if tier is None:
                    continue
                _menu_run(cmd_user_create, SimpleNamespace(
                    app=app, login=login, name=name, role=None, tier=tier,
                    email="", id=""))
        elif choice == "4":
            app = _menu_pick_app()
            if app is None:
                continue
            login = _menu_text("Login: ")
            if login:
                _menu_run(cmd_user_passwd, SimpleNamespace(app=app, login=login))
        elif choice == "5":
            app = _menu_pick_app()
            if app is None:
                continue
            login = _menu_text("Login: ")
            if not login:
                continue
            force = _menu_yes("Izinkan paksa bila owner/tier-0 satu-satunya? (y/N): ")
            if app in SPORT_APPS:
                role = _menu_pick_role()
                if role is None:
                    continue
                _menu_run(cmd_user_set_role, SimpleNamespace(
                    app=app, login=login, role=role, force=force))
            else:
                tier = _menu_pick_tier()
                if tier is None:
                    continue
                _menu_run(cmd_user_set_tier, SimpleNamespace(
                    app=app, login=login, tier=tier, force=force))
        elif choice == "6":
            app = _menu_pick_app()
            if app is None:
                continue
            login = _menu_text("Login: ")
            if not login:
                continue
            active = _menu_text("Aktif? (true/false): ").lower()
            if active not in ("true", "false"):
                print("Jawaban harus true atau false.")
                continue
            force = _menu_yes("Izinkan paksa bila owner/tier-0 satu-satunya? (y/N): ")
            _menu_run(cmd_user_set_active, SimpleNamespace(
                app=app, login=login, active=active, force=force))
        elif choice == "7":
            app = _menu_pick_app()
            if app is None:
                continue
            login = _menu_text("Login: ")
            if not login:
                continue
            force = _menu_yes("Izinkan paksa bila owner/tier-0 satu-satunya? (y/N): ")
            _menu_run(cmd_user_delete, SimpleNamespace(
                app=app, login=login, force=force))
        elif choice == "8":
            _menu_settings()
        elif choice == "9":
            app = _menu_pick_app()
            if app is None:
                continue
            out = _menu_text("File backup: ")
            if out:
                _menu_run(cmd_db_backup, SimpleNamespace(app=app, out=out))
        elif choice == "10":
            app = _menu_pick_app()
            if app is None:
                continue
            print("PERINGATAN: reset menghapus SEMUA data di satu schema.")
            backup = _menu_text("File backup (kosong = tanpa backup, harus dikonfirmasi lagi): ")
            confirm = _menu_text(f"Ketik nama app untuk konfirmasi ({app}): ")
            _menu_run(cmd_db_reset, SimpleNamespace(
                app=app, confirm=confirm,
                backup=backup, no_backup=(backup == ""),
                create_owner=False))
        elif choice == "11":
            basics = _menu_yes("Sekalian buat settings dasar olahraga? (y/N): ")
            _menu_run(cmd_db_init, SimpleNamespace(basics=basics))
        else:
            print("Pilihan tidak dikenal.")


# ---------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dbcli.py",
        description="d-backend database CLI. Kata sandi selalu diminta "
                    "interaktif, tidak pernah lewat argumen.")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("menu", help="menu interaktif bernomor (memanggil perintah yang sama)")
    m.set_defaults(fn=cmd_menu)

    s = sub.add_parser("status", help="daftar schema, tabel, dan jumlah baris (baca saja)")
    s.set_defaults(fn=cmd_status)

    u = sub.add_parser("users", help="daftar user semua aplikasi")
    usub = u.add_subparsers(dest="ulcmd", required=True)
    ul = usub.add_parser("list", help="daftar user (id, login, role/tier)")
    ul.add_argument("--app", required=True, choices=list(ALL_APPS))
    ul.set_defaults(fn=cmd_users_list)

    ug = sub.add_parser("user", help="kelola satu user")
    usub = ug.add_subparsers(dest="ucmd", required=True)

    c = usub.add_parser("create", help="buat user baru (kata sandi diminta interaktif)")
    c.add_argument("--app", required=True, choices=list(ALL_APPS))
    c.add_argument("--login", required=True,
                   help="sport: login; dbacc: email; dfin/claisrox: username")
    c.add_argument("--name", required=True)
    c.add_argument("--role", default=None, choices=list(SPORT_ROLES),
                   help="wajib untuk sport, tidak dipakai app tier")
    c.add_argument("--tier", type=int, default=None, choices=[0, 1, 2, 3],
                   help="untuk app tier, default 3 bila tidak diisi")
    c.add_argument("--email", default="")
    c.add_argument("--id", default="", help="hanya untuk sport (default id u berikutnya)")
    c.set_defaults(fn=cmd_user_create)

    pw = usub.add_parser("passwd", help="ganti kata sandi user (diminta interaktif)")
    pw.add_argument("--app", required=True, choices=list(ALL_APPS))
    pw.add_argument("--login", required=True)
    pw.set_defaults(fn=cmd_user_passwd)

    d = usub.add_parser("delete", help="hapus user (konfirmasi ketik login)")
    d.add_argument("--app", required=True, choices=list(ALL_APPS))
    d.add_argument("--login", required=True)
    d.add_argument("--force", action="store_true",
                   help="izinkan hapus owner/tier-0 satu-satunya (disengaja)")
    d.set_defaults(fn=cmd_user_delete)

    r = usub.add_parser("set-role", help="ubah role user (hanya sport)")
    r.add_argument("--app", required=True, choices=list(ALL_APPS))
    r.add_argument("--login", required=True)
    r.add_argument("--role", required=True, choices=list(SPORT_ROLES))
    r.add_argument("--force", action="store_true",
                   help="izinkan penurunan role owner satu-satunya (disengaja)")
    r.set_defaults(fn=cmd_user_set_role)

    t = usub.add_parser("set-tier", help="ubah tier user (hanya dbacc/dfin/claisrox)")
    t.add_argument("--app", required=True, choices=list(ALL_APPS))
    t.add_argument("--login", required=True)
    t.add_argument("--tier", type=int, required=True, choices=[0, 1, 2, 3])
    t.add_argument("--force", action="store_true",
                   help="izinkan penurunan tier owner satu-satunya (disengaja)")
    t.set_defaults(fn=cmd_user_set_tier)

    a = usub.add_parser("set-active", help="aktifkan/nonaktifkan user (hanya app tier)")
    a.add_argument("--app", required=True, choices=list(ALL_APPS))
    a.add_argument("--login", required=True)
    a.add_argument("--active", required=True, choices=["true", "false"],
                   help="true = aktif, false = nonaktif")
    a.add_argument("--force", action="store_true",
                   help="izinkan nonaktifkan owner/tier-0 satu-satunya (disengaja)")
    a.set_defaults(fn=cmd_user_set_active)

    sg = sub.add_parser("settings", help="kelola settings aplikasi olahraga")
    ssub = sg.add_subparsers(dest="scmd", required=True)

    sl = ssub.add_parser("list", help="daftar semua setting")
    sl.add_argument("--app", required=True, choices=list(ALL_APPS))
    sl.set_defaults(fn=cmd_settings_list)

    sget = ssub.add_parser("get", help="tampilkan satu setting sebagai JSON")
    sget.add_argument("--app", required=True, choices=list(ALL_APPS))
    sget.add_argument("--key", required=True)
    sget.set_defaults(fn=cmd_settings_get)

    sset = ssub.add_parser("set", help="buat/ubah satu setting (nilai harus JSON)")
    sset.add_argument("--app", required=True, choices=list(ALL_APPS))
    sset.add_argument("--key", required=True)
    sset.add_argument("--value", required=True, help="nilai JSON, misal '{\"klien\": 5}'")
    sset.set_defaults(fn=cmd_settings_set)

    dg = sub.add_parser("db", help="operasi database per schema")
    dsub = dg.add_subparsers(dest="dcmd", required=True)

    di = dsub.add_parser("init", help="buat schema dan tabel bila belum ada")
    di.add_argument("--basics", action="store_true",
                    help="sekalian buat settings targets/acl olahraga bila belum ada")
    di.set_defaults(fn=cmd_db_init)

    dbk = dsub.add_parser("backup", help="pg_dump satu schema ke file")
    dbk.add_argument("--app", required=True, choices=list(ALL_APPS))
    dbk.add_argument("--out", required=True)
    dbk.set_defaults(fn=cmd_db_backup)

    dr = dsub.add_parser("reset", help="KOSONGKAN satu schema (destruktif, triple konfirmasi)")
    dr.add_argument("--app", required=True, choices=list(ALL_APPS))
    dr.add_argument("--confirm", required=True, help="harus persis sama dengan --app")
    dr.add_argument("--backup", default="", help="file backup pg_dump sebelum reset")
    dr.add_argument("--no-backup", action="store_true",
                    help="reset tanpa backup (disengaja, tanggung sendiri)")
    dr.add_argument("--create-owner", action="store_true",
                    help="setelah reset, tampilkan perintah buat owner baru")
    dr.set_defaults(fn=cmd_db_reset)

    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
