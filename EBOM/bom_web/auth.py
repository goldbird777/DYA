"""
인증 모듈 — SQLite 기반 사용자 관리, JWT 토큰, bcrypt 비밀번호 해시
"""
import sqlite3, os, hashlib
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Request
from fastapi.responses import RedirectResponse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, 'users.db')

SECRET_KEY    = os.environ.get('BOM_SECRET', 'dya-bom-secret-2025-change-in-production')
ALGORITHM     = 'HS256'
TOKEN_EXPIRE  = 60 * 8   # 8시간

pwd_ctx = CryptContext(schemes=['bcrypt'], deprecated='auto')


# ── DB 초기화 ─────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            username  TEXT UNIQUE NOT NULL,
            email     TEXT UNIQUE NOT NULL,
            hashed_pw TEXT NOT NULL,
            dept      TEXT DEFAULT '',
            role      TEXT DEFAULT 'pending',
            created   TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    con.execute('''
        CREATE TABLE IF NOT EXISTS vehicle_codes (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            code    TEXT UNIQUE NOT NULL,
            name    TEXT NOT NULL,
            memo    TEXT DEFAULT '',
            created TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    admin = con.execute("SELECT id FROM users WHERE role='admin'").fetchone()
    if not admin:
        con.execute(
            "INSERT OR IGNORE INTO users (username,email,hashed_pw,dept,role) VALUES (?,?,?,?,?)",
            ('admin', 'admin@dya.co.kr', _hash('admin1234'), 'DYA 관리자', 'admin')
        )
    con.commit()
    con.close()


def _hash(pw: str) -> str:
    return pwd_ctx.hash(pw)


def verify_pw(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)


# ── 사용자 CRUD ───────────────────────────────────────────────────────────────
def get_user(username: str) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    con.close()
    return dict(row) if row else None


def get_all_users() -> list:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id,username,email,dept,role,created FROM users ORDER BY created DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


def create_user(username: str, email: str, password: str, dept: str = '') -> dict:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "INSERT INTO users (username,email,hashed_pw,dept,role) VALUES (?,?,?,?,?)",
            (username.strip(), email.strip().lower(), _hash(password), dept.strip(), 'pending')
        )
        con.commit()
        return {'ok': True}
    except sqlite3.IntegrityError as e:
        return {'ok': False, 'msg': '이미 사용 중인 아이디 또는 이메일입니다.'}
    finally:
        con.close()


def approve_user(user_id: int):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE users SET role='user' WHERE id=?", (user_id,))
    con.commit(); con.close()


def reject_user(user_id: int):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE users SET role='rejected' WHERE id=?", (user_id,))
    con.commit(); con.close()


def delete_user(user_id: int):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM users WHERE id=? AND role!='admin'", (user_id,))
    con.commit(); con.close()


def set_role(user_id: int, role: str):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    con.commit(); con.close()


# ── 차종 코드 CRUD ────────────────────────────────────────────────────────────
def get_all_vehicle_codes() -> list:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM vehicle_codes ORDER BY code").fetchall()
    con.close()
    return [dict(r) for r in rows]


def add_vehicle_code(code: str, name: str, memo: str = '') -> dict:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("INSERT INTO vehicle_codes (code,name,memo) VALUES (?,?,?)",
                    (code.strip().upper(), name.strip(), memo.strip()))
        con.commit()
        return {'ok': True}
    except sqlite3.IntegrityError:
        return {'ok': False, 'msg': '이미 존재하는 차종 코드입니다.'}
    finally:
        con.close()


def update_vehicle_code(code_id: int, code: str, name: str, memo: str = ''):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE vehicle_codes SET code=?,name=?,memo=? WHERE id=?",
                (code.strip().upper(), name.strip(), memo.strip(), code_id))
    con.commit(); con.close()


def delete_vehicle_code(code_id: int):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM vehicle_codes WHERE id=?", (code_id,))
    con.commit(); con.close()


# ── JWT ───────────────────────────────────────────────────────────────────────
def create_token(username: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE)
    return jwt.encode({'sub': username, 'exp': expire}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[str]:
    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return data.get('sub')
    except JWTError:
        return None


# ── Request 헬퍼 ─────────────────────────────────────────────────────────────
def current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get('bom_token')
    if not token:
        return None
    username = decode_token(token)
    if not username:
        return None
    user = get_user(username)
    if not user or user['role'] not in ('user', 'admin'):
        return None
    return user


def require_login(request: Request) -> Optional[RedirectResponse]:
    """로그인 안 됐으면 리다이렉트 응답 반환, 로그인 됐으면 None"""
    if not current_user(request):
        return RedirectResponse('/login?next=' + request.url.path, status_code=302)
    return None


def require_admin(request: Request) -> Optional[RedirectResponse]:
    user = current_user(request)
    if not user or user['role'] != 'admin':
        return RedirectResponse('/login', status_code=302)
    return None
