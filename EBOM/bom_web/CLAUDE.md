# EBOM/bom_web — 작업 메모

FastAPI 기반 BOM/PEL 코드 웹 도구 (`main.py` 엔트리포인트, `templates/`, `alc2_convert.py`,
`bom_generator.py` 등). Oracle Cloud 인스턴스에 배포되어 설계/생산관리가 함께 사용한다.
접속 정보는 아래 "배포" 절 참고.

## 최근 결정 사항 (최신이 위)

- **2026-07-22: 사이드바 이모지 아이콘 23개 → 모노톤 SVG 라인 아이콘으로 교체.**
  이모지가 OS/브라우저마다 렌더링이 달라 "촌스럽다"는 피드백 → `_sidebar.html`(단일 소스) 안에
  인라인 `<svg viewBox="0 0 24 24">` 라인아이콘으로 직접 교체(외부 CDN 없음, self-contained).
  `#sidebar .m-icon svg{stroke:currentColor;...}`로 메뉴 텍스트 색을 그대로 따라가게 해서
  hover/active(흰색 전환)·접힌 레일·모바일 오버레이 전부 별도 처리 없이 자동 대응. 서브메뉴는
  15px로 살짝 축소(`#sidebar .submenu .m-icon svg`). 검증: PC 확장(220px)·PC 아이콘레일(56px)·
  모바일 레일(56px)·모바일 오버레이(284px, 서브메뉴 포함) 전부 23개 아이콘 정상 렌더링,
  active 상태 색 자동 전환(흰색) 확인.

