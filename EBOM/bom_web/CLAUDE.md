# EBOM/bom_web — 작업 메모

FastAPI 기반 BOM/PEL 코드 웹 도구 (`main.py` 엔트리포인트, `templates/`, `alc2_convert.py`,
`bom_generator.py` 등). Oracle Cloud 인스턴스에 배포되어 설계/생산관리가 함께 사용한다.
접속 정보는 아래 "배포" 절 참고.

## 최근 결정 사항 (최신이 위)

- **2026-07-31: 게시판 4종 신설 — 시트편집(2안)·품목관리·이용방법·RFP + 비교게시판 고도화.**
  외주 프로그램(DayouBOM) 비교분석 후 «2안(웹 엑셀 편집)»으로 방향 확정하고 연달아 구축.
  - **E-BOM 시트 편집(`/ebom-sheet`, 2안)**: 엑셀을 셀 단위로 DB 적재(`ebom_sheets`/
    `_cells`/`_revs`) → 웹에서 엑셀과 동일한 화면으로 편집 → 저장 시 리비전 → **원본
    서식 100% 보존 다운로드**(새 워크북을 만들지 않고 원본에 «바뀐 셀만» 덮어쓰기 —
    `alc2_convert.build_qpart_merge`에서 검증된 방식). 실측 485행×75열: 셀 9,392개
    적재 10.6초, 렌더 1.29초/36,711셀, 병합 29·열너비·셀배경 원본과 동일.
    **편집 락**: 한 번에 한 명, 무활동 30분 자동 만료, 관리자 강제 해제. 락 없으면
    서버가 저장 자체를 거부(화면만 막은 게 아님).
  - **엑셀 서식 재현**: `_extract_layout()`이 열너비·행높이·병합·셀스타일 추출.
    셀마다 스타일을 담으면 커지므로 중복제거(36,368셀 → 고유 141개, JSON 578KB).
    ⚠️ **열 너비는 «범위»로 저장된다** — `<col min="2" max="5" width="2.33"/>` 하나가
    B~E를 지정. 열 문자로만 조회하면 중간 열(C·D·E·H·I)이 통째로 빠져 기본값으로
    그려지고 4배 넓게 보인다(실측 버그). `min~max`를 펼쳐야 한다.
  - **처리 버전 자동 갱신(`proc_ver` + `SHEET_PROC_VER`)**: 서식 추출·자동등록 로직을
    고치면 상수만 올리면 된다. 기존 시트는 **열 때 원본에서 자동 재처리**되어 재업로드가
    필요 없다(원본을 `file_path`에 보관하기에 가능). **셀 값은 절대 안 건드린다** —
    사용자 편집이 들어 있기 때문. 실측: 구버전 재현 후 열기만 해도 열너비 28→75개
    복구·품목 409건 등록, 편집값 보존, 재열기 시 중복 갱신 안 함.
  - **품목 관리(`/parts`, 2D/3D)**: BOM 업로드 시 전 레벨 품번·품명 **자동 등록**
    (시트 업로드 1회 → 409건). 품목별 스펙 20개 필드 + 도면·첨부 + REVISION 이력.
    자동 등록은 **빈 칸만** 채우고 수기 스펙은 덮어쓰지 않는다(BOM 재업로드 안전).
    메뉴는 «마스터 데이터»가 아니라 **E-BOM 시스템** 아래(2D/3D를 BOM과 함께 관리).
    시트 편집 화면 우측에 **품목 정보 패널**(품번 셀 클릭 → 스펙·도면 표시).
  - **E-BOM & M-BOM 비교 고도화**: 품번 존재여부 → **축(옵션그룹) 단위 사양 대조**로
    전환(`spec_compare.py`). 사양 집합 통째 비교는 실측 **완전일치 0%**라 못 쓴다 —
    계열vs특정값·표기 세밀도·마스터 미등록 셋 다 진짜 오류가 아니기 때문. 판정을
    OK/MISMATCH/NEED_MASTER/NEED_EBOM 4종으로 나눠 **«PEL 마스터를 고쳐라»와 «BOM
    파일을 고쳐라»를 구분**해 안내. M-BOM 입력은 파일 업로드 → `mbom_history` 게시글
    선택으로 변경. `ebom_items.spec` 컬럼 추가(기존 `description`은 품명이라 사양
    비교에 못 씀) + lazy backfill.
  - **이용방법(`/guide/usage`, 관리자 전용) / RFP(`/guide/rfp`)**: `doc_posts`·
    `doc_files` 한 스키마 + kind 구분. 경로가 `/docs`면 FastAPI Swagger와 겹쳐 `/guide`.
    RFP 초기 문서는 **자체 구축분을 «구축완료»로 명시**해 업체 견적 제외 근거로 쓴다
    (`tools/seed_docs.py`, 재실행해도 중복 안 생김).
  - **미결**: ①럼버 32건은 `8828A5`·`8828A6` 설명란에 `L/SUPT` 별칭을 넣으면 정리될
    가능성 큼. ②헤드레스트는 VC헤더 `4W STD`/`2W HAN`이 어느 PEL 코드인지 확인 필요
    (STD/HANGER 축이 PEL 마스터에 아예 없음). ③`B/COVER`(N열 최다 71회)를 `B/BOARD`
    계열 어느 코드에 붙일지. ④2안을 여러 차종·동시 사용자로 확대하려면 서버 증설
    (ARM A1.Flex 최대 24GB) 선행 필요.

