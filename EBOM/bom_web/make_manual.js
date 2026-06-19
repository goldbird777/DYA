const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle,
  WidthType, ShadingType, VerticalAlign, PageNumber, PageBreak,
  LevelFormat, ExternalHyperlink
} = require('docx');
const fs = require('fs');

// ── 공통 색상 ────────────────────────────────────────────────────────────────
const NAVY   = '002C5F';
const BLUE   = '1565C0';
const RED    = 'C62828';
const ORANGE = 'E65100';
const GREEN  = '1B5E20';
const LGRAY  = 'F5F5F5';
const MGRAY  = 'CCCCCC';
const WHITE  = 'FFFFFF';

const brd = (color = MGRAY) => ({ style: BorderStyle.SINGLE, size: 1, color });
const borders = (c = MGRAY) => ({ top: brd(c), bottom: brd(c), left: brd(c), right: brd(c) });
const cell_m = { top: 80, bottom: 80, left: 140, right: 140 };

// ── 헬퍼 ─────────────────────────────────────────────────────────────────────
function hd(text, level = HeadingLevel.HEADING_1) {
  return new Paragraph({ heading: level, children: [new TextRun(text)] });
}

function p(text, opts = {}) {
  return new Paragraph({
    spacing: { after: 120 },
    children: [new TextRun({ text, font: '맑은 고딕', size: 20, ...opts })]
  });
}

function bullet(text, bold_prefix = '') {
  return new Paragraph({
    numbering: { reference: 'bullets', level: 0 },
    spacing: { after: 80 },
    children: [
      ...(bold_prefix ? [new TextRun({ text: bold_prefix, bold: true, font: '맑은 고딕', size: 20 })] : []),
      new TextRun({ text, font: '맑은 고딕', size: 20 })
    ]
  });
}

function num(text, bold_prefix = '') {
  return new Paragraph({
    numbering: { reference: 'numbers', level: 0 },
    spacing: { after: 80 },
    children: [
      ...(bold_prefix ? [new TextRun({ text: bold_prefix, bold: true, font: '맑은 고딕', size: 20 })] : []),
      new TextRun({ text, font: '맑은 고딕', size: 20 })
    ]
  });
}

function space(n = 1) {
  return Array.from({ length: n }, () => new Paragraph({ spacing: { after: 60 }, children: [] }));
}

function divider() {
  return new Paragraph({
    border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: BLUE, space: 4 } },
    spacing: { after: 160 },
    children: []
  });
}

function code(text) {
  return new Paragraph({
    spacing: { after: 80 },
    shading: { fill: 'F0F0F0', type: ShadingType.CLEAR },
    children: [new TextRun({ text, font: 'Courier New', size: 18, color: '1A1A1A' })]
  });
}

function sectionTitle(text, fill = NAVY) {
  return new Paragraph({
    spacing: { before: 240, after: 160 },
    shading: { fill, type: ShadingType.CLEAR },
    children: [new TextRun({ text: '  ' + text, font: '맑은 고딕', size: 24, bold: true, color: WHITE })]
  });
}

// ── 표 생성 헬퍼 ─────────────────────────────────────────────────────────────
function makeTable(headers, rows, colWidths) {
  const totalW = colWidths.reduce((a, b) => a + b, 0);
  return new Table({
    width: { size: totalW, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [
      new TableRow({
        tableHeader: true,
        children: headers.map((h, i) => new TableCell({
          borders: borders(BLUE),
          width: { size: colWidths[i], type: WidthType.DXA },
          shading: { fill: NAVY, type: ShadingType.CLEAR },
          margins: cell_m,
          verticalAlign: VerticalAlign.CENTER,
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            children: [new TextRun({ text: h, font: '맑은 고딕', size: 18, bold: true, color: WHITE })]
          })]
        }))
      }),
      ...rows.map((row, ri) => new TableRow({
        children: row.map((cell, ci) => new TableCell({
          borders: borders(),
          width: { size: colWidths[ci], type: WidthType.DXA },
          shading: { fill: ri % 2 === 0 ? WHITE : LGRAY, type: ShadingType.CLEAR },
          margins: cell_m,
          verticalAlign: VerticalAlign.CENTER,
          children: [new Paragraph({
            children: [new TextRun({
              text: String(cell ?? ''),
              font: '맑은 고딕', size: 18,
              bold: typeof cell === 'string' && cell.startsWith('**'),
            })]
          })]
        }))
      }))
    ]
  });
}

