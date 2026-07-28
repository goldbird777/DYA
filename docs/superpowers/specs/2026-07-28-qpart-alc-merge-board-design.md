# Q파트 ALC 통합 게시판 — 설계 문서

날짜: 2026-07-28
대상 코드베이스: `EBOM/bom_web` (FastAPI + Jinja2 + SQLite)

## 배경 / 목적

기존 "HKMC Q파트 & ALC 이력 관리" 게시판(`mbom_history`, URL `/mbom-history`)은
Q파트 종합 + ALC 코드집 5개(FRT LH/RH, RR BACK LH/CUSH/RH) 총 6개 파일을 업로드하면
★통합 ALC2 코드 대장(마스터 문서)을 기준으로 신규/기존 매칭을 판정하는 게시판이다.

이번에 추가하는 게시판은 **같은 6개 파일**을 입력으로 받지만 목적이 다르다:
Q파트 종합 파일 자체를 베이스로 삼아, 각 생산행 오른쪽 끝에 5개 ALC 파일에서 읽은
시트별 품번과, PEL 코드 마스터 기준 사양(O/X)을 그대로 열로 덧붙인 **병합본**을
만들어 협력사에 배포하기 위함이다. 휴먼 에러(수기 대사) 방지와 협력사 배포용
단일 문서 확보가 목적.

기존 게시판과는 **완전히 별도의 신설 게시판**으로 만든다(사용자 확정) — 별도 메뉴,
별도 DB 테이블, 별도 라우트. 기존 게시판의 ★통합 ALC2 코드 대장/DYA ALC-2 매칭
로직은 이 신설 게시판에서 다루지 않는다(사용자 확정 — 품번·사양 O/X만 필요).

## 범위

**포함**
- 신설 게시판 `/qpart-merge` (사이드바 메뉴명 "Q파트 ALC 통합", 기존 Q파트&ALC
  이력관리 메뉴 바로 아래)
- 6슬롯 업로드 UI(Q파트 종합 + ALC 5종) — 기존 `mbom_history.html` 게시판과 동일한
  게시글 목록 + 업로드 모달 구조를 그대로 복제
- 변환 실행: Q파트 종합 워크북을 그대로 복사해 오른쪽 끝에 품번 5열 + 사양 O/X열들을
  덧붙인 새 xlsx 생성