- **2026-07-30: BOM 검증 — HKMC 양식 오검출 제거 + PEL 마스터 기반 «사양 배정 모순»(⑥) 신설.**
  외주 BOM 프로그램(DayouBOM, `T:\...\dayoubom-main`) 비교분석 중 우리 검증기가 **실제
  HKMC 수령 양식을 읽지 못하고 있다**는 것을 발견해 고친 작업. 커밋 `04c9e4c`, `9adf7bc`, `bc4c840`.
  - **원인①**: `bom_parser`가 VC 번호 행을 **4행 고정**(`df.iloc[3]`)으로 찾았다. DYA 표준양식은
    4행(`bom_generator.MATRIX_VC_ROW=4`)이지만 HKMC 양식은 **8행**이라 VC가 0개로 잡혀
    수량 기반 검증 4개가 무동작하고 «오사양 누락»만 **145건 전건 오검출**됐다.
    → 상단 12행 중 3자리 숫자(ci≥20)가 가장 많은 행을 VC 헤더로 동적 판정.
  - **원인②**: `validators`가 1레벨 행의 VC를 A열에서만 읽었다. HKMC 양식 A열은 일련번호(No)라
    매트릭스 VC 코드와 하나도 안 맞았다(실측 A열이 VC코드인 1레벨 행 **0/50**).
    → A열 값이 VC 코드 집합에 없으면 **수량이 찍힌 열에서 유도**(실측 유도 가능 49/50).
  - 실측(250425 NQ5 PE FRT SEAT BOM, VC 53 / 부품 452행): 수정 후 오검출 145→0건,
    수량 5,857셀 인식, **실제 오류 1건 발견** — `88751-P1500`이 SLAB SPONGE(R475)와
    PAPER TAPE(R476) 두 부품에 동일 품번.
  - **⑥ 사양 배정 모순 신설**: ①②는 하위 «품명»을 보는데 품명은 구조 명칭(CUSH/PAD/BACK ASSY)이라
    사양이 거의 안 드러난다(실측: PEL 사양 65개 중 품명 등장 **5개**뿐). 실제 사양은 **N열**에
    `THORAX+CLOTH+L/SUPT` 형태로 있고, 이를 **VC 사양 헤더**(VC 번호 행 위쪽 여러 단)와 대조하면
    배정 오류를 잡는다(수동 실측 정합 **98.5%**). `bom_parser(with_vc_specs=True)`로 수집.
    ※ **필요조건만** 검사한다 — 사양이 맞아도 배정 안 될 수 있어(예측 정확도 30.7%) «누락»은 판정 안 함.
  - **PEL 마스터가 사양 정의의 원천**(`load_spec_vocab()`, mtime 캐시, 실패 시 `SPEC_KEYWORDS` 폴백).
    별칭 = «사양»+«설명»의 콤마 조각. **단 다른 코드의 «사양» 필드값과 충돌하는 조각은 버린다** —
    설명란에 별칭 아닌 서술이 섞여 있어서다(실측: `8828A2 L/SUPT` 설명이 그냥 "PWR"이라 그대로
    쓰면 PWR을 전부 L/SUPT로 오인, 충돌 4건).
  - **안전장치 2개가 필수였다**: ⓐ`EXCLUSIVE_GROUPS` 화이트리스트 — 옵션그룹 전부를 배타로 보면
    «OPTION»에 뭉친 PWR·VENT·USB·SBR 정상 조합이 **전건 오탐(116건)**. 현재 `COVER'G`·`H/REST`·
    `LUMBAR`만(H/REST·LUMBAR 택1은 사용자 확정). ⓑ**계열/계층 허용** — `_spec_index`가
    {어휘:사양키**집합**}이라 한 별칭이 여러 사양에 걸리면 전부 허용(`A/LEATHER`→A/LEA(1)+(2)),
    추가로 VC 사양명이 부품 사양명을 문자열 포함하면 정상(`PWR(IMS)`⊃`PWR`).
    **계열 범위를 코드가 아니라 설명란이 결정**한다 — `8811M1`에 `A/LEATHER`를 달면 PU가 인조가죽
    계열, 안 달면 별개 원단(A/B 실측: 모순 2건 vs 14건).
  - **사용자 확정 도메인 규칙**: 백커버=BACK COVER=BACK BOARD 동일 / `S/HTR`·`V/HTR`은 «HTR 단독»과
    «통풍 동반»을 편의상 구분한 표기이지 PEL 사양이 아니라 **검증 제외** / `MNL`은 HKMC가 PWR만
    송부해 코드가 없고 **"PWR 부재=MNL" 부정 조건**으로 판정 → 셋 다 `PEL_SPEC_SKIP`.
  - **미결(다음 세션 착수점)**: ⑥번 실검출이 아직 0건인데 원인은 코드가 아니라 **마스터 별칭 미입력**이다.
    VC 사양헤더 고유토큰 20종 중 해석되는 건 4종(`IMS`/`PU`/`PWR`/`T&P`)뿐. 넣어야 할 것:
    `8811F2`·`8811F3`←`A/LEATHER, 인조` / `8811C1`·`8811C2`←`CLOTH, 천` / `8811E2`←`P/LEATHER, 천연`
    / `8828A2`←`럼버`(이 한 줄로 LUMBAR 축 활성) / `961SA3`←`USB` / `B/BOARD` 계열(`886BA2`·`886BA3`·
    `8836A2`·`8836A3`·`8836A8`)←`B/COVER`(N열 최다 71회). H/REST는 VC 헤더 `4W STD`/`4W HAN`/
    `2W STD`/`2W HAN`이 어느 PEL 코드에 대응하는지 사용자 확인 필요. 별칭 입력 후 R446(`88700-P1600`)·
    R477(`88760-P1500`)이 검출 예정 — 인조가죽 부품이 천연가죽 VC에 배정된 건으로 실제 검토 대상.
  - **참고: 서버 마스터가 로컬보다 최신이다**(서버 89행 vs 로컬 65행). 사양 관련 분석은 반드시
    서버 사본(`scp`로 스크래치패드에 받아서)으로 할 것. 로컬 사본 기준으로 분석해 "설명란 콤마
    다중용어 0건"·"옵션그룹이 OPTION에 뭉쳐 있음" 같은 오판을 한 적 있음(서버엔 11건 있고
    `Airbag`·`LUMBAR`·`VENT` 그룹이 이미 분리돼 있었다).