- **2026-07-22: 모바일 사이드바 오버레이 추가 버그 2건 수정.**
  ①오버레이 폭이 고정 78vw(최대300px)라 텍스트보다 넓어 보이던 것 → `width:fit-content;
  min-width:220px;max-width:82vw`로 내용 길이에 맞춰 자동 축소(실측 ~285px).
  ②E-BOM/M-BOM 하위메뉴가 화살표만 회전하고 안 보이던 진짜 원인: 각 페이지 head의 **기존
  데스크톱용** 규칙 `#sidebar.collapsed .submenu{display:none}`(아이콘 레일에서 하위메뉴
  강제숨김)이 모바일 오버레이에서도 그대로 적용되고 있었음(모바일 CSS 작성 시 이 규칙 하나만
  되돌리는 걸 빠뜨림) → `#sidebar.sidebar.collapsed .submenu{display:block;transition:none}`로
  명시적으로 되돌림(transition:none은 이전 width 버그와 동일한 "동적 클래스 토글 시 전환 애니메이션이
  타이밍상 안 걸리는" 이 자동화 테스트 환경 특이현상 회피용 — 실브라우저에서도 안전한 방어적 조치).
  검증: E-BOM 5개·M-BOM 2개 하위 항목 모두 정상 노출, PC용 규칙(width:56px, 미디어쿼리 밖)은
  코드상 전혀 안 건드림 확인.
  ── 별개 검토(미구현, 보고만): 사이드바 이모지 아이콘이 "촌스럽다"는 피드백 →
  모노톤 SVG 라인 아이콘(Material Symbols/Lucide 계열)으로 교체를 권장(아이콘 완전 제거는
  접힌 레일 상태의 탐색 기능을 해치므로 비권장, 사용자 직접 업로드는 22개 항목 반복 부담).

- **2026-07-22: 전체 흐름도 SVG 디자인 고급화 + 사진으로 대체 가능하게.**
  `process_overview.html`의 SVG(임원진 열람용)를 좌표/화살표 경로는 그대로 두고 스타일만
  전면 리디자인: 열 헤더에 네이비 그라데이션+그림자, 항목 박스는 흰 배경+컬럼별 accent
  컬러(슬레이트/블루/앰버/그린) 좌측 바+연한 틴트+그림자로 변경(플랫 옐로 박스 → 카드형),
  ★통합 서식·생산대시보드는 그라데이션 강조, 화살표는 2색(진행=블루, 참조/비교=회색 점선)으로
  통일하고 round cap/join 적용. 하단 "한 문장 요약" 패널도 그라데이션+큰 라운드+그림자로 격상.
  신규: 관리자가 PNG/JPG를 올리면 **이 SVG 자리를 그 사진이 대체**하도록 기능 추가(아래
  갤러리와는 별개, 이 흐름도 전용) — `flowchart_override` 테이블(단일 행, id=1)에
  filename/file_path/uploaded_by 저장. `/process-overview/flowchart/upload`(admin),
  `/flowchart/view`(이미지 서빙), `/flowchart/reset`(admin, 삭제 시 SVG로 복원). 카드 헤더에
  "🖼️ 사진으로 교체"(항상 표시)·"↩ 원본 디자인 복원"(사진 등록 시에만) 버튼 추가.
  실측: 업로드→img 전환→파일 서빙(200)→복원→svg 복귀 전부 로컬 검증 완료.

- **2026-07-22: 페이지 정렬 통일 + 생산 대시보드 색상/단위 개선 + 프로세스 개요 순서·화살표.**
  ①**정렬 불일치 원인**: 일부 페이지가 `.container{max-width:Npx; margin:0 auto; ...}` 패턴(가운데
  정렬)을 쓰고, 다른 페이지(국가코드 등)는 `.content{flex:1}`(사이드바에 붙는 좌측 정렬)을 씀 —
  같은 게시판 스타일인데 우연히 두 패턴이 혼재. `admin/auto_bom/index/m_bom/pel_code/
  process_overview/production_dashboard/vehicles` 8개 파일의 `.container` 규칙에서
  `margin:0 auto` → `margin:0`으로 통일(max-width는 유지해 초광폭 모니터에서 과도하게 안 늘어남).
  ②**생산 대시보드**: 기존엔 수량 그래프(계획=파랑 고정/실적=초록 고정)와 매출·영업이익 그래프
  (차종별 고유색)가 서로 다른 색 규칙이라 차종 색이 안 맞았음 — 수량 그래프도 `vcColor(idx)`
  차종별 고유색으로 통일(계획=진하게·실적=연하게, 매출/영업이익과 동일 규칙). 금액은 원 단위
  숫자가 너무 길어 막대에 가려지던 문제 → `fmtThousand()`로 천원 단위 표시, 카드 헤더 우측에
  "단위: 천원" 라벨 추가.
  ③**프로세스 개요**: 업로드 갤러리 카드를 위로, 코드로 그린 전체 흐름도 SVG 카드를 아래로 순서
  교체. 화살표 `stroke-width` 3→1.75, 화살표머리 `markerWidth/Height` 11→7 로 얇고 정제되게.
  (참고: 이 SVG는 별도 아티팩트가 아니라 `templates/process_overview.html`에 직접 그려진
  프로덕션 코드라 `mcp__visualize`/Artifact 툴 대상이 아님 — 디자인 품질을 높이고 싶으면 "이
  다이어그램 디자인만 다듬어줘"처럼 디자인에 초점을 맞춰 요청하면 스타일 가이드라인을 더
  적극적으로 적용함. 별도의 숨겨진 "모드"가 있는 게 아니라 요청의 초점 차이.)
  검증: 3개 항목 모두 로컬 브라우저에서 실측 확인(정렬 gap=0, 색상 인덱스 일치, 천원 라벨,
  카드 순서, 화살표 두께) 후 배포.

- **2026-07-22: 모바일 사이드바 — 아이콘 레일 대신 '탭하면 라벨까지 전체 오버레이' 방식으로 전환.**
  사용자가 참고 사이트(microbitcoin.co.kr)처럼 상단 토글을 탭하면 사이드바가 라벨 포함 전체로
  본문 위에 슬라이드 오버레이되길 원함(아이콘만으론 어느 게시판인지 구분 어려움). 기존 `toggleSidebar()`
  JS(전 페이지 공통, 변경 없음)가 켜는 `.collapsed` 클래스를 **모바일 미디어쿼리 안에서만** "확장
  오버레이" 의미로 재정의 — 데스크톱에서 `.collapsed`는 여전히 원래 의미(56px 축소). 기본(닫힘) 상태는
  기존 아이콘 레일(56px) 그대로, 토글 시 `position:fixed; width:78vw(최대 300px)`로 라벨까지 보이는
  오버레이가 됨. `_sidebar.html`에 반투명 배경 `<div class="mobile-sidebar-backdrop">`을 `</nav>` 뒤에
  추가(탭하면 `onclick="toggleSidebar()"`로 닫힘 — 기존 함수 재사용, 새 JS 없음).
  **버그 2개 발견·수정**: ①데스크톱용 `.sidebar{transition:width .25s}`가 모바일에서 `position`이
  `static→fixed`로 동시에 바뀌는 토글과 겹치면 일부 엔진에서 width 전환이 멈춰버림(새로고침 시엔
  정상, 동적 토글에서만 재현) → 모바일 전용 `#sidebar.sidebar{transition:none}`으로 해결.
  ②백드롭 `<div>`가 미디어쿼리 밖(PC)에는 규칙이 없어 기본 `display:block`으로 렌더링되어
  `.layout`(flex) 안에 빈 형제 요소로 끼어드는 실제 PC 레이아웃 버그 → 미디어쿼리 밖에
  `.mobile-sidebar-backdrop{display:none}` 기본값을 반드시 추가해야 함(교훈: 미디어쿼리 안에서만
  새 요소를 숨기면 그 미디어쿼리 밖 뷰포트에서는 브라우저 기본 display가 그대로 노출됨 — 새 DOM
  요소를 추가할 때는 항상 비-모바일 기본 상태도 명시할 것). 검증: 360/412px 전개(281px/300px 클램프)·
  백드롭 탭 닫힘·서브메뉴 토글·21개 메뉴링크 정상, PC(1366) 사이드바 220px·백드롭 0x0 미노출 확인.

- **2026-07-22: BOM 변환 게시판에 생성 이력 신설(DB 영구 보관 + 재다운로드).**
  기존엔 업로드/변환할 때마다 `GENERATED_BOMS`(메모리 dict)에만 잠깐 남아서 서버 재시작하면
  과거에 뭘 올렸었는지 전혀 안 남았음. 신규 `bom_generate_history` 테이블(auth.py)에 업로드
  성공 시마다 자동 기록: 차종/UPG정보, 공장별 원본 파일명(광주/화성 PEL·BRE), 표준양식 리비전,
  VC수·매칭·미매칭, `plants_used`(JSON), **output_path(생성물 실제 경로)**. 이 output_path 덕분에
  `/bom-generate/history/download/{id}`는 인메모리 `GENERATED_BOMS`와 무관하게 항상 재다운로드
  가능 — 실측: 로컬에서 서버 프로세스를 완전히 재시작한 뒤에도 과거 이력의 다운로드가 200으로
  정상 동작함을 확인. 이력 삭제는 본인 또는 관리자만(DB행 + 실제 파일 함께 삭제). `auto_bom.html`
  하단에 이력 테이블 카드 추가(새로고침 버튼, 다운로드/삭제 링크), 업로드 성공 시 자동 새로고침.
  재생성(`regenerate`)은 원본 파일 그대로 재사용하는 것이라 별도 이력 행을 새로 남기지 않고
  기존 output_path 파일만 갱신(그 이력 항목의 다운로드는 항상 최신 재생성 결과를 받게 됨).

- **2026-07-22: BOM 변환 게시판 — 차종(운전석) 기준 광주+화성 슬롯 업로드로 확장.**
  기존엔 PEL 1개 + BRE 1개(단일 공장)만 되던 것을, M-BOM 게시판(Q파트&ALC 세트 등록)과 같은
  **슬롯형 업로드**로 바꿈 — 광주/화성 각각 PEL(필수 중 최소 1개)·BRE(선택) 슬롯. `bom_generator.py`
  `generate_bom_from_sources(sources, ...)`가 여러 공장 소스를 순서대로 이어붙여 **하나의 표준
  BOM**을 생성(광주 VC 먼저, 화성 VC 그 다음 — 행/매트릭스 열이 연속으로 이어짐). 각 VC는 자기
  공장의 BRE로 V열(공장)·1레벨(J열)이 채워짐. `generate_bom()` 시그니처가
  `generate_bom(sources: list, pel_path, output_path, template_path)`로 변경(단일 파일 호출은
  더 이상 지원 안 함 — main.py만 호출하므로 하위호환 불필요). `main.py`: `/bom-generate/upload`가
  `pel_gj/pel_hs/bre_gj/bre_hs` 4개 선택 파일을 받음, `GENERATED_BOMS`가 `(out_path, filename,
  source_meta)`로 저장(공장별 spec/bre 경로 리스트) — 재생성 시 이 메타로 그대로 재파싱.
  `auto_bom.html` UI를 드래그드롭 1개에서 **광주/화성 × PEL/BRE 표 형태 슬롯**으로 교체, 하단에
  "🚀 변환 시작" 버튼(자동 트리거 아님 — 여러 슬롯을 다 채운 뒤 한 번에 시작). 실측 검증: 광주
  57VC+화성 101VC 업로드 → 158행 하나의 BOM, 경계(R64광주/R65화성) 정확, HTTP 라우트·다운로드·
  재생성·브라우저 UI(파일 라벨 갱신·결과 렌더링) 전부 확인.

- **2026-07-22: BOM 변환 게시판에 BRE 업로드 추가 → 생산공장(V열)·VC별 1레벨 P/NO 자동 채움.**
  표준양식 **rev_002('생산 공장' 열 추가, 사용자 업로드)로 열이 한 칸씩 밀림**: U(21)=지역,
  **V(22)=생산 공장**, W(23)=MATERIAL(기존 V→W), X(24~)=VC 매트릭스(기존 W→X). `bom_generator.py`
  상수(`PLANT_COL=22, MATERIAL_COL=23, MATRIX_START_COL=24`)로 정정 — 이 template 변경이 기존
  생성 코드의 열을 어긋나게 했던 것도 함께 해결. 신규 `parse_bre()`: **공장은 시트명 접두 2글자**
  (BSUPGEA=광주, DEUPGEA=화성 — 파일명 같아도 시트로 자동 구분, 부품번호 중간 BS/DE는 공통부품이
  BS 공유라 신뢰 불가), **VC→1레벨 P/NO는 품명에 'SEAT ASSY' 포함 Level1 행이 각 VC열에 1.0
  표기된 것**으로 매핑(VC001→88001BS000 …). `generate_bom(..., bre_info=)`로 전달, V열=공장,
  J열=VC별 1레벨, 매트릭스 X6도 VC별 1레벨. `/bom-generate/upload`에 `bre` 선택 파일 추가,
  `GENERATED_BOMS` 튜플에 bre_path 저장해 재생성에도 반영. UI(`auto_bom.html`)는 PEL 드롭존 위에
  "② 고객 BRE(선택)" 슬롯 추가(공장 자동인식 안내). 실측 검증: 광주 PEL+BRE → 57 VC 전부 V=광주,
  J=88001BS…, 화성은 101 VC. BRE 실파일 경로: `S:\O3_01_진행 Project\01_HKMC\01_16_SP3\04_BOM\
  03.BRE BOM\FR\260427 접수(광주,화성)\{광주|화성}\...BRE.xlsm` (로컬 S드라이브 접근 가능).
  미결: PEL과 BRE의 VC 세트가 어긋나는 경우(부분 매칭) 처리·경고는 추후 보강.

- **2026-07-22: 모바일 반응형 보정(안드로이드 삼성인터넷/크롬 화면 겹침·오른쪽 잘림 수정).**
  원인: ①`header`가 flex인데 flex-wrap 없고 자식이 min-width:auto라 긴 h1+nav가 안 줄어들어
  글자가 세로로 쪼개짐 ②`.sidebar` 고정 220px(flex-shrink:0)가 360px 화면 대부분을 먹고
  `.layout{overflow:hidden}`이 넘친 본문을 잘라냄. viewport meta는 정상. 수정: **PC(769px↑)는
  절대 안 건드리고 `@media(max-width:768px)`만 추가**. `_sidebar.html`에 전 페이지 공통 모바일
  블록 1개 신설(`<style id="unified-mobile-css">`) — 헤더 한 줄 유지(h1 14px·부제 숨김·nav 축소),
  사이드바를 기존 접힘(아이콘) 레일 56px로(구조·JS·메뉴·링크 그대로), `.main-content`/`.main`에
  min-width:0. `index.html`엔 페이지 전용 블록(조회 조건 세로배치·컨테이너 패딩). 표 있는 페이지는
  `.tbl-wrap{overflow-x:auto}`로 표 내부만 스크롤(production_dashboard). 백업 `.bak` 로컬 보관.
  검증: 360/412/768 모바일 가로넘침 없음·헤더 60px·사이드바 56px·메뉴링크 21개 href 보존,
  1366/1920 PC는 사이드바 220·h1 18px·부제 보임·조회폼 가로로 수정 전과 동일. **주의: 페이지마다
  `<style>`가 중복돼 있어 헤더/사이드바 공통 모바일 규칙은 반드시 `_sidebar.html`(전 페이지 include)
  한 곳에만. 페이지 고유 콘텐츠(폼·표) 모바일 보정은 각 페이지 `<style>` 맨끝에 넣어야
  베이스 규칙보다 뒤라 이김(미디어쿼리는 특이도 안 올림).**

- **2026-07-22: 생산 대시보드 — 매출·영업이익 그래프 추가(수기 입력).** `production_qty`에
  `revenue`/`profit` 컬럼 추가(수기 입력, 나중에 영업 단가 원본에서 자동 산출 예정). 주별 입력
  테이블에 「매출액(원)」「영업이익(원)」 열 추가, 생산 수량 그래프 **아래에 별도 카드**로
  매출·영업이익 막대그래프 신설(단위가 대 vs 원이라 통합 안 하고 분리 — 사용자 선택). 그래프는
  차종별 고유 색(`VEHICLE_COLORS` 팔레트, index순 배정), 매출=진하게·영업이익=같은 색 opacity .5.
  `get_production_summary`가 revenue/profit 합계도 반환, `upsert_production_qty`에 두 인자 추가.
  ── 미결(계속): 2번 BRE→표준BOM V열 공장명 자동출력 + BOM 변환 게시판에 PEL/BRE 업로드 탭
  분리(현재 auto_bom.html은 PEL 드롭존 1개뿐). 표준BOM 활성 템플릿 V열 확인 + BRE 샘플 필요.

- **2026-07-22: 서버 크래시(OOM hang) 사고 + systemd 안전장치.** RAM 956MB(E2.1.Micro)에서
  옛 코드(무거운 async 블로킹) + 사용자 활동 + `systemctl restart` 겹침 → OOM hang → 부팅 시
  bom.service `Restart=always` 크래시 루프로 서버 전체(SSH 포함) 응답불능. 복구: 부팅 직후
  SSH 창 포착해 서비스 정지. 재발 방지로 **스왑 1GB→3GB 증설**(fstab 등록) + systemd drop-in
  (`/etc/systemd/system/bom.service.d/override.conf`)에 `MemoryMax=750M`(폭주 시 그 서비스만
  종료, OS/SSH 보호) + `StartLimitIntervalSec=300`/`StartLimitBurst=4`(크래시 루프 차단) +
  `RestartSec=20`. **배포 주의: `systemctl restart`는 메모리 여유 있을 때(서비스 정지 상태 등)
  신중히 — 사용자 활동 중 재시작 겹치면 OOM 위험.** 사용자 2~3명뿐이라 트래픽이 아니라
  메모리 부족이 원인이었음. 근본 여유는 무료 ARM A1.Flex(최대 24GB) 이전이 최선(추후 검토).

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
