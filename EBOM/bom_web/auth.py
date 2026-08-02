"""
인증 모듈 — SQLite 기반 사용자 관리, JWT 토큰, bcrypt 비밀번호 해시
"""
import sqlite3, os, hashlib, json, re
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

# 개발단계 = 부품 개발 단계 (참조 라벨, 연결 키 아님). 차종 구분(SP3/SP3 PE/SP3 27MY)은 차종 마스터에서 관리.
DEFAULT_DEV_STAGES = ['모델고정', 'P1', 'P2', 'M', 'SOP']
# 이전(차종성 라벨 혼입) 시드를 자동 정정하기 위한 판별용
_LEGACY_STAGE_MARKERS = {'PE', '24MY', '25MY', '26MY', '기본차', '양산', 'P3'}


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
    try:
        con.execute("ALTER TABLE users ADD COLUMN name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    con.execute('''
        CREATE TABLE IF NOT EXISTS vehicle_codes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            code       TEXT NOT NULL,
            name       TEXT NOT NULL,
            memo       TEXT DEFAULT '',
            mfg_code   TEXT DEFAULT '',
            powertrain TEXT DEFAULT '',
            created    TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(code, mfg_code)
        )
    ''')
    con.execute('''
        CREATE TABLE IF NOT EXISTS production_qty (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_code TEXT NOT NULL,
            year         INTEGER NOT NULL,
            month        INTEGER NOT NULL,
            week_no      INTEGER NOT NULL,
            plan_qty     INTEGER DEFAULT 0,
            actual_qty   INTEGER DEFAULT 0,
            updated_by   TEXT DEFAULT '',
            updated      TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(vehicle_code, year, month, week_no)
        )
    ''')
    # 매출/영업이익 수기 입력 컬럼 (나중에 영업 단가 원본에서 자동 산출 예정)
    for _col in ('revenue', 'profit'):
        try:
            con.execute(f"ALTER TABLE production_qty ADD COLUMN {_col} INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
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
    # 기존 DB 마이그레이션 — file_hash / stage 컬럼 추가 (이미 있으면 무시)
    try:
        con.execute("ALTER TABLE stored_boms ADD COLUMN file_hash TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        con.execute("ALTER TABLE stored_boms ADD COLUMN stage TEXT DEFAULT ''")
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
    # HKMC PEL(부품사양서) → DYA E-BOM 자동생성 이력 (차종/공장 기준 슬롯 업로드)
    con.execute('''
        CREATE TABLE IF NOT EXISTS bom_generate_history (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_info     TEXT DEFAULT '',
            pel_gj_filename  TEXT DEFAULT '',
            pel_hs_filename  TEXT DEFAULT '',
            bre_gj_filename  TEXT DEFAULT '',
            bre_hs_filename  TEXT DEFAULT '',
            template_rev     INTEGER,
            template_filename TEXT DEFAULT '',
            vc_count         INTEGER DEFAULT 0,
            matched          INTEGER DEFAULT 0,
            unmatched        INTEGER DEFAULT 0,
            plants_used      TEXT DEFAULT '',
            output_path      TEXT NOT NULL,
            output_filename  TEXT DEFAULT '',
            uploaded_by      TEXT NOT NULL,
            created          TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    # CCC 업로드 이력
    con.execute('''
        CREATE TABLE IF NOT EXISTS ccc_uploads (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_code TEXT NOT NULL,
            stage        TEXT NOT NULL,
            revision     TEXT NOT NULL DEFAULT 'VER.1',
            description  TEXT DEFAULT '',
            filename     TEXT NOT NULL,
            file_id      TEXT NOT NULL,
            file_ext     TEXT NOT NULL,
            uploaded_by  TEXT NOT NULL,
            is_active    INTEGER DEFAULT 1,
            created      TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    # CCC 코드 항목
    con.execute('''
        CREATE TABLE IF NOT EXISTS ccc_items (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id     INTEGER NOT NULL,
            ccc_code      TEXT NOT NULL,
            material_type TEXT DEFAULT '',
            color_name    TEXT DEFAULT '',
            key_color     TEXT DEFAULT '',
            door_code     TEXT DEFAULT '',
            stitch_code   TEXT DEFAULT '',
            market_codes  TEXT DEFAULT '',
            remarks       TEXT DEFAULT '',
            FOREIGN KEY (upload_id) REFERENCES ccc_uploads(id)
        )
    ''')
    # 영업 단가 입력 (13자리 기준)
    con.execute('''
        CREATE TABLE IF NOT EXISTS sales_prices (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_code    TEXT NOT NULL,
            stage           TEXT NOT NULL,
            part_no_10      TEXT NOT NULL,
            ccc_code        TEXT NOT NULL,
            part_no_13      TEXT UNIQUE NOT NULL,
            unit_price      REAL,
            currency        TEXT DEFAULT 'KRW',
            effective_date  TEXT DEFAULT '',
            input_by        TEXT NOT NULL,
            created         TEXT DEFAULT (datetime('now','localtime')),
            updated         TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    # E-BOM 게시판 업로드 이력
    con.execute('''
        CREATE TABLE IF NOT EXISTS ebom_uploads (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_code TEXT NOT NULL,
            stage        TEXT NOT NULL,
            revision     TEXT NOT NULL DEFAULT 'VER.1',
            description  TEXT DEFAULT '',
            filename     TEXT NOT NULL,
            file_id      TEXT NOT NULL,
            uploaded_by  TEXT NOT NULL,
            is_active    INTEGER DEFAULT 1,
            created      TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    # 마이그레이션 — 열/위치/파일경로/사양구분(variant) 컬럼 추가
    for col in ('row_num', 'position', 'file_path', 'variant'):
        try:
            con.execute(f"ALTER TABLE ebom_uploads ADD COLUMN {col} TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
    # E-BOM 1레벨 품번/품명
    con.execute('''
        CREATE TABLE IF NOT EXISTS ebom_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id    INTEGER NOT NULL,
            level        INTEGER DEFAULT 1,
            pno          TEXT NOT NULL,
            description  TEXT DEFAULT '',
            variant_code TEXT DEFAULT '',
            qty          TEXT DEFAULT '',
            FOREIGN KEY (upload_id) REFERENCES ebom_uploads(id)
        )
    ''')
    # E-BOM N열 사양 («T&P+A/LEATHER+PWR» 형태). description 은 품명이라 사양 비교에
    # 쓸 수 없어 별도 컬럼으로 둔다 — E-BOM & M-BOM 비교의 축별 판정 입력값.
    try:
        con.execute("ALTER TABLE ebom_items ADD COLUMN spec TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # ── E-BOM 시트 편집(2안) — 엑셀을 셀 단위로 DB에 적재해 웹에서 편집 ────────
    # 파일을 통째로 메모리에 올리면 485행x75열짜리 하나에 수십 초·수백 MB가 들어
    # 서버(RAM 956MB, 서비스 상한 750MB)가 위험하다. 셀을 DB에 넣고 필요한 만큼만
    # 내려주는 구조로 간다. 다운로드는 원본 워크북에 변경 셀만 덮어써 서식을 보존한다.
    con.execute('''
        CREATE TABLE IF NOT EXISTS ebom_sheets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_code  TEXT NOT NULL,
            stage         TEXT DEFAULT '',
            title         TEXT DEFAULT '',
            filename      TEXT NOT NULL,
            file_path     TEXT NOT NULL,
            sheet_name    TEXT DEFAULT '',
            n_rows        INTEGER DEFAULT 0,
            n_cols        INTEGER DEFAULT 0,
            current_rev   INTEGER DEFAULT 0,
            locked_by     TEXT DEFAULT '',
            locked_at     TEXT DEFAULT '',
            created_by    TEXT NOT NULL,
            created       TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    # 현재 셀 값. 원본 그대로 적재한 뒤 편집분을 이 테이블에 반영한다.
    con.execute('''
        CREATE TABLE IF NOT EXISTS ebom_sheet_cells (
            sheet_id  INTEGER NOT NULL,
            row_idx   INTEGER NOT NULL,
            col_idx   INTEGER NOT NULL,
            value     TEXT DEFAULT '',
            PRIMARY KEY (sheet_id, row_idx, col_idx)
        ) WITHOUT ROWID
    ''')
    # 리비전 — 저장할 때마다 «그 저장에서 바뀐 셀»만 JSON으로 남긴다(전체 스냅샷은
    # 36000셀×리비전이라 금방 커진다). 되돌리기는 before 값으로 역적용한다.
    con.execute('''
        CREATE TABLE IF NOT EXISTS ebom_sheet_revs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            sheet_id  INTEGER NOT NULL,
            rev_num   INTEGER NOT NULL,
            changes   TEXT DEFAULT '',
            n_changes INTEGER DEFAULT 0,
            note      TEXT DEFAULT '',
            saved_by  TEXT NOT NULL,
            saved_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_sheet_rev ON ebom_sheet_revs(sheet_id, rev_num DESC)''')
    # 엑셀 서식(열너비·행높이·병합·셀 색/글꼴/정렬)을 JSON으로 보관 — 화면을 엑셀과
    # 똑같이 그리기 위함. 셀마다 스타일을 다 넣으면 커지므로 스타일을 중복제거해
    # {스타일ID: 정의} + {셀: 스타일ID} 형태로 저장한다(엑셀 자체와 같은 방식).
    try:
        con.execute("ALTER TABLE ebom_sheets ADD COLUMN layout TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # 처리 버전 — 서식 추출 로직이나 자동등록 규칙이 개선되면 이 값을 올린다.
    # 원본 파일을 보관하고 있으므로, 시트를 열 때 구버전이면 재업로드 없이 그 자리에서
    # 다시 뽑아 갱신한다(사용자 편집 셀은 건드리지 않고 서식·품목등록만 갱신).
    try:
        con.execute("ALTER TABLE ebom_sheets ADD COLUMN proc_ver INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # ── 품목 마스터(PLM 연동 대상) ────────────────────────────────────────────
    # BOM 엑셀을 올리면 전 레벨 품번·품명이 자동 등록되고, 각 품목의 스펙(재질·중량·
    # MS SPEC·도면 등)을 사람이 채워 넣는다. 품번이 회사 전체의 연결 키라 UNIQUE.
    con.execute('''
        CREATE TABLE IF NOT EXISTS parts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            part_no       TEXT UNIQUE NOT NULL,
            part_name     TEXT DEFAULT '',
            vehicle_code  TEXT DEFAULT '',
            level         INTEGER,
            oem           TEXT DEFAULT '',
            customer_pno  TEXT DEFAULT '',
            co_vehicle    TEXT DEFAULT '',
            ms_spec       TEXT DEFAULT '',
            material      TEXT DEFAULT '',
            catia_weight  TEXT DEFAULT '',
            real_weight   TEXT DEFAULT '',
            thickness     TEXT DEFAULT '',
            surface       TEXT DEFAULT '',
            drawing_size  TEXT DEFAULT '',
            release_date  TEXT DEFAULT '',
            supplier      TEXT DEFAULT '',
            supplier_pno  TEXT DEFAULT '',
            seat_type1    TEXT DEFAULT '',
            seat_type2    TEXT DEFAULT '',
            status_part   TEXT DEFAULT '',
            note          TEXT DEFAULT '',
            revision      INTEGER DEFAULT 0,
            created_by    TEXT DEFAULT '',
            updated_by    TEXT DEFAULT '',
            created       TEXT DEFAULT (datetime('now','localtime')),
            updated       TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_parts_veh ON parts(vehicle_code)''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_parts_name ON parts(part_name)''')
    # 첨부파일·도면 (kind: attach | drawing)
    con.execute('''
        CREATE TABLE IF NOT EXISTS part_files (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            part_no     TEXT NOT NULL,
            kind        TEXT DEFAULT 'attach',
            filename    TEXT NOT NULL,
            file_path   TEXT NOT NULL,
            uploaded_by TEXT DEFAULT '',
            uploaded    TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_partfile ON part_files(part_no)''')
    # 파일 리비전 — 같은 품번·같은 종류(도면/첨부)로 다시 올리면 리비전이 올라가고
    # 이전 파일은 이력으로 남는다. 없으면 어느 것이 최신인지 알 수 없다.
    for _col, _ddl in (('revision', "ALTER TABLE part_files ADD COLUMN revision INTEGER DEFAULT 1"),
                       ('eo_no', "ALTER TABLE part_files ADD COLUMN eo_no TEXT DEFAULT ''"),
                       ('note', "ALTER TABLE part_files ADD COLUMN note TEXT DEFAULT ''")):
        try:
            con.execute(_ddl)
        except sqlite3.OperationalError:
            pass
    # REVISION 현황 — 스펙을 저장할 때마다 스냅샷을 남긴다
    con.execute('''
        CREATE TABLE IF NOT EXISTS part_revs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            part_no   TEXT NOT NULL,
            rev_num   INTEGER NOT NULL,
            part_name TEXT DEFAULT '',
            material  TEXT DEFAULT '',
            eo_no     TEXT DEFAULT '',
            approval  TEXT DEFAULT '미상신',
            note      TEXT DEFAULT '',
            saved_by  TEXT DEFAULT '',
            saved_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_partrev ON part_revs(part_no, rev_num DESC)''')
    # ── 문서 게시판 (kind: usage=사이트 이용방법 / rfp=PLM·ERP RFP) ──────────────
    # 둘 다 «섹션별 문서 + 첨부 + 수정 이력» 구조라 한 스키마로 처리한다.
    # usage 는 관리자만 열람·작성, rfp 는 관리자 작성 + 로그인 사용자 열람.
    con.execute('''
        CREATE TABLE IF NOT EXISTS doc_posts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            kind       TEXT NOT NULL,
            category   TEXT DEFAULT '',
            title      TEXT NOT NULL,
            body       TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            revision   INTEGER DEFAULT 1,
            created_by TEXT NOT NULL,
            updated_by TEXT DEFAULT '',
            created    TEXT DEFAULT (datetime('now','localtime')),
            updated    TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_doc_kind ON doc_posts(kind, sort_order, id)''')
    con.execute('''
        CREATE TABLE IF NOT EXISTS doc_files (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id     INTEGER NOT NULL,
            filename    TEXT NOT NULL,
            file_path   TEXT NOT NULL,
            uploaded_by TEXT DEFAULT '',
            uploaded    TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_docfile ON doc_files(post_id)''')
    # ── 설계변경 통보서 (EO) ──────────────────────────────────────────────────
    # PLM 설계변경 통보서 화면과 같은 구성. 엑셀 일괄 업로드와 개별 등록을 모두 지원한다.
    con.execute('''
        CREATE TABLE IF NOT EXISTS eo_notices (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            eo_no          TEXT NOT NULL,
            eo_date        TEXT DEFAULT '',
            content        TEXT DEFAULT '',
            vehicle_code   TEXT DEFAULT '',
            event_stage    TEXT DEFAULT '',
            dev_schedule   TEXT DEFAULT '',
            ecr_no         TEXT DEFAULT '',
            cust_eo_no     TEXT DEFAULT '',
            cust_part_no   TEXT DEFAULT '',
            registrant     TEXT DEFAULT '',
            eo_type        TEXT DEFAULT '',
            status         TEXT DEFAULT '',
            note           TEXT DEFAULT '',
            created_by     TEXT DEFAULT '',
            created        TEXT DEFAULT (datetime('now','localtime')),
            updated        TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(eo_no)
        )
    ''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_eo_date ON eo_notices(eo_date DESC, id DESC)''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_eo_veh ON eo_notices(vehicle_code)''')
    con.execute('''
        CREATE TABLE IF NOT EXISTS eo_files (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            eo_id       INTEGER NOT NULL,
            filename    TEXT NOT NULL,
            file_path   TEXT NOT NULL,
            uploaded_by TEXT DEFAULT '',
            uploaded    TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_eofile ON eo_files(eo_id)''')
    # 통보서 상세 항목 — 결재/참조, 변경사유 9종, 변동내역, 귀책구분 등
    for _col, _type in (
            ('approval_json', "TEXT DEFAULT ''"),     # [{role,name,date}]
            ('refs_text', "TEXT DEFAULT ''"),         # 참조자
            ('code_type', "TEXT DEFAULT ''"),         # 설계 / 지정 / 자체
            ('apply_date', "TEXT DEFAULT ''"),        # 적용시기
            ('mandatory_eo', "TEXT DEFAULT ''"),
            ('past_part_no', "TEXT DEFAULT ''"),      # 과거자번호
            ('analysis_result', "TEXT DEFAULT ''"),   # 해석결과
            ('reasons_json', "TEXT DEFAULT ''"),      # {A:건수, ..., I:건수}
            ('cost_change', "TEXT DEFAULT ''"),       # 원가(원)
            ('weight_change', "TEXT DEFAULT ''"),     # 중량(g)
            ('investment', "TEXT DEFAULT ''"),        # 투자비(천원)
            ('fault_own', "TEXT DEFAULT ''"),         # 당사 귀책
            ('fault_supplier', "TEXT DEFAULT ''"),    # 협력사 귀책
            ('fault_customer', "TEXT DEFAULT ''")):   # 고객사 귀책
        try:
            con.execute(f"ALTER TABLE eo_notices ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass
    # 도면현황 — 2D/3D, 설계용/배포용 구분
    for _col, _type in (('doc_kind', "TEXT DEFAULT 'doc'"),      # 2d | 3d | doc
                        ('purpose', "TEXT DEFAULT ''"),          # design | dist
                        ('size_no', "TEXT DEFAULT ''"),
                        ('file_type', "TEXT DEFAULT 'file'"),
                        ('modified', "TEXT DEFAULT ''")):
        try:
            con.execute(f"ALTER TABLE eo_files ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass
    # 품목현황 — 통보서에 걸린 품번 목록
    con.execute('''
        CREATE TABLE IF NOT EXISTS eo_items (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            eo_id      INTEGER NOT NULL,
            part_no    TEXT NOT NULL,
            part_name  TEXT DEFAULT '',
            revision   TEXT DEFAULT '',
            note       TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0
        )
    ''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_eoitem ON eo_items(eo_id, sort_order)''')
    # 전자결재 — 결재선 단계별 승인/반려와 의견
    con.execute('''
        CREATE TABLE IF NOT EXISTS eo_approvals (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            eo_id     INTEGER NOT NULL,
            round_no  INTEGER DEFAULT 1,
            step_no   INTEGER NOT NULL,
            role      TEXT DEFAULT '',
            approver  TEXT DEFAULT '',
            status    TEXT DEFAULT 'pending',
            comment   TEXT DEFAULT '',
            acted_at  TEXT DEFAULT ''
        )
    ''')
    # 반려 후 재상신하면 «회차»를 올려 새 결재선을 쌓는다 — 지난 반려 사유가 남아야 하기 때문
    try:
        con.execute('ALTER TABLE eo_approvals ADD COLUMN round_no INTEGER DEFAULT 1')
    except sqlite3.OperationalError:
        pass
    con.execute('''CREATE INDEX IF NOT EXISTS idx_eoappr ON eo_approvals(eo_id, step_no)''')
    # 메일 발송 이력 — SMTP 미설정이면 실제 발송 없이 기록만 남긴다(오발송 방지)
    con.execute('''
        CREATE TABLE IF NOT EXISTS eo_mails (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            eo_id    INTEGER NOT NULL,
            to_addr  TEXT DEFAULT '',
            subject  TEXT DEFAULT '',
            body     TEXT DEFAULT '',
            status   TEXT DEFAULT '',
            detail   TEXT DEFAULT '',
            sent_by  TEXT DEFAULT '',
            sent_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_eomail ON eo_mails(eo_id, id DESC)''')
    for _col in ('approval_status', 'submitted_by', 'submitted_at'):
        try:
            con.execute(f"ALTER TABLE eo_notices ADD COLUMN {_col} TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
    # 고객 마스터 — 고객EO 게시판의 «고객» 드롭다운. 관리자가 추가·수정한다.
    con.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            code    TEXT UNIQUE NOT NULL,
            name    TEXT DEFAULT '',
            sort_no INTEGER DEFAULT 0,
            active  INTEGER DEFAULT 1,
            note    TEXT DEFAULT ''
        )
    ''')
    # 고객 EO — 사내 PLM「고객EO 등록」과 같은 구조
    con.execute('''
        CREATE TABLE IF NOT EXISTS cust_eo (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cust_code       TEXT DEFAULT '',
            cust_eo_no      TEXT UNIQUE NOT NULL,
            cust_eo_no2     TEXT DEFAULT '',
            eo_date         TEXT DEFAULT '',
            recv_date       TEXT DEFAULT '',
            vehicle_code    TEXT DEFAULT '',
            event_stage     TEXT DEFAULT '',
            dev_schedule    TEXT DEFAULT '',
            eo_type         TEXT DEFAULT '',
            as_compat       TEXT DEFAULT '',
            weight_g        TEXT DEFAULT '',
            cost_w          TEXT DEFAULT '',
            content         TEXT DEFAULT '',
            design_apply_at TEXT DEFAULT '',
            mfg_apply_at    TEXT DEFAULT '',
            extra_part_nos  TEXT DEFAULT '',
            registrant      TEXT DEFAULT '',
            note            TEXT DEFAULT '',
            approval_json   TEXT DEFAULT '',
            approval_status TEXT DEFAULT 'draft',
            submitted_by    TEXT DEFAULT '',
            submitted_at    TEXT DEFAULT '',
            created_by      TEXT DEFAULT '',
            created         TEXT DEFAULT (datetime('now','localtime')),
            updated         TEXT DEFAULT ''
        )
    ''')
    con.execute('''CREATE TABLE IF NOT EXISTS cust_eo_links (
            cust_eo_id INTEGER NOT NULL, eo_id INTEGER NOT NULL,
            PRIMARY KEY (cust_eo_id, eo_id))''')
    con.execute('''CREATE TABLE IF NOT EXISTS cust_eo_rels (
            cust_eo_id INTEGER NOT NULL, rel_cust_eo_id INTEGER NOT NULL,
            PRIMARY KEY (cust_eo_id, rel_cust_eo_id))''')
    con.execute('''
        CREATE TABLE IF NOT EXISTS cust_eo_files (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cust_eo_id  INTEGER NOT NULL,
            kind        TEXT DEFAULT 'etc',
            filename    TEXT DEFAULT '',
            file_path   TEXT DEFAULT '',
            size_no     INTEGER DEFAULT 0,
            uploaded_by TEXT DEFAULT '',
            uploaded    TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_custeo_f ON cust_eo_files(cust_eo_id)''')
    # 결재는 내부 EO 와 같은 표를 쓰되 doc_type 으로 문서 종류를 구분한다
    try:
        con.execute("ALTER TABLE eo_approvals ADD COLUMN doc_type TEXT DEFAULT 'eo'")
    except sqlite3.OperationalError:
        pass
    # 조직도 — 사내 PLM에서 1회 등록. emp_id 가 곧 우리 로그인 계정(users.username)이다.
    con.execute('''
        CREATE TABLE IF NOT EXISTS org_members (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_id    TEXT UNIQUE NOT NULL,
            name      TEXT DEFAULT '',
            site      TEXT DEFAULT '',
            dept_code TEXT DEFAULT '',
            dept_name TEXT DEFAULT '',
            position  TEXT DEFAULT '',
            email     TEXT DEFAULT '',
            phone     TEXT DEFAULT '',
            sort_no   INTEGER DEFAULT 0,
            active    INTEGER DEFAULT 1,
            updated   TEXT DEFAULT ''
        )
    ''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_org ON org_members(dept_name, name)''')
    # 결재선 단계에 «결재형태 코드»(PLM 0/3/4/9/1)와 사번을 남긴다
    for _c in ('role_code', 'emp_id'):
        try:
            con.execute(f"ALTER TABLE eo_approvals ADD COLUMN {_c} TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
    # 카티아 2D/3D 파일 — 파일명이 «품번__리비전__품명__EO번호__날짜» 규칙이라 메타를 자동 추출한다.
    # 폴더 단계(선행검토/양금시작차/SOP…)는 «폴더로 나누지 않고» stage 열로 들고 있다가 표에서 보여준다.
    con.execute('''
        CREATE TABLE IF NOT EXISTS catia_files (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_code TEXT DEFAULT '',
            row_level    TEXT DEFAULT '',
            stage        TEXT DEFAULT '',
            part_group   TEXT DEFAULT '',
            kind         TEXT DEFAULT '',
            part_no      TEXT DEFAULT '',
            rev          TEXT DEFAULT '',
            rev_sort     INTEGER DEFAULT 0,
            part_name    TEXT DEFAULT '',
            part_type    TEXT DEFAULT '',
            side         TEXT DEFAULT '',
            eo_no        TEXT DEFAULT '',
            file_date    TEXT DEFAULT '',
            filename     TEXT DEFAULT '',
            file_path    TEXT DEFAULT '',
            ext          TEXT DEFAULT '',
            size_no      INTEGER DEFAULT 0,
            parsed       INTEGER DEFAULT 1,
            note         TEXT DEFAULT '',
            uploaded_by  TEXT DEFAULT '',
            uploaded     TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    # 도면 체크아웃(잠금)과 수명주기 상태 — PLM 기본 기능. 잠금 단위는 «차종 + 품번».
    con.execute('''
        CREATE TABLE IF NOT EXISTS catia_items (
            vehicle_code TEXT NOT NULL,
            base_no      TEXT NOT NULL,
            state        TEXT DEFAULT 'work',
            locked_by    TEXT DEFAULT '',
            locked_at    TEXT DEFAULT '',
            locked_note  TEXT DEFAULT '',
            released_rev TEXT DEFAULT '',
            updated_by   TEXT DEFAULT '',
            updated      TEXT DEFAULT '',
            PRIMARY KEY (vehicle_code, base_no)
        )
    ''')
    con.execute('''
        CREATE TABLE IF NOT EXISTS catia_item_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_code TEXT DEFAULT '',
            base_no      TEXT DEFAULT '',
            action       TEXT DEFAULT '',
            username     TEXT DEFAULT '',
            detail       TEXT DEFAULT '',
            at           TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_catia_log ON catia_item_log(vehicle_code, base_no, id DESC)''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_catia ON catia_files(vehicle_code, part_group, part_no)''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_catia_pno ON catia_files(part_no, kind, rev_sort)''')
    # 국가코드 마스터
    con.execute('''
        CREATE TABLE IF NOT EXISTS country_codes (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            code           TEXT UNIQUE NOT NULL,
            region         TEXT DEFAULT '',
            hkmc_code      TEXT DEFAULT '',
            code1          TEXT DEFAULT '',
            code2          TEXT DEFAULT '',
            countries      TEXT DEFAULT '',
            display_order  INTEGER DEFAULT 0,
            created        TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    # 기존 DB 마이그레이션 — hkmc_code/code1/code2 컬럼 추가
    for col in ('hkmc_code', 'code1', 'code2'):
        try:
            con.execute(f"ALTER TABLE country_codes ADD COLUMN {col} TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
    # 국가코드 PPT/이미지 리비전
    con.execute('''
        CREATE TABLE IF NOT EXISTS country_ppt_revisions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            rev_num     INTEGER NOT NULL,
            filename    TEXT NOT NULL,
            file_path   TEXT NOT NULL,
            file_ext    TEXT NOT NULL,
            uploaded_by TEXT NOT NULL,
            uploaded_at TEXT DEFAULT (datetime('now','localtime')),
            note        TEXT DEFAULT '',
            is_active   INTEGER DEFAULT 0
        )
    ''')
    con.execute('''
        CREATE TABLE IF NOT EXISTS process_diagrams (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            title         TEXT NOT NULL,
            description   TEXT DEFAULT '',
            filename      TEXT NOT NULL,
            file_path     TEXT NOT NULL,
            file_ext      TEXT NOT NULL,
            uploaded_by   TEXT NOT NULL,
            uploaded_at   TEXT DEFAULT (datetime('now','localtime')),
            display_order INTEGER DEFAULT 0
        )
    ''')
    # 전체 흐름도(코드로 그린 SVG) 대체용 사진 — 단일 행(id=1)만 존재, 관리자가 업로드하면
    # 그 사진이 SVG 자리를 대체하고, 삭제(복원)하면 다시 SVG가 보임
    con.execute('''
        CREATE TABLE IF NOT EXISTS flowchart_override (
            id           INTEGER PRIMARY KEY CHECK (id=1),
            filename     TEXT NOT NULL,
            file_path    TEXT NOT NULL,
            file_ext     TEXT NOT NULL,
            uploaded_by  TEXT NOT NULL,
            uploaded_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    # 게시판별 "사용 방법" 안내 이미지 — board(게시판 식별자)당 1장, 관리자가 업로드/삭제.
    # 신입사원이 게시판 용도를 한눈에 이해하도록 사용 설명서 아래에 그림으로 보여줌.
    con.execute('''
        CREATE TABLE IF NOT EXISTS board_guide_image (
            board        TEXT PRIMARY KEY,
            filename     TEXT NOT NULL,
            file_path    TEXT NOT NULL,
            file_ext     TEXT NOT NULL,
            uploaded_by  TEXT NOT NULL,
            uploaded_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    # CCC 매트릭스 (재질 × 국가코드 → CCC 코드) — 차종별
    con.execute('''
        CREATE TABLE IF NOT EXISTS ccc_matrix (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_code  TEXT NOT NULL,
            stage         TEXT NOT NULL,
            material_type TEXT NOT NULL,
            country_code  TEXT NOT NULL,
            ccc_code      TEXT NOT NULL DEFAULT '',
            updated_by    TEXT DEFAULT '',
            updated       TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(vehicle_code, stage, material_type, country_code)
        )
    ''')
    # CCC 차종단독 전환 — stage를 ''로 통합 (연결 키는 차종). 중복 (차종,재질,국가) 시 최신만 유지.
    if con.execute("SELECT 1 FROM ccc_matrix WHERE stage!='' LIMIT 1").fetchone():
        allrows = con.execute(
            "SELECT id, vehicle_code, material_type, country_code FROM ccc_matrix "
            "ORDER BY updated DESC, id DESC"
        ).fetchall()
        seen, keep = set(), set()
        for rid, v, m, ct in allrows:
            k = (v, m, ct)
            if k in seen:
                continue
            seen.add(k); keep.add(rid)
        for rid, v, m, ct in allrows:
            if rid in keep:
                con.execute("UPDATE ccc_matrix SET stage='' WHERE id=?", (rid,))
            else:
                con.execute("DELETE FROM ccc_matrix WHERE id=?", (rid,))
    # 영업단가 매트릭스 (1레벨품번 × 재질 × 국가코드 → CCC + 단가)
    con.execute('''
        CREATE TABLE IF NOT EXISTS sales_prices_v2 (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_code  TEXT NOT NULL,
            stage         TEXT NOT NULL,
            part_no       TEXT NOT NULL,
            part_name     TEXT DEFAULT '',
            material_type TEXT NOT NULL,
            country_code  TEXT NOT NULL,
            ccc_code      TEXT DEFAULT '',
            unit_price    REAL,
            currency      TEXT DEFAULT 'KRW',
            effective_date TEXT DEFAULT '',
            input_by      TEXT NOT NULL,
            created       TEXT DEFAULT (datetime('now','localtime')),
            updated       TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(vehicle_code, stage, part_no, material_type, country_code)
        )
    ''')
    # 마이그레이션 — 비교품번(변경전) 컬럼
    try:
        con.execute("ALTER TABLE sales_prices_v2 ADD COLUMN compare_pno TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # 기본 국가코드 데이터 삽입 (없을 때만)
    if not con.execute("SELECT id FROM country_codes LIMIT 1").fetchone():
        default_countries = [
            ('K1', '내수', '한국', 1),
            ('K2', '일반', '아프가니스탄,방글라데시,부탄,미얀마,캄보디아,타이완,피지,인도,인도네시아 아,라오스,말레이시아,몽골,네팔,파키스탄,파푸아뉴기니,필리핀,싱가포르,스리랑카,태국,베트남,사모아,소로몬제도,마카오,마샬제도,미크로네시아,팔라우,아르헨티나,바하마,바베이도스,볼리비아,칠레,콜롬비아,코스타리카,쿠바,도미니카공화국,에콰도르,엘살바도르,과테말라,아이티,온두라스,자메이카,니카라과,파나마,파라과이,페루,수리남,트리니다드토바고,우루과이,베네수엘라,벨리즈,요르단,레바논,모로코,시리아,앙골라,보츠와나,부룬디,카메룬,중앙아프리카공화국,콩고,가봉,감비아,가나,기니,기니비사우,코트디부아르,케냐,라이베리아,마다가스카르,말라위,말리,모리셔스,모잠비크,니제르,나이지리아,르완다,세네갈,세이셸,남아프리카공화국,탄자니아,토고,우간다,콩고민주공화국,잠비아,베냉,부르키나파소', 2),
            ('K3', '중동', '브라질,바레인,이집트,이라크,쿠웨이트,리비아,오만,카타르,사우디아라비아,수단,튀니지,아랍에미리트,예멘,지부티,이란', 3),
            ('K4', '유럽', '일본,뉴질랜드,홍콩,뉴칼레도니아,멕시코,오스트리아,벨기에,덴마크,핀란드,프랑스,독일,아이슬란드,아일랜드,이탈리아,몰타,네덜란드,노르웨이,포르투갈,스페인,스웨덴,스위스,영국,알바니아,불가리아,체코,헝가리,폴란드,루마니아,유고슬라비아,슬로베니아,크로아티아,카자흐스탄,러시아,우크라이나,리투아니아,라트비아,에스토니아,솔로바키아,아르메니아,아제르바이잔,벨라루스,조지아,키르기스스탄,마케도니아,보스니아,이란,이스라엘,터키,그리스,팔레스타인', 4),
            ('K5', '호주', '오스트레일리아', 5),
            ('K6', '캐나다', '캐나다', 6),
            ('K7', '미국', 'U.S.A', 7),
            ('K8', '중국', 'CHINA', 8),
            ('K9', '러시아', '러시아', 9),
            ('KB', '브라질', '브라질', 10),
        ]
        for code, region, countries, order in default_countries:
            con.execute(
                "INSERT OR IGNORE INTO country_codes (code,region,countries,display_order) VALUES (?,?,?,?)",
                (code, region, countries, order)
            )
    # PEL 이력 관리 (차종별 게시판)
    con.execute('''
        CREATE TABLE IF NOT EXISTS pel_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_code  TEXT NOT NULL,
            stage         TEXT NOT NULL DEFAULT '',
            revision      TEXT NOT NULL DEFAULT 'VER.1',
            title         TEXT NOT NULL,
            description   TEXT DEFAULT '',
            filename      TEXT DEFAULT '',
            file_id       TEXT DEFAULT '',
            file_path     TEXT DEFAULT '',
            uploaded_by   TEXT NOT NULL,
            created       TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    con.execute('''CREATE INDEX IF NOT EXISTS idx_pelhist_vehicle
                   ON pel_history(vehicle_code, created DESC)''')
    # 개발단계 마스터 (전 게시판 공통 단계 목록)
    con.execute('''
        CREATE TABLE IF NOT EXISTS dev_stages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            code          TEXT UNIQUE NOT NULL,
            name          TEXT DEFAULT '',
            display_order INTEGER DEFAULT 0,
            created       TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    _stage_rows = [r[0] for r in con.execute("SELECT code FROM dev_stages").fetchall()]
    _need_reset = (not _stage_rows) or bool(set(_stage_rows) & _LEGACY_STAGE_MARKERS)
    if _need_reset:
        # 부품 개발 단계로 정정 (모델고정/P1/P2/M/SOP). 단계는 참조 라벨이므로
        # 기존 데이터 행의 stage 문자열에는 영향 없음(마스터 드롭다운 목록만 교체).
        con.execute("DELETE FROM dev_stages")
        for i, code in enumerate(DEFAULT_DEV_STAGES):
            con.execute("INSERT OR IGNORE INTO dev_stages (code,name,display_order) VALUES (?,?,?)",
                        (code, code, i + 1))
    # 차종 마스터 — 생관 차종 코드 (예: NQ5 → GY)
    try:
        con.execute("ALTER TABLE vehicle_codes ADD COLUMN mfg_code TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # 차종 마스터 A안 마이그레이션 — 파워트레인 열 + (차종코드,생관코드) 복합 유일키
    #   기존 code UNIQUE 제약을 제거하고 (code,mfg_code) 복합 유일키로 재구성.
    #   powertrain 열 부재 = 구 스키마 → 테이블 재작성(1회).
    _vc_cols = [r[1] for r in con.execute("PRAGMA table_info(vehicle_codes)")]
    if 'powertrain' not in _vc_cols:
        _mfg_sel = "COALESCE(mfg_code,'')" if 'mfg_code' in _vc_cols else "''"
        con.executescript(f'''
            CREATE TABLE vehicle_codes_new (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                code       TEXT NOT NULL,
                name       TEXT NOT NULL,
                memo       TEXT DEFAULT '',
                mfg_code   TEXT DEFAULT '',
                powertrain TEXT DEFAULT '',
                created    TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(code, mfg_code)
            );
            INSERT INTO vehicle_codes_new (id,code,name,memo,mfg_code,created)
                SELECT id, code, name, COALESCE(memo,''), {_mfg_sel},
                       COALESCE(created, datetime('now','localtime'))
                FROM vehicle_codes;
            DROP TABLE vehicle_codes;
            ALTER TABLE vehicle_codes_new RENAME TO vehicle_codes;
        ''')
        con.commit()
    # 파트 네임 정의 마스터 (KEY02~KEY06 시트 네임 매핑)
    con.execute('''
        CREATE TABLE IF NOT EXISTS part_names (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_code  TEXT DEFAULT '',
            part_key      TEXT NOT NULL,
            part_name     TEXT DEFAULT '',
            display_order INTEGER DEFAULT 0,
            created       TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(vehicle_code, part_key)
        )
    ''')
    # 원단코드 마스터 (DYA ALC-2 채번용)
    con.execute('''
        CREATE TABLE IF NOT EXISTS fabric_codes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            code          TEXT UNIQUE NOT NULL,
            fabric_code   TEXT DEFAULT '',
            name          TEXT DEFAULT '',
            stitch_color  TEXT DEFAULT '',
            base_color    TEXT DEFAULT '',
            hkmc_code     TEXT DEFAULT '',
            display_order INTEGER DEFAULT 0
        )
    ''')
    try:
        con.execute("ALTER TABLE fabric_codes ADD COLUMN hkmc_code TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    if not con.execute("SELECT id FROM fabric_codes LIMIT 1").fetchone():
        _fabrics = [
            ('A', 'KR6', 'X라인 블랙M그레이', '', 'OVG'),
            ('B', 'KR7', 'X라인브라운W그레이', '', 'CTD'),
            ('C', 'KRU', 'X라인그린S블랙', '', 'GKG'),
            ('D', 'KRW', 'X라인S블랙', '', 'OVS'),
            ('F', 'KRJ', '블랙M그레이', '', 'OVS'),
            ('G', 'KRN', '엠보브라운W그레이', '', 'CTD'),
            ('H', 'KRR', '엠보블랙M그레이', '', 'OVS'),
            ('K', 'KRZ', '펀칭브라운W그레이', '', 'CTD'),
            ('L', 'KRV', '펀칭블랙M그레이', '', 'OVS'),
        ]
        for i, (c, fc, nm, st, bc) in enumerate(_fabrics):
            con.execute("INSERT OR IGNORE INTO fabric_codes (code,fabric_code,name,stitch_color,base_color,display_order) VALUES (?,?,?,?,?,?)",
                        (c, fc, nm, st, bc, i + 1))
    # PEL 사양변경 (부품사양서 → 사양수현황 그리드) 이력
    con.execute('''
        CREATE TABLE IF NOT EXISTS pel_spec_uploads (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_code  TEXT NOT NULL,
            powertrain    TEXT DEFAULT '전체',
            factory       TEXT DEFAULT '공통',
            my_code       TEXT DEFAULT '',
            revision      TEXT NOT NULL DEFAULT 'VER.1',
            title         TEXT NOT NULL,
            description   TEXT DEFAULT '',
            filename      TEXT DEFAULT '',
            file_id       TEXT DEFAULT '',
            file_path     TEXT DEFAULT '',
            uploaded_by   TEXT NOT NULL,
            created       TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    for _col, _decl in [('factory', "TEXT DEFAULT '공통'"), ('my_code', "TEXT DEFAULT ''"),
                        ('row_level', "TEXT DEFAULT ''")]:
        try:
            con.execute(f"ALTER TABLE pel_spec_uploads ADD COLUMN {_col} {_decl}")
        except sqlite3.OperationalError:
            pass
    # 영업 단가 원본 파일 (차종별 리비전 게시판)
    con.execute('''
        CREATE TABLE IF NOT EXISTS sales_price_files (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_code  TEXT NOT NULL,
            powertrain    TEXT DEFAULT '전체',
            revision      TEXT NOT NULL DEFAULT 'VER.1',
            title         TEXT NOT NULL,
            description   TEXT DEFAULT '',
            filename      TEXT DEFAULT '',
            file_id       TEXT DEFAULT '',
            file_path     TEXT DEFAULT '',
            edits_json    TEXT DEFAULT '',
            uploaded_by   TEXT NOT NULL,
            created       TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    try:
        con.execute("ALTER TABLE sales_price_files ADD COLUMN edits_json TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # M-BOM: HKMC Q파트 & ALC 이력 관리 (게시글당 파일 5개)
    con.execute('''
        CREATE TABLE IF NOT EXISTS mbom_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_code  TEXT NOT NULL,
            stage         TEXT DEFAULT '',
            revision      TEXT DEFAULT 'VER.1',
            title         TEXT NOT NULL,
            description   TEXT DEFAULT '',
            uploaded_by   TEXT NOT NULL,
            created       TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    con.execute('''
        CREATE TABLE IF NOT EXISTS mbom_history_files (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id   INTEGER NOT NULL,
            slot      TEXT DEFAULT '',
            filename  TEXT NOT NULL,
            file_id   TEXT NOT NULL,
            file_path TEXT NOT NULL,
            FOREIGN KEY (post_id) REFERENCES mbom_history(id)
        )
    ''')
    # Q파트 ALC 통합 게시판 (mbom_history와 별개 신설 게시판 — 품번·사양 O/X 병합)
    con.execute('''
        CREATE TABLE IF NOT EXISTS qpart_merge_posts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle      TEXT NOT NULL,
            dev_stage    TEXT DEFAULT '',
            title        TEXT DEFAULT '',
            uploaded_by  TEXT NOT NULL,
            created      TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    con.execute('''
        CREATE TABLE IF NOT EXISTS qpart_merge_files (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id   INTEGER NOT NULL,
            slot      TEXT NOT NULL,
            filename  TEXT NOT NULL,
            file_path TEXT NOT NULL,
            uploaded  TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (post_id) REFERENCES qpart_merge_posts(id)
        )
    ''')
    con.execute('''
        CREATE TABLE IF NOT EXISTS qpart_merge_runs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id          INTEGER NOT NULL,
            output_path      TEXT NOT NULL,
            output_filename  TEXT DEFAULT '',
            spec_col_count   INTEGER DEFAULT 0,
            row_count        INTEGER DEFAULT 0,
            created          TEXT DEFAULT (datetime('now','localtime')),
            created_by       TEXT NOT NULL,
            FOREIGN KEY (post_id) REFERENCES qpart_merge_posts(id)
        )
    ''')
    # 마이그레이션 — 열 구분 컬럼 추가
    try:
        con.execute("ALTER TABLE pel_history ADD COLUMN column_div TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
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
    rows = con.execute("SELECT id,username,email,name,dept,role,created FROM users ORDER BY created DESC, id DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


def create_user(username: str, email: str, password: str, dept: str = '', name: str = '') -> dict:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "INSERT INTO users (username,email,hashed_pw,dept,role,name) VALUES (?,?,?,?,?,?)",
            (username.strip(), email.strip().lower(), _hash(password), dept.strip(), 'pending', name.strip())
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


def set_name(user_id: int, name: str):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE users SET name=? WHERE id=?", (name.strip(), user_id))
    con.commit(); con.close()


# ── 차종 코드 CRUD ────────────────────────────────────────────────────────────
def get_all_vehicle_codes(distinct: bool = True) -> list:
    """차종 목록. distinct=True: 드롭다운용(차종코드 1개씩, 최초 등록 행).
       distinct=False: 마스터 편집용(생산코드별 전체 행)."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    if distinct:
        rows = con.execute(
            "SELECT * FROM vehicle_codes WHERE id IN "
            "(SELECT MIN(id) FROM vehicle_codes GROUP BY code) ORDER BY code"
        ).fetchall()
    else:
        rows = con.execute("SELECT * FROM vehicle_codes ORDER BY code, mfg_code").fetchall()
    con.close()
    return [dict(r) for r in rows]


def add_vehicle_code(code: str, name: str, memo: str = '', mfg_code: str = '', powertrain: str = '') -> dict:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("INSERT INTO vehicle_codes (code,name,memo,mfg_code,powertrain) VALUES (?,?,?,?,?)",
                    (code.strip().upper(), name.strip(), memo.strip(),
                     mfg_code.strip().upper(), powertrain.strip()))
        con.commit()
        return {'ok': True}
    except sqlite3.IntegrityError:
        return {'ok': False, 'msg': '이미 등록된 (차종코드 + 생관코드) 조합입니다.'}
    finally:
        con.close()


def update_vehicle_code(code_id: int, code: str, name: str, memo: str = '',
                        mfg_code: str = '', powertrain: str = '') -> dict:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("UPDATE vehicle_codes SET code=?,name=?,memo=?,mfg_code=?,powertrain=? WHERE id=?",
                    (code.strip().upper(), name.strip(), memo.strip(),
                     mfg_code.strip().upper(), powertrain.strip(), code_id))
        con.commit()
        return {'ok': True}
    except sqlite3.IntegrityError:
        return {'ok': False, 'msg': '이미 등록된 (차종코드 + 생관코드) 조합입니다.'}
    finally:
        con.close()


def delete_vehicle_code(code_id: int):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM vehicle_codes WHERE id=?", (code_id,))
    con.commit(); con.close()


def get_vehicle_by_id(code_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM vehicle_codes WHERE id=?", (code_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def get_vehicle_by_code_mfg(code: str, mfg_code: str) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM vehicle_codes WHERE code=? AND mfg_code=?",
                      (code.strip().upper(), mfg_code.strip().upper())).fetchone()
    con.close()
    return dict(row) if row else None


def get_production_qty_rows(year: Optional[int] = None, month: Optional[int] = None) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    if year is not None and month is not None:
        rows = con.execute(
            "SELECT * FROM production_qty WHERE year=? AND month=? ORDER BY vehicle_code, week_no",
            (year, month)).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM production_qty ORDER BY year DESC, month DESC, vehicle_code, week_no").fetchall()
    con.close()
    return [dict(r) for r in rows]


def upsert_production_qty(vehicle_code: str, year: int, month: int, week_no: int,
                          plan_qty: int, actual_qty: int, updated_by: str = '',
                          revenue: int = 0, profit: int = 0) -> dict:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "INSERT INTO production_qty (vehicle_code,year,month,week_no,plan_qty,actual_qty,revenue,profit,updated_by) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(vehicle_code,year,month,week_no) DO UPDATE SET "
            "plan_qty=excluded.plan_qty, actual_qty=excluded.actual_qty, "
            "revenue=excluded.revenue, profit=excluded.profit, "
            "updated_by=excluded.updated_by, updated=datetime('now','localtime')",
            (vehicle_code.strip().upper(), year, month, week_no, plan_qty, actual_qty,
             revenue, profit, updated_by))
        con.commit()
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'msg': str(e)}
    finally:
        con.close()


def delete_production_qty(row_id: int):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM production_qty WHERE id=?", (row_id,))
    con.commit(); con.close()


def get_production_summary(year: int, month: int) -> list:
    """차종별 계획/실적 합계(해당 월의 전체 주차 합산)."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT vehicle_code, SUM(plan_qty) AS plan_sum, SUM(actual_qty) AS actual_sum, "
        "SUM(revenue) AS revenue_sum, SUM(profit) AS profit_sum "
        "FROM production_qty WHERE year=? AND month=? GROUP BY vehicle_code",
        (year, month)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_vehicle_code_by_code(code: str) -> Optional[dict]:
    """차종코드로 최초 등록 행 1개 반환(호환용)."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM vehicle_codes WHERE code=? ORDER BY id LIMIT 1",
                      (code.strip().upper(),)).fetchone()
    con.close()
    return dict(row) if row else None


def update_vehicle_code_by_code(old_code: str, code: str, name: str, memo: str = '', mfg_code: str = '') -> dict:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("UPDATE vehicle_codes SET code=?, name=?, memo=?, mfg_code=? WHERE code=?",
                    (code.strip().upper(), name.strip(), memo.strip(), mfg_code.strip().upper(), old_code.strip().upper()))
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
                    uploader: str, memo: str = '', file_hash: str = '', stage: str = '') -> dict:
    """새 BOM 저장. 같은 (차종, 열, 위치, kind) 조합 안에서 version_num 자동 증가."""
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "SELECT COALESCE(MAX(version_num), 0) FROM stored_boms WHERE vehicle_code=? AND row_num=? AND position=? AND kind=?",
        (vehicle_code, row_num, position, kind)
    )
    next_ver = cur.fetchone()[0] + 1
    con.execute('''
        INSERT INTO stored_boms (vehicle_code, row_num, position, kind, filename, file_id,
                                  file_path, uploader, memo, version_num, file_hash, stage)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (vehicle_code, row_num, position, kind, filename, file_id,
          file_path, uploader, memo, next_ver, file_hash, stage))
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
    """저장된 BOM 의 메타데이터 수정 (vehicle_code/row_num/position/memo/stage)."""
    allowed = {'vehicle_code', 'row_num', 'position', 'memo', 'stage'}
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


# ── HKMC PEL(부품사양서) → DYA E-BOM 자동생성 이력 ────────────────────────────
def add_bom_generate_history(**fields) -> int:
    """부품사양서 업로드/변환 성공 시마다 호출 — 영구 이력 기록 (서버 재시작 후에도 재다운로드용)."""
    con = sqlite3.connect(DB_PATH)
    cols = ['vehicle_info', 'pel_gj_filename', 'pel_hs_filename', 'bre_gj_filename', 'bre_hs_filename',
            'template_rev', 'template_filename', 'vc_count', 'matched', 'unmatched',
            'plants_used', 'output_path', 'output_filename', 'uploaded_by']
    vals = [fields.get(c) for c in cols]
    cur = con.execute(
        f"INSERT INTO bom_generate_history ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
        vals)
    new_id = cur.lastrowid
    con.commit(); con.close()
    return new_id


def get_bom_generate_history_list(limit: int = 200) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM bom_generate_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_bom_generate_history(item_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM bom_generate_history WHERE id=?", (item_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def delete_bom_generate_history(item_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM bom_generate_history WHERE id=?", (item_id,)).fetchone()
    if not row:
        con.close(); return None
    info = dict(row)
    con.execute("DELETE FROM bom_generate_history WHERE id=?", (item_id,))
    con.commit(); con.close()
    return info


# ── CCC 업로드 CRUD ───────────────────────────────────────────────────────────
def add_ccc_upload(vehicle_code, stage, revision, description, filename, file_id, file_ext, username) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "INSERT INTO ccc_uploads (vehicle_code,stage,revision,description,filename,file_id,file_ext,uploaded_by) VALUES (?,?,?,?,?,?,?,?)",
        (vehicle_code, stage, revision, description, filename, file_id, file_ext, username)
    )
    upload_id = cur.lastrowid
    con.commit(); con.close()
    return upload_id


def save_ccc_items(upload_id: int, items: list):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM ccc_items WHERE upload_id=?", (upload_id,))
    for it in items:
        con.execute(
            "INSERT INTO ccc_items (upload_id,ccc_code,material_type,color_name,key_color,door_code,stitch_code,market_codes,remarks) VALUES (?,?,?,?,?,?,?,?,?)",
            (upload_id, it.get('ccc_code',''), it.get('material_type',''), it.get('color_name',''),
             it.get('key_color',''), it.get('door_code',''), it.get('stitch_code',''),
             it.get('market_codes',''), it.get('remarks',''))
        )
    con.commit(); con.close()


def get_ccc_uploads(vehicle_code=None, stage=None) -> list:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    q = "SELECT * FROM ccc_uploads WHERE 1=1"
    params = []
    if vehicle_code:
        q += " AND vehicle_code=?"; params.append(vehicle_code)
    if stage:
        q += " AND stage=?"; params.append(stage)
    q += " ORDER BY created DESC, id DESC"
    rows = con.execute(q, params).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_ccc_upload(upload_id: int) -> dict:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM ccc_uploads WHERE id=?", (upload_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def get_ccc_items(upload_id: int) -> list:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM ccc_items WHERE upload_id=? ORDER BY material_type, ccc_code", (upload_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_ccc_items_by_vehicle(vehicle_code: str, stage: str = None) -> list:
    """활성 업로드 중 가장 최신 CCC 항목 반환"""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    q = """SELECT ci.* FROM ccc_items ci
           JOIN ccc_uploads cu ON ci.upload_id = cu.id
           WHERE cu.vehicle_code=? AND cu.is_active=1"""
    params = [vehicle_code]
    if stage:
        q += " AND cu.stage=?"; params.append(stage)
    q += " ORDER BY cu.created DESC"
    rows = con.execute(q, params).fetchall()
    con.close()
    return [dict(r) for r in rows]


def delete_ccc_upload(upload_id: int):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM ccc_items WHERE upload_id=?", (upload_id,))
    con.execute("DELETE FROM ccc_uploads WHERE id=?", (upload_id,))
    con.commit(); con.close()


# ── 영업 단가 CRUD ────────────────────────────────────────────────────────────
def upsert_sales_price(vehicle_code, stage, part_no_10, ccc_code, unit_price, currency, effective_date, username) -> dict:
    part_no_13 = f"{part_no_10}-{ccc_code}"
    con = sqlite3.connect(DB_PATH)
    existing = con.execute("SELECT id FROM sales_prices WHERE part_no_13=?", (part_no_13,)).fetchone()
    if existing:
        con.execute(
            "UPDATE sales_prices SET unit_price=?,currency=?,effective_date=?,input_by=?,updated=datetime('now','localtime') WHERE part_no_13=?",
            (unit_price, currency, effective_date, username, part_no_13)
        )
    else:
        con.execute(
            "INSERT INTO sales_prices (vehicle_code,stage,part_no_10,ccc_code,part_no_13,unit_price,currency,effective_date,input_by) VALUES (?,?,?,?,?,?,?,?,?)",
            (vehicle_code, stage, part_no_10, ccc_code, part_no_13, unit_price, currency, effective_date, username)
        )
    con.commit(); con.close()
    return {'ok': True}


def get_sales_prices(vehicle_code=None, stage=None) -> list:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    q = "SELECT * FROM sales_prices WHERE 1=1"
    params = []
    if vehicle_code:
        q += " AND vehicle_code=?"; params.append(vehicle_code)
    if stage:
        q += " AND stage=?"; params.append(stage)
    q += " ORDER BY part_no_10, ccc_code"
    rows = con.execute(q, params).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ── E-BOM 게시판 CRUD ─────────────────────────────────────────────────────────

def add_ebom_upload(vehicle_code, stage, revision, description, filename, file_id, username,
                    row_num='', position='', file_path='', variant='') -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "INSERT INTO ebom_uploads (vehicle_code,stage,revision,description,filename,file_id,uploaded_by,row_num,position,file_path,variant) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (vehicle_code, stage, revision, description, filename, file_id, username, row_num, position, file_path, variant)
    )
    upload_id = cur.lastrowid
    con.commit(); con.close()
    return upload_id


def save_ebom_items(upload_id: int, items: list):
    con = sqlite3.connect(DB_PATH)
    for item in items:
        con.execute(
            "INSERT INTO ebom_items (upload_id,level,pno,description,variant_code,qty,spec) VALUES (?,?,?,?,?,?,?)",
            (upload_id, item.get('level', 1), item['pno'], item.get('description', ''),
             item.get('variant_code', ''), item.get('qty', ''), item.get('spec', ''))
        )
    con.commit(); con.close()


def replace_ebom_items(upload_id: int, items: list):
    """해당 업로드의 기존 품목을 지우고 새로 저장 (재파싱용)."""
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM ebom_items WHERE upload_id=?", (upload_id,))
    for item in items:
        con.execute(
            "INSERT INTO ebom_items (upload_id,level,pno,description,variant_code,qty,spec) VALUES (?,?,?,?,?,?,?)",
            (upload_id, item.get('level', 1), item['pno'], item.get('description', ''),
             item.get('variant_code', ''), item.get('qty', ''), item.get('spec', ''))
        )
    con.commit(); con.close()


def get_ebom_uploads(vehicle_code=None, stage=None, row_num=None, position=None) -> list:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    q = "SELECT * FROM ebom_uploads WHERE is_active=1"
    params = []
    if vehicle_code:
        q += " AND vehicle_code=?"; params.append(vehicle_code)
    if stage:
        q += " AND stage=?"; params.append(stage)
    if row_num is not None:
        q += " AND row_num=?"; params.append(row_num)
    if position is not None:
        q += " AND position=?"; params.append(position)
    q += " ORDER BY created DESC, id DESC"
    rows = con.execute(q, params).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_ebom_upload(upload_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM ebom_uploads WHERE id=?", (upload_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def get_ebom_items(upload_id: int) -> list:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM ebom_items WHERE upload_id=? ORDER BY id",
        (upload_id,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_ebom_items_by_vehicle(vehicle_code: str, stage=None, only_level1: bool = True) -> list:
    """영업단가/M-BOM비교 API용 — 차종(+단계)의 품목 반환.
       E-BOM은 열(1/2/3열)×위치(운전석/조수석 등)별로 별도 파일이 올라오므로,
       각 (row_num,position) 조합마다 '최신 업로드'를 골라 그 품목들을 합쳐서 반환한다.
       only_level1=True(기본): 1레벨만 반환 (영업단가는 1레벨 완제품 기준)."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    q = "SELECT * FROM ebom_uploads WHERE is_active=1 AND vehicle_code=?"
    params = [vehicle_code]
    if stage:
        q += " AND stage=?"; params.append(stage)
    q += " ORDER BY created DESC, id DESC"
    uploads = con.execute(q, params).fetchall()

    # (열,위치,사양구분)별 최신 업로드만 채택 → 폴딩/다이브 등 동시 사양은 모두 유지
    seen = set()
    chosen = []
    for u in uploads:
        key = (u['row_num'] or '', u['position'] or '', (u['variant'] if 'variant' in u.keys() else '') or '')
        if key in seen:
            continue
        seen.add(key)
        chosen.append(u)

    items = []
    for u in chosen:
        iq = "SELECT * FROM ebom_items WHERE upload_id=?"
        iparams = [u['id']]
        if only_level1:
            iq += " AND level=1"
        iq += " ORDER BY id"
        for r in con.execute(iq, iparams).fetchall():
            d = dict(r)
            d['row_num'] = u['row_num']
            d['position'] = u['position']
            d['upload_id'] = u['id']
            items.append(d)
    con.close()
    return items


def get_ebom_board_revisions(vehicle_code: str, row_num: str, position: str) -> list:
    """E-BOM 트리구조 전개 게시판 — 특정 (차종,열,위치)의 리비전 이력 (최신순), 품목 수 포함"""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM ebom_uploads WHERE is_active=1 AND vehicle_code=? AND row_num=? AND position=? "
        "ORDER BY created DESC, id DESC",
        (vehicle_code, row_num, position)
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['lv1_count'] = con.execute(
            "SELECT COUNT(*) FROM ebom_items WHERE upload_id=? AND level=1", (d['id'],)
        ).fetchone()[0]
        d['total_count'] = con.execute(
            "SELECT COUNT(*) FROM ebom_items WHERE upload_id=?", (d['id'],)
        ).fetchone()[0]
        result.append(d)
    con.close()
    return result


def delete_ebom_upload(upload_id: int):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE ebom_uploads SET is_active=0 WHERE id=?", (upload_id,))
    con.commit(); con.close()


# ── 개발단계 마스터 CRUD ───────────────────────────────────────────────────────

def get_all_dev_stages() -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM dev_stages ORDER BY display_order, code").fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_dev_stage_codes() -> list:
    """드롭다운용 단계 코드 문자열 리스트"""
    return [r['code'] for r in get_all_dev_stages()]


def upsert_dev_stage(code: str, name: str = '', display_order: int = 0) -> dict:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "INSERT INTO dev_stages (code,name,display_order) VALUES (?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET name=excluded.name, display_order=excluded.display_order",
            (code.strip(), (name or code).strip(), display_order)
        )
        con.commit()
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'msg': str(e)}
    finally:
        con.close()


def delete_dev_stage(code: str):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM dev_stages WHERE code=?", (code.strip(),))
    con.commit(); con.close()


# ── 원단코드 마스터 CRUD ───────────────────────────────────────────────────────

def get_all_fabric_codes() -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM fabric_codes ORDER BY display_order, code").fetchall()
    con.close()
    return [dict(r) for r in rows]


def upsert_fabric_code(code, fabric_code='', name='', stitch_color='', base_color='', display_order=0, hkmc_code='') -> dict:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "INSERT INTO fabric_codes (code,fabric_code,name,stitch_color,base_color,hkmc_code,display_order) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET fabric_code=excluded.fabric_code, name=excluded.name, "
            "stitch_color=excluded.stitch_color, base_color=excluded.base_color, hkmc_code=excluded.hkmc_code, display_order=excluded.display_order",
            (code.strip().upper(), fabric_code.strip(), name.strip(), stitch_color.strip(), base_color.strip(), hkmc_code.strip(), display_order))
        con.commit()
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'msg': str(e)}
    finally:
        con.close()


def delete_fabric_code(code: str):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM fabric_codes WHERE code=?", (code.strip().upper(),))
    con.commit(); con.close()


# ── 파트 네임 정의 CRUD ────────────────────────────────────────────────────────

def get_all_part_names(vehicle_code: str = '') -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    if vehicle_code:
        rows = con.execute(
            "SELECT * FROM part_names WHERE vehicle_code IN (?, '') ORDER BY display_order, part_key",
            (vehicle_code.strip().upper(),)).fetchall()
    else:
        rows = con.execute("SELECT * FROM part_names ORDER BY vehicle_code, display_order, part_key").fetchall()
    con.close()
    return [dict(r) for r in rows]


def upsert_part_name(part_key: str, part_name: str, vehicle_code: str = '', display_order: int = 0) -> dict:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "INSERT INTO part_names (vehicle_code,part_key,part_name,display_order) VALUES (?,?,?,?) "
            "ON CONFLICT(vehicle_code,part_key) DO UPDATE SET part_name=excluded.part_name, display_order=excluded.display_order",
            (vehicle_code.strip().upper(), part_key.strip(), part_name.strip(), display_order)
        )
        con.commit()
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'msg': str(e)}
    finally:
        con.close()


def delete_part_name(row_id: int):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM part_names WHERE id=?", (row_id,))
    con.commit(); con.close()


# ── M-BOM 이력 (HKMC Q파트 & ALC) CRUD ────────────────────────────────────────

def add_mbom_history(vehicle_code, stage, revision, title, description, uploaded_by) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "INSERT INTO mbom_history (vehicle_code,stage,revision,title,description,uploaded_by) VALUES (?,?,?,?,?,?)",
        (vehicle_code, stage, revision, title, description, uploaded_by))
    pid = cur.lastrowid
    con.commit(); con.close()
    return pid


def add_mbom_file(post_id, slot, filename, file_id, file_path):
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT INTO mbom_history_files (post_id,slot,filename,file_id,file_path) VALUES (?,?,?,?,?)",
                (post_id, slot, filename, file_id, file_path))
    con.commit(); con.close()


def get_mbom_history(vehicle_code: str) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    posts = [dict(r) for r in con.execute(
        "SELECT * FROM mbom_history WHERE vehicle_code=? ORDER BY created DESC, id DESC", (vehicle_code,)).fetchall()]
    for p in posts:
        p['files'] = [dict(f) for f in con.execute(
            "SELECT id,slot,filename,file_id FROM mbom_history_files WHERE post_id=? ORDER BY slot", (p['id'],)).fetchall()]
    con.close()
    return posts


def get_mbom_history_post(post_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM mbom_history WHERE id=?", (post_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def get_mbom_files_by_post(post_id: int) -> list:
    """게시글의 슬롯별 파일 (slot, filename, file_path)."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT slot,filename,file_path FROM mbom_history_files WHERE post_id=?", (post_id,)).fetchall()]
    con.close()
    return rows


# ── 설계변경 통보서(EO) CRUD ─────────────────────────────────────────────────
# 엑셀 열 이름 → DB 컬럼. 통보서 양식이 회사마다 조금씩 달라 별칭을 여러 개 둔다.
EO_FIELDS = ['eo_no', 'eo_date', 'content', 'vehicle_code', 'event_stage', 'dev_schedule',
             'ecr_no', 'cust_eo_no', 'cust_part_no', 'registrant', 'eo_type', 'status', 'note',
             # 상세 항목
             'approval_json', 'refs_text', 'code_type', 'apply_date', 'mandatory_eo',
             'past_part_no', 'analysis_result', 'reasons_json',
             'cost_change', 'weight_change', 'investment',
             'fault_own', 'fault_supplier', 'fault_customer']

# 목록 엑셀로 들어오는 기본 항목 — 일괄 등록은 이 범위만 다룬다(상세는 화면에서 입력)
EO_IMPORT_FIELDS = ['eo_no', 'eo_date', 'content', 'vehicle_code', 'event_stage',
                    'dev_schedule', 'ecr_no', 'cust_eo_no', 'cust_part_no',
                    'registrant', 'eo_type', 'status']

# 변경사유 9종 — 통보서 양식 그대로
EO_REASONS = [('A', '초도승인'), ('B', '법규/신뢰성'), ('C', '상품성 향상'),
              ('D', '조립성 향상'), ('E', '품질 개선'), ('F', '사양 변경'),
              ('G', '오기정정'), ('H', '원가/중량'), ('I', '기타')]

EO_APPROVAL_ROLES = ['작성', '검토', '승인', '승인']

EO_COLUMN_ALIASES = {
    'eo_no':        ['EO 번호', 'EO번호', 'EO NO', 'EONO'],
    'eo_date':      ['EO 일자', 'EO일자', 'EO DATE', '일자'],
    'content':      ['EO 변경내용', 'EO변경내용', '변경내용', '변경 내용'],
    'vehicle_code': ['차종코드', '차종 코드', '차종'],
    'event_stage':  ['이벤트 단계', '이벤트단계', '이벤트'],
    'dev_schedule': ['개발일정', '개발 일정'],
    'ecr_no':       ['ECR 번호', 'ECR번호', 'ECR NO'],
    'cust_eo_no':   ['고객EO번호', '고객 EO번호', '고객EO'],
    'cust_part_no': ['고객품번', '고객 품번'],
    'registrant':   ['등록자'],
    'eo_type':      ['유형'],
    'status':       ['진행상태', '진행 상태', '상태'],
}


def upsert_eo_notices(rows: list, username: str) -> dict:
    """EO 번호 기준 등록/갱신. 같은 EO 번호가 오면 값이 있는 항목만 갱신한다
       (엑셀을 다시 올려도 기존에 채워둔 내용이 빈 값으로 덮이지 않게)."""
    con = sqlite3.connect(DB_PATH)
    added = updated = skipped = 0
    for r in rows:
        eo = str(r.get('eo_no') or '').strip()
        if not eo:
            continue
        cur = con.execute("SELECT * FROM eo_notices WHERE eo_no=?", (eo,)).fetchone()
        # 일괄 등록은 목록 항목만 다룬다 — 화면에서 채운 상세(결재·사유·귀책)를 지우지 않기 위함
        fields = [c for c in EO_IMPORT_FIELDS if c in r] or EO_IMPORT_FIELDS
        if cur is None:
            vals = [str(r.get(c) or '').strip() for c in fields]
            con.execute(
                f"INSERT INTO eo_notices ({','.join(fields)},created_by) "
                f"VALUES ({','.join(['?'] * len(fields))},?)", vals + [username])
            added += 1
        else:
            names = [d[0] for d in con.execute("SELECT * FROM eo_notices LIMIT 0").description]
            cur_d = dict(zip(names, cur))
            sets, vals = [], []
            for c in fields:
                if c == 'eo_no':
                    continue
                v = str(r.get(c) or '').strip()
                if v and v != (cur_d.get(c) or ''):
                    sets.append(f'{c}=?'); vals.append(v)
            if sets:
                sets.append('updated=?')
                vals.append(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                vals.append(eo)
                con.execute(f"UPDATE eo_notices SET {','.join(sets)} WHERE eo_no=?", vals)
                updated += 1
            else:
                skipped += 1
    con.commit(); con.close()
    return {'added': added, 'updated': updated, 'skipped': skipped}


def search_eo_notices(q='', vehicle='', status='', date_from='', date_to='',
                      limit=500, offset=0) -> dict:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    where, params = [], []
    if vehicle:
        where.append('vehicle_code=?'); params.append(vehicle)
    if status:
        where.append('status LIKE ?'); params.append(f'%{status}%')
    if date_from:
        where.append('eo_date>=?'); params.append(date_from)
    if date_to:
        where.append('eo_date<=?'); params.append(date_to)
    if q:
        where.append('(eo_no LIKE ? OR content LIKE ? OR cust_eo_no LIKE ? OR registrant LIKE ?)')
        params += [f'%{q}%'] * 4
    w = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = con.execute(f'SELECT COUNT(*) FROM eo_notices{w}', params).fetchone()[0]
    rows = con.execute(
        f'SELECT * FROM eo_notices{w} ORDER BY eo_date DESC, id DESC LIMIT ? OFFSET ?',
        params + [limit, offset]).fetchall()
    items = [dict(r) for r in rows]
    ids = [i['id'] for i in items]
    fmap = {}
    if ids:
        qm = ','.join(['?'] * len(ids))
        for f in con.execute(f'SELECT id,eo_id,filename FROM eo_files WHERE eo_id IN ({qm})', ids):
            fmap.setdefault(f['eo_id'], []).append({'id': f['id'], 'filename': f['filename']})
    for i in items:
        i['files'] = fmap.get(i['id'], [])
    con.close()
    return {'total': total, 'items': items}


def get_eo_notice(eo_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute('SELECT * FROM eo_notices WHERE id=?', (eo_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def update_eo_notice(eo_id: int, fields: dict) -> dict:
    con = sqlite3.connect(DB_PATH)
    sets, vals = [], []
    for c in EO_FIELDS:
        if c in fields:
            sets.append(f'{c}=?'); vals.append(str(fields[c] or '').strip())
    if not sets:
        con.close(); return {'ok': True, 'changed': 0}
    sets.append('updated=?'); vals.append(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    vals.append(eo_id)
    con.execute(f"UPDATE eo_notices SET {','.join(sets)} WHERE id=?", vals)
    con.commit(); con.close()
    return {'ok': True, 'changed': len(sets) - 1}


def delete_eo_notice(eo_id: int) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    paths = [r['file_path'] for r in con.execute(
        'SELECT file_path FROM eo_files WHERE eo_id=?', (eo_id,)).fetchall()]
    con.execute('DELETE FROM eo_files WHERE eo_id=?', (eo_id,))
    con.execute('DELETE FROM eo_notices WHERE id=?', (eo_id,))
    con.commit(); con.close()
    return paths


def add_eo_file(eo_id, filename, file_path, username,
                doc_kind='doc', purpose='', size_no='', file_type='file') -> int:
    """doc_kind: 2d(PDF·CATDrawing·dwg·dxf) / 3d(CATPart) / doc(통보서·BOM 등)
       purpose : design(설계용) / dist(배포용)"""
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        'INSERT INTO eo_files (eo_id,filename,file_path,uploaded_by,doc_kind,purpose,'
        'size_no,file_type,modified) VALUES (?,?,?,?,?,?,?,?,?)',
        (eo_id, filename, file_path, username, doc_kind, purpose, size_no, file_type,
         datetime.now().strftime('%Y-%m-%d')))
    fid = cur.lastrowid
    con.commit(); con.close()
    return fid


# ── 통보서 전자결재 ──────────────────────────────────────────────────────────
# 상태 흐름:  draft(작성중) → submitted(상신, 1단계 승인대기) → in_progress(중간 승인)
#             → approved(최종승인) / rejected(반려 — 반려되면 draft 로 되돌려 재상신 가능)
EO_APPROVAL_STATUS = {
    'draft': '작성중', 'submitted': '승인대기', 'in_progress': '결재진행',
    'approved': '결재완료', 'rejected': '반려',
}


def submit_eo_approval(eo_id: int, line: list, username: str, doc_type: str = 'eo') -> dict:
    """상신 — 결재선을 만들고 1단계를 승인대기로 둔다.
       line: [{'role':'검토','approver':'홍길동'}, ...] (작성자는 결재선에서 제외)"""
    line = [l for l in (line or []) if str(l.get('approver') or '').strip()]
    if not line:
        return {'ok': False, 'msg': '결재선에 승인자를 최소 1명 지정하세요.'}
    tbl = 'cust_eo' if doc_type == 'cust' else 'eo_notices'
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    cur = con.execute(f'SELECT approval_status FROM {tbl} WHERE id=?', (eo_id,)).fetchone()
    if not cur:
        con.close(); return {'ok': False, 'msg': '통보서를 찾을 수 없습니다.'}
    st = (cur['approval_status'] or 'draft')
    if st in ('submitted', 'in_progress'):
        con.close(); return {'ok': False, 'msg': '이미 결재가 진행 중입니다.'}
    if st == 'approved':
        con.close(); return {'ok': False, 'msg': '이미 결재가 완료되었습니다.'}
    # 지난 회차는 지우지 않는다 — 반려 사유를 나중에도 볼 수 있어야 한다.
    rnd = (con.execute('SELECT MAX(round_no) FROM eo_approvals WHERE eo_id=? AND doc_type=?',
                       (eo_id, doc_type)).fetchone()[0] or 0) + 1
    con.execute("UPDATE eo_approvals SET status='canceled' WHERE eo_id=? AND doc_type=? "
                "AND status='pending'", (eo_id, doc_type))
    for i, l in enumerate(line, 1):
        con.execute('INSERT INTO eo_approvals (eo_id,doc_type,round_no,step_no,role,approver,status) '
                    'VALUES (?,?,?,?,?,?,?)',
                    (eo_id, doc_type, rnd, i, str(l.get('role') or '').strip(),
                     str(l.get('approver') or '').strip(), 'pending'))
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    con.execute(f"UPDATE {tbl} SET approval_status='submitted', submitted_by=?, submitted_at=? "
                f"WHERE id=?", (username, now, eo_id))
    con.commit(); con.close()
    return {'ok': True, 'status': 'submitted', 'steps': len(line)}


def act_eo_approval(eo_id: int, username: str, action: str, comment: str = '',
                    is_admin: bool = False, doc_type: str = 'eo') -> dict:
    """승인 또는 반려. 본인 차례(가장 앞선 pending 단계)만 처리할 수 있다.
       관리자는 대결(다른 사람 차례 처리)이 가능하되 처리자 이름은 실제 계정으로 남는다."""
    if action not in ('approve', 'reject'):
        return {'ok': False, 'msg': '잘못된 요청입니다.'}
    tbl = 'cust_eo' if doc_type == 'cust' else 'eo_notices'
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    head = con.execute(f'SELECT approval_status FROM {tbl} WHERE id=?', (eo_id,)).fetchone()
    if not head:
        con.close(); return {'ok': False, 'msg': '통보서를 찾을 수 없습니다.'}
    if (head['approval_status'] or 'draft') not in ('submitted', 'in_progress'):
        con.close(); return {'ok': False, 'msg': '결재 진행 중인 문서가 아닙니다.'}
    step = con.execute("SELECT * FROM eo_approvals WHERE eo_id=? AND doc_type=? "
                       "AND status='pending' ORDER BY round_no, step_no LIMIT 1",
                       (eo_id, doc_type)).fetchone()
    if not step:
        con.close(); return {'ok': False, 'msg': '대기 중인 결재 단계가 없습니다.'}
    # 결재선에는 계정(username)이 아니라 «성명»을 적는 경우가 많다 — 둘 다 인정한다.
    urow = con.execute('SELECT name FROM users WHERE username=?', (username,)).fetchone()
    my_names = {username, (urow['name'] or '').strip() if urow else ''} - {''}
    is_mine = (step['approver'] or '').strip() in my_names
    if not is_mine and not is_admin:
        con.close()
        return {'ok': False, 'msg': f"현재 결재 차례는 «{step['approver']}» 입니다."}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    new_st = 'approved' if action == 'approve' else 'rejected'
    con.execute('UPDATE eo_approvals SET status=?, comment=?, acted_at=?, approver=? WHERE id=?',
                (new_st, comment, now,
                 step['approver'] if is_mine else f"{step['approver']}(대결:{username})",
                 step['id']))
    if action == 'reject':
        # 반려하면 나머지 단계는 취소하고 작성중으로 되돌려 재상신할 수 있게 한다
        con.execute("UPDATE eo_approvals SET status='canceled' WHERE eo_id=? AND doc_type=? "
                    "AND status='pending'", (eo_id, doc_type))
        con.execute(f"UPDATE {tbl} SET approval_status='rejected' WHERE id=?", (eo_id,))
        doc_st = 'rejected'
    else:
        left = con.execute("SELECT COUNT(*) FROM eo_approvals WHERE eo_id=? AND doc_type=? "
                           "AND status='pending'", (eo_id, doc_type)).fetchone()[0]
        doc_st = 'approved' if left == 0 else 'in_progress'
        con.execute(f'UPDATE {tbl} SET approval_status=? WHERE id=?', (doc_st, eo_id))
    con.commit(); con.close()
    return {'ok': True, 'status': doc_st, 'step': step['step_no']}


def reopen_eo_approval(eo_id: int, doc_type: str = 'eo') -> dict:
    """반려된 문서를 다시 작성중으로 — 수정 후 재상신용."""
    tbl = 'cust_eo' if doc_type == 'cust' else 'eo_notices'
    con = sqlite3.connect(DB_PATH)
    con.execute(f"UPDATE {tbl} SET approval_status='draft' WHERE id=? AND approval_status='rejected'",
                (eo_id,))
    con.commit(); con.close()
    return {'ok': True, 'status': 'draft'}


def get_eo_approvals(eo_id: int, doc_type: str = 'eo') -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute('SELECT * FROM eo_approvals WHERE eo_id=? AND doc_type=? '
                       'ORDER BY round_no, step_no', (eo_id, doc_type)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def add_eo_mail(eo_id, to_addr, subject, body, status, detail, username) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute('INSERT INTO eo_mails (eo_id,to_addr,subject,body,status,detail,sent_by) '
                      'VALUES (?,?,?,?,?,?,?)',
                      (eo_id, to_addr, subject, body, status, detail, username))
    mid = cur.lastrowid
    con.commit(); con.close()
    return mid


def get_eo_mails(eo_id: int) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute('SELECT * FROM eo_mails WHERE eo_id=? ORDER BY id DESC', (eo_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_user_emails(names: list) -> dict:
    """성명 또는 계정으로 이메일을 찾는다 — 결재선·참조자에게 메일 보낼 때 사용."""
    if not names:
        return {}
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    out = {}
    for n in names:
        n = str(n or '').strip()
        if not n:
            continue
        row = con.execute('SELECT username,name,email FROM users WHERE username=? OR name=?',
                          (n, n)).fetchone()
        if row:
            out[n] = row['email']
    con.close()
    return out


# ── 통보서 품목현황 ──────────────────────────────────────────────────────────
def set_eo_items(eo_id: int, items: list):
    """통보서에 걸린 품번 목록을 통째로 교체한다."""
    con = sqlite3.connect(DB_PATH)
    con.execute('DELETE FROM eo_items WHERE eo_id=?', (eo_id,))
    for i, it in enumerate(items):
        pno = str(it.get('part_no') or '').strip()
        if not pno:
            continue
        con.execute('INSERT INTO eo_items (eo_id,part_no,part_name,revision,note,sort_order) '
                    'VALUES (?,?,?,?,?,?)',
                    (eo_id, pno, str(it.get('part_name') or '').strip(),
                     str(it.get('revision') or '').strip(), str(it.get('note') or '').strip(), i))
    con.commit(); con.close()


def get_eo_items(eo_id: int) -> list:
    """품목현황. 품목 마스터에 있으면 품명·도면 보유 여부를 함께 채워 준다."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        'SELECT * FROM eo_items WHERE eo_id=? ORDER BY sort_order, id', (eo_id,)).fetchall()]
    for r in rows:
        p = con.execute('SELECT part_name FROM parts WHERE part_no=?', (r['part_no'],)).fetchone()
        r['in_master'] = p is not None
        if p and not r['part_name']:
            r['part_name'] = p['part_name']
        r['drawings'] = con.execute(
            "SELECT COUNT(*) FROM part_files WHERE part_no=? AND kind='drawing'",
            (r['part_no'],)).fetchone()[0]
    con.close()
    return rows


def get_eo_files(eo_id: int) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute('SELECT * FROM eo_files WHERE eo_id=? ORDER BY id DESC', (eo_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_eo_file(file_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute('SELECT * FROM eo_files WHERE id=?', (file_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def delete_eo_file(file_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute('SELECT * FROM eo_files WHERE id=?', (file_id,)).fetchone()
    if not row:
        con.close(); return None
    info = dict(row)
    con.execute('DELETE FROM eo_files WHERE id=?', (file_id,))
    con.commit(); con.close()
    return info


def get_eo_stats() -> dict:
    con = sqlite3.connect(DB_PATH)
    total = con.execute('SELECT COUNT(*) FROM eo_notices').fetchone()[0]
    fin = con.execute("SELECT COUNT(*) FROM eo_notices WHERE status LIKE '%Finish%'").fetchone()[0]
    withfile = con.execute('SELECT COUNT(DISTINCT eo_id) FROM eo_files').fetchone()[0]
    vehicles = [r[0] for r in con.execute(
        "SELECT DISTINCT vehicle_code FROM eo_notices WHERE vehicle_code<>'' ORDER BY vehicle_code")]
    con.close()
    return {'total': total, 'finished': fin, 'with_file': withfile, 'vehicles': vehicles}


# ── 문서 게시판 CRUD (이용방법 / RFP) ────────────────────────────────────────
def add_doc_post(kind, category, title, body, username, sort_order=0) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "INSERT INTO doc_posts (kind,category,title,body,sort_order,created_by,updated_by) "
        "VALUES (?,?,?,?,?,?,?)",
        (kind, category, title, body, sort_order, username, username))
    pid = cur.lastrowid
    con.commit(); con.close()
    return pid


def get_doc_posts(kind: str) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM doc_posts WHERE kind=? ORDER BY sort_order, id", (kind,)).fetchall()
    posts = [dict(r) for r in rows]
    for p in posts:
        p['files'] = [dict(f) for f in con.execute(
            "SELECT id,filename FROM doc_files WHERE post_id=? ORDER BY id", (p['id'],)).fetchall()]
    con.close()
    return posts


def get_doc_post(post_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM doc_posts WHERE id=?", (post_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def update_doc_post(post_id: int, category, title, body, username, sort_order=None) -> dict:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    cur = con.execute("SELECT * FROM doc_posts WHERE id=?", (post_id,)).fetchone()
    if not cur:
        con.close(); return {'ok': False, 'msg': '문서를 찾을 수 없습니다.'}
    cur = dict(cur)
    changed = (cur['category'] != category or cur['title'] != title or cur['body'] != body)
    rev = (cur['revision'] or 1) + (1 if changed else 0)
    so = cur['sort_order'] if sort_order is None else sort_order
    con.execute("UPDATE doc_posts SET category=?,title=?,body=?,sort_order=?,revision=?,"
                "updated_by=?,updated=? WHERE id=?",
                (category, title, body, so, rev, username,
                 datetime.now().strftime('%Y-%m-%d %H:%M:%S'), post_id))
    con.commit(); con.close()
    return {'ok': True, 'revision': rev, 'changed': changed}


def delete_doc_post(post_id: int) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    paths = [r['file_path'] for r in con.execute(
        "SELECT file_path FROM doc_files WHERE post_id=?", (post_id,)).fetchall()]
    con.execute("DELETE FROM doc_files WHERE post_id=?", (post_id,))
    con.execute("DELETE FROM doc_posts WHERE id=?", (post_id,))
    con.commit(); con.close()
    return paths


def add_doc_file(post_id, filename, file_path, username) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute("INSERT INTO doc_files (post_id,filename,file_path,uploaded_by) VALUES (?,?,?,?)",
                      (post_id, filename, file_path, username))
    fid = cur.lastrowid
    con.commit(); con.close()
    return fid


def get_doc_file(file_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM doc_files WHERE id=?", (file_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def delete_doc_file(file_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM doc_files WHERE id=?", (file_id,)).fetchone()
    if not row:
        con.close(); return None
    info = dict(row)
    con.execute("DELETE FROM doc_files WHERE id=?", (file_id,))
    con.commit(); con.close()
    return info


# ── 품목 마스터 CRUD ─────────────────────────────────────────────────────────
# 스펙 필드 목록 — 화면·저장·리비전이 모두 이 목록 하나를 기준으로 움직이게 해서
# 필드를 늘릴 때 한 곳만 고치면 되도록 한다.
PART_SPEC_FIELDS = [
    'part_name', 'vehicle_code', 'level', 'oem', 'customer_pno', 'co_vehicle',
    'ms_spec', 'material', 'catia_weight', 'real_weight', 'thickness', 'surface',
    'drawing_size', 'release_date', 'supplier', 'supplier_pno',
    'seat_type1', 'seat_type2', 'status_part', 'note',
]


def upsert_parts_bulk(items: list, vehicle_code: str, username: str) -> dict:
    """BOM 엑셀에서 뽑은 품번·품명을 일괄 등록. 이미 있는 품번은 «비어 있는 칸만»
       채우고 사람이 입력한 스펙은 절대 덮어쓰지 않는다(자동등록이 수기 입력을
       지우면 안 되기 때문)."""
    con = sqlite3.connect(DB_PATH)
    added = updated = skipped = 0
    for it in items:
        pno = str(it.get('pno') or '').strip()
        if not pno:
            continue
        name = str(it.get('part_name') or it.get('description') or '').strip()
        lv = it.get('level')
        cur = con.execute("SELECT part_name, vehicle_code, level FROM parts WHERE part_no=?",
                          (pno,)).fetchone()
        if cur is None and not pno.upper().startswith('X'):
            # 도면에서 먼저 등록된 개발 품번(X88010-P1010)이 있으면 새 행을 만들지 않고
            # «양산 품번으로 승격»한다. X 는 양산 전 임시 표기라 같은 제품이므로
            # 이력(도면 연결·스펙·리비전)이 한 줄로 이어져야 한다(사용자 확정 2026-08-02).
            xrow = con.execute("SELECT part_name, vehicle_code, level FROM parts WHERE part_no=?",
                               ('X' + pno,)).fetchone()
            if xrow is not None:
                con.execute("UPDATE parts SET part_no=? WHERE part_no=?", (pno, 'X' + pno))
                for _t, _c in (('part_files', 'part_no'), ('part_revs', 'part_no')):
                    try:
                        con.execute(f"UPDATE {_t} SET {_c}=? WHERE {_c}=?", (pno, 'X' + pno))
                    except sqlite3.OperationalError:
                        pass
                cur = xrow
        if cur is None:
            con.execute(
                "INSERT INTO parts (part_no,part_name,vehicle_code,level,created_by,updated_by) "
                "VALUES (?,?,?,?,?,?)", (pno, name, vehicle_code, lv, username, username))
            added += 1
        else:
            sets, vals = [], []
            if not (cur[0] or '').strip() and name:
                sets.append("part_name=?"); vals.append(name)
            if not (cur[1] or '').strip() and vehicle_code:
                sets.append("vehicle_code=?"); vals.append(vehicle_code)
            if cur[2] is None and lv is not None:
                sets.append("level=?"); vals.append(lv)
            if sets:
                vals.append(pno)
                con.execute(f"UPDATE parts SET {','.join(sets)} WHERE part_no=?", vals)
                updated += 1
            else:
                skipped += 1
    con.commit(); con.close()
    return {'added': added, 'updated': updated, 'skipped': skipped}


def search_parts(q: str = '', vehicle_code: str = '', level: str = '',
                 limit: int = 1000, offset: int = 0) -> dict:
    """레벨 분포도 함께 돌려준다 — 목록이 잘렸을 때 «어느 레벨이 안 보이는지» 알 수 있게.
       (기본 limit이 300이던 시절, 레벨순 정렬 탓에 5~7레벨이 화면에 안 나오는 문제가 있었다)"""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    where, params = [], []
    if vehicle_code:
        where.append("vehicle_code=?"); params.append(vehicle_code)
    if str(level).strip():
        where.append("level=?"); params.append(int(level))
    if q:
        # 개발 품번(X88010-P1010)과 양산 품번(88010-P1010)은 앞의 X 하나만 다르다.
        # 어느 쪽으로 검색해도 찾히도록 «X를 뗀 값»으로도 대조한다.
        qb = q[1:] if (len(q) > 1 and q[0] in 'Xx' and q[1].isdigit()) else q
        where.append("(part_no LIKE ? OR part_name LIKE ? OR part_no LIKE ? OR ('X'||part_no) LIKE ?)")
        params += [f'%{q}%', f'%{q}%', f'%{qb}%', f'%{q}%']
    w = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = con.execute(f"SELECT COUNT(*) FROM parts{w}", params).fetchone()[0]
    rows = con.execute(
        f"SELECT * FROM parts{w} ORDER BY level, part_no LIMIT ? OFFSET ?",
        params + [limit, offset]).fetchall()
    # 레벨 분포는 검색·차종 조건만 반영(레벨 필터 자체는 제외)
    lw, lp = [], []
    if vehicle_code:
        lw.append("vehicle_code=?"); lp.append(vehicle_code)
    if q:
        qb2 = q[1:] if (len(q) > 1 and q[0] in 'Xx' and q[1].isdigit()) else q
        lw.append("(part_no LIKE ? OR part_name LIKE ? OR part_no LIKE ? OR ('X'||part_no) LIKE ?)")
        lp += [f'%{q}%', f'%{q}%', f'%{qb2}%', f'%{q}%']
    lws = (' WHERE ' + ' AND '.join(lw)) if lw else ''
    dist = [{'level': r[0], 'n': r[1]} for r in con.execute(
        f"SELECT COALESCE(level,0), COUNT(*) FROM parts{lws} GROUP BY level ORDER BY level", lp)]
    con.close()
    return {'total': total, 'items': [dict(r) for r in rows],
            'level_dist': dist, 'limit': limit, 'offset': offset}


def get_part(part_no: str) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM parts WHERE part_no=?", (part_no,)).fetchone()
    con.close()
    return dict(row) if row else None


def update_part(part_no: str, fields: dict, username: str, eo_no: str = '',
                approval: str = '미상신') -> dict:
    """스펙 저장 — 실제로 바뀐 게 있을 때만 리비전을 올린다."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    cur = con.execute("SELECT * FROM parts WHERE part_no=?", (part_no,)).fetchone()
    if not cur:
        con.close(); return {'ok': False, 'msg': '품번을 찾을 수 없습니다.'}
    cur = dict(cur)
    sets, vals, changed = [], [], []
    for k in PART_SPEC_FIELDS:
        if k not in fields:
            continue
        v = fields[k]
        if k == 'level':
            try: v = int(v) if str(v).strip() != '' else None
            except ValueError: v = cur.get('level')
        else:
            v = str(v or '').strip()
        if (cur.get(k) or '') != (v or ''):
            sets.append(f"{k}=?"); vals.append(v); changed.append(k)
    if not sets:
        con.close(); return {'ok': True, 'changed': 0, 'revision': cur['revision']}
    new_rev = (cur['revision'] or 0) + 1
    sets += ["revision=?", "updated_by=?", "updated=?"]
    vals += [new_rev, username, datetime.now().strftime('%Y-%m-%d %H:%M:%S')]
    vals.append(part_no)
    con.execute(f"UPDATE parts SET {','.join(sets)} WHERE part_no=?", vals)
    con.execute("INSERT INTO part_revs (part_no,rev_num,part_name,material,eo_no,approval,note,saved_by) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (part_no, new_rev, fields.get('part_name', cur.get('part_name', '')),
                 fields.get('material', cur.get('material', '')), eo_no, approval,
                 ','.join(changed), username))
    con.commit(); con.close()
    return {'ok': True, 'changed': len(changed), 'revision': new_rev, 'fields': changed}


def get_part_revs(part_no: str) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM part_revs WHERE part_no=? ORDER BY rev_num DESC",
                       (part_no,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def add_part_file(part_no: str, kind: str, filename: str, file_path: str, username: str,
                  eo_no: str = '', note: str = '') -> dict:
    """같은 품번·같은 종류로 다시 올리면 리비전을 올린다. 이전 파일은 이력으로 남는다."""
    con = sqlite3.connect(DB_PATH)
    cur_rev = con.execute("SELECT MAX(revision) FROM part_files WHERE part_no=? AND kind=?",
                          (part_no, kind)).fetchone()[0]
    rev = (cur_rev or 0) + 1
    cur = con.execute(
        "INSERT INTO part_files (part_no,kind,filename,file_path,uploaded_by,revision,eo_no,note) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (part_no, kind, filename, file_path, username, rev, eo_no, note))
    fid = cur.lastrowid
    con.commit(); con.close()
    return {'id': fid, 'revision': rev}


def get_part_files(part_no: str, latest_only: bool = False) -> list:
    """종류별 최신 리비전이 앞에 오도록 정렬. latest_only=True 면 종류별 최신 1건씩만."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM part_files WHERE part_no=? ORDER BY kind, revision DESC, id DESC",
        (part_no,)).fetchall()
    con.close()
    items = [dict(r) for r in rows]
    # 종류별 최신 여부 표시 — 화면에서 «현재본»과 «이력»을 구분하기 위함
    seen = set()
    for it in items:
        it['is_latest'] = it['kind'] not in seen
        seen.add(it['kind'])
    if latest_only:
        items = [i for i in items if i['is_latest']]
    return items


def get_part_file(file_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM part_files WHERE id=?", (file_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def delete_part_file(file_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM part_files WHERE id=?", (file_id,)).fetchone()
    if not row:
        con.close(); return None
    info = dict(row)
    con.execute("DELETE FROM part_files WHERE id=?", (file_id,))
    con.commit(); con.close()
    return info


def get_parts_stats() -> dict:
    con = sqlite3.connect(DB_PATH)
    total = con.execute("SELECT COUNT(*) FROM parts").fetchone()[0]
    filled = con.execute("SELECT COUNT(*) FROM parts WHERE material<>'' OR ms_spec<>''").fetchone()[0]
    drawn = con.execute("SELECT COUNT(DISTINCT part_no) FROM part_files WHERE kind='drawing'").fetchone()[0]
    con.close()
    return {'total': total, 'spec_filled': filled, 'with_drawing': drawn}


# ── E-BOM 시트 편집(2안) CRUD ────────────────────────────────────────────────
# 편집 락은 «한 번에 한 명만»을 보장한다. 브라우저를 그냥 닫으면 영영 잠기므로
# 무활동 30분이면 자동 만료시키고, 관리자는 강제 해제할 수 있다.
LOCK_TIMEOUT_MIN = 30


def add_ebom_sheet(vehicle_code, stage, title, filename, file_path, sheet_name,
                   n_rows, n_cols, created_by) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "INSERT INTO ebom_sheets (vehicle_code,stage,title,filename,file_path,sheet_name,"
        "n_rows,n_cols,created_by) VALUES (?,?,?,?,?,?,?,?,?)",
        (vehicle_code, stage, title, filename, file_path, sheet_name, n_rows, n_cols, created_by))
    sid = cur.lastrowid
    con.commit(); con.close()
    return sid


def save_ebom_sheet_cells(sheet_id: int, cells: list):
    """cells: [(row_idx, col_idx, value)] — 업로드 직후 원본 전체 적재용."""
    con = sqlite3.connect(DB_PATH)
    con.executemany(
        "INSERT OR REPLACE INTO ebom_sheet_cells (sheet_id,row_idx,col_idx,value) VALUES (?,?,?,?)",
        [(sheet_id, r, c, v) for r, c, v in cells])
    con.commit(); con.close()


def set_ebom_sheet_layout(sheet_id: int, layout_json: str, proc_ver: int = None):
    con = sqlite3.connect(DB_PATH)
    if proc_ver is None:
        con.execute("UPDATE ebom_sheets SET layout=? WHERE id=?", (layout_json, sheet_id))
    else:
        con.execute("UPDATE ebom_sheets SET layout=?, proc_ver=? WHERE id=?",
                    (layout_json, proc_ver, sheet_id))
    con.commit(); con.close()


def get_ebom_sheets(vehicle_code: str = '') -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    if vehicle_code:
        rows = con.execute("SELECT * FROM ebom_sheets WHERE vehicle_code=? ORDER BY id DESC",
                           (vehicle_code,)).fetchall()
    else:
        rows = con.execute("SELECT * FROM ebom_sheets ORDER BY id DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_ebom_sheet(sheet_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM ebom_sheets WHERE id=?", (sheet_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def get_ebom_sheet_cells(sheet_id: int, row_from: int = 0, row_to: int = 10 ** 9) -> list:
    """행 범위만 잘라서 반환 — 화면에 필요한 만큼만 내려 메모리를 아낀다."""
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT row_idx,col_idx,value FROM ebom_sheet_cells WHERE sheet_id=? "
        "AND row_idx>=? AND row_idx<=? ORDER BY row_idx,col_idx",
        (sheet_id, row_from, row_to)).fetchall()
    con.close()
    return rows


def _lock_expired(locked_at: str) -> bool:
    if not locked_at:
        return True
    try:
        t = datetime.strptime(locked_at, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return True
    return (datetime.now() - t) > timedelta(minutes=LOCK_TIMEOUT_MIN)


def acquire_ebom_sheet_lock(sheet_id: int, username: str) -> dict:
    """편집 락 획득. 이미 다른 사람이 쥐고 있고 만료되지 않았으면 실패."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT locked_by,locked_at FROM ebom_sheets WHERE id=?", (sheet_id,)).fetchone()
    if not row:
        con.close(); return {'ok': False, 'msg': '시트를 찾을 수 없습니다.'}
    holder = (row['locked_by'] or '').strip()
    if holder and holder != username and not _lock_expired(row['locked_at']):
        con.close()
        return {'ok': False, 'msg': f'{holder} 님이 편집 중입니다. (시작 {row["locked_at"]})',
                'locked_by': holder, 'locked_at': row['locked_at']}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    con.execute("UPDATE ebom_sheets SET locked_by=?, locked_at=? WHERE id=?", (username, now, sheet_id))
    con.commit(); con.close()
    return {'ok': True, 'locked_by': username, 'locked_at': now}


def touch_ebom_sheet_lock(sheet_id: int, username: str):
    """편집 중 활동 갱신 — 30분 무활동 만료 타이머를 미룬다."""
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE ebom_sheets SET locked_at=? WHERE id=? AND locked_by=?",
                (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), sheet_id, username))
    con.commit(); con.close()


def release_ebom_sheet_lock(sheet_id: int, username: str, force: bool = False) -> dict:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT locked_by FROM ebom_sheets WHERE id=?", (sheet_id,)).fetchone()
    if not row:
        con.close(); return {'ok': False, 'msg': '시트를 찾을 수 없습니다.'}
    holder = (row['locked_by'] or '').strip()
    if holder and holder != username and not force:
        con.close(); return {'ok': False, 'msg': f'{holder} 님의 편집 락입니다.'}
    con.execute("UPDATE ebom_sheets SET locked_by='', locked_at='' WHERE id=?", (sheet_id,))
    con.commit(); con.close()
    return {'ok': True}


def get_ebom_sheet_lock_state(sheet_id: int) -> dict:
    s = get_ebom_sheet(sheet_id)
    if not s:
        return {'locked': False}
    holder = (s.get('locked_by') or '').strip()
    if not holder or _lock_expired(s.get('locked_at') or ''):
        return {'locked': False}
    return {'locked': True, 'locked_by': holder, 'locked_at': s.get('locked_at')}


def apply_ebom_sheet_edits(sheet_id: int, username: str, edits: list, note: str = '') -> dict:
    """edits: [{'r':행,'c':열,'v':새값}] — 락 보유자만 저장할 수 있다.
       바뀐 셀만 리비전에 남긴다(before/after 함께 저장해 되돌리기 가능)."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT locked_by,locked_at,current_rev FROM ebom_sheets WHERE id=?",
                      (sheet_id,)).fetchone()
    if not row:
        con.close(); return {'ok': False, 'msg': '시트를 찾을 수 없습니다.'}
    holder = (row['locked_by'] or '').strip()
    if holder != username:
        con.close()
        return {'ok': False, 'msg': ('편집 락이 없습니다. «편집 시작»을 먼저 누르세요.'
                                     if not holder else f'{holder} 님이 편집 중입니다.')}
    changes = []
    for e in edits:
        r, c, v = int(e['r']), int(e['c']), '' if e.get('v') is None else str(e['v'])
        cur = con.execute("SELECT value FROM ebom_sheet_cells WHERE sheet_id=? AND row_idx=? AND col_idx=?",
                          (sheet_id, r, c)).fetchone()
        before = cur['value'] if cur else ''
        if before == v:
            continue
        con.execute("INSERT OR REPLACE INTO ebom_sheet_cells (sheet_id,row_idx,col_idx,value) VALUES (?,?,?,?)",
                    (sheet_id, r, c, v))
        changes.append({'r': r, 'c': c, 'before': before, 'after': v})
    if not changes:
        con.close(); return {'ok': True, 'changed': 0, 'rev': row['current_rev']}
    new_rev = (row['current_rev'] or 0) + 1
    con.execute("INSERT INTO ebom_sheet_revs (sheet_id,rev_num,changes,n_changes,note,saved_by) "
                "VALUES (?,?,?,?,?,?)",
                (sheet_id, new_rev, json.dumps(changes, ensure_ascii=False), len(changes), note, username))
    con.execute("UPDATE ebom_sheets SET current_rev=?, locked_at=? WHERE id=?",
                (new_rev, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), sheet_id))
    con.commit(); con.close()
    return {'ok': True, 'changed': len(changes), 'rev': new_rev}


def get_ebom_sheet_revs(sheet_id: int) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id,rev_num,n_changes,note,saved_by,saved_at FROM ebom_sheet_revs "
                       "WHERE sheet_id=? ORDER BY rev_num DESC", (sheet_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_ebom_sheet_applied_changes(sheet_id: int) -> dict:
    """모든 리비전의 변경을 순서대로 합쳐 «최종 셀 값»만 남긴다.
       반환: {(row, col): value} — 다운로드 시 원본에 덮어쓸 목록."""
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT changes FROM ebom_sheet_revs WHERE sheet_id=? ORDER BY rev_num",
                       (sheet_id,)).fetchall()
    con.close()
    applied = {}
    for (ch,) in rows:
        try:
            for c in json.loads(ch or '[]'):
                applied[(int(c['r']), int(c['c']))] = c.get('after', '')
        except (ValueError, KeyError, TypeError):
            continue
    return applied


def delete_ebom_sheet(sheet_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM ebom_sheets WHERE id=?", (sheet_id,)).fetchone()
    if not row:
        con.close(); return None
    info = dict(row)
    con.execute("DELETE FROM ebom_sheet_cells WHERE sheet_id=?", (sheet_id,))
    con.execute("DELETE FROM ebom_sheet_revs WHERE sheet_id=?", (sheet_id,))
    con.execute("DELETE FROM ebom_sheets WHERE id=?", (sheet_id,))
    con.commit(); con.close()
    return info


def get_mbom_posts_with_files() -> list:
    """ALC 파일이 붙어 있는 M-BOM 게시글 목록 (E-BOM & M-BOM 비교의 «M-BOM 선택»용)."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT p.id, p.vehicle_code, p.stage, p.revision, p.title, p.created, "
        "       (SELECT COUNT(*) FROM mbom_history_files f WHERE f.post_id=p.id) AS nfiles "
        "FROM mbom_history p ORDER BY p.created DESC, p.id DESC").fetchall()
    con.close()
    return [dict(r) for r in rows if r['nfiles']]


def get_latest_mbom_post_with_alc() -> Optional[int]:
    """ALC 코드집이 올라온 가장 최근 게시글 id (코드집 용어 후보 추출용)."""
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT post_id FROM mbom_history_files WHERE slot LIKE '%FRT LH%' "
        "ORDER BY post_id DESC LIMIT 1").fetchone()
    con.close()
    return row[0] if row else None


def get_mbom_file(file_row_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM mbom_history_files WHERE id=?", (file_row_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def delete_mbom_history(post_id: int) -> list:
    """게시글 + 파일 삭제. 물리파일 경로 목록 반환."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    paths = [r['file_path'] for r in con.execute(
        "SELECT file_path FROM mbom_history_files WHERE post_id=?", (post_id,)).fetchall()]
    row = con.execute("SELECT * FROM mbom_history WHERE id=?", (post_id,)).fetchone()
    info = dict(row) if row else None
    con.execute("DELETE FROM mbom_history_files WHERE post_id=?", (post_id,))
    con.execute("DELETE FROM mbom_history WHERE id=?", (post_id,))
    con.commit(); con.close()
    return {'info': info, 'paths': paths}


# ── Q파트 ALC 통합 게시판 CRUD (mbom_history와 별개) ──────────────────────────

def add_qpart_merge_post(vehicle, dev_stage, title, uploaded_by) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "INSERT INTO qpart_merge_posts (vehicle,dev_stage,title,uploaded_by) VALUES (?,?,?,?)",
        (vehicle, dev_stage, title, uploaded_by))
    pid = cur.lastrowid
    con.commit(); con.close()
    return pid


def add_qpart_merge_file(post_id, slot, filename, file_path):
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT INTO qpart_merge_files (post_id,slot,filename,file_path) VALUES (?,?,?,?)",
                (post_id, slot, filename, file_path))
    con.commit(); con.close()


def get_qpart_merge_history(vehicle: str = '') -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    if vehicle:
        posts = [dict(r) for r in con.execute(
            "SELECT * FROM qpart_merge_posts WHERE vehicle=? ORDER BY created DESC, id DESC", (vehicle,)).fetchall()]
    else:
        posts = [dict(r) for r in con.execute(
            "SELECT * FROM qpart_merge_posts ORDER BY created DESC, id DESC").fetchall()]
    for p in posts:
        p['files'] = [dict(f) for f in con.execute(
            "SELECT id,slot,filename FROM qpart_merge_files WHERE post_id=? ORDER BY slot", (p['id'],)).fetchall()]
        p['run_count'] = con.execute(
            "SELECT COUNT(*) FROM qpart_merge_runs WHERE post_id=?", (p['id'],)).fetchone()[0]
    con.close()
    return posts


def get_qpart_merge_post(post_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM qpart_merge_posts WHERE id=?", (post_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def get_qpart_merge_files_by_post(post_id: int) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT slot,filename,file_path FROM qpart_merge_files WHERE post_id=?", (post_id,)).fetchall()]
    con.close()
    return rows


def get_qpart_merge_file(file_row_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM qpart_merge_files WHERE id=?", (file_row_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def delete_qpart_merge_post(post_id: int) -> dict:
    """게시글 + 슬롯파일 + 변환 이력(run) 삭제. 물리파일 경로 목록 반환(슬롯파일 + run 결과물)."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    paths = [r['file_path'] for r in con.execute(
        "SELECT file_path FROM qpart_merge_files WHERE post_id=?", (post_id,)).fetchall()]
    paths += [r['output_path'] for r in con.execute(
        "SELECT output_path FROM qpart_merge_runs WHERE post_id=?", (post_id,)).fetchall()]
    row = con.execute("SELECT * FROM qpart_merge_posts WHERE id=?", (post_id,)).fetchone()
    info = dict(row) if row else None
    con.execute("DELETE FROM qpart_merge_files WHERE post_id=?", (post_id,))
    con.execute("DELETE FROM qpart_merge_runs WHERE post_id=?", (post_id,))
    con.execute("DELETE FROM qpart_merge_posts WHERE id=?", (post_id,))
    con.commit(); con.close()
    return {'info': info, 'paths': paths}


def add_qpart_merge_run(post_id, output_path, output_filename, spec_col_count, row_count, created_by) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "INSERT INTO qpart_merge_runs (post_id,output_path,output_filename,spec_col_count,row_count,created_by) "
        "VALUES (?,?,?,?,?,?)",
        (post_id, output_path, output_filename, spec_col_count, row_count, created_by))
    rid = cur.lastrowid
    con.commit(); con.close()
    return rid


def get_qpart_merge_runs(post_id: int) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM qpart_merge_runs WHERE post_id=? ORDER BY id DESC", (post_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_qpart_merge_run(run_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM qpart_merge_runs WHERE id=?", (run_id,)).fetchone()
    con.close()
    return dict(row) if row else None


# ── 국가코드 CRUD ──────────────────────────────────────────────────────────────

def get_all_country_codes() -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM country_codes ORDER BY display_order, code").fetchall()
    con.close()
    return [dict(r) for r in rows]


def upsert_country_code(code: str, region: str, countries: str, display_order: int = 0,
                         code1: str = '', code2: str = '', hkmc_code: str = '') -> dict:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "INSERT INTO country_codes (code,region,hkmc_code,code1,code2,countries,display_order) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET region=excluded.region, hkmc_code=excluded.hkmc_code, "
            "code1=excluded.code1, code2=excluded.code2, countries=excluded.countries, display_order=excluded.display_order",
            (code.strip().upper(), region.strip(), hkmc_code.strip(), code1.strip(), code2.strip(), countries.strip(), display_order)
        )
        con.commit()
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'msg': str(e)}
    finally:
        con.close()


def delete_country_code(code: str):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM country_codes WHERE code=?", (code.strip().upper(),))
    con.commit(); con.close()


def get_country_code(code: str) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM country_codes WHERE code=?", (code.strip().upper(),)).fetchone()
    con.close()
    return dict(row) if row else None


# ── 국가코드 PPT 리비전 CRUD ───────────────────────────────────────────────────

def list_country_ppt_revisions() -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM country_ppt_revisions ORDER BY rev_num DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


def add_country_ppt_revision(filename: str, file_path: str, file_ext: str,
                              uploaded_by: str, note: str = '') -> dict:
    con = sqlite3.connect(DB_PATH)
    next_rev = con.execute(
        "SELECT COALESCE(MAX(rev_num),0)+1 FROM country_ppt_revisions"
    ).fetchone()[0]
    con.execute("UPDATE country_ppt_revisions SET is_active=0")
    con.execute(
        "INSERT INTO country_ppt_revisions (rev_num,filename,file_path,file_ext,uploaded_by,note,is_active) VALUES (?,?,?,?,?,?,1)",
        (next_rev, filename, file_path, file_ext, uploaded_by, note)
    )
    con.commit(); con.close()
    return {'ok': True, 'rev_num': next_rev}


def delete_country_ppt_revision(rev_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM country_ppt_revisions WHERE id=?", (rev_id,)).fetchone()
    if not row:
        con.close(); return None
    info = dict(row)
    con.execute("DELETE FROM country_ppt_revisions WHERE id=?", (rev_id,))
    con.commit(); con.close()
    return info


# ── 프로세스 다이어그램(공정도) 게시판 CRUD ───────────────────────────────────
def get_all_process_diagrams() -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM process_diagrams ORDER BY display_order, id").fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_process_diagram(diagram_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM process_diagrams WHERE id=?", (diagram_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def add_process_diagram(title: str, description: str, filename: str, file_path: str,
                        file_ext: str, uploaded_by: str) -> dict:
    con = sqlite3.connect(DB_PATH)
    next_order = con.execute(
        "SELECT COALESCE(MAX(display_order),0)+1 FROM process_diagrams").fetchone()[0]
    cur = con.execute(
        "INSERT INTO process_diagrams (title,description,filename,file_path,file_ext,uploaded_by,display_order) "
        "VALUES (?,?,?,?,?,?,?)",
        (title.strip(), description.strip(), filename, file_path, file_ext, uploaded_by, next_order))
    con.commit()
    new_id = cur.lastrowid
    con.close()
    return {'ok': True, 'id': new_id}


def replace_process_diagram_file(diagram_id: int, filename: str, file_path: str,
                                 file_ext: str, uploaded_by: str) -> Optional[dict]:
    """제목/순서는 유지하고 파일만 교체한다. 이전 파일 경로를 반환(호출측에서 정리)."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM process_diagrams WHERE id=?", (diagram_id,)).fetchone()
    if not row:
        con.close(); return None
    old = dict(row)
    con.execute(
        "UPDATE process_diagrams SET filename=?, file_path=?, file_ext=?, uploaded_by=?, "
        "uploaded_at=datetime('now','localtime') WHERE id=?",
        (filename, file_path, file_ext, uploaded_by, diagram_id))
    con.commit(); con.close()
    return old


def delete_process_diagram(diagram_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM process_diagrams WHERE id=?", (diagram_id,)).fetchone()
    if not row:
        con.close(); return None
    info = dict(row)
    con.execute("DELETE FROM process_diagrams WHERE id=?", (diagram_id,))
    con.commit(); con.close()
    return info


# ── 전체 흐름도 대체 사진 ─────────────────────────────────────────────────────
def get_flowchart_override() -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM flowchart_override WHERE id=1").fetchone()
    con.close()
    return dict(row) if row else None


def set_flowchart_override(filename: str, file_path: str, file_ext: str, uploaded_by: str) -> dict:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO flowchart_override (id,filename,file_path,file_ext,uploaded_by) VALUES (1,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET filename=excluded.filename, file_path=excluded.file_path, "
        "file_ext=excluded.file_ext, uploaded_by=excluded.uploaded_by, uploaded_at=datetime('now','localtime')",
        (filename, file_path, file_ext, uploaded_by))
    con.commit(); con.close()
    return {'ok': True}


def clear_flowchart_override() -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM flowchart_override WHERE id=1").fetchone()
    if not row:
        con.close(); return None
    info = dict(row)
    con.execute("DELETE FROM flowchart_override WHERE id=1")
    con.commit(); con.close()
    return info


# ── 게시판별 "사용 방법" 안내 이미지 ───────────────────────────────────────────
def get_board_guide_image(board: str) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM board_guide_image WHERE board=?", (board,)).fetchone()
    con.close()
    return dict(row) if row else None


def set_board_guide_image(board: str, filename: str, file_path: str, file_ext: str, uploaded_by: str) -> dict:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO board_guide_image (board,filename,file_path,file_ext,uploaded_by) VALUES (?,?,?,?,?) "
        "ON CONFLICT(board) DO UPDATE SET filename=excluded.filename, file_path=excluded.file_path, "
        "file_ext=excluded.file_ext, uploaded_by=excluded.uploaded_by, uploaded_at=datetime('now','localtime')",
        (board, filename, file_path, file_ext, uploaded_by))
    con.commit(); con.close()
    return {'ok': True}


def clear_board_guide_image(board: str) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM board_guide_image WHERE board=?", (board,)).fetchone()
    if not row:
        con.close(); return None
    info = dict(row)
    con.execute("DELETE FROM board_guide_image WHERE board=?", (board,))
    con.commit(); con.close()
    return info


def get_country_ppt_revision(rev_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM country_ppt_revisions WHERE id=?", (rev_id,)).fetchone()
    con.close()
    return dict(row) if row else None


# ── CCC 매트릭스 CRUD ─────────────────────────────────────────────────────────

MATERIAL_TYPES = ['CLOTH', 'A/CL(콤비)', 'A/LE(인조)', 'P/L(천연)']


def get_ccc_matrix(vehicle_code: str) -> list:
    """차종의 CCC 매트릭스 (차종 단독 연결 — 단계 무관)."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM ccc_matrix WHERE vehicle_code=? ORDER BY material_type, country_code",
        (vehicle_code,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def upsert_ccc_matrix(vehicle_code: str, material_type: str, country_code: str,
                       ccc_code: str, username: str):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO ccc_matrix (vehicle_code,stage,material_type,country_code,ccc_code,updated_by,updated) "
        "VALUES (?,'',?,?,?,?,datetime('now','localtime')) "
        "ON CONFLICT(vehicle_code,stage,material_type,country_code) DO UPDATE SET "
        "ccc_code=excluded.ccc_code, updated_by=excluded.updated_by, updated=excluded.updated",
        (vehicle_code, material_type, country_code, ccc_code, username)
    )
    con.commit(); con.close()


def delete_ccc_matrix(vehicle_code: str):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM ccc_matrix WHERE vehicle_code=?", (vehicle_code,))
    con.commit(); con.close()


def get_ccc_codes_for_dropdown(vehicle_code: str) -> list:
    """영업단가 드롭다운용: 해당 차종의 고유 CCC 코드 목록"""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT DISTINCT material_type, country_code, ccc_code FROM ccc_matrix "
        "WHERE vehicle_code=? AND ccc_code!='' ORDER BY material_type, country_code",
        (vehicle_code,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ── 영업단가 v2 CRUD ──────────────────────────────────────────────────────────

def upsert_sales_price_v2(vehicle_code, stage, part_no, part_name,
                           material_type, country_code, ccc_code,
                           unit_price, currency, effective_date, username,
                           compare_pno='') -> dict:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO sales_prices_v2 "
        "(vehicle_code,stage,part_no,part_name,material_type,country_code,ccc_code,compare_pno,"
        "unit_price,currency,effective_date,input_by,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime')) "
        "ON CONFLICT(vehicle_code,stage,part_no,material_type,country_code) DO UPDATE SET "
        "part_name=excluded.part_name, ccc_code=excluded.ccc_code, compare_pno=excluded.compare_pno, "
        "unit_price=excluded.unit_price, currency=excluded.currency, effective_date=excluded.effective_date, "
        "input_by=excluded.input_by, updated=excluded.updated",
        (vehicle_code, stage, part_no, part_name, material_type, country_code, ccc_code, compare_pno,
         unit_price, currency, effective_date, username)
    )
    con.commit(); con.close()
    return {'ok': True}


def get_sales_prices_v2(vehicle_code=None, stage=None) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    q = "SELECT * FROM sales_prices_v2 WHERE 1=1"
    params = []
    if vehicle_code:
        q += " AND vehicle_code=?"; params.append(vehicle_code)
    if stage:
        q += " AND stage=?"; params.append(stage)
    q += " ORDER BY part_no, material_type, country_code"
    rows = con.execute(q, params).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ── PEL 이력 관리 CRUD ─────────────────────────────────────────────────────────

# 단계 표시 순서 (게시판 정렬용)
PEL_STAGE_ORDER = ['MY', 'PE', 'P1', 'P2', 'P3', 'M', 'SOP', '양산', '기타']
# 열 구분 옵션 (시트 열 구분)
PEL_COLUMN_DIVS = ['1열', '2열', '3열', '공통', '기타']


def add_pel_history(vehicle_code, stage, revision, title, description,
                    filename, file_id, file_path, uploaded_by, column_div='') -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "INSERT INTO pel_history (vehicle_code,stage,column_div,revision,title,description,"
        "filename,file_id,file_path,uploaded_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (vehicle_code, stage, column_div, revision, title, description,
         filename, file_id, file_path, uploaded_by)
    )
    new_id = cur.lastrowid
    con.commit(); con.close()
    return new_id


def update_pel_history(item_id, stage, column_div, revision, title, description,
                       filename=None, file_id=None, file_path=None) -> bool:
    """텍스트 필드 수정. 파일 인자가 주어지면(새 첨부) 파일 정보도 교체."""
    con = sqlite3.connect(DB_PATH)
    sets = ["stage=?", "column_div=?", "revision=?", "title=?", "description=?"]
    params = [stage, column_div, revision, title, description]
    if file_id is not None:
        sets += ["filename=?", "file_id=?", "file_path=?"]
        params += [filename, file_id, file_path]
    params.append(item_id)
    cur = con.execute(f"UPDATE pel_history SET {', '.join(sets)} WHERE id=?", params)
    n = cur.rowcount
    con.commit(); con.close()
    return n > 0


def get_pel_history(vehicle_code: str) -> list:
    """차종별 이력 — 단계 순서 → 최신 등록 우선"""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM pel_history WHERE vehicle_code=? ORDER BY created DESC, id DESC",
        (vehicle_code,)
    ).fetchall()
    con.close()
    items = [dict(r) for r in rows]
    # 단계 순서로 정렬 (같은 단계 안에서는 최신 우선 = 이미 created DESC)
    def stage_key(it):
        st = (it.get('stage') or '').upper()
        return PEL_STAGE_ORDER.index(st) if st in PEL_STAGE_ORDER else len(PEL_STAGE_ORDER)
    items.sort(key=stage_key)
    return items


def get_pel_history_item(item_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM pel_history WHERE id=?", (item_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def delete_pel_history(item_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM pel_history WHERE id=?", (item_id,)).fetchone()
    if not row:
        con.close(); return None
    info = dict(row)
    con.execute("DELETE FROM pel_history WHERE id=?", (item_id,))
    con.commit(); con.close()
    return info


# ── PEL 사양변경 CRUD ──────────────────────────────────────────────────────────

def add_pel_spec(vehicle_code, powertrain, revision, title, description,
                 filename, file_id, file_path, uploaded_by, factory='공통', my_code='',
                 row_level='') -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "INSERT INTO pel_spec_uploads (vehicle_code,powertrain,factory,my_code,row_level,revision,title,description,"
        "filename,file_id,file_path,uploaded_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (vehicle_code, powertrain, factory, my_code, row_level, revision, title, description,
         filename, file_id, file_path, uploaded_by))
    new_id = cur.lastrowid
    con.commit(); con.close()
    return new_id


def get_pel_spec_row_levels(vehicle_code: str) -> list:
    """차종에 업로드된 열구분(row_level) 목록 (빈 값 제외, 통합 그리드 탭용)."""
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT DISTINCT row_level FROM pel_spec_uploads WHERE vehicle_code=? AND row_level<>'' "
        "ORDER BY row_level", (vehicle_code,)).fetchall()
    con.close()
    return [r[0] for r in rows]


def get_pel_spec_latest_by_factory(vehicle_code: str, row_level: str = '') -> list:
    """차종(및 열구분)의 공장별 최신 PEL 업로드 1건씩 (통합 그리드용)."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    if row_level:
        rows = con.execute(
            "SELECT * FROM pel_spec_uploads WHERE id IN "
            "(SELECT MAX(id) FROM pel_spec_uploads WHERE vehicle_code=? AND row_level=? GROUP BY factory) "
            "ORDER BY factory", (vehicle_code, row_level)).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM pel_spec_uploads WHERE id IN "
            "(SELECT MAX(id) FROM pel_spec_uploads WHERE vehicle_code=? GROUP BY factory) "
            "ORDER BY factory", (vehicle_code,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_pel_spec_list(vehicle_code: str) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM pel_spec_uploads WHERE vehicle_code=? ORDER BY created DESC, id DESC",
                       (vehicle_code,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_pel_spec(item_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM pel_spec_uploads WHERE id=?", (item_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def delete_pel_spec(item_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM pel_spec_uploads WHERE id=?", (item_id,)).fetchone()
    if not row:
        con.close(); return None
    info = dict(row)
    con.execute("DELETE FROM pel_spec_uploads WHERE id=?", (item_id,))
    con.commit(); con.close()
    return info


# ── 영업 단가 원본 파일 (차종별 리비전) ──────────────────────────────────────────
def add_sales_file(vehicle_code, powertrain, revision, title, description,
                   filename, file_id, file_path, uploaded_by) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "INSERT INTO sales_price_files (vehicle_code,powertrain,revision,title,description,"
        "filename,file_id,file_path,uploaded_by) VALUES (?,?,?,?,?,?,?,?,?)",
        (vehicle_code, powertrain, revision, title, description, filename, file_id, file_path, uploaded_by))
    new_id = cur.lastrowid
    con.commit(); con.close()
    return new_id


def get_sales_file_list(vehicle_code: str) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM sales_price_files WHERE vehicle_code=? ORDER BY created DESC, id DESC",
                       (vehicle_code,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_sales_file(item_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM sales_price_files WHERE id=?", (item_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def delete_sales_file(item_id: int) -> Optional[dict]:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM sales_price_files WHERE id=?", (item_id,)).fetchone()
    if not row:
        con.close(); return None
    info = dict(row)
    con.execute("DELETE FROM sales_price_files WHERE id=?", (item_id,))
    con.commit(); con.close()
    return info


def update_sales_file_edits(item_id: int, edits_json: str):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE sales_price_files SET edits_json=? WHERE id=?", (edits_json, item_id))
    con.commit(); con.close()


# ══════════════════════════════════════════════════════════════════════════════
# 카티아 2D/3D 파일 관리
# ══════════════════════════════════════════════════════════════════════════════
# 실측(SP3 FRT 03_PLASTIC 598개, 규칙 일치 98.8%): 파일명이
#   «품번__리비전__품명__EO번호__날짜.확장자»  로 통일돼 있다.
#   예) 88010-BS010__A__SHIELD COVER ASSY-FR SEAT OTR, LH__SP3-EO-24-016__240403.CATDrawing
# 덕분에 사용자가 메타데이터를 다시 칠 필요가 없다 — 올리면 자동으로 뽑는다.

# 부품군: 실제 NAS 폴더(01_FRM ~ 08_HW)와 1:1 로 맞춘다. 그 «아래»는 폴더를 더 파지 않는다 —
# 실측에서 03_PLASTIC 안에 PAD 11품번이 섞여 있었고, 부품군마다 폴더 깊이도 제각각이었다.
# 소분류는 품명에서 자동 추출(part_type)해 «필터»로 제공한다.
CATIA_PART_GROUPS = [
    ('FRM',      '프레임'),
    ('PAD',      '패드'),
    ('PLASTIC',  '플라스틱'),
    ('H_REST',   '헤드레스트'),
    ('A_REST',   '암레스트'),
    ('SUPPLIER', '협력사 승인품'),
    ('PURCHASE', '사급품'),
    ('HW',       'HW'),
    ('ETC',      '기타'),
]
CATIA_GROUP_LABEL = dict(CATIA_PART_GROUPS)

# 단계 — 폴더로 나누면 한눈에 안 보인다는 사용자 지적에 따라 «열»로만 들고 있는다.
CATIA_STAGES = ['선행검토', '선행시트', '양금시작차', 'SOP', '승인도', '기타']

CATIA_2D_EXTS = ('.catdrawing', '.pdf', '.dwg', '.dxf', '.tif', '.tiff')
CATIA_3D_EXTS = ('.catpart', '.catproduct', '.stp', '.step', '.igs', '.iges', '.jt', '.stl')

_CATIA_PAT = re.compile(
    r'^(?P<pno>[A-Z]?[0-9][0-9A-Z\-]*)__(?P<rev>[^_]+)__(?P<name>.+?)__'
    r'(?P<eo>[A-Z0-9\-]+)__(?P<date>\d{6})(?:__(?P<tail>.+))?$')


def catia_kind_of(ext: str) -> str:
    e = (ext or '').lower()
    if e in CATIA_3D_EXTS:
        return '3D'
    if e in CATIA_2D_EXTS:
        return '2D'
    return ''


def _rev_sort(rev: str) -> int:
    """리비전 정렬용 숫자. 00→0, A→1 … Z→26, R01/REV01→101…, 그 외는 끝으로."""
    r = (rev or '').strip().upper()
    if r.isdigit():
        return int(r)
    m = re.match(r'^R(?:EV)?[\-_]?(\d+)$', r)
    if m:
        return 100 + int(m.group(1))
    if len(r) == 1 and 'A' <= r <= 'Z':
        return ord(r) - 64
    if len(r) == 2 and r.isalpha():          # AA, AB … (Z 다음)
        return 26 + (ord(r[0]) - 64) * 26 + (ord(r[1]) - 64)
    return 9999


def _part_type_of(part_name: str) -> str:
    """품명에서 부품 유형을 뽑는다. 'SHIELD COVER ASSY-FR SEAT OTR, LH' → 'SHIELD COVER'.
       실측 100품번이 27종으로 깔끔히 떨어졌다(쉴드커버 15·백보드 12·패드 11·레버 8…)."""
    n = (part_name or '').split('-')[0].strip().upper()
    # 하이픈이 없는 품명(SUPT BRKT_FR SEAT BACK UPR)은 밑줄로 한 번 더 자른다.
    # 단 H_REST·A_REST·B_COVER 처럼 밑줄이 이름의 일부인 경우가 있어 «길 때만» 자른다.
    if len(n) > 20 and '_' in n:
        n = n.split('_')[0].strip()
    n = re.sub(r'\s+ASSY$', '', n).strip()
    return n[:40]


def _side_of(part_name: str) -> str:
    n = (part_name or '').strip().upper()
    if re.search(r'[,_\s]RH\b', n) or n.endswith('RH'):
        return 'RH'
    if re.search(r'[,_\s]LH\b', n) or n.endswith('LH'):
        return 'LH'
    return ''


def parse_catia_filename(filename: str) -> dict:
    """파일명에서 메타 추출. 규칙에 안 맞으면 parsed=0 으로 두고 화면에서 수기 보정하게 한다."""
    stem, ext = os.path.splitext(filename or '')
    ext = ext.lower()
    out = {'filename': filename, 'ext': ext, 'kind': catia_kind_of(ext),
           'part_no': '', 'rev': '', 'part_name': '', 'eo_no': '', 'file_date': '',
           'part_type': '', 'side': '', 'rev_sort': 9999, 'parsed': 0, 'note': ''}
    m = _CATIA_PAT.match(stem)
    if not m:
        return out
    g = m.groupdict()
    d = g['date']
    out.update(part_no=g['pno'].strip(), rev=g['rev'].strip(), part_name=g['name'].strip(),
               eo_no=g['eo'].strip(), file_date='20%s-%s-%s' % (d[0:2], d[2:4], d[4:6]),
               part_type=_part_type_of(g['name']), side=_side_of(g['name']),
               rev_sort=_rev_sort(g['rev']), parsed=1)
    if g.get('tail'):
        # 88351-BS050__00__…__240315__R01 처럼 뒤에 다른 리비전 표기가 붙는 사례가 실제로 있다
        out['note'] = '파일명 뒤 표기: ' + g['tail']
    return out


# part_type·side·rev_sort 는 «파일명에서 계산된 값»이라 규칙을 고치면 기존 행도 따라와야 한다.
# 사용자가 직접 입력한 값이 아니므로 재계산이 항상 안전하다. 규칙을 바꾸면 이 숫자만 올리면 된다.
CATIA_DERIVE_VER = 2


def _get_meta(key: str, default: str = '') -> str:
    con = sqlite3.connect(DB_PATH)
    con.execute('CREATE TABLE IF NOT EXISTS app_meta (k TEXT PRIMARY KEY, v TEXT)')
    row = con.execute('SELECT v FROM app_meta WHERE k=?', (key,)).fetchone()
    con.commit(); con.close()
    return row[0] if row else default


def _set_meta(key: str, value: str):
    con = sqlite3.connect(DB_PATH)
    con.execute('CREATE TABLE IF NOT EXISTS app_meta (k TEXT PRIMARY KEY, v TEXT)')
    con.execute('INSERT INTO app_meta (k,v) VALUES (?,?) '
                'ON CONFLICT(k) DO UPDATE SET v=excluded.v', (key, str(value)))
    con.commit(); con.close()


def refresh_catia_derived(force: bool = False) -> int:
    """추출 규칙이 바뀌면 기존 행의 파생값을 다시 계산한다. 품번·리비전 «수기 보정분은 건드리지 않는다»."""
    if not force and _get_meta('catia_derive_ver') == str(CATIA_DERIVE_VER):
        return 0
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute('SELECT id,part_name,rev FROM catia_files').fetchall()
    n = 0
    for r in rows:
        con.execute('UPDATE catia_files SET part_type=?, side=?, rev_sort=? WHERE id=?',
                    (_part_type_of(r['part_name']), _side_of(r['part_name']),
                     _rev_sort(r['rev']), r['id']))
        n += 1
    con.commit(); con.close()
    _set_meta('catia_derive_ver', CATIA_DERIVE_VER)
    return n


def add_catia_file(meta: dict, username: str) -> int:
    con = sqlite3.connect(DB_PATH)
    cols = ('vehicle_code', 'row_level', 'stage', 'part_group', 'kind', 'part_no', 'rev',
            'rev_sort', 'part_name', 'part_type', 'side', 'eo_no', 'file_date',
            'filename', 'file_path', 'ext', 'size_no', 'parsed', 'note')
    nums = ('rev_sort', 'size_no', 'parsed')
    vals = [int(meta.get(c) or 0) if c in nums else str(meta.get(c) or '') for c in cols]
    cur = con.execute(
        'INSERT INTO catia_files (%s,uploaded_by) VALUES (%s,?)'
        % (','.join(cols), ','.join(['?'] * len(cols))), vals + [username])
    fid = cur.lastrowid
    con.commit(); con.close()
    return fid


def find_catia_duplicate(vehicle: str, part_no: str, rev: str, kind: str, filename: str):
    """같은 차종·품번·리비전·종류가 이미 있으면 알려 준다(덮어쓰기 사고 방지)."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = None
    if part_no:
        row = con.execute(
            'SELECT * FROM catia_files WHERE vehicle_code=? AND part_no=? AND rev=? AND kind=? '
            'ORDER BY id DESC LIMIT 1', (vehicle, part_no, rev, kind)).fetchone()
    if not row:
        row = con.execute('SELECT * FROM catia_files WHERE filename=? ORDER BY id DESC LIMIT 1',
                          (filename,)).fetchone()
    con.close()
    return dict(row) if row else None


def get_catia_facets(vehicle: str = '') -> dict:
    """필터 칩에 쓸 값별 건수. 폴더 트리 대신 이걸로 좁힌다."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    out = {}
    for key, col in (('vehicles', 'vehicle_code'), ('rows', 'row_level'), ('stages', 'stage'),
                     ('groups', 'part_group'), ('types', 'part_type'), ('sides', 'side')):
        if vehicle and col != 'vehicle_code':
            w, p = ' WHERE vehicle_code=?', [vehicle]
        else:
            w, p = '', []
        rows = con.execute(
            'SELECT %s AS v, COUNT(DISTINCT part_no) AS pc, COUNT(*) AS fc '
            'FROM catia_files%s GROUP BY %s ORDER BY pc DESC' % (col, w, col), p).fetchall()
        out[key] = [{'value': r['v'] or '', 'parts': r['pc'], 'files': r['fc']}
                    for r in rows if (r['v'] or '')]
    con.close()
    return out


def search_catia_parts(vehicle='', row_level='', stage='', part_group='', part_type='',
                       side='', kind='', q='', limit=400) -> dict:
    """품번 단위로 «접어서» 돌려준다 — 파일을 전부 늘어놓으면 그게 곧 탐색기라 웹에서 의미가 없다.
       실측 598 파일 → 100 행."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    where, params = [], []
    for col, val in (('vehicle_code', vehicle), ('row_level', row_level), ('stage', stage),
                     ('part_group', part_group), ('part_type', part_type), ('side', side),
                     ('kind', kind)):
        if val:
            where.append(col + '=?'); params.append(val)
    if q:
        where.append('(part_no LIKE ? OR part_name LIKE ? OR eo_no LIKE ? OR filename LIKE ?)')
        params += ['%' + q + '%'] * 4
    w = (' WHERE ' + ' AND '.join(where)) if where else ''
    rows = con.execute('SELECT * FROM catia_files%s ORDER BY part_no, kind, rev_sort' % w,
                       params).fetchall()
    total_files = len(rows)

    groups = {}
    for r in rows:
        d = dict(r)
        key = d['part_no'] or ('(미인식) ' + d['filename'])
        g = groups.setdefault(key, {
            'part_no': d['part_no'], 'part_name': d['part_name'], 'part_type': d['part_type'],
            'side': d['side'], 'part_group': d['part_group'], 'stage': d['stage'],
            'vehicle_code': d['vehicle_code'], 'row_level': d['row_level'],
            'key': key, 'files': {'2D': [], '3D': [], '': []}, 'names': set(),
        })
        g['files'].setdefault(d['kind'] or '', []).append(d)
        if d['part_name']:
            g['names'].add(d['part_name'])
            if not g['part_name']:
                g['part_name'] = d['part_name']

    # 품목 마스터 매칭 — 카티아 품번과 BOM 품번 형식이 같아(88010-BS010 / 88005-P1000) 그대로 붙는다
    pnos = [g['part_no'] for g in groups.values() if g['part_no']]
    master = {}
    for i in range(0, len(pnos), 400):
        chunk = pnos[i:i + 400]
        qm = ','.join(['?'] * len(chunk))
        for m in con.execute(
                'SELECT part_no,part_name,level FROM parts WHERE part_no IN (%s)' % qm, chunk):
            master[m['part_no']] = {'name': m['part_name'], 'level': m['level']}
    con.close()

    def _rev_view(f):
        # rev_sort 를 같이 내려보낸다 — 화면에서 «리비전 순»으로 정렬하려면 필요하다
        # (문자열 정렬로는 00 < A < … < Z < R01 순서가 안 나온다)
        return {'id': f['id'], 'rev': f['rev'], 'rev_sort': f['rev_sort'],
                'eo_no': f['eo_no'], 'file_date': f['file_date'],
                'filename': f['filename'], 'size_no': f['size_no'], 'stage': f['stage'],
                'note': f['note'], 'parsed': f['parsed'], 'ext': f['ext'],
                'kind': f['kind'], 'part_no': f['part_no']}

    items = []
    for key, g in groups.items():
        rec = {k: v for k, v in g.items() if k not in ('files', 'names')}
        rec['issues'] = []
        for kk in ('2D', '3D'):
            fl = sorted(g['files'].get(kk, []), key=lambda x: (x['rev_sort'], x['id']))
            rec[kk] = {'count': len(fl),
                       'latest': _rev_view(fl[-1]) if fl else None,
                       'revs': [_rev_view(f) for f in fl]}
            # 리비전 중복·결번 — 폴더에서는 절대 안 보이던 것. 실측 SP3 플라스틱에서 17건 나왔다.
            seen = [(f['rev'] or '').upper() for f in fl]
            dup = sorted({r for r in seen if seen.count(r) > 1 and r})
            if dup:
                rec['issues'].append('%s 리비전 중복 %s' % (kk, '·'.join(dup)))
            letters = sorted({r for r in seen if len(r) == 1 and r.isalpha()})
            if len(letters) >= 2:
                gap = [chr(c) for c in range(ord(letters[0]), ord(letters[-1]) + 1)
                       if chr(c) not in letters]
                if gap:
                    rec['issues'].append('%s 리비전 결번 %s' % (kk, '·'.join(gap)))
        other = g['files'].get('', [])
        rec['other'] = {'count': len(other), 'revs': [_rev_view(f) for f in other]}
        if not g['part_no']:
            # 파일명 규칙 밖이라 품번을 못 읽은 줄 — 2D/3D 짝 검사는 의미가 없다
            rec['issues'] = ['파일명 규칙 밖 — 품번·리비전 수기 입력 필요']
            rec['in_master'] = False
            rec['master_name'] = ''
            items.append(rec)
            continue
        if rec['3D']['count'] and not rec['2D']['count']:
            rec['issues'].append('2D 도면 없음')
        if rec['2D']['count'] and not rec['3D']['count']:
            rec['issues'].append('3D 데이터 없음')
        if len(g['names']) > 1:
            rec['issues'].append('품명 표기 %d종 불일치' % len(g['names']))
        m = master.get(g['part_no'])
        rec['in_master'] = bool(m)
        rec['master_name'] = (m['name'] if m else '')
        # 품목 마스터와 품명이 다르면 알려 준다(도면 품명 오기 또는 BOM 오기)
        if m and m['name'] and rec['part_name'] and \
                m['name'].replace(' ', '').upper() != rec['part_name'].replace(' ', '').upper():
            rec['issues'].append('품목마스터 품명과 다름')
        items.append(rec)

    items.sort(key=lambda r: r['key'])
    return {'items': items[:limit], 'total_parts': len(items), 'total_files': total_files}


def get_catia_file(file_id: int):
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute('SELECT * FROM catia_files WHERE id=?', (file_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def update_catia_file(file_id: int, fields: dict) -> dict:
    """규칙에 안 맞는 파일명을 수기 보정할 때 쓴다."""
    allowed = ('part_no', 'rev', 'part_name', 'eo_no', 'file_date', 'kind',
               'stage', 'part_group', 'row_level', 'vehicle_code', 'note', 'side')
    sets, vals = [], []
    for k, v in (fields or {}).items():
        if k in allowed:
            sets.append(k + '=?'); vals.append(str(v or ''))
    if not sets:
        return {'ok': False, 'msg': '변경할 항목이 없습니다.'}
    if 'rev' in fields:
        sets.append('rev_sort=?'); vals.append(_rev_sort(str(fields.get('rev') or '')))
    if 'part_name' in fields:
        sets.append('part_type=?'); vals.append(_part_type_of(str(fields.get('part_name') or '')))
        sets.append('side=?'); vals.append(_side_of(str(fields.get('part_name') or '')))
    sets.append('parsed=1')
    con = sqlite3.connect(DB_PATH)
    con.execute('UPDATE catia_files SET %s WHERE id=?' % ','.join(sets), vals + [file_id])
    con.commit(); con.close()
    return {'ok': True}


def delete_catia_file(file_id: int) -> dict:
    f = get_catia_file(file_id)
    if not f:
        return {'ok': False, 'msg': '파일을 찾을 수 없습니다.'}
    con = sqlite3.connect(DB_PATH)
    con.execute('DELETE FROM catia_files WHERE id=?', (file_id,))
    con.commit(); con.close()
    try:
        if f.get('file_path') and os.path.exists(f['file_path']):
            os.remove(f['file_path'])
    except OSError:
        pass
    return {'ok': True}


def get_catia_stats(vehicle: str = '') -> dict:
    con = sqlite3.connect(DB_PATH)
    w, p = ('', []) if not vehicle else (' WHERE vehicle_code=?', [vehicle])
    j = ' AND ' if w else ' WHERE '
    q = lambda extra, pp=None: con.execute(
        'SELECT %s FROM catia_files%s' % (extra, w), p if pp is None else pp).fetchone()[0]
    out = {'files': q('COUNT(*)'), 'parts': q('COUNT(DISTINCT part_no)'),
           'size': q('COALESCE(SUM(size_no),0)')}
    for k, cond in (('d2', "kind='2D'"), ('d3', "kind='3D'"), ('unparsed', 'parsed=0')):
        out[k] = con.execute('SELECT COUNT(*) FROM catia_files%s%s%s' % (w, j, cond),
                             p).fetchone()[0]
    con.close()
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 조직도 (사내 PLM 1회 등록)
# ══════════════════════════════════════════════════════════════════════════════
# 사내 PLM(ds.dayou.co.kr, Oracle mod_plsql)의 결재선지정 창을 실측한 결과:
#   · 조직도 = /qms/wf_htp_pkg.gen_org (iframe), 사원 목록 열 = 사업장/소속/성명/아이디
#   · p_emp_id 값이 'shkim' — «사번 = 아이디 = 우리 시스템 로그인 계정»이라 그대로 매칭된다
#   · 결재형태 5종: 0=접수, 3=검토, 4=협조, 9=승인, 1=참조
# 자동 연동은 PLM 로그인 세션이 필요해 사용자가 «1회 등록» 방식을 선택했다.

# 결재형태 — PLM 코드값을 그대로 쓴다(나중에 연동해도 코드가 어긋나지 않게).
# 참조(1)는 «결재를 막지 않는» 통보 대상이라 승인 차례 계산에서 빠진다.
EO_APPROVAL_TYPES = [
    ('0', '접수'),
    ('3', '검토'),
    ('4', '협조'),
    ('9', '승인'),
    ('1', '참조'),
]
EO_TYPE_LABEL = dict(EO_APPROVAL_TYPES)
EO_TYPE_REFERENCE = '1'          # 참조 — 승인 차례를 갖지 않는다

ORG_COLUMN_ALIASES = {
    'emp_id':    ['아이디', '사번', 'id', 'emp_id', '사원번호', '계정', '사용자id'],
    'name':      ['성명', '이름', 'name', '사원명', '성 명'],
    'site':      ['사업장', 'site', '회사', '법인'],
    'dept_name': ['소속', '부서', '부서명', 'dept', '팀', '조직'],
    'dept_code': ['부서코드', 'dept_code', '조직코드', 'org_id'],
    'position':  ['직급', '직위', 'position', '직책'],
    'email':     ['이메일', 'email', '메일', 'e-mail'],
    'phone':     ['전화', '연락처', 'phone', '휴대폰'],
}


def _norm_hdr(s: str) -> str:
    return re.sub(r'[\s_\-()]', '', str(s or '')).lower()


def upsert_org_members(rows: list, username: str = '') -> dict:
    """조직도 일괄 등록. emp_id 기준 upsert — 다시 올리면 갱신되고 없던 사람은 추가된다.
       빈 칸은 기존 값을 덮어쓰지 않는다(부분 갱신 안전)."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    added = updated = skipped = 0
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for r in rows:
        emp = str(r.get('emp_id') or '').strip()
        if not emp:
            skipped += 1
            continue
        cur = con.execute('SELECT * FROM org_members WHERE emp_id=?', (emp,)).fetchone()
        cols = ('name', 'site', 'dept_code', 'dept_name', 'position', 'email', 'phone')
        if cur:
            sets, vals = [], []
            for c in cols:
                v = str(r.get(c) or '').strip()
                if v and v != (cur[c] or ''):
                    sets.append(c + '=?'); vals.append(v)
            if sets:
                sets.append('updated=?'); vals.append(now)
                con.execute('UPDATE org_members SET %s WHERE emp_id=?' % ','.join(sets),
                            vals + [emp])
                updated += 1
            else:
                skipped += 1
        else:
            con.execute(
                'INSERT INTO org_members (emp_id,name,site,dept_code,dept_name,position,'
                'email,phone,updated) VALUES (?,?,?,?,?,?,?,?,?)',
                [emp] + [str(r.get(c) or '').strip() for c in cols] + [now])
            added += 1
    con.commit(); con.close()
    return {'ok': True, 'added': added, 'updated': updated, 'skipped': skipped}


def parse_org_rows(table: list) -> dict:
    """헤더 이름으로 열을 자동 인식한다. 엑셀 열 순서가 회사마다 달라서 위치 고정은 못 쓴다."""
    if not table:
        return {'rows': [], 'columns': [], 'header_row': -1}
    alias = {}
    for key, names in ORG_COLUMN_ALIASES.items():
        for n in names:
            alias[_norm_hdr(n)] = key
    # 헤더 행 탐색 — «아는 열 이름»이 가장 많은 행. 화면을 통째로 복사(Ctrl+A)해서 붙여넣으면
    # 표 위에 메뉴·안내 문구가 수십 줄 붙어 오므로 넉넉히 훑는다.
    best_i, best_map = -1, {}
    for i, row in enumerate(table[:80]):
        m = {}
        for ci, cell in enumerate(row):
            k = alias.get(_norm_hdr(cell))
            if k and k not in m:
                m[k] = ci
        if len(m) > len(best_map):
            best_i, best_map = i, m
    # 헤더가 «세로»로 떨어져 오는 경우 — PLM 그리드(dhtmlxGrid)를 복사하면 머리글 셀이
    # 한 줄에 하나씩 나온다. 그때는 나온 «순서»가 곧 열 순서다.
    if 'emp_id' not in best_map or 'name' not in best_map:
        seq, seen = [], set()
        for i, row in enumerate(table[:80]):
            cells = [c for c in row if str(c or '').strip()]
            if len(cells) != 1:
                if seq and len(cells) > len(seq):
                    best_i, best_map = i - 1, {k: n for n, k in enumerate(seq)}
                    break
                continue
            k = alias.get(_norm_hdr(cells[0]))
            if k and k not in seen:
                seq.append(k); seen.add(k)

    if 'emp_id' not in best_map or 'name' not in best_map:
        return {'rows': [], 'columns': list(best_map.keys()), 'header_row': best_i,
                'error': '«아이디(사번)»와 «성명» 열을 찾지 못했습니다.'}

    # 머리글이 안 붙은 뒷열 보강 — PLM 그리드는 실제로
    # [사번, 성명, 부서, 회사코드, 부서코드, 내부키, 사업장] 7열로 나온다.
    body = [r for r in table[best_i + 1:] if len([c for c in r if str(c or '').strip()]) >= 3]
    wide = body and (sum(1 for r in body if len(r) >= 7) > len(body) * 0.8)
    if wide and best_map.get('emp_id') == 0 and best_map.get('name') == 1:
        if 'dept_code' not in best_map:
            best_map['dept_code'] = 4
        if 'site' not in best_map:
            best_map['site'] = 6

    out = []
    for row in table[best_i + 1:]:
        rec = {k: (str(row[ci]).strip() if ci < len(row) and row[ci] is not None else '')
               for k, ci in best_map.items()}
        # 머리글 글자가 데이터에 다시 섞여 들어오는 경우가 있어 걸러 낸다
        if rec.get('emp_id') and rec.get('name') and \
                _norm_hdr(rec['emp_id']) not in ('사번', '아이디', 'id'):
            out.append(rec)
    return {'rows': out, 'columns': list(best_map.keys()), 'header_row': best_i}


def get_org_members(q: str = '', dept: str = '', active_only: bool = True) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    where, params = [], []
    if active_only:
        where.append('active=1')
    if dept:
        where.append('dept_name=?'); params.append(dept)
    if q:
        where.append('(emp_id LIKE ? OR name LIKE ? OR dept_name LIKE ? OR position LIKE ?)')
        params += ['%' + q + '%'] * 4
    w = (' WHERE ' + ' AND '.join(where)) if where else ''
    rows = con.execute(
        'SELECT * FROM org_members%s ORDER BY site, dept_name, sort_no, name' % w, params).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_org_tree() -> list:
    """사업장 > 부서 > 사람. 결재선 선택 창이 폴더처럼 펼쳐 볼 수 있게 계층으로 준다.
       계정이 있는 사람만 실제로 승인 버튼을 누를 수 있으므로 has_account 를 같이 실어 보낸다."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    accounts = {r['username'] for r in
                con.execute("SELECT username FROM users WHERE role IN ('user','admin')")}
    rows = con.execute('SELECT * FROM org_members WHERE active=1 '
                       'ORDER BY site, dept_name, sort_no, name').fetchall()
    con.close()
    tree, idx = [], {}
    for r in rows:
        site = r['site'] or '본사'
        dept = r['dept_name'] or '(부서 미지정)'
        sk = idx.get(site)
        if sk is None:
            sk = {'site': site, 'depts': [], '_d': {}}
            idx[site] = sk; tree.append(sk)
        dk = sk['_d'].get(dept)
        if dk is None:
            dk = {'dept': dept, 'members': []}
            sk['_d'][dept] = dk; sk['depts'].append(dk)
        dk['members'].append({
            'emp_id': r['emp_id'], 'name': r['name'], 'position': r['position'] or '',
            'email': r['email'] or '', 'dept': dept, 'site': site,
            'has_account': r['emp_id'] in accounts,
        })
    for s in tree:
        s.pop('_d', None)
    return tree


def get_org_stats() -> dict:
    con = sqlite3.connect(DB_PATH)
    total = con.execute('SELECT COUNT(*) FROM org_members WHERE active=1').fetchone()[0]
    depts = con.execute('SELECT COUNT(DISTINCT dept_name) FROM org_members WHERE active=1').fetchone()[0]
    sites = con.execute('SELECT COUNT(DISTINCT site) FROM org_members WHERE active=1').fetchone()[0]
    linked = con.execute(
        "SELECT COUNT(*) FROM org_members o WHERE o.active=1 AND EXISTS "
        "(SELECT 1 FROM users u WHERE u.username=o.emp_id AND u.role IN ('user','admin'))"
    ).fetchone()[0]
    con.close()
    return {'total': total, 'depts': depts, 'sites': sites, 'linked': linked}


def delete_org_member(emp_id: str) -> dict:
    con = sqlite3.connect(DB_PATH)
    con.execute('DELETE FROM org_members WHERE emp_id=?', (emp_id,))
    con.commit(); con.close()
    return {'ok': True}


def clear_org_members() -> int:
    con = sqlite3.connect(DB_PATH)
    n = con.execute('SELECT COUNT(*) FROM org_members').fetchone()[0]
    con.execute('DELETE FROM org_members')
    con.commit(); con.close()
    return n


# ══════════════════════════════════════════════════════════════════════════════
# 카티아 파일 ↔ 품목 마스터 연결
# ══════════════════════════════════════════════════════════════════════════════
# 실측(2026-08-02): 카티아 32품번 중 품목 마스터와 매칭된 것이 «0개»였다. 원인 두 가지 —
#  ① 카티아 업로드가 품목 마스터에 등록을 하지 않았다(BOM 업로드만 자동 등록하고 있었다).
#  ② 개발단계 품번은 앞에 X가 붙는다(X88010-P1010) — 양산 품번(88010-P1010)과 글자가 달라
#     그대로는 절대 안 붙는다. 같은 부품이므로 «X를 뗀 값»으로 맞춘다.
# BOM 이 먼저 올라오든 도면이 먼저 올라오든 양쪽 다 등록되게 해서 순서를 안 타게 한다.

# SQL 안에서 X 접두를 떼는 식 — 인덱스는 못 타지만 대상이 수천 행이라 문제되지 않는다
_BASE_EXPR = ("CASE WHEN {t}.part_no LIKE 'X%' AND LENGTH({t}.part_no) > 1 "
              "THEN SUBSTR({t}.part_no, 2) ELSE {t}.part_no END")


def base_part_no(pno: str) -> str:
    """개발 품번의 X 접두를 뗀 «대조용» 품번. 표시는 언제나 원래 값을 쓴다."""
    p = str(pno or '').strip()
    if len(p) > 1 and p[0] in ('X', 'x') and p[1].isdigit():
        return p[1:]
    return p


def upsert_parts_from_catia(rows: list, vehicle_code: str, username: str) -> dict:
    """카티아 파일에서 읽은 품번을 품목 마스터에 등록한다.
       이미 있으면 «빈 칸만» 채운다 — 사람이 넣은 스펙을 덮어쓰지 않기 위해서.
       X 접두 품번은 양산 품번이 이미 있으면 그쪽에 붙이고 새로 만들지 않는다."""
    con = sqlite3.connect(DB_PATH)
    added = filled = skipped = 0
    for r in rows:
        pno = str(r.get('part_no') or '').strip()
        if not pno:
            continue
        name = str(r.get('part_name') or '').strip()
        base = base_part_no(pno)
        # 양산 품번(X 없는 것)이 이미 있으면 그것을 쓴다
        target = None
        for cand in ([base, pno] if base != pno else [pno]):
            if con.execute('SELECT 1 FROM parts WHERE part_no=?', (cand,)).fetchone():
                target = cand
                break
        if target is None:
            con.execute('INSERT INTO parts (part_no,part_name,vehicle_code,created_by,updated_by) '
                        'VALUES (?,?,?,?,?)', (pno, name, vehicle_code, username, username))
            added += 1
            continue
        cur = con.execute('SELECT part_name,vehicle_code FROM parts WHERE part_no=?',
                          (target,)).fetchone()
        sets, vals = [], []
        if not (cur[0] or '').strip() and name:
            sets.append('part_name=?'); vals.append(name)
        if not (cur[1] or '').strip() and vehicle_code:
            sets.append('vehicle_code=?'); vals.append(vehicle_code)
        if sets:
            vals.append(target)
            con.execute('UPDATE parts SET %s WHERE part_no=?' % ','.join(sets), vals)
            filled += 1
        else:
            skipped += 1
    con.commit(); con.close()
    return {'added': added, 'filled': filled, 'skipped': skipped}


def get_catia_counts(part_nos: list) -> dict:
    """품번별 카티아 2D·3D 건수와 최신 리비전. X 접두를 뗀 값으로 대조한다."""
    if not part_nos:
        return {}
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    want = {}
    for p in part_nos:
        want.setdefault(base_part_no(p), []).append(p)
    out = {p: {'d2': 0, 'd3': 0, 'rev2': '', 'rev3': '', 'catia_no': ''} for p in part_nos}
    rows = con.execute(
        'SELECT %s AS base, part_no, kind, rev, rev_sort FROM catia_files c '
        'ORDER BY rev_sort' % _BASE_EXPR.format(t='c')).fetchall()
    con.close()
    for r in rows:
        for p in want.get(r['base'], []):
            e = out[p]
            e['catia_no'] = r['part_no']
            if r['kind'] == '2D':
                e['d2'] += 1; e['rev2'] = r['rev'] or ''
            elif r['kind'] == '3D':
                e['d3'] += 1; e['rev3'] = r['rev'] or ''
    return out


def backfill_parts_from_catia(username: str = 'system') -> dict:
    """이미 올라간 카티아 파일을 품목 마스터에 뒤늦게 반영한다.
       연결 기능을 나중에 붙였기 때문에 «재업로드 없이» 따라오게 하기 위한 것."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        'SELECT part_no, part_name, vehicle_code FROM catia_files '
        "WHERE part_no <> '' GROUP BY part_no").fetchall()
    con.close()
    by_veh = {}
    for r in rows:
        by_veh.setdefault(r['vehicle_code'] or '', []).append(
            {'part_no': r['part_no'], 'part_name': r['part_name']})
    tot = {'added': 0, 'filled': 0, 'skipped': 0}
    for veh, items in by_veh.items():
        res = upsert_parts_from_catia(items, veh, username)
        for k in tot:
            tot[k] += res[k]
    return tot


# ══════════════════════════════════════════════════════════════════════════════
# 카티아 도면 — 체크아웃/체크인(잠금)과 수명주기 상태
# ══════════════════════════════════════════════════════════════════════════════
# PLM 기본 동작을 그대로 따른다:
#   · 체크아웃 = 그 부품에 대한 «배타적 편집권». 잠근 사람만 새 리비전을 올리거나 지울 수 있다.
#   · 체크인   = 편집권 반납. 보통 새 리비전을 올린 뒤에 한다.
#   · 배포완료(released)면 «아무도» 못 고친다 — 고치려면 먼저 개정(작업중으로 되돌림)해야 한다.
# 잠금 단위는 «차종 + 품번»이다(화면 한 줄과 같은 단위). 개발 품번(X접두)은 양산 품번과
# 같은 부품이므로 X를 뗀 값으로 묶는다 — 안 그러면 같은 부품을 두 사람이 각각 잠글 수 있다.
#
# 자동 만료는 두지 않았다. 시트 편집(30분)과 달리 카티아 작업은 몇 시간씩 걸려서
# 자동으로 풀면 남의 작업 중에 편집권을 뺏는 사고가 난다. 대신 «얼마나 잠겨 있는지»를
# 보여 주고 관리자가 강제 해제할 수 있게 했다.

CATIA_STATES = [
    ('work',     '작업중'),
    ('review',   '검토중'),
    ('released', '배포완료'),
    ('obsolete', '폐기'),
]
CATIA_STATE_LABEL = dict(CATIA_STATES)
CATIA_STATE_LOCKED_EDIT = ('released', 'obsolete')   # 이 상태에서는 잠금과 무관하게 수정 불가


def _catia_key(vehicle: str, part_no: str) -> tuple:
    return (str(vehicle or '').strip(), base_part_no(part_no))


def get_catia_item(vehicle: str, part_no: str) -> dict:
    """상태·잠금 정보. 아직 아무 조작이 없었으면 기본값(작업중·잠금없음)을 돌려준다."""
    veh, base = _catia_key(vehicle, part_no)
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute('SELECT * FROM catia_items WHERE vehicle_code=? AND base_no=?',
                      (veh, base)).fetchone()
    con.close()
    if row:
        return dict(row)
    return {'vehicle_code': veh, 'base_no': base, 'state': 'work', 'locked_by': '',
            'locked_at': '', 'locked_note': '', 'released_rev': '', 'updated_by': '',
            'updated': ''}


def get_catia_items_map(pairs: list) -> dict:
    """목록용 일괄 조회. 키는 (차종, X뗀품번)."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute('SELECT * FROM catia_items').fetchall()
    con.close()
    m = {(r['vehicle_code'], r['base_no']): dict(r) for r in rows}
    out = {}
    for veh, pno in pairs:
        k = _catia_key(veh, pno)
        out[k] = m.get(k) or {'vehicle_code': k[0], 'base_no': k[1], 'state': 'work',
                              'locked_by': '', 'locked_at': '', 'locked_note': '',
                              'released_rev': '', 'updated_by': '', 'updated': ''}
    return out


def _catia_log(con, veh, base, action, username, detail=''):
    con.execute('INSERT INTO catia_item_log (vehicle_code,base_no,action,username,detail) '
                'VALUES (?,?,?,?,?)', (veh, base, action, username, detail))


def _catia_upsert(con, veh, base, sets: dict, username: str):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sets = dict(sets); sets['updated_by'] = username; sets['updated'] = now
    cur = con.execute('SELECT 1 FROM catia_items WHERE vehicle_code=? AND base_no=?',
                      (veh, base)).fetchone()
    if cur:
        con.execute('UPDATE catia_items SET %s WHERE vehicle_code=? AND base_no=?'
                    % ','.join(k + '=?' for k in sets),
                    list(sets.values()) + [veh, base])
    else:
        cols = ['vehicle_code', 'base_no'] + list(sets.keys())
        con.execute('INSERT INTO catia_items (%s) VALUES (%s)'
                    % (','.join(cols), ','.join(['?'] * len(cols))),
                    [veh, base] + list(sets.values()))


def catia_checkout(vehicle: str, part_no: str, username: str, note: str = '') -> dict:
    """체크아웃 — 배타적 편집권을 잡는다."""
    veh, base = _catia_key(vehicle, part_no)
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute('SELECT * FROM catia_items WHERE vehicle_code=? AND base_no=?',
                      (veh, base)).fetchone()
    if row and (row['locked_by'] or ''):
        if row['locked_by'] == username:
            con.close(); return {'ok': True, 'already': True, 'msg': '이미 체크아웃한 상태입니다.'}
        who, at = row['locked_by'], row['locked_at']
        con.close()
        return {'ok': False, 'msg': f'«{who}» 님이 {at} 부터 작업 중입니다.'}
    if row and (row['state'] or 'work') in CATIA_STATE_LOCKED_EDIT:
        st = CATIA_STATE_LABEL.get(row['state'], row['state'])
        con.close()
        return {'ok': False, 'msg': f'«{st}» 상태라 수정할 수 없습니다. 먼저 «개정»을 눌러 '
                                    f'작업중으로 되돌리세요.'}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _catia_upsert(con, veh, base, {'locked_by': username, 'locked_at': now,
                                   'locked_note': note}, username)
    _catia_log(con, veh, base, 'checkout', username, note)
    con.commit(); con.close()
    return {'ok': True, 'locked_by': username, 'locked_at': now}


def catia_checkin(vehicle: str, part_no: str, username: str, comment: str = '',
                  is_admin: bool = False, cancel: bool = False) -> dict:
    """체크인(반납). cancel=True 면 «체크아웃 취소»로 기록만 달라진다.
       관리자는 남의 잠금도 풀 수 있고, 그 사실이 이력에 남는다."""
    veh, base = _catia_key(vehicle, part_no)
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute('SELECT * FROM catia_items WHERE vehicle_code=? AND base_no=?',
                      (veh, base)).fetchone()
    if not row or not (row['locked_by'] or ''):
        con.close(); return {'ok': False, 'msg': '체크아웃된 상태가 아닙니다.'}
    owner = row['locked_by']
    if owner != username and not is_admin:
        con.close(); return {'ok': False, 'msg': f'«{owner}» 님이 체크아웃한 항목입니다.'}
    forced = (owner != username)
    act = 'cancel' if cancel else 'checkin'
    if forced:
        act = 'force_unlock'
        comment = (comment + ' / ' if comment else '') + f'관리자({username}) 강제 해제'
    _catia_upsert(con, veh, base, {'locked_by': '', 'locked_at': '', 'locked_note': ''}, username)
    _catia_log(con, veh, base, act, username, comment)
    con.commit(); con.close()
    return {'ok': True, 'forced': forced, 'owner': owner}


def catia_set_state(vehicle: str, part_no: str, state: str, username: str,
                    comment: str = '', is_admin: bool = False) -> dict:
    """수명주기 상태 변경. 배포완료로 올리려면 «잠겨 있지 않아야» 한다 —
       누군가 편집 중인 것을 배포하면 안 되기 때문."""
    if state not in CATIA_STATE_LABEL:
        return {'ok': False, 'msg': '알 수 없는 상태입니다.'}
    # 배포·폐기와 «배포 해제(개정)»는 설계자만 할 수 있다(사용자 확정 2026-08-02).
    # 검토 요청은 누구나 할 수 있게 둔다 — 흐름을 시작하는 것뿐이라 위험하지 않다.
    veh, base = _catia_key(vehicle, part_no)
    _cur = get_catia_item(vehicle, part_no).get('state') or 'work'
    if state in CATIA_STATE_LOCKED_EDIT or _cur in CATIA_STATE_LOCKED_EDIT:
        ok_d, why_d = is_design_user(username, is_admin)
        if not ok_d:
            act = '배포는' if state == 'released' else ('폐기는' if state == 'obsolete' else '개정은')
            return {'ok': False, 'msg': f'{act} 설계자만 할 수 있습니다 — {why_d}'}
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    row = con.execute('SELECT * FROM catia_items WHERE vehicle_code=? AND base_no=?',
                      (veh, base)).fetchone()
    cur_state = (row['state'] if row else 'work') or 'work'
    locked_by = (row['locked_by'] if row else '') or ''
    if locked_by and locked_by != username and not is_admin:
        con.close(); return {'ok': False, 'msg': f'«{locked_by}» 님이 작업 중이라 상태를 바꿀 수 없습니다.'}
    if state == 'released' and locked_by:
        con.close(); return {'ok': False, 'msg': '체크아웃된 상태로는 배포할 수 없습니다. 먼저 체크인하세요.'}
    if cur_state in CATIA_STATE_LOCKED_EDIT and state not in CATIA_STATE_LOCKED_EDIT \
            and not is_admin and state != 'work':
        con.close(); return {'ok': False, 'msg': '배포완료 상태에서는 «개정»(작업중)으로만 되돌릴 수 있습니다.'}
    sets = {'state': state}
    if state == 'released':
        # 배포 시점의 최신 리비전을 박아 둔다 — 무엇을 배포했는지 나중에 확인하기 위해
        rev = con.execute(
            "SELECT rev FROM catia_files WHERE vehicle_code=? AND "
            "(CASE WHEN part_no LIKE 'X%' AND LENGTH(part_no)>1 THEN SUBSTR(part_no,2) "
            "ELSE part_no END)=? ORDER BY rev_sort DESC LIMIT 1", (veh, base)).fetchone()
        sets['released_rev'] = (rev['rev'] if rev else '')
    _catia_upsert(con, veh, base, sets, username)
    _catia_log(con, veh, base, 'state:' + state, username, comment)
    con.commit(); con.close()
    return {'ok': True, 'state': state, 'released_rev': sets.get('released_rev', '')}


def catia_can_modify(vehicle: str, part_no: str, username: str, is_admin: bool = False) -> tuple:
    """새 리비전 업로드·삭제가 가능한지. (가능여부, 사유) 를 돌려준다.
       아직 등록된 적 없는 부품은 «누구나 가능»(첫 등록을 막으면 안 되므로)."""
    it = get_catia_item(vehicle, part_no)
    st = it.get('state') or 'work'
    if st in CATIA_STATE_LOCKED_EDIT and not is_admin:
        return False, f'«{CATIA_STATE_LABEL.get(st, st)}» 상태 — 개정 후 작업하세요'
    lb = it.get('locked_by') or ''
    if lb and lb != username and not is_admin:
        return False, f'«{lb}» 님이 체크아웃 중'
    if not lb and st == 'work':
        return True, ''
    return True, ''


def get_catia_item_log(vehicle: str, part_no: str, limit: int = 50) -> list:
    veh, base = _catia_key(vehicle, part_no)
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute('SELECT * FROM catia_item_log WHERE vehicle_code=? AND base_no=? '
                       'ORDER BY id DESC LIMIT ?', (veh, base, limit)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_catia_lock_stats(vehicle: str = '') -> dict:
    con = sqlite3.connect(DB_PATH)
    w, p = ('', []) if not vehicle else (' WHERE vehicle_code=?', [vehicle])
    j = ' AND ' if w else ' WHERE '
    locked = con.execute("SELECT COUNT(*) FROM catia_items%s%s locked_by<>''" % (w, j), p).fetchone()[0]
    rel = con.execute("SELECT COUNT(*) FROM catia_items%s%s state='released'" % (w, j), p).fetchone()[0]
    con.close()
    return {'locked': locked, 'released': rel}


# ── 배포 권한: 설계자만 ────────────────────────────────────────────────────────
# 사용자 확정(2026-08-02): «배포는 설계자만». 판정은 부서명으로 한다.
# 부서 출처 우선순위: ①조직도(PLM에서 받은 것 — 신뢰도 높음) ②계정의 dept(수기 입력).
# 계정 dept 에는 실제로 오타가 있었다(«설계킴»). 그래서 조직도를 먼저 본다.
# 기준 낱말은 app_meta 에 저장해 나중에 코드 수정 없이 넓히거나 좁힐 수 있다.
DESIGN_DEPT_DEFAULT = '설계,선행연구'


def get_design_keywords() -> list:
    v = _get_meta('design_dept_keywords', DESIGN_DEPT_DEFAULT)
    return [w.strip() for w in v.split(',') if w.strip()]


def set_design_keywords(words: str):
    _set_meta('design_dept_keywords', words)


def get_user_dept(username: str) -> tuple:
    """(부서명, 출처). 조직도에 있으면 그것을, 없으면 계정의 dept 를 쓴다."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    r = con.execute('SELECT dept_name FROM org_members WHERE emp_id=? AND active=1',
                    (username,)).fetchone()
    if r and (r['dept_name'] or '').strip():
        con.close(); return r['dept_name'].strip(), '조직도'
    u = con.execute('SELECT dept FROM users WHERE username=?', (username,)).fetchone()
    con.close()
    return ((u['dept'] or '').strip() if u else ''), '계정'


def is_design_user(username: str, is_admin: bool = False) -> tuple:
    """(설계자 여부, 사유). 관리자는 언제나 허용한다."""
    if is_admin:
        return True, '관리자'
    dept, src = get_user_dept(username)
    if not dept:
        return False, '부서 정보가 없습니다 — 조직도에 등록되어야 합니다'
    for w in get_design_keywords():
        if w in dept:
            return True, f'{dept} ({src})'
    return False, f'{dept} — 설계 부서가 아닙니다'


_PART_NO_RE = re.compile(r'^X?[0-9][0-9A-Z]{4}-[0-9A-Z]{4,6}$')


def get_bom_part_numbers() -> set:
    """BOM 에 «실제로 등장하는» 품번 집합. 두 곳을 모두 본다 —
       ①ebom_items(E-BOM 게시판 업로드분) ②ebom_sheet_cells(시트 편집분, 편집 결과 반영).
       레벨 유무로 짐작하지 않고 원본을 직접 확인한다(레벨이 비어도 BOM 에 있을 수 있다)."""
    con = sqlite3.connect(DB_PATH)
    out = set()
    for (p,) in con.execute("SELECT DISTINCT pno FROM ebom_items WHERE pno<>''"):
        s = str(p or '').replace(' ', '').upper()
        if s:
            out.add(s); out.add(base_part_no(s))
    for (v,) in con.execute("SELECT DISTINCT value FROM ebom_sheet_cells WHERE value<>''"):
        s = str(v or '').replace(' ', '').upper()
        if _PART_NO_RE.match(s):
            out.add(s); out.add(base_part_no(s))
    con.close()
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 고객 EO (고객사에서 받은 설계변경 지시)
# ══════════════════════════════════════════════════════════════════════════════
# 사내 PLM「고객EO 등록」화면을 그대로 옮긴 것. 내부 설계변경통보서와 구조가 거의 같고,
# 다른 점은 ①고객사 구분 ②고객EO번호가 두 개(자동차EO / 다이모스) ③내부 EO 와 상호 연결
# ④비슷한 내용의 다른 고객EO 와도 연결. 도면현황은 파일을 또 올리지 않고
# «고객EO → 연결된 내부EO → 품목현황 품번 → 카티아 2D/3D» 경로로 따라간다.

CUST_EO_TYPES = ['정규', '임시']

# 화면·저장 대상 필드(캡처의 입력칸과 1:1)
CUST_EO_FIELDS = (
    'cust_code', 'cust_eo_no', 'cust_eo_no2', 'eo_date', 'recv_date',
    'vehicle_code', 'event_stage', 'dev_schedule', 'eo_type',
    'as_compat', 'weight_g', 'cost_w', 'content',
    'design_apply_at', 'mfg_apply_at', 'registrant', 'note',
    'approval_json', 'extra_part_nos',
)


def get_customers(active_only: bool = True) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    w = ' WHERE active=1' if active_only else ''
    rows = con.execute(f'SELECT * FROM customers{w} ORDER BY sort_no, code').fetchall()
    con.close()
    return [dict(r) for r in rows]


def upsert_customer(code: str, name: str, sort_no: int = 0, active: int = 1,
                    note: str = '') -> dict:
    code = str(code or '').strip().upper()
    if not code:
        return {'ok': False, 'msg': '고객 코드를 입력하세요.'}
    con = sqlite3.connect(DB_PATH)
    con.execute('INSERT INTO customers (code,name,sort_no,active,note) VALUES (?,?,?,?,?) '
                'ON CONFLICT(code) DO UPDATE SET name=excluded.name, sort_no=excluded.sort_no, '
                'active=excluded.active, note=excluded.note',
                (code, str(name or '').strip(), int(sort_no or 0), int(active), str(note or '')))
    con.commit(); con.close()
    return {'ok': True}


def delete_customer(code: str) -> dict:
    con = sqlite3.connect(DB_PATH)
    n = con.execute('SELECT COUNT(*) FROM cust_eo WHERE cust_code=?', (code,)).fetchone()[0]
    if n:
        con.close()
        return {'ok': False, 'msg': f'이 고객으로 등록된 EO 가 {n}건 있어 삭제할 수 없습니다. '
                                    f'«사용안함»으로 바꾸세요.'}
    con.execute('DELETE FROM customers WHERE code=?', (code,))
    con.commit(); con.close()
    return {'ok': True}


def seed_customers():
    """최초 1회 기본 고객 등록 — 사용자 지정(KMC·HMC·현대트랜시스·KG모터스)."""
    if _get_meta('customers_seeded') == '1':
        return
    for i, (c, n) in enumerate([('KMC', '기아'), ('HMC', '현대자동차'),
                                ('HTS', '현대트랜시스'), ('KGM', 'KG모빌리티')], 1):
        upsert_customer(c, n, i * 10)
    _set_meta('customers_seeded', '1')


# ── 고객EO CRUD ───────────────────────────────────────────────────────────────
def create_cust_eo(fields: dict, username: str) -> dict:
    no = str(fields.get('cust_eo_no') or '').strip()
    if not no:
        return {'ok': False, 'msg': '고객EO번호는 필수입니다.'}
    con = sqlite3.connect(DB_PATH)
    if con.execute('SELECT 1 FROM cust_eo WHERE cust_eo_no=?', (no,)).fetchone():
        con.close(); return {'ok': False, 'msg': f'이미 등록된 고객EO번호입니다 — {no}'}
    cols = [c for c in CUST_EO_FIELDS if c in fields]
    vals = [str(fields.get(c) or '') for c in cols]
    cur = con.execute(
        'INSERT INTO cust_eo (%s,created_by,registrant) VALUES (%s,?,?)'
        % (','.join(cols), ','.join(['?'] * len(cols))),
        vals + [username, str(fields.get('registrant') or username)])
    cid = cur.lastrowid
    con.commit(); con.close()
    return {'ok': True, 'id': cid}


def update_cust_eo(cid: int, fields: dict) -> dict:
    sets, vals = [], []
    for k in CUST_EO_FIELDS:
        if k in fields:
            sets.append(k + '=?'); vals.append(str(fields.get(k) or ''))
    if not sets:
        return {'ok': True, 'changed': 0}
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE cust_eo SET %s, updated=datetime('now','localtime') WHERE id=?"
                % ','.join(sets), vals + [cid])
    con.commit(); con.close()
    return {'ok': True, 'changed': len(sets)}


def get_cust_eo(cid: int):
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    r = con.execute('SELECT * FROM cust_eo WHERE id=?', (cid,)).fetchone()
    con.close()
    return dict(r) if r else None


def search_cust_eo(q='', cust='', vehicle='', eo_type='', dev_schedule='',
                   date_from='', date_to='', part_no='', limit=500, offset=0) -> dict:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    where, params = [], []
    for col, val in (('cust_code', cust), ('vehicle_code', vehicle),
                     ('eo_type', eo_type), ('dev_schedule', dev_schedule)):
        if val:
            where.append(col + '=?'); params.append(val)
    if date_from:
        where.append('eo_date>=?'); params.append(date_from)
    if date_to:
        where.append('eo_date<=?'); params.append(date_to)
    if q:
        where.append('(cust_eo_no LIKE ? OR cust_eo_no2 LIKE ? OR content LIKE ? '
                     'OR vehicle_code LIKE ? OR registrant LIKE ?)')
        params += ['%' + q + '%'] * 5
    if part_no:
        # 품번으로 찾기 — 연결된 내부EO의 품목현황과 직접 지정한 품번을 함께 본다
        where.append('(extra_part_nos LIKE ? OR id IN (SELECT l.cust_eo_id FROM cust_eo_links l '
                     'JOIN eo_items i ON i.eo_id=l.eo_id WHERE i.part_no LIKE ?))')
        params += ['%' + part_no + '%', '%' + part_no + '%']
    w = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = con.execute(f'SELECT COUNT(*) FROM cust_eo{w}', params).fetchone()[0]
    rows = con.execute(
        f'SELECT * FROM cust_eo{w} ORDER BY eo_date DESC, id DESC LIMIT ? OFFSET ?',
        params + [limit, offset]).fetchall()
    items = [dict(r) for r in rows]
    # 연결된 내부EO 번호를 목록에도 실어 보낸다(캡처의 «설계변경통보서번호» 열)
    for it in items:
        it['linked_eos'] = [dict(x) for x in con.execute(
            'SELECT e.id, e.eo_no FROM cust_eo_links l JOIN eo_notices e ON e.id=l.eo_id '
            'WHERE l.cust_eo_id=? ORDER BY e.eo_no', (it['id'],))]
        it['files'] = con.execute('SELECT COUNT(*) FROM cust_eo_files WHERE cust_eo_id=?',
                                  (it['id'],)).fetchone()[0]
    con.close()
    return {'total': total, 'items': items}


def delete_cust_eo(cid: int) -> dict:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    paths = [r['file_path'] for r in
             con.execute('SELECT file_path FROM cust_eo_files WHERE cust_eo_id=?', (cid,))]
    for t in ('cust_eo_files', 'cust_eo_links'):
        con.execute(f'DELETE FROM {t} WHERE cust_eo_id=?', (cid,))
    con.execute('DELETE FROM cust_eo_rels WHERE cust_eo_id=? OR rel_cust_eo_id=?', (cid, cid))
    con.execute("DELETE FROM eo_approvals WHERE doc_type='cust' AND eo_id=?", (cid,))
    con.execute('DELETE FROM cust_eo WHERE id=?', (cid,))
    con.commit(); con.close()
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    return {'ok': True}


# ── 상호 연결 ─────────────────────────────────────────────────────────────────
def set_cust_eo_links(cid: int, eo_ids: list) -> dict:
    """고객EO ↔ 내부 설계변경통보서. 여러 건 연결 가능(캡처: EO-26-004, EO-26-005)."""
    con = sqlite3.connect(DB_PATH)
    con.execute('DELETE FROM cust_eo_links WHERE cust_eo_id=?', (cid,))
    for e in eo_ids or []:
        try:
            con.execute('INSERT OR IGNORE INTO cust_eo_links (cust_eo_id,eo_id) VALUES (?,?)',
                        (cid, int(e)))
        except (TypeError, ValueError):
            continue
    con.commit(); con.close()
    return {'ok': True, 'count': len(eo_ids or [])}


def get_cust_eo_links(cid: int) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        'SELECT e.id, e.eo_no, e.eo_date, e.content, e.vehicle_code, e.approval_status '
        'FROM cust_eo_links l JOIN eo_notices e ON e.id=l.eo_id WHERE l.cust_eo_id=? '
        'ORDER BY e.eo_no', (cid,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_eo_cust_links(eo_id: int) -> list:
    """반대 방향 — 내부 설계변경통보서에서 연결된 고객EO 를 본다."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        'SELECT c.id, c.cust_eo_no, c.cust_eo_no2, c.cust_code, c.eo_date, c.content '
        'FROM cust_eo_links l JOIN cust_eo c ON c.id=l.cust_eo_id WHERE l.eo_id=? '
        'ORDER BY c.cust_eo_no', (eo_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def set_eo_cust_links(eo_id: int, cust_eo_ids: list) -> dict:
    """설계변경통보서 쪽에서 고객EO 를 연결(같은 표를 반대로 쓴다)."""
    con = sqlite3.connect(DB_PATH)
    con.execute('DELETE FROM cust_eo_links WHERE eo_id=?', (eo_id,))
    for c in cust_eo_ids or []:
        try:
            con.execute('INSERT OR IGNORE INTO cust_eo_links (cust_eo_id,eo_id) VALUES (?,?)',
                        (int(c), eo_id))
        except (TypeError, ValueError):
            continue
    con.commit(); con.close()
    return {'ok': True, 'count': len(cust_eo_ids or [])}


def set_cust_eo_rels(cid: int, rel_ids: list) -> dict:
    """비슷한 내용의 다른 고객EO 연결. 한쪽에서 걸면 양쪽에서 보이도록 «대칭»으로 저장한다."""
    con = sqlite3.connect(DB_PATH)
    con.execute('DELETE FROM cust_eo_rels WHERE cust_eo_id=? OR rel_cust_eo_id=?', (cid, cid))
    for r in rel_ids or []:
        try:
            rid = int(r)
        except (TypeError, ValueError):
            continue
        if rid == cid:
            continue
        con.execute('INSERT OR IGNORE INTO cust_eo_rels (cust_eo_id,rel_cust_eo_id) VALUES (?,?)',
                    (cid, rid))
        con.execute('INSERT OR IGNORE INTO cust_eo_rels (cust_eo_id,rel_cust_eo_id) VALUES (?,?)',
                    (rid, cid))
    con.commit(); con.close()
    return {'ok': True, 'count': len(rel_ids or [])}


def get_cust_eo_rels(cid: int) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute(
        'SELECT c.id, c.cust_eo_no, c.cust_code, c.eo_date, c.vehicle_code, c.content '
        'FROM cust_eo_rels r JOIN cust_eo c ON c.id=r.rel_cust_eo_id WHERE r.cust_eo_id=? '
        'ORDER BY c.cust_eo_no', (cid,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ── 도면현황 ─────────────────────────────────────────────────────────────────
def get_cust_eo_drawings(cid: int) -> dict:
    """고객EO → 연결된 내부EO → 품목현황 품번 → 카티아 2D/3D.
       설계용 = 최신 리비전, 배포용 = «배포완료» 상태인 것만(배포 통제를 상태로 건다)."""
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    ce = con.execute('SELECT extra_part_nos, vehicle_code FROM cust_eo WHERE id=?',
                     (cid,)).fetchone()
    pnos, src = [], {}
    for r in con.execute(
            'SELECT i.part_no, e.eo_no FROM cust_eo_links l JOIN eo_items i ON i.eo_id=l.eo_id '
            'JOIN eo_notices e ON e.id=l.eo_id WHERE l.cust_eo_id=?', (cid,)):
        p = (r['part_no'] or '').strip()
        if p:
            pnos.append(p); src.setdefault(p, []).append(r['eo_no'])
    for p in re.split(r'[,\s;]+', (ce['extra_part_nos'] if ce else '') or ''):
        p = p.strip()
        if p:
            pnos.append(p); src.setdefault(p, []).append('직접지정')
    con.close()

    seen, out = set(), []
    for p in pnos:
        b = base_part_no(p)
        if b in seen:
            continue
        seen.add(b)
        res = search_catia_parts(q=b)
        mine = [x for x in res['items'] if base_part_no(x['part_no']) == b]
        state = 'work'
        for m in mine:
            st = get_catia_item(m['vehicle_code'], m['part_no'])
            state = st.get('state') or 'work'
        rec = {'part_no': p, 'from': ', '.join(sorted(set(src.get(p, [])))),
               'state': state, 'released': state == 'released', 'files': []}
        for m in mine:
            for kind in ('2D', '3D'):
                o = m.get(kind) or {'revs': []}
                if not o.get('revs'):
                    continue
                latest = o['revs'][-1]
                rec['files'].append({
                    'kind': kind, 'part_no': m['part_no'], 'rev': latest['rev'],
                    'rev_sort': latest.get('rev_sort', 0),
                    'filename': latest['filename'], 'size_no': latest['size_no'],
                    'file_date': latest['file_date'], 'stage': latest['stage'],
                    'ext': latest['ext'], 'id': latest['id'],
                    # 배포용은 «배포완료» 상태일 때만 내려받게 한다
                    'dist_ok': (state == 'released'),
                })
        out.append(rec)
    return {'items': out, 'parts': len(out)}


# ── 첨부파일 ─────────────────────────────────────────────────────────────────
CUST_EO_FILE_KINDS = {'cover': '고객EO 감지', 'bom': '고객 BOM', 'etc': '기타'}


def add_cust_eo_file(cid: int, kind: str, filename: str, file_path: str,
                     size_no: int, username: str) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        'INSERT INTO cust_eo_files (cust_eo_id,kind,filename,file_path,size_no,uploaded_by) '
        'VALUES (?,?,?,?,?,?)',
        (cid, kind if kind in CUST_EO_FILE_KINDS else 'etc', filename, file_path,
         int(size_no or 0), username))
    fid = cur.lastrowid
    con.commit(); con.close()
    return fid


def get_cust_eo_files(cid: int) -> list:
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = con.execute('SELECT * FROM cust_eo_files WHERE cust_eo_id=? ORDER BY kind, id',
                       (cid,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_cust_eo_file(fid: int):
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    r = con.execute('SELECT * FROM cust_eo_files WHERE id=?', (fid,)).fetchone()
    con.close()
    return dict(r) if r else None


def delete_cust_eo_file(fid: int) -> dict:
    f = get_cust_eo_file(fid)
    if not f:
        return {'ok': False, 'msg': '파일을 찾을 수 없습니다.'}
    con = sqlite3.connect(DB_PATH)
    con.execute('DELETE FROM cust_eo_files WHERE id=?', (fid,))
    con.commit(); con.close()
    try:
        if f.get('file_path') and os.path.exists(f['file_path']):
            os.remove(f['file_path'])
    except OSError:
        pass
    return {'ok': True}


def get_cust_eo_stats() -> dict:
    con = sqlite3.connect(DB_PATH)
    total = con.execute('SELECT COUNT(*) FROM cust_eo').fetchone()[0]
    linked = con.execute('SELECT COUNT(DISTINCT cust_eo_id) FROM cust_eo_links').fetchone()[0]
    done = con.execute("SELECT COUNT(*) FROM cust_eo WHERE approval_status='approved'").fetchone()[0]
    con.close()
    return {'total': total, 'linked': linked, 'approved': done, 'unlinked': total - linked}