- 결과를 엑셀 형태 그리드(열별 오토필터, `pel_spec.html` 패턴 재사용)로 화면에 표시
- 결과 xlsx 다운로드
- 변환 결과 DB 영구 저장(서버 재시작 후에도 과거 결과 재다운로드 가능 — "BOM 변환
  게시판"의 `bom_generate_history` 패턴과 동일)

**제외 (비범위)**
- ★통합 ALC2 코드 대장 갱신, DYA ALC-2 신규채번/매칭 판정 — 기존 게시판 그대로 유지,
  이 신설 게시판은 건드리지 않음
- 협력사 대상 별도 공개(비로그인) URL — 기존과 동일하게 로그인 후 조회, 다운로드한
  파일을 이메일/메신저로 전달하는 기존 방식 유지
- 품번을 품번/원단코드로 분리 표기 — 원본 ALC 파일의 PART NO 문자열(품번+원단코드
  결합형)을 그대로 한 열에 표기(분리하지 않음)

## 데이터 흐름

1. 사용자가 게시글 등록 모달에서 6개 슬롯 파일(Q파트 종합, FRT LH, FRT RH,
   RR BACK LH, RR CUSH, RR BACK RH) + 차종/단계/제목을 입력하고 업로드
2. 서버는 각 파일을 슬롯별로 저장하고 게시글(post) 행 생성
3. 게시글 목록에서 "🔄 변환 실행" 클릭 → 서버가 6개 파일을 읽어 병합 xlsx 생성,
   `qpart_merge_runs`에 결과 경로를 기록
4. 브라우저는 생성된 병합 결과를 파싱한 그리드(JSON)를 받아 엑셀형 오토필터 테이블로
   렌더링
5. 사용자는 그리드를 그대로 검토하거나, "⬇ 엑셀 다운로드"로 실제 xlsx를 받아 협력사에
   전달
6. 같은 게시글에서 재실행 가능 — 매 실행마다 새 run 이력이 남고 과거 run도
   재다운로드/재조회 가능(서버 재시작과 무관)

## DB 스키마 (`auth.py`)

```sql
CREATE TABLE IF NOT EXISTS qpart_merge_posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle      TEXT NOT NULL,
    dev_stage    TEXT DEFAULT '',
    title        TEXT DEFAULT '',
    uploaded_by  TEXT NOT NULL,
    created      TEXT DEFAULT (datetime('now','localtime'))
)

CREATE TABLE IF NOT EXISTS qpart_merge_files (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id   INTEGER NOT NULL,
    slot      TEXT NOT NULL,
    filename  TEXT NOT NULL,
    file_path TEXT NOT NULL,
    uploaded  TEXT DEFAULT (datetime('now','localtime'))
)

CREATE TABLE IF NOT EXISTS qpart_merge_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id          INTEGER NOT NULL,
    output_path      TEXT NOT NULL,
    output_filename  TEXT NOT NULL,
    spec_col_count   INTEGER DEFAULT 0,
    row_count        INTEGER DEFAULT 0,
    created          TEXT DEFAULT (datetime('now','localtime')),
    created_by       TEXT NOT NULL
)
```

CRUD 함수(기존 `mbom_history`류 함수 명명 패턴 그대로): `add_qpart_merge_post`,
`add_qpart_merge_file`, `get_qpart_merge_history`, `get_qpart_merge_post`,
`get_qpart_merge_files_by_post`, `delete_qpart_merge_post`(게시글+슬롯파일+run 이력·
결과파일까지 함께 삭제), `add_qpart_merge_run`, `get_qpart_merge_runs`,
`get_qpart_merge_run`.

## 라우트 (`main.py`)

```
GET  /qpart-merge                          게시판 페이지
GET  /qpart-merge/list                     게시글 목록 JSON
POST /qpart-merge/upload                   6슬롯 업로드 → 게시글 생성
GET  /qpart-merge/download/{file_row_id}   업로드 원본 파일 다운로드
POST /qpart-merge/delete/{post_id}         게시글 삭제(본인 또는 관리자)
POST /qpart-merge/run/{post_id}            변환 실행 → run 행 생성 + 그리드 JSON 반환
GET  /qpart-merge/runs/{post_id}           그 게시글의 과거 변환 이력 목록
GET  /qpart-merge/grid/{run_id}            과거 실행 결과를 그리드로 다시 조회
GET  /qpart-merge/download-result/{run_id} 병합 xlsx 다운로드
```

무거운 처리(run 라우트)는 기존 컨벤션(2026-07-22 결정사항)에 따라 `async def`가 아닌
동기 `def`로 작성해 스레드풀에서 실행되게 한다(동시 사용자 블로킹 방지).

## 변환 엔진 (`alc2_convert.py`)

새 함수 `build_qpart_merge(qpart_path, alc_paths, master_pel)` 추가:

1. `read_qpart(qpart_path)`로 생산행 목록(각 행의 `excel_row`, `keys` 포함) 확보
2. Q파트 종합 워크북을 openpyxl로 열어(서식 보존을 위해 `load_workbook`, `data_only`
   아님) 그대로 복사본으로 사용
3. 헤더행(`_find_header`로 탐지한 행) 기준, `ws.max_column + 1`부터 순서대로 헤더 기록:
   - 품번 5열: "FRT LH 품번" / "FRT RH 품번" / "RR BACK LH 품번" / "RR CUSH 품번" /
     "RR BACK RH 품번" — 각 슬롯의 `read_alc_partno()` 결과에서 해당 KEY(4자리 코드)로
     조회한 PART NO 원본 문자열(품번+원단코드 결합형, 공백 제거)
   - 사양 O/X열: `build_ox()`와 동일한 로직으로 PEL 마스터 기준 사양명을 동적 열로
     생성(그룹별 인접 배치, `_spec_label()` 규칙 그대로 재사용), 값은 `●`(해당 사양
     있음)/`X`(없음)
4. 각 생산행(`excel_row`)에 위 값들을 원본 행 그대로 채워 넣음(원본 A~기존마지막열은
   전혀 수정하지 않음)
5. 새 파일로 저장(`REPORTS_DIR`), 반환값에 `row_count`/`spec_col_count`/생성 경로 포함

기존 `read_qpart`, `read_alc_partno`, `_spec_label`, PEL 마스터 로딩(`load_pel_master`)을
그대로 재사용하고 새 파싱 로직은 추가하지 않는다.

## 그리드 뷰어 (`templates/qpart_merge.html`)

`pel_spec.html`의 열별 오토필터 그리드 컴포넌트(헤더 고정, 열별 필터 드롭다운, 가로
스크롤 `.tbl-wrap`)를 그대로 복제 적용. 서버가 생성된 병합 xlsx를 파싱해
`{headers: [...], rows: [[...], ...]}` JSON으로 내려주면 동일한 JS 렌더링 함수로
표시한다. 게시글 목록 UI(업로드 모달, 슬롯 6개, 게시글 테이블)는 `mbom_history.html`
구조를 그대로 복제하되 결과 표시 영역만 위 그리드 컴포넌트로 교체.

## 사이드바

`_sidebar.html`(단일 소스)에 "Q파트 ALC 통합" 메뉴 항목 추가, 기존 "Q파트&ALC
이력관리" 항목 바로 아래 배치. 사이드바 중복 하드코딩 방지 원칙(자동 메모리
`sidebar-duplication-guard` 참고)에 따라 이 파일 하나만 수정한다.