- **2026-07-29: "Q파트 ALC 통합" 게시판 신설(`/qpart-merge`) — 품번·사양 O/X를 Q파트 종합에
  직접 병합.** 기존 "HKMC Q파트 & ALC 이력 관리"(`/mbom-history`, DYA ALC-2 채번·매칭 전용)와는
  완전히 별개 게시판/DB/라우트로 분리(브레인스토밍 설계 문서:
  `docs/superpowers/specs/2026-07-28-qpart-alc-merge-board-design.md`). 입력은 동일한 6개 파일
  (Q파트 종합 + ALC 5종, `alc2_convert.ALC_SLOTS`)이지만 목적이 다름 — ★통합 ALC2 코드 대장/DYA
  ALC-2 매칭은 다루지 않고, **Q파트 종합 워크북을 그대로 복사해 오른쪽 끝에** 좌석별 품번 5열
  (`read_alc_partno` 원본 PART NO 문자열) + PEL 마스터 기준 사양 O/X열(`build_ox`와 동일 로직,
  `_spec_label` 재사용)을 덧붙인 새 xlsx를 생성(`alc2_convert.build_qpart_merge()`). 결과는
  `alc2_convert.read_grid()`로 파싱해 화면에 **엑셀형 오토필터 그리드**(`qpart_merge.html`,
  헤더 클릭→열별 체크박스 필터, `pel_spec.html`의 오토필터 UX를 범용화한 버전 — 도메인 고정
  열 대신 임의 헤더/행 배열 기반)로 즉시 표시. DB 신규 테이블 3종(`qpart_merge_posts/
  _files/_runs`, `auth.py`) — `_runs`가 `output_path`를 영구 저장해 "BOM 변환 게시판"
  (`bom_generate_history`)과 같은 패턴으로 서버 재시작 후에도 과거 변환 결과 재조회/재다운로드
  가능(실측: 서버 프로세스 재시작 후 과거 run의 그리드 재조회·다운로드 링크 정상 확인). 게시글
  삭제 시 슬롯 파일 + 모든 run 결과 파일까지 함께 정리(`delete_qpart_merge_post`). 사이드바
  M-BOM 섹션에 "HKMC Q파트 & ALC 이력 관리" 바로 아래 배치. 검증: 실제 업로드 이력 파일(post_id 2,
  SP3, 58개 생산행)로 엔진 함수 단독 실행 + 브라우저 API 왕복 + 오토필터 클릭 동작까지 확인,
  서버 재시작 전후 결과 일치.

