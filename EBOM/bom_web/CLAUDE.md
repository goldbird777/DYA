# EBOM/bom_web — 작업 메모

FastAPI 기반 BOM/PEL 코드 웹 도구 (`main.py` 엔트리포인트, `templates/`, `alc2_convert.py`,
`bom_generator.py` 등). Oracle Cloud 인스턴스에 배포되어 설계/생산관리가 함께 사용한다.
접속 정보는 아래 "배포" 절 참고.

## 최근 결정 사항 (최신이 위)

- **2026-07-22: "여러 명이 쓰면 로딩 걸림" 증상 — 무거운 라우트를 스레드풀로 분리.**
  서버가 uvicorn 워커 1개(단일 프로세스, RAM 956MB/스왑 1GB 거의 풀)로 도는데, PEL 사양변경·
  ALC2 변환·BOM 생성 등 openpyxl/pandas 기반 무거운 엑셀 처리 라우트가 전부 `async def` 안에서
  동기(블로킹) 코드를 그대로 실행하고 있었다 — 한 명이 무거운 파일을 처리하는 동안 이벤트 루프
  전체가 막혀서 다른 모든 사용자 요청(홈페이지 포함)도 같이 로딩에 걸리는 구조적 문제였다.
  FastAPI/Starlette는 `async def`가 아닌 일반 `def` 라우트는 자동으로 스레드풀에서 실행하므로,
  본문에 `await`가 없는 무거운 라우트들(`validate`, `view_excel`, `bom_generate_upload/regenerate`,
  `pel_code_upload`, `ccc_upload`, `ebom_board_upload/reparse`, `mbom_history_alc2_run`,
  `mbom_alc2_run`, `pel_spec_*`, `sales_files_upload/sheet`, `bom_template_upload`,
  `country_ppt_upload`, `pel_history_upload` 등)를 `async def` → `def`로 바꿔 동시 요청이
  서로 안 막히게 했다. `ebom_board_upload`는 `await file.read()`를 동기 `file.file.read()`로
  교체. `ebom_mbom_compare_run`처럼 `await request.form()`을 써야 하는 라우트는 async를
  유지한 채 무거운 파싱·엑셀 생성 부분만 `starlette.concurrency.run_in_threadpool`로 분리.
  서버 자체 메모리(956MB, 스왑 거의 풀)도 여유가 없는 상태라 근본적으로는 인스턴스 증설이
  필요하지만, 이번 수정만으로 "동시 사용자 로딩 멈춤" 증상은 크게 개선될 것으로 예상.

- **2026-07-22: PEL 사양변경 — 열구분(1열LH/RH, 2열, 3열) 필드 추가 + 사이드바 새창 열기.**
  `pel_spec_uploads`에 `row_level` 컬럼 추가(`auth.py`). 업로드 모달에서 파일명으로 자동 추정
  (`main.py`의 `_detect_row_level()` — FRT+DRV/LH→1열 LH, FRT+PASS/RH→1열 RH, RR/2열→2열,
  3열→3열, 실패시 수동 선택) 후 사용자가 수정 가능. "광주+화성 통합 그리드" 버튼은
  차종에 등록된 열구분 종류별로 동적 버튼 목록(`/pel-spec/row-levels`)으로 분리되어, 원하는
  열만 골라 공장별 최신 업로드를 병합해 볼 수 있다(`get_pel_spec_latest_by_factory(vehicle,
  row_level)`). 기존 업로드 이력은 row_level이 빈 값('-')으로 표시되며 정상 동작에 문제없음.
  또한 사이드바(`_sidebar.html`) 전체 메뉴 항목에 JS로 "⧉ 새창에서 열기" 아이콘을 주입 —
  느린 업로드/변환 작업 중 다른 메뉴로 이동해도 진행 중이던 창은 그대로 유지되도록 함.
  BRE(BOM Report Excel) 기반 1레벨 매칭은 별도 미결 과제로 보류(사용자 자료 제공 후 진행 예정).

- **2026-07-21: "전체 프로세스 개요" 게시판 신설(사이드바 최상단).** `/process-overview`.
  상단은 ERP 전체 흐름도를 `process_overview.html`에 SVG로 직접 그림(항상 코드와 함께 최신
  유지 — 별도 이미지 파일 아님). 하단은 회사 프로세스 다이어그램(HKMC↔MES↔협력사 생산계획
  사이클 등)을 관리자가 이미지/PPT로 업로드·교체·삭제하는 갤러리(`process_diagrams` 테이블,
  국가코드 PPT 첨부 패턴 참고해서 다중 항목 지원으로 구현).

- **2026-07-21: 생산 대시보드 신설(차종별 계획/실적 막대그래프).** 사이드바 "마스터 데이터" 위에
  "대시보드" 섹션 추가(`/production-dashboard`). DB `production_qty`(vehicle_code,year,month,
  week_no,plan_qty,actual_qty) — 차종명은 저장 안 하고 항상 차종 마스터에서 조회. 주차는
  월요일 시작, 1일이 포함된 첫 주는 1주차(사용자 확정 규칙, `main.py`의 `_month_weeks()`).
  계획/실적 둘 다 **수동 입력**으로 시작 — M-BOM Q파트 종합의 날짜열이 업로드마다 범위가
  달라 자동 파싱이 불안정하고 실적 자동 연동 소스가 아직 없어서 사용자와 협의 후 결정.
  그래프는 외부 라이브러리 없이 커스텀 SVG로 구현(`production_dashboard.html`).
  2열(후석) SEAT BUCKLE/CHILD ANCHOR/CTR A/REST 등 O/X 미결 항목은 보류 — 1열 검증 먼저
  마치고 나중에 처리하기로 함(사용자 지시, 2026-07-21).

