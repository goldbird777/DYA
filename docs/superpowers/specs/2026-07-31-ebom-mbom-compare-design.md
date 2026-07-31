# E-BOM & M-BOM 비교 게시판 고도화 — 설계 문서

날짜: 2026-07-31
대상: `EBOM/bom_web` (`/ebom-mbom-compare`)

## 배경

현재 게시판은 **품번 존재 여부**만 대조한다. E-BOM 1레벨 10자리 품번과 업로드한 ALC 파일의
10자리 품번을 집합 비교해 `OK` / `E-BOM에만` / `M-BOM에만` 세 가지로 판정한다.

목업이 요구하는 것은 **사양 일치 검증**이다. 같은 품번에 대해 설계(E-BOM)가 적은 사양과
생관(M-BOM ALC)이 적은 사양이 같은지를 본다. 최종 목적은 오류 적발보다 **1레벨 BOM 자동화
준비** — 설계 PEL 코드와 생산 ALC 코드를 어떻게 맞출지 알아내기 위해, 두 문서의 기준정보가
어디서 어긋나는지 드러내는 것이다(사용자 확정).

## 실측 근거

E-BOM #5(NQ5/27MY 1열 운전석)와 M-BOM #1의 FRT LH ALC를 실제 대조한 결과:

- 품번 매칭: 공통 49건 (E-BOM only 0, M-BOM only 41)
- **사양 완전일치: 0%** — 완전일치 판정은 쓸 수 없다
- 축(옵션그룹)별로 나누면 총 237건 판정: 일치 129 / E-BOM 미해석 73 / 값상이 35

| 축 | 결과 |
|---|---|
| `COVER'G`(원단) | 49건 전부 일치 |
| `Airbag` | 45건 일치 |
| `VENT` | 13건 일치 |
| `H/REST` | 49건 전부 E-BOM 미해석 |
| `LUMBAR` | 32건 값상이 (`L/SUPT` ↔ `L/SUPT(4CELL)`) |
| `OPTION` | 일치 22 / 미해석 24 / 값상이 3 |

완전일치가 0%인 이유는 세 가지이며 **전부 진짜 오류가 아니다**:
1. 계열 vs 특정값 — E-BOM `A/LEATHER`(상위) ↔ M-BOM `A/LEA(1)`(하위)
2. 표기 세밀도 — `USB` ↔ `USB(27W)`, `L/SUPT` ↔ `L/SUPT(4CELL)`
3. PEL 마스터 미등록 — E-BOM N열의 `2WAY H/REST STD`(23회)·`B/COVER`(27회)가 마스터에 없어
   해석 실패. **E-BOM이 안 적은 게 아니라 마스터에 용어가 없는 것**이다(실측 확인).

## 범위

**포함**
- M-BOM 입력을 파일 업로드 → `mbom_history` 게시글 **선택**으로 변경
- E-BOM 입력을 차종+단계 선택으로 정리(열/위치별 최신본 자동 수집, 기존 동작 유지)
- `ebom_items`에 `spec` 컬럼 추가 + N열 사양 추출 + 기존 등록본 백필
- 축(옵션그룹) 단위 사양 비교와 사유별 판정
- 전용 템플릿 신설(현재는 범용 `mbom_placeholder.html` 사용 중)
- 결과 엑셀 다운로드 갱신

**제외**
- 1레벨 BOM 자동 생성 자체(이번 결과는 그 준비 자료)
- 2레벨 이하 비교
- PEL 마스터 자동 보정 — 어떤 별칭을 넣을지는 사람이 판단

## 데이터 흐름

```
E-BOM 선택(차종+단계)
  → ebom_uploads 에서 (열,위치,variant)별 최신본
  → ebom_items(level=1) 의 pno + spec(N열)
  → PEL 마스터 어휘로 사양키 해석 → 축별 집합

M-BOM 선택(mbom_history 게시글)
  → 슬롯별 ALC 파일 5종
  → read_alc_partno() 로 CODE→13자리 품번(앞 10자리 사용)
  → read_alc_pel() 로 CODE→PEL 코드들 → PEL 마스터 → 사양키 → 축별 집합

10자리 품번으로 매칭 → 축별 비교 → 행별 판정 + 축별 요약
```

## 판정 규칙

품번 하나에 대해 양쪽의 축별 사양 집합을 비교한다. 축 = PEL 마스터의 **옵션그룹**.

