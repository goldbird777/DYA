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
    con.execute('''
        CREATE TABLE IF NOT EXISTS stored_boms (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_code  TEXT NOT NULL,
            row_num       TEXT NOT NULL,
            position      TEXT NOT NULL,
            kind          TEXT NOT NULL,
            filename      TEXT NOT NULL,
            file_id       TEXT UNIQUE NOT NULL,
            file_path     TEXT NOT NULL,
            uploader      TEXT NOT NULL,
            uploaded_at   TEXT DEFAULT (datetime('now','localtime')),
            memo          TEXT DEFAULT '',
            version_num   INTEGER NOT NULL DEFAULT 1,
            file_hash     TEXT DEFAULT ''
        )
    ''')
    # 기존 DB 마이그레이션 — file_hash 컬럼 추가 (이미 있으면 무시)
    try:
        con.execute("ALTER TABLE stored_boms ADD COLUMN file_hash TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    con.execute('''CREATE INDEX IF NOT EXISTS idx_stored_lookup
                   ON stored_boms(vehicle_code, row_num, position, kind, version_num DESC)''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_stored_hash
                   ON stored_boms(file_hash)''')
    con.execute('''
        CREATE TABLE IF NOT EXISTS bom_template_revisions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            rev_num     INTEGER NOT NULL,
            filename    TEXT NOT NULL,
            file_path   TEXT NOT NULL,
            uploaded_by TEXT NOT NULL,
            uploaded_at TEXT DEFAULT (datetime('now','localtime')),
            note        TEXT DEFAULT '',
            is_active   INTEGER NOT NULL DEFAULT 0
        )
    ''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_tpl_active
                   ON bom_template_revisions(is_active)''')
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


def get_vehicle_code_by_code(code: str) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM vehicle_codes WHERE code=?", (code.strip().upper(),)).fetchone()
    con.close()
    return dict(row) if row else None


def update_vehicle_code_by_code(old_code: str, code: str, name: str, memo: str = '') -> dict:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("UPDATE vehicle_codes SET code=?, name=?, memo=? WHERE code=?",
                    (code.strip().upper(), name.strip(), memo.strip(), old_code.strip().upper()))
        con.commit()
        return {'ok': True}
    except sqlite3.IntegrityError:
        return {'ok': False, 'msg': '이미 사용중인 코드입니다.'}
    finally:
        con.close()


def delete_vehicle_code_by_code(code: str):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM vehicle_codes WHERE code=?", (code.strip().upper(),))
    con.commit(); con.close()


# ── 저장된 BOM (stored_boms) CRUD ─────────────────────────────────────────────
def save_stored_bom(vehicle_code: str, row_num: str, position: str,
                    kind: str, filename: str, file_id: str, file_path: str,
                    uploader: str, memo: str = '', file_hash: str = '') -> dict:
    """새 BOM 저장. 같은 (차종, 열, 위치, kind) 조합 안에서 version_num 자동 증가."""
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "SELECT COALESCE(MAX(version_num), 0) FROM stored_boms WHERE vehicle_code=? AND row_num=? AND position=? AND kind=?",
        (vehicle_code, row_num, position, kind)
    )
    next_ver = cur.fetchone()[0] + 1
    con.execute('''
        INSERT INTO stored_boms (vehicle_code, row_num, position, kind, filename, file_id,
                                  file_path, uploader, memo, version_num, file_hash)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    ''', (vehicle_code, row_num, position, kind, filename, file_id,
          file_path, uploader, memo, next_ver, file_hash))
    con.commit(); con.close()
    return {'ok': True, 'version': next_ver}


def find_duplicate_by_hash(vehicle_code: str, row_num: str, position: str,
                            kind: str, file_hash: str) -> Optional[dict]:
    """같은 (차종, 열, 위치, kind) 조합 안에서 동일 해시의 기존 저장본 찾기."""
    if not file_hash:
        return None
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM stored_boms WHERE vehicle_code=? AND row_num=? AND position=? AND kind=? AND file_hash=? ORDER BY version_num DESC LIMIT 1",
        (vehicle_code, row_num, position, kind, file_hash)
    ).fetchone()
    con.close()
    return dict(row) if row else None


def list_stored_boms(vehicle_code: str = None, row_num: str = None,
                     position: str = None, kind: str = None) -> list:
    """필터 조건에 맞는 저장된 BOM 목록 (최신 버전 우선)."""
    sql = "SELECT * FROM stored_boms WHERE 1=1"
    params = []
    if vehicle_code: sql += " AND vehicle_code=?"; params.append(vehicle_code)
    if row_num:      sql += " AND row_num=?";      params.append(row_num)
    if position:     sql += " AND position=?";     params.append(position)
    if kind:         sql += " AND kind=?";         params.append(kind)
    sql += " ORDER BY uploaded_at DESC, version_num DESC"
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(sql, params).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_stored_bom(file_id: str) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM stored_boms WHERE file_id=?", (file_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def delete_stored_bom(file_id: str) -> bool:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute("DELETE FROM stored_boms WHERE file_id=?", (file_id,))
    deleted = cur.rowcount
    con.commit(); con.close()
    return deleted > 0


def update_stored_bom_meta(file_id: str, **fields) -> bool:
    """저장된 BOM 의 메타데이터 수정 (vehicle_code/row_num/position/memo)."""
    allowed = {'vehicle_code', 'row_num', 'position', 'memo'}
    sets = []
    params = []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?"); params.append(v)
    if not sets:
        return False
    params.append(file_id)
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(f"UPDATE stored_boms SET {', '.join(sets)} WHERE file_id=?", params)
    n = cur.rowcount
    con.commit(); con.close()
    return n > 0


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


# ── 표준화 BOM 템플릿 리비전 ─────────────────────────────────────────────────
def list_bom_template_revisions() -> list:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM bom_template_revisions ORDER BY rev_num DESC"
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_active_bom_template() -> Optional[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM bom_template_revisions WHERE is_active=1 ORDER BY rev_num DESC LIMIT 1"
    ).fetchone()
    con.close()
    return dict(row) if row else None


def get_bom_template_revision(rev_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM bom_template_revisions WHERE id=?", (rev_id,)
    ).fetchone()
    con.close()
    return dict(row) if row else None


def add_bom_template_revision(filename: str, file_path: str,
                              uploaded_by: str, note: str = '') -> dict:
    con = sqlite3.connect(DB_PATH)
    next_rev = con.execute(
        "SELECT COALESCE(MAX(rev_num), 0) + 1 FROM bom_template_revisions"
    ).fetchone()[0]
    con.execute("UPDATE bom_template_revisions SET is_active=0")
    con.execute(
        "INSERT INTO bom_template_revisions "
        "(rev_num, filename, file_path, uploaded_by, note, is_active) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        (next_rev, filename, file_path, uploaded_by, note)
    )
    con.commit()
    con.close()
    return get_active_bom_template()


def activate_bom_template_revision(rev_id: int) -> bool:
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT id FROM bom_template_revisions WHERE id=?", (rev_id,)).fetchone()
    if not row:
        con.close()
        return False
    con.execute("UPDATE bom_template_revisions SET is_active=0")
    con.execute("UPDATE bom_template_revisions SET is_active=1 WHERE id=?", (rev_id,))
    con.commit()
    con.close()
    return True


def delete_bom_template_revision(rev_id: int) -> Optional[dict]:
    """삭제 전 정보 반환 (호출자가 파일 정리). 활성이었다면 가장 최근 리비전을 자동 활성화."""
    import os as _os
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM bom_template_revisions WHERE id=?", (rev_id,)).fetchone()
    if not row:
        con.close()
        return None
    info = dict(row)
    was_active = bool(info.get('is_active'))
    con.execute("DELETE FROM bom_template_revisions WHERE id=?", (rev_id,))
    if was_active:
        latest = con.execute(
            "SELECT id FROM bom_template_revisions ORDER BY rev_num DESC LIMIT 1"
        ).fetchone()
        if latest:
            con.execute("UPDATE bom_template_revisions SET is_active=1 WHERE id=?", (latest['id'],))
    con.commit()
    con.close()
    return info


def update_bom_template_note(rev_id: int, note: str) -> bool:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "UPDATE bom_template_revisions SET note=? WHERE id=?", (note, rev_id)
    )
    con.commit()
    affected = cur.rowcount
    con.close()
    return affected > 0