- **2026-07-23: 전 페이지 폭 제한(max-width) 제거 + HKMC PEL→E-BOM 게시판 제목 위치·중복 설명 정리.**
  admin/auto_bom/index/m_bom/pel_code/process_overview/production_dashboard/vehicles 8개
  페이지의 `.container{max-width:Npx}`를 전부 제거해 pel_spec.html처럼 화면 폭을 꽉 채우도록
  통일(넓은 모니터에서 안내 그림·카드가 화면 절반만 차던 문제 해결, 1920px에서 1701px까지 확장
  확인). auto_bom.html: `<div class="page-title">`가 `<details class="board-guide">` **뒤**에
  있어서 게시판 제목이 화면 중간에 나오던 것 → 앞으로 이동. 또한 board-guide-body 상단 설명과
  별도 `.help-tip` 박스가 "부품사양서 업로드→PEL 마스터 조회→1레벨 BOM 생성"을 사실상 같은
  내용으로 두 번 말하고 있던 것 → 하나로 병합(옵션제약 영역·OPT 자동인식 문구는 병합된 설명
  안으로 흡수), 중복 `.help-tip` 삭제.

- **2026-07-23: 게시판 안내 그림 기능을 HKMC PEL(부품사양서) → DYA E-BOM 게시판에도 적용.**
  범용 설계(`board_guide_image` 테이블 + `/board-guide/{board}/*` 라우트)가 그대로 재사용됨 —
  `BOARD_GUIDE_ALLOWED`에 `'bom_generate'`만 추가, `bom_generate_page`가 `guide_image` 컨텍스트
  전달, `auto_bom.html`에 pel_spec.html과 동일한 `guideImageBox`/업로드·삭제 버튼 패턴 추가
  (이 템플릿은 toast 대신 `alert()` 관례라 그에 맞춤). 검증: 업로드→표시→서빙(200)→삭제→숨김
  복귀 확인, PEL 사양변경 쪽 회귀 없음 확인.

- **2026-07-23: 게시판 "사용 방법" 안내 그림 업로드 기능 신설 (PEL 사양변경부터 적용).**
  신입/경력 구분 없이 게시판 용도를 그림으로 직관적으로 이해하도록, 사용 설명서의 "사용 방법"
  아래에 관리자가 이미지(png/jpg/gif/webp)를 업로드하면 보여주는 기능 추가. `process_overview`의
  흐름도-사진-대체 기능과 같은 패턴이지만 **여러 게시판에 재사용 가능하게 범용 설계**함:
  `board_guide_image` 테이블(board 식별자 PK, 게시판당 1장), `/board-guide/{board}/upload|view|reset`
  범용 라우트(화이트리스트 `BOARD_GUIDE_ALLOWED`로 허용 게시판 제한 — 지금은 `pel_spec`만).
  다른 게시판에 적용할 땐 화이트리스트에 식별자만 추가하고, 해당 템플릿에 동일한
  `guideImageBox`/`uploadGuideImage()`/`resetGuideImage()` 패턴만 복사하면 됨(pel_spec.html
  참고). 검증: 업로드→표시→서빙(200)→삭제→숨김 복귀 전부 로컬 확인.

- **2026-07-23: 모바일 사이드바 오버레이 폭 — fit-content 대신 고정 220px로 재조정.**
  이전에 `width:fit-content;min-width:220px;max-width:82vw`로 했었는데, 실제 안드로이드
  기기에서는 여전히 화면의 78~80%까지 넓게 나온다는 피드백. 원인: fit-content의 shrink-to-fit
  계산이 하위메뉴 긴 라벨("HKMC Q파트 & ALC 이력 관리" 등)의 **줄바꿈 전(max-content) 폭**까지
  반영해버려서 실질적으로 82vw 캡에 거의 항상 닿고 있었음. 고정 `width:220px;max-width:72vw`로
  바꾸고, 긴 하위메뉴 라벨은 기존 word-break:keep-all로 2줄 자연 줄바꿈되게 함(가로 스크롤 없음
  실측 확인). 새창 열기(⧉) 아이콘은 줄바꿈된 멀티라인 항목과 겹칠 수 있어 오버레이 모드에서만
  숨김 처리. 검증: 360px→220px(61%), 412px→220px(53%), PC(1366)는 여전히 220px 그대로.

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
