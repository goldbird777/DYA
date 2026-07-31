"""
인증 모듈 — SQLite 기반 사용자 관리, JWT 토큰, bcrypt 비밀번호 해시
"""
import sqlite3, os, hashlib, json
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