## 에러 처리

기존 게시판과 동일한 방식:
- 6개 슬롯 중 하나라도 없으면 실행 시 `{'error': '...슬롯이 없습니다.'}` 400 응답
- Q파트/ALC 파일에서 헤더를 못 찾으면 `alc2_convert`의 기존 `ValueError` 메시지를
  그대로 노출(예: "Q파트에서 헤더(차종/KEY01~)를 찾지 못했습니다.")
- PEL 코드 마스터에 없는 코드는 조용히 건너뜀(기존 `build_ox` 동작과 동일 — 사양
  누락은 이 게시판의 책임 범위 밖)

## 검증 계획

1. `python -m py_compile auth.py main.py alc2_convert.py`
2. 로컬 브라우저:
   - 게시글 등록(6개 파일 업로드) → 목록에 정상 표시
   - "변환 실행" → 그리드에 품번 5열 + 사양 O/X열 정상 표기, 원본 Q파트 열 그대로인지
     확인
   - "엑셀 다운로드" → 받은 xlsx를 열어 원본 서식 유지 + 새 열 값이 올바른 행에
     채워졌는지 실측
   - 같은 게시글 재실행 → 새 run 이력 추가, 과거 run도 목록에 남고 재다운로드 가능
   - 게시글 삭제 → 슬롯 파일 + run 결과 파일까지 함께 삭제되는지 확인
3. 기존 "Q파트&ALC 이력관리" 게시판(`mbom_history` 관련 코드/DB/템플릿)은 전혀
   건드리지 않으므로 회귀 없음 — 별도 테이블/라우트/템플릿

## 배포

기존 절차 그대로: 로컬 커밋 → `git push origin master` → SSH로 Oracle 서버 접속 →
`git pull` → `systemctl restart bom.service` (비어있는 시간대에, 동시 사용자 활동
중 재시작 지양 — 2026-07-22 OOM 사고 교훈 참고).