| 상황 | 판정 | 비고란 문구 |
|---|---|---|
| 양쪽 값 있고 교집합 있음 | 일치 | `일치` |
| 양쪽 값 있고 교집합 없음 | 불일치 | `불일치 — 원단: E-BOM «천» ↔ M-BOM «A/LEA(1)» · BOM 파일 또는 ALC 확인 필요` |
| E-BOM 텍스트는 있으나 마스터 미등록 | 보완필요 | `보완필요 — E-BOM의 «2WAY H/REST STD»가 PEL CODE 마스터에 없음 · PEL CODE 마스터 설명란에 추가` |
| E-BOM에 그 축 자체가 없음 | 보완필요 | `보완필요 — 백보드 사양이 E-BOM N열에 없음 · E-BOM 파일 N열 확인` |
| M-BOM에만 있는 축 | 보완필요 | 위 두 경우 중 해당하는 쪽으로 표기 |

핵심은 **"PEL 마스터를 고쳐라"와 "BOM 파일을 고쳐라"를 구분해 알려주는 것**이다.
`PEL_SPEC_SKIP`(`S/HTR`·`V/HTR`·`MNL`)은 판정·표시 모두에서 제외한다(사용자 확정).

계열 판정은 `validators._spec_index`(어휘→사양키 **집합**)를 그대로 쓴다. `A/LEATHER`가
A/LEA(1)·A/LEA(2)·PU를 모두 가리키므로 교집합 판정으로 자연히 흡수된다.

## 화면

**상단 — 축별 요약 카드** (개별 행보다 이쪽이 핵심 정보)
```
✅ 정합 완료        원단 · 에어백 · 통풍
🔧 마스터 보완 필요  헤드레스트(49건), 백보드(27건) → PEL CODE 마스터 설명란에 별칭 추가
⚠️ 값 확인 필요      럼버(32건) — E-BOM «L/SUPT» ↔ M-BOM «L/SUPT(4CELL)»
```

**중단 — 비교 실행**: `E-BOM 선택`(차종+단계) · `M-BOM 선택`(게시글) · `▶ 비교 실행`

**하단 — 결과표**

| 구분 | 1LV 품번 | E-BOM (리비전) | M-BOM (리비전) | 비고 |
|---|---|---|---|---|
| 1열 | 88005-P1000 | A/LEATHER + T&P | A/LEA(1) + T&P + HTR(3STEP) + … | 보완필요 — … |

`구분`은 `ebom_uploads.row_num`(1열/2열), 리비전은 각각 `ebom_uploads.revision`·
`mbom_history.revision`에서 온다. 열별 오토필터는 `pel_spec.html` 패턴을 재사용한다.

## 구현 항목

**auth.py**
- `ebom_items`에 `spec TEXT DEFAULT ''` 추가(ALTER TABLE + try/except 관례)
- `save_ebom_items` / `replace_ebom_items`가 `spec` 저장

**main.py**
- `_detect_spec_col(df, pno_col)` 신설 — `+`를 포함한 텍스트가 가장 많은 열을 사양 열로 판정.
  실측: 대상 파일에서 13열이 215건으로 2위(2건)와 압도적 차이. 시트당 1회만 계산.
- `_parse_ebom_xlsx`가 `spec` 채움 (기존 `description`=품명은 그대로 둠 — 다른 기능이 씀)
- 기존 등록본 백필: 관리자용 `/ebom-board/reparse-all` 또는 비교 실행 시 `spec`이 비었으면
  즉시 재파싱 후 저장(lazy backfill). **lazy 방식 채택** — 별도 운영 절차가 필요 없고
  file_path가 없는 옛 등록본(#3·#4)은 자연히 건너뛴다.
- `/ebom-mbom-compare` 페이지 컨텍스트에 E-BOM 후보(차종+단계)와 M-BOM 게시글 목록 전달
- `/ebom-mbom-compare/run` 재작성 — 축별 비교, 무거운 파싱은 기존대로 스레드풀
- 결과 엑셀에 축별 요약 시트 추가

**templates/ebom_mbom_compare.html** 신설 (현재 `mbom_placeholder.html` 대체)

## 에러 처리

- E-BOM 등록본 없음 / M-BOM 게시글에 ALC 파일 없음 → 400과 안내 문구
- `spec`이 전부 빈 등록본(옛 파일, file_path 없음) → 그 열은 결과에서 제외하고 상단에 경고
- PEL 마스터 로딩 실패 → `validators.load_spec_vocab()`이 None을 반환하므로 비교 불가 안내

## 검증 계획

1. `python -m py_compile`
2. 서버 실데이터로 대조: E-BOM #5(NQ5/27MY) × M-BOM #1 — 위 실측 수치와 일치하는지
   (축별 일치 129 / 미해석 73 / 값상이 35)
3. 백필 동작: `spec`이 빈 등록본을 비교에 넣었을 때 자동 채워지는지
4. 웹 라우트 HTTP 200, 서버 오류 없음
5. 기존 `/ebom-board` 업로드 회귀 없음(`description`=품명 유지 확인)

## 배포

로컬 커밋 → `git push` → SSH → `git pull` → `systemctl restart bom.service`.
`data/pel_code_master.xlsx`는 서버 실데이터라 커밋 대상에서 제외.