- **2026-07-19: ALC-2 O/X 자동 표기 — 전석/후석 좌석위치 매핑, DT 검증, COMBI 전용 읽기,
  시트종류(POWER/MANUAL) PWR코드 판정.** 참고 문서 `EBOM/bom_web/ALC2_PEL_OX_REFERENCE.md`.
  - `alc2_convert.SLOT_TOP_MAP`: FRT LH→DRIVER, FRT RH→PASSENGER(LHD/RHD 무관 고정 — 실측:
    두 파일 모두 LHD/RHD 행이 섞여 있지만 파일 역할은 항상 DRV/PASS), 후석 3개는 물리위치 고정.
  - `check_frt_dt()`: DT(LHD/RHD) 헤더를 고정 위치가 아니라 매번 동적 탐색. DT는 방향
    검증·경고 전용이며 DRIVER/PASSENGER 역할을 절대 바꾸지 않는다. KEY01을
    `country_codes.hkmc_code`와 대조해 미등록 경고.
  - **중요 버그 수정**: ALC 원본 파일은 CODE 행에 `COMBI`(실제 적용 옵션) 열 구간과
    `EXCLU`/`EO-IN`/`EO-OUT`(제외·예외 정보, 옵션 아님) 열 구간이 나란히 있는데, 예전 코드는
    행 전체를 스캔해서 EXCLU의 코드까지 옵션으로 잘못 읽고 있었다(사용자 실측 지적:
    572J/629J의 "POWER SEAT" 코드가 실제로는 EXCLU 구간에 있었음). `_combi_col_range()`로
    COMBI 구간만 동적으로 찾아 그 안에서만 PEL 코드를 읽도록 고침 — 이미 배포됐던 후석
    O/X에도 영향 있던 버그였음.
  - 「시트종류」(POWER/MANUAL) 열은 템플릿 리프 라벨 텍스트 매칭이 아니라
    `_is_power_seat_pel()`(옵션그룹=OPTION + 사양이 PWR 계열)로 그 좌석에 파워시트 코드가
    있는지 판정해서 정확히 하나만 표기(`_seat_type_columns()` + `build_option_marks()`).
  - 아직 미구현: 서식의 F열(DRV TYPE), H열(사양지/국가), K~Y열(품번+원단코드+KMC코드) —
    사용자가 NQ 정렬 비교 파일 업로드 예정, 사출컬러·커버링 사양은 범위 제외 확인됨.

- **2026-07-19: PEL 코드 마스터 「설명」란 — 콤마 다중 용어 + ALC-2 O/X 매칭 반영.**
  「설명」란은 설계 사양과 별개로 생관(HKMC ALC 코드집) 용어를 콤마(`,`)로 이어붙여 여러 개
  넣는 수동 텍스트 필드다. 예전에 있던 [+] 버튼(팝업 검색 선택기, 커밋 `05f134a`)은 제거했고,
  ALC-2 O/X 변환(`alc2_convert.py`의 `_spec_label()`)이 「사양」이 비어 있을 때 「설명」의
  콤마 구분 용어 중 첫 번째로 대체하도록 반영됨. 표준 BOM 변환(`bom_generator.py`의
  `pel_to_name()`)은 이 폴백을 쓰지 않는다 — 사양 비면 코드 그대로 유지가 사용자 명시 사항.
  자세한 내용은 자동 메모리 `pel-desc-multi-term.md` 참고.

## 세션 운영 규칙

- **새 채팅을 시작하기 전에 이 파일이 최신인지 확인할 것.** 이전 세션에서 "이게 반영됐나?"를
  git log·코드 재탐색으로 확인하느라 토큰을 많이 썼다면, 그 결론을 이 파일의 "최근 결정 사항"에
  적어두고 세션을 마칠 것 — 다음 채팅이 같은 탐색을 반복하지 않도록 하기 위함.
- 이 파일에 없는 오래된 세부사항(정확한 줄 번호, 특정 함수 존재 여부)은 항상 현재 코드로
  재확인 후 사실로 단정할 것 — 이 파일은 스냅샷이지 실시간 상태가 아니다.

## 배포

- git origin: `https://github.com/goldbird777/DYA.git` (**public** 저장소 — 서버 접속 정보는
  여기 커밋하지 말 것. 실제 IP/계정/경로는 Claude 자동 메모리(로컬, 비공개)에만 저장되어 있음)
- 배포 절차: 로컬에서 커밋 → `git push origin master` → 서버 SSH 접속 →
  저장소 경로에서 `git pull origin master` → 서비스 재시작(systemd)
- 주의: 서버의 `EBOM/bom_web/data/pel_code_master.xlsx`는 운영 중 웹 UI로 실시간 수정되는
  실데이터라서 로컬 작업본과 다를 수 있다 — 커밋/배포 시 이 파일은 건드리지 말 것
  (커밋 전 반드시 `git status`로 확인).