// ── 문서 본문 ─────────────────────────────────────────────────────────────────
const doc = new Document({
  numbering: {
    config: [
      { reference: 'bullets', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•',
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 640, hanging: 320 } } } }] },
      { reference: 'numbers', levels: [{ level: 0, format: LevelFormat.DECIMAL, text: '%1.',
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 640, hanging: 320 } } } }] },
      { reference: 'sub-bullets', levels: [{ level: 0, format: LevelFormat.BULLET, text: '-',
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 1100, hanging: 320 } } } }] },
    ]
  },
  styles: {
    default: { document: { run: { font: '맑은 고딕', size: 20 } } },
    paragraphStyles: [
      { id: 'Heading1', name: 'Heading 1', basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 30, bold: true, font: '맑은 고딕', color: NAVY },
        paragraph: { spacing: { before: 320, after: 160 }, outlineLevel: 0,
          border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: NAVY, space: 4 } } } },
      { id: 'Heading2', name: 'Heading 2', basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 24, bold: true, font: '맑은 고딕', color: BLUE },
        paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 } },
      { id: 'Heading3', name: 'Heading 3', basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 22, bold: true, font: '맑은 고딕', color: '37474F' },
        paragraph: { spacing: { before: 180, after: 80 }, outlineLevel: 2 } },
    ]
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1440, right: 1260, bottom: 1440, left: 1260 }
      }
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: NAVY, space: 4 } },
          children: [
            new TextRun({ text: 'DYA BOM 자동 검증 시스템', font: '맑은 고딕', size: 18, bold: true, color: NAVY }),
            new TextRun({ text: '  |  개발자 사용 설명서', font: '맑은 고딕', size: 18, color: '888888' }),
          ]
        })]
      })
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          border: { top: { style: BorderStyle.SINGLE, size: 2, color: MGRAY, space: 4 } },
          children: [
            new TextRun({ text: 'DYA Co., Ltd.  |  Page ', font: '맑은 고딕', size: 16, color: '888888' }),
            new TextRun({ children: [PageNumber.CURRENT], font: '맑은 고딕', size: 16, color: '888888' }),
          ]
        })]
      })
    },
    children: [

      // ═══════════════════════════════════════════════════════
      // 표지
      // ═══════════════════════════════════════════════════════
      new Paragraph({ spacing: { before: 1800 }, children: [] }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 80 },
        shading: { fill: NAVY, type: ShadingType.CLEAR },
        children: [new TextRun({ text: '  DYA 시트 BOM 자동 검증 시스템  ', font: '맑은 고딕', size: 52, bold: true, color: WHITE })]
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 600 },
        shading: { fill: BLUE, type: ShadingType.CLEAR },
        children: [new TextRun({ text: '  개발자 사용 설명서 (Developer Guide)  ', font: '맑은 고딕', size: 28, color: 'BBDEFB' })]
      }),
      ...space(2),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: 'Version 1.0', font: '맑은 고딕', size: 22, color: '555555' })]
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: '2025년 6월', font: '맑은 고딕', size: 22, color: '555555' })]
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 200 },
        children: [new TextRun({ text: 'DYA Co., Ltd.', font: '맑은 고딕', size: 22, bold: true, color: NAVY })]
      }),
      new Paragraph({ children: [new PageBreak()] }),

      // ═══════════════════════════════════════════════════════
      // 1. 개요
      // ═══════════════════════════════════════════════════════
      hd('1. 시스템 개요'),
      p('DYA BOM 자동 검증 시스템은 자동차 시트 E-BOM(Engineering Bill of Materials) Excel 파일을 웹 브라우저에서 업로드하면 ' +
        '취소선·음영 셀을 자동 인식하고, 사양 누락·수량 오기입·구조 오류 등 휴먼 에러를 자동으로 검출하여 Excel 리포트로 출력하는 웹 애플리케이션입니다.'),
      ...space(),

      hd('1.1 개발 목적', HeadingLevel.HEADING_2),
      bullet('BOM 검토 시 발생하는 반복적 휴먼 에러 제로화'),
      bullet('취소선(폐지 부품) / 음영(변경 부품) 자동 인식으로 오기입 방지'),
      bullet('향후 PLM 시스템 연동 및 Oracle Cloud 배포 대비 웹 기반 구조 채택'),
      ...space(),

      hd('1.2 검증 항목 (우선순위 순)', HeadingLevel.HEADING_2),
      makeTable(
        ['우선순위', '유형', '심각도', '설명'],
        [
          ['①', '오사양 누락', 'ERROR', '1레벨 DESCRIPTION에 기재된 사양(HTR, SAB 등)이 하위 부품에 없음'],
          ['②', '사양 불일치', 'WARNING', '하위 부품 사양 키워드가 1레벨 DESCRIPTION에 없음'],
          ['③', '수량 오기입', 'WARNING', '소수점·음수·비정상 대수량(50 초과) 감지'],
          ['④', '레벨 중복', 'ERROR', '동일 Variant에 동일 ASSY 타입 2개 이상 배정'],
          ['⑤', 'P/NO 중복', 'WARNING', '동일 레벨2 ASSY 하위에 같은 P/NO 중복 (하드웨어 제외)'],
          ['⑥', 'P/NO 누락', 'WARNING', '수량이 있는데 P/NO가 비어있는 행'],
        ],
        [900, 1500, 1300, 5660]
      ),
      ...space(),
      new Paragraph({ children: [new PageBreak()] }),

      // ═══════════════════════════════════════════════════════
      // 2. 시스템 구성
      // ═══════════════════════════════════════════════════════
      hd('2. 시스템 구성'),

      hd('2.1 기술 스택', HeadingLevel.HEADING_2),
      makeTable(
        ['구분', '기술', '버전', '역할'],
        [
          ['백엔드', 'Python FastAPI', '최신', '웹 서버, API 라우팅, 파일 처리'],
          ['BOM 파싱', 'openpyxl + pandas', '최신', 'Excel 읽기, 취소선/음영 인식'],
          ['프론트엔드', 'HTML / CSS / JavaScript', 'Vanilla', '업로드 UI, 결과 대시보드'],
          ['서버 실행', 'Uvicorn', '최신', 'ASGI 서버 (개발: --reload)'],
          ['Python', '3.11.x', '3.11.9', '주 언어 (3.12+ 호환성 미검증)'],
        ],
        [1400, 2000, 1400, 4560]
      ),
      ...space(),

      hd('2.2 처리 흐름', HeadingLevel.HEADING_2),
      p('사용자 브라우저 → (Excel 파일 업로드) → FastAPI 서버 → BOM 파싱 → 검증 로직 → Excel 리포트 생성 → 결과 JSON 반환 → 웹 화면 표시 + 리포트 다운로드'),
      ...space(),

      hd('2.3 파일 구조', HeadingLevel.HEADING_2),
      makeTable(
        ['파일/폴더', '역할'],
        [
          ['bom_web/', '프로젝트 루트 디렉터리'],
          ['  main.py', 'FastAPI 서버 엔트리포인트, 라우팅 (/validate, /download)'],
          ['  bom_parser.py', 'Excel BOM 파싱 — 취소선/음영 셀 인식'],
          ['  validators.py', '5가지 검증 로직 + 사양 키워드 매핑 테이블'],
          ['  report.py', '검증 결과 Excel 리포트 생성 (openpyxl)'],
          ['  templates/index.html', '웹 업로드/결과 화면 (단일 페이지)'],
          ['  reports/', '생성된 리포트 임시 저장 디렉터리'],
          ['  run.bat', '서버 실행 배치 파일 (더블클릭으로 실행)'],
        ],
        [3200, 6160]
      ),
      ...space(),
      new Paragraph({ children: [new PageBreak()] }),

      // ═══════════════════════════════════════════════════════
      // 3. 설치 및 실행
      // ═══════════════════════════════════════════════════════
      hd('3. 설치 및 실행'),

      hd('3.1 사전 요구사항', HeadingLevel.HEADING_2),
      bullet('Windows 10/11 (현재 로컬 환경 기준)'),
      bullet('Python 3.11.x (공식 사이트 설치, MS Store 버전 사용 불가)'),
      bullet('인터넷 연결 (최초 라이브러리 설치 시)'),
      ...space(),

      hd('3.2 Python 설치', HeadingLevel.HEADING_2),
      p('아래 명령어를 PowerShell 또는 CMD에서 실행합니다.'),
      code('winget install Python.Python.3.11 --accept-package-agreements --accept-source-agreements'),
      p('설치 완료 후 터미널을 재시작하여 PATH를 반영합니다.'),
      ...space(),

      hd('3.3 라이브러리 설치', HeadingLevel.HEADING_2),
      code('pip install fastapi uvicorn openpyxl pandas python-multipart jinja2 xlrd'),
      ...space(),

      hd('3.4 서버 실행', HeadingLevel.HEADING_2),
      p('방법 1 — run.bat 더블클릭 (권장, 비개발자용)'),
      p('방법 2 — 터미널에서 직접 실행:'),
      code('cd C:\\Users\\shkim\\Desktop\\ERP\\EBOM\\bom_web'),
      code('python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload'),
      ...space(),
      p('서버 실행 확인 메시지:'),
      code('INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)'),
      ...space(),

      hd('3.5 접속 방법', HeadingLevel.HEADING_2),
      makeTable(
        ['접속 환경', 'URL'],
        [
          ['서버 PC 본인', 'http://localhost:8000'],
          ['사내 동일 네트워크 PC', 'http://[서버PC IP주소]:8000'],
          ['Oracle Cloud 배포 후', 'http://[공인IP]:8000 (또는 도메인)'],
        ],
        [3000, 6360]
      ),
      ...space(),
      new Paragraph({ children: [new PageBreak()] }),

      // ═══════════════════════════════════════════════════════
      // 4. BOM 파일 구조
      // ═══════════════════════════════════════════════════════
      hd('4. BOM Excel 파일 구조'),
      p('본 시스템이 읽어들이는 BOM 파일의 컬럼 구조는 아래와 같습니다.'),
      ...space(),
      makeTable(
        ['Excel 열', '컬럼 인덱스 (0-based)', '내용', '비고'],
        [
          ['A (col 1)', '0', 'VC / 섹션명', '■로 시작하면 섹션 구분자'],
          ['B~I (col 2~9)', '1~8', '레벨 1~8', '해당 셀에 숫자가 있는 열이 레벨'],
          ['J (col 10)', '9', 'P/NO (부품번호)', '취소선 기준: 폐지 부품 판단'],
          ['L (col 12)', '11', 'P/NAME (부품명)', '사양 키워드 인식 대상'],
          ['N (col 14)', '13', 'DESCRIPTION', '1레벨 사양 기재, 사양 불일치 기준'],
          ['U (col 21)', '20', 'REGION', '지역 코드'],
          ['V (col 22)', '21', 'MAT', '재질'],
          ['W열 이후', '22+', 'Variant 수량', '4행에 3자리 숫자 코드가 있는 열'],
        ],
        [1600, 2200, 2400, 3160]
      ),
      ...space(),

      hd('4.1 취소선 / 음영 인식 규칙', HeadingLevel.HEADING_2),
      makeTable(
        ['셀 서식', '의미', '시스템 처리'],
        [
          ['취소선 (Strikethrough)', '오기입 삭제 또는 미적용 부품', '검증에서 완전 제외, 취소선 탭에 별도 목록화'],
          ['배경 음영 (Highlight)', '변경된 부분 또는 중요 표시', '검증은 정상 진행, 음영 변경 탭에 별도 목록화'],
        ],
        [2500, 2800, 4060]
      ),
      ...space(),
      new Paragraph({ children: [new PageBreak()] }),

      // ═══════════════════════════════════════════════════════
      // 5. 검증 로직 상세
      // ═══════════════════════════════════════════════════════
      hd('5. 검증 로직 상세'),

      hd('5.1 오사양 누락 (ERROR)', HeadingLevel.HEADING_2),
      p('1레벨 DESCRIPTION에 기재된 사양 키워드가 해당 Variant의 하위 부품 중 어디에도 없을 때 오류로 검출합니다.'),
      bullet('예: 1레벨 DESC = "SAB+HTR+CLOTH" → 하위에 HTR 관련 부품이 없으면 오류'),
      bullet('검출 파일: validators.py → validate_bom() 함수 ① 섹션'),
      bullet('수정 위치: 1레벨 N열(DESCRIPTION) 수정 또는 하위 사양 부품 추가'),
      ...space(),

      hd('5.2 사양 불일치 (WARNING)', HeadingLevel.HEADING_2),
      p('하위 부품의 P/NAME에서 감지된 사양 키워드가 1레벨 DESCRIPTION에 없을 때 경고를 발생시킵니다.'),
      bullet('예: 하위 부품명 = "HEATER WIRE ASSY" → HTR 키워드 감지 → 1레벨에 HTR 없으면 경고'),
      bullet('하드웨어(SCREW, BOLT, BUCKLE 등)는 사양 검증 제외'),
      bullet('"공용" 텍스트가 포함된 부품은 제외'),
      ...space(),

      hd('5.3 P/NO 중복 체크 로직', HeadingLevel.HEADING_2),
      p('동일한 하드웨어 부품(스크류 등)이 여러 ASSY에 사용되는 것은 정상입니다. 따라서 아래 기준으로만 중복을 판단합니다.'),
      makeTable(
        ['케이스', '판단', '이유'],
        [
          ['같은 P/NO가 다른 레벨2 ASSY 하위에 각각 있음', '정상', '다른 조립 위치에 사용되는 공용 하드웨어'],
          ['같은 P/NO가 동일 레벨2 ASSY 하위에 2회 이상 (하드웨어)', '정상', 'HW_PAT 해당 부품은 중복 체크 제외'],
          ['같은 P/NO가 동일 레벨2 ASSY 하위에 2회 이상 (비하드웨어)', '경고', '진짜 중복 등록 가능성 있음'],
        ],
        [3600, 1200, 4560]
      ),
      ...space(),
      new Paragraph({ children: [new PageBreak()] }),

      // ═══════════════════════════════════════════════════════
      // 6. 사양 키워드 기준표
      // ═══════════════════════════════════════════════════════
      hd('6. 사양 키워드 기준표 (PEL_CODE 기반)'),
      p('아래 키워드는 validators.py의 SPEC_KEYWORDS 딕셔너리에 정의되어 있습니다. 인식 패턴이 P/NAME 또는 DESCRIPTION에 포함되면 해당 사양으로 판단합니다.'),
      ...space(),
      makeTable(
        ['키워드', '한글 의미', '인식 패턴', '검증 제외'],
        [
          ['SAB', '사이드 에어백', 'SAB, SIDE AIR, AIRBAG, THORAX, T&P', ''],
          ['CSAB', '센터 에어백', 'CSAB, CENTER SAB, CENTER AIR', ''],
          ['HTR', '시트 히터', 'HTR, HEATER, HEAT', ''],
          ['VENT', '시트 통풍', 'VENT, VENTIL', ''],
          ['WMR', '워머', 'WMR, WARMER, WARM', ''],
          ['PWR', '전동 시트', 'PWR, POWER SEAT, PWR SEAT', ''],
          ['IMS', '통합 메모리', 'IMS', '제외'],
          ['L/SUPT', '럼버 서포트', 'L/SUPT, LUMBAR, LUMB, 2WAY L/SUPT', ''],
          ['P_EXT', '파워 연장 쿠션', 'P_EXT, P/EXT, EXTENDABLE, EXT CUSH', ''],
          ['ARMREST', '암레스트', 'ARMREST, ARM REST, A/REST, ARMRST', ''],
          ['W/IN', '워크인', 'W/IN, WALK-IN, WALK IN, WALKIN', ''],
          ['SBR', '시트벨트 리마인더', 'SBR, BELT REM, BELT REMIND', ''],
          ['CLOTH', '패브릭 원단', 'CLOTH', '제외'],
          ['A/CLOTH', '인조 패브릭', 'A/CLOTH', '제외'],
          ['A/LEATHER', '인조가죽', 'A/LEATHER, A/LEA, LEATHER', '제외'],
          ['P/LEA', '순정가죽', 'P/LEA, PURE LEATHER', '제외'],
          ['B/BOARD', '백보드', 'B/BOARD', '제외'],
          ['USB', 'USB 충전', 'USB', '제외'],
          ['TABLE', '폴드업 트레이', 'TABLE, TRAY, FOLD UP TRAY', ''],
          ['EASY_ACC', 'Easy Access', 'EASY ACCESS, EASY-ACCESS', ''],
          ['ODS', '승객감지 센서', 'ODS', '제외'],
        ],
        [1300, 1800, 3500, 1200]
      ),
      ...space(),
      p('※ "검증 제외" 항목은 선택 사양이 아닌 구조·안전 부품으로 사양 불일치 체크에서 제외됩니다.', { color: '888888', size: 18 }),
      ...space(),
      new Paragraph({ children: [new PageBreak()] }),

      // ═══════════════════════════════════════════════════════
      // 7. 핵심 모듈 설명
      // ═══════════════════════════════════════════════════════
      hd('7. 핵심 모듈 설명'),

      hd('7.1 bom_parser.py', HeadingLevel.HEADING_2),
      p('Excel BOM 파일을 읽어 rows, variant_cols, struck_parts, highlighted_parts를 반환합니다.'),
      makeTable(
        ['함수/변수', '설명'],
        [
          ['parse_bom(filepath)', '메인 파싱 함수. openpyxl로 서식 인식, pandas로 데이터 읽기'],
          ['_is_strike(cell)', 'font.strike 속성으로 취소선 여부 판단'],
          ['_highlight_color(cell)', 'fill.fgColor.rgb로 음영 색상 감지 (흰색/검정 제외)'],
          ['variant_cols', '4행에서 3자리 숫자 코드를 가진 열 위치 딕셔너리 {col_idx: vc_code}'],
          ['rows', '전체 BOM 행 리스트. 각 행은 dict (level, pno, pname, desc, qtys 등 포함)'],
          ['struck_parts', '취소선 P/NO 행 리스트 (검증 제외 대상)'],
          ['highlighted_parts', '음영 행 리스트 (별도 탭에 표시)'],
        ],
        [2800, 6560]
      ),
      ...space(),

      hd('7.2 validators.py', HeadingLevel.HEADING_2),
      p('5가지 검증 로직을 실행하고 errors 리스트와 lv1_by_vc 딕셔너리를 반환합니다.'),
      makeTable(
        ['주요 상수', '설명'],
        [
          ['SPEC_KEYWORDS', '사양명 → 인식 패턴 목록 딕셔너리 (PEL_CODE 기반)'],
          ['SKIP_SPEC_KEYS', '사양 불일치 체크 제외 키워드 집합'],
          ['HW_PAT', '하드웨어 부품 인식 정규식 (SCREW, BOLT, BUCKLE 등)'],
          ['QTY_WARN_THRESHOLD', '수량 경고 기준값 (현재 50)'],
        ],
        [2800, 6560]
      ),
      ...space(),

      hd('7.3 report.py', HeadingLevel.HEADING_2),
      p('검증 결과를 8개 시트로 구성된 Excel 리포트로 저장합니다.'),
      makeTable(
        ['시트명', '내용'],
        [
          ['📋 검증 요약', 'Variant별 오류 현황 요약 및 전체 통계'],
          ['🔴 오사양 누락', 'ERROR: 1레벨 사양이 하위 부품에 없는 경우'],
          ['⚠️ 사양 불일치', 'WARNING: 하위 부품 사양이 1레벨 DESC에 없는 경우'],
          ['🟡 수량 오기입', 'WARNING: 소수점/음수/대수량 이상 수량'],
          ['❌ 레벨 중복', 'ERROR: 동일 Variant에 동일 ASSY 중복'],
          ['🔵 PNO 오류', 'WARNING: P/NO 중복 또는 누락'],
          ['🔕 취소선 폐지 부품', '검증 제외된 취소선 부품 목록'],
          ['🌟 음영 변경 부품', '음영(배경색) 감지 부품 목록'],
          ['📖 키워드 기준표', '사양 키워드 매핑 기준표'],
        ],
        [2400, 6960]
      ),
      ...space(),
      new Paragraph({ children: [new PageBreak()] }),

      // ═══════════════════════════════════════════════════════
      // 8. API 명세
      // ═══════════════════════════════════════════════════════
      hd('8. API 명세'),
      makeTable(
        ['Method', 'Endpoint', '설명', '요청', '응답'],
        [
          ['GET', '/', '웹 업로드 화면 반환', '-', 'HTML'],
          ['POST', '/validate', 'BOM 파일 검증', 'multipart/form-data (file)', 'JSON (errors, variants, report_id)'],
          ['GET', '/download/{report_id}', '검증 리포트 Excel 다운로드', 'report_id (10자리 hex)', '.xlsx 파일'],
        ],
        [900, 2000, 2200, 2500, 1760]
      ),
      ...space(),

      hd('8.1 /validate 응답 JSON 구조', HeadingLevel.HEADING_2),
      code('{'),
      code('  "filename": "BOM파일명.xlsx",'),
      code('  "variant_count": 30,          // 1레벨 Variant 수'),
      code('  "struck_count": 5,            // 취소선 부품 수'),
      code('  "highlighted_count": 12,      // 음영 부품 수'),
      code('  "err_count": 3,               // ERROR 건수'),
      code('  "warn_count": 18,             // WARNING 건수'),
      code('  "report_id": "a1b2c3d4e5",   // 다운로드 ID'),
      code('  "errors": [ {...}, ... ],     // 오류 배열'),
      code('  "lv1_variants": [ {...}, ... ] // Variant 목록'),
      code('}'),
      ...space(),
      new Paragraph({ children: [new PageBreak()] }),

      // ═══════════════════════════════════════════════════════
      // 9. 향후 개선 방향
      // ═══════════════════════════════════════════════════════
      hd('9. 향후 개선 방향'),

      hd('9.1 Oracle Cloud 배포', HeadingLevel.HEADING_2),
      num('Oracle Cloud Free Tier 인스턴스 (ARM 2코어 / 1GB RAM) 접속'),
      num('Docker 설치 후 Dockerfile 작성'),
      num('docker build 및 docker run --port 8000:8000 실행'),
      num('공인 IP 또는 도메인으로 사내 접속'),
      ...space(),

      hd('9.2 하드웨어 품번 관리 기능', HeadingLevel.HEADING_2),
      p('현재는 HW_PAT 정규식(SCREW, BOLT, BUCKLE 등 P/NAME 기반)으로 하드웨어를 식별합니다. ' +
        '향후 사이트 내 하드웨어 품번 관리 메뉴를 추가하여 P/NO 직접 등록 방식으로 전환하면 정확도가 향상됩니다.'),
      bullet('관리 화면: 하드웨어 P/NO 목록 추가/삭제/조회'),
      bullet('DB 또는 JSON 파일로 목록 영속 관리'),
      bullet('등록된 P/NO는 P/NO 중복 체크 및 사양 검증에서 자동 제외'),
      ...space(),

      hd('9.3 PLM 연동', HeadingLevel.HEADING_2),
      p('PLM 도입 후 아래 방향으로 연동이 가능합니다.'),
      bullet('PLM API에서 BOM 데이터를 직접 가져와 검증 (파일 업로드 불필요)'),
      bullet('검증 결과를 PLM에 피드백 (오류 태그 자동 등록)'),
      bullet('본 시스템의 /validate API를 PLM 웹훅으로 호출하는 방식으로 확장 가능'),
      ...space(),

      hd('9.4 사양 키워드 관리 기능', HeadingLevel.HEADING_2),
      p('현재 validators.py 소스코드에 하드코딩된 SPEC_KEYWORDS를 웹 화면에서 관리할 수 있도록 개선합니다.'),
      bullet('신규 차종/사양 추가 시 코드 수정 없이 키워드 등록 가능'),
      bullet('키워드별 활성/비활성 토글'),
      bullet('SKIP_SPEC_KEYS 관리 (검증 제외 대상 설정)'),
      ...space(),
      new Paragraph({ children: [new PageBreak()] }),

      // ═══════════════════════════════════════════════════════
      // 10. 트러블슈팅
      // ═══════════════════════════════════════════════════════
      hd('10. 트러블슈팅'),
      makeTable(
        ['증상', '원인', '해결 방법'],
        [
          ['run.bat 실행 시 python 인식 안 됨', 'PATH 미등록', 'Python 재설치 (Add to PATH 체크), 터미널 재시작'],
          ['500 Internal Server Error (브라우저)', 'Jinja2 API 버전 불일치', 'main.py TemplateResponse 파라미터 확인'],
          ['Invalid character / in sheet title', 'Excel 시트명에 / 사용', 'report.py 시트명에서 / 제거 (P/NO → PNO)'],
          ['ModuleNotFoundError: xlrd', '.xls 파일 지원 라이브러리 없음', 'pip install xlrd 실행'],
          ['취소선/음영이 인식 안 됨', 'xlsx 대신 xls 저장된 경우', '파일을 xlsx로 다시 저장 후 업로드'],
          ['P/NO 중복 경고가 너무 많음', '하드웨어 부품 미인식', 'HW_PAT 패턴에 해당 부품명 키워드 추가'],
          ['사양 경고가 오탐임', '키워드가 너무 광범위', 'SPEC_KEYWORDS 패턴 수정 또는 SKIP_SPEC_KEYS 추가'],
        ],
        [2400, 2200, 4760]
      ),
      ...space(),

      // ═══════════════════════════════════════════════════════
      // 11. 수정 이력
      // ═══════════════════════════════════════════════════════
      hd('11. 수정 이력'),
      makeTable(
        ['버전', '날짜', '주요 변경 내용'],
        [
          ['v1.0', '2025-06-15', '최초 개발 — 웹 업로드/검증/리포트 기본 기능 구현'],
          ['v1.0', '2025-06-15', '취소선 부품 인식 및 검증 제외 처리'],
          ['v1.0', '2025-06-15', 'SBR 키워드 수정 (SEAT BELT 제외, 물리 부품 오탐 제거)'],
          ['v1.0', '2025-06-15', 'P/NO 중복 로직 개선 (동일 L2 ASSY 하위만 중복 판단)'],
          ['v1.0', '2025-06-15', '하드웨어 P/NO 중복 체크 제외 (HW_PAT 확장)'],
          ['v1.0', '2025-06-15', 'PEL_CODE 기준 사양 키워드 전체 반영 (THORAX, A/LEA, WMR 등)'],
        ],
        [900, 1600, 6860]
      ),
      ...space(2),

      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 400 },
        shading: { fill: NAVY, type: ShadingType.CLEAR },
        children: [new TextRun({
          text: '  DYA Co., Ltd.  |  BOM 자동 검증 시스템 개발자 사용 설명서  v1.0  ',
          font: '맑은 고딕', size: 18, color: WHITE
        })]
      }),
    ]
  }]
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync('C:\\Users\\shkim\\Desktop\\ERP\\EBOM\\DYA_BOM검증시스템_개발자설명서_v1.0.docx', buffer);
  console.log('완료: DYA_BOM검증시스템_개발자설명서_v1.0.docx');
});
