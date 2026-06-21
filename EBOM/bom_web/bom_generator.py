"""
부품사양서 → BOM 자동 생성기 (표준화 양식 기반)

흐름:
  1) 부품사양서 파싱
     - 사양군 (C~H, K2~K7) ○ → 국가/지역 코드  (예: "일반+중동")
     - 옵션제약 COMB OPT1~OPT21 PEL CODE → DESCRIPTION (PEL 마스터 룩업, +로 연결)
     - B2 옆 "Level1 P/NO" 사용자 입력값 → 시트 어셈블리 1레벨 품번
  2) 표준화 양식 로드 (활성 리비전)
  3) 양식 셀에 데이터 채워서 저장
     - 각 VC당 1행 (rowN = 8 + VC인덱스)
     - 매트릭스: W열부터 가로로 VC당 1컬럼

양식이 자주 바뀌므로 컬럼은 헤더 텍스트로 동적 탐색.
"""
import os, shutil
from copy import copy as _copy_style
import openpyxl
from openpyxl.styles import Font, PatternFill
import pandas as pd
from datetime import datetime


# ════════════════════════════════════════════════════════════════════════════
# 1. 부품사양서 파싱
# ════════════════════════════════════════════════════════════════════════════
def _is_mark(v) -> bool:
    """○, ●, * 등 마킹 문자 판정."""
    if v is None: return False
    s = str(v).strip()
    return bool(s) and s in ('○', '●', '◯', '*', '✓', 'O', 'o', 'V', 'v')


def parse_part_spec(filepath: str) -> dict:
    """부품사양서 → VC 리스트.

    각 VC: {
      'vc': '001',
      'region': '일반+중동',
      'opts': [{'opt': 'OPT1', 'pel_code': '5693A3'}, ...],
    }
    + 헤더 메타: opt_labels, level1_pno, vehicle_info, ...
    """
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb.active

    # ── 1) 헤더 행 자동 탐색 (A열에 "UPG VC" 있는 행)
    group_row = None
    for r in range(1, min(ws.max_row + 1, 30)):
        v = ws.cell(r, 1).value
        if v and 'UPG VC' in str(v):
            group_row = r
            break
    if not group_row:
        raise ValueError('헤더를 찾을 수 없습니다 (1열에 "UPG VC" 없음)')

    sub_row    = group_row + 1   # COMB / EXCL
    detail_row = group_row + 2   # 라벨 (일반, 중동, OPT1, OPT2 ...)
    data_start = group_row + 3

    # ── 2) "사양군" 그룹 열 범위 탐색
    spec_group_start = None
    spec_group_end = None
    opt_start_col = None
    for c in range(1, ws.max_column + 1):
        v = ws.cell(group_row, c).value
        if not v: continue
        s = str(v).strip()
        if s == '사양군':
            spec_group_start = c
        elif spec_group_start and spec_group_end is None and s and s != '사양군':
            spec_group_end = c - 1
        if '옵션제약' in s:
            opt_start_col = c
            if spec_group_end is None and spec_group_start:
                spec_group_end = c - 1
            break
    if not opt_start_col:
        raise ValueError('"옵션제약" 그룹을 찾을 수 없습니다')

    # ── 3) 옵션제약 안에서 COMB 서브섹션만 추출
    initial_sub = ws.cell(sub_row, opt_start_col).value
    initial_sub = str(initial_sub).strip() if initial_sub else ''

    opt_cols = []
    for c in range(opt_start_col, ws.max_column + 1):
        grp = ws.cell(group_row, c).value
        if c > opt_start_col and grp and str(grp).strip():
            break
        sub = ws.cell(sub_row, c).value
        if c > opt_start_col and sub and str(sub).strip() and str(sub).strip() != initial_sub:
            break
        lbl = ws.cell(detail_row, c).value
        if not lbl or not str(lbl).upper().startswith('OPT'):
            break
        opt_cols.append((c, str(lbl).strip()))
    if not opt_cols:
        raise ValueError('OPT 컬럼을 찾을 수 없습니다')

    # ── 4) 사양군 라벨 (행 7) 미리 추출
    region_labels = {}
    if spec_group_start:
        # spec_group_end가 None이면 옵션제약 직전까지
        end = spec_group_end or (opt_start_col - 1)
        for c in range(spec_group_start, end + 1):
            lbl = ws.cell(detail_row, c).value
            if lbl: region_labels[c] = str(lbl).strip()

    # ── 5) B2 옆에서 "Level1 P/NO" 라벨 + 값 탐색
    level1_pno = ''
    for r in range(1, 6):
        for c in range(1, 20):
            v = ws.cell(r, c).value
            if v and 'Level1 P/NO' in str(v):
                # 옆 셀 (오른쪽) 또는 같은 셀 안에 ":" 뒤 값
                s = str(v).strip()
                if ':' in s and s.split(':', 1)[1].strip():
                    level1_pno = s.split(':', 1)[1].strip()
                else:
                    nxt = ws.cell(r, c + 1).value
                    if nxt: level1_pno = str(nxt).strip()
                break
        if level1_pno: break

    # ── 6) 데이터 행: VC별로 사양군 ○ 라벨 모으기 + OPT PEL CODE 모으기
    rows = []
    for r in range(data_start, ws.max_row + 1):
        vc_raw = ws.cell(r, 1).value
        if vc_raw is None: continue
        vc = str(vc_raw).strip()
        if not vc: continue

        # 사양군 영역 ○ 라벨
        region_parts = []
        if spec_group_start and region_labels:
            end = spec_group_end or (opt_start_col - 1)
            for c in range(spec_group_start, end + 1):
                if _is_mark(ws.cell(r, c).value):
                    lab = region_labels.get(c, '')
                    if lab: region_parts.append(lab)
        region = '+'.join(region_parts)

        # OPT PEL CODE
        opts = []
        for col_idx, opt_label in opt_cols:
            pel = ws.cell(r, col_idx).value
            if pel is not None and str(pel).strip():
                opts.append({'opt': opt_label, 'pel_code': str(pel).strip()})

        if opts:
            rows.append({'vc': vc, 'region': region, 'opts': opts})

    return {
        'vehicle_info': str(ws.cell(2, 1).value or ''),
        'opt_count': len(opt_cols),
        'opt_labels': [lbl for _, lbl in opt_cols],
        'level1_pno': level1_pno,
        'spec_group_range': (spec_group_start, spec_group_end),
        'vcs': rows,
    }


# ════════════════════════════════════════════════════════════════════════════
# 2. PEL 마스터 로드
# ════════════════════════════════════════════════════════════════════════════
def load_pel_master(pel_path: str) -> dict:
    if not os.path.exists(pel_path):
        return {'data': {}, 'columns': [], 'code_col': None}
    df = pd.read_excel(pel_path, sheet_name=0).fillna('')
    cols = [str(c) for c in df.columns]

    code_col = None
    for c in cols:
        if str(c).strip().upper() == 'CODE':
            code_col = c
            break
    if not code_col:
        code_col = cols[1] if len(cols) >= 2 else (cols[0] if cols else None)

    master = {}
    for _, row in df.iterrows():
        code = str(row[code_col]).strip() if code_col else ''
        if not code: continue
        master[code] = {c: str(row[c]) for c in cols}
    return {'data': master, 'columns': cols, 'code_col': code_col}


# ════════════════════════════════════════════════════════════════════════════
# 3. 표준화 양식 채우기
# ════════════════════════════════════════════════════════════════════════════
DATA_START_ROW   = 8   # A8부터 VC 데이터 시작
MATRIX_START_COL = 23  # W열 = 23
MATRIX_VC_ROW    = 4   # W4 = VC 번호
MATRIX_REGION_ROW = 5  # W5 = 지역
MATRIX_LV1_ROW   = 6   # W6 = 1레벨 P/NO


def generate_bom_from_template(part_spec_path: str, pel_path: str,
                                template_path: str, output_path: str) -> dict:
    """활성 표준화 양식을 로드해서 부품사양서 데이터로 채움."""
    spec = parse_part_spec(part_spec_path)
    master_info = load_pel_master(pel_path)
    master = master_info['data']
    master_cols = master_info['columns']

    def pick_col(cands):
        for c in master_cols:
            cu = str(c).strip().upper()
            for cand in cands:
                if cand in cu or cand in str(c):
                    return c
        return None

    spec_col = pick_col(['사양', '명칭', 'NAME', 'SPEC']) or (master_cols[2] if len(master_cols) > 2 else None)

    def pel_to_name(code: str) -> str:
        """PEL 코드 → 사양(spec) 텍스트.
        사양이 비어 있으면 원본 코드 그대로 유지 (설명으로 fallback 하지 않음 — 사용자 명시)."""
        entry = master.get(code)
        if not entry: return code
        if spec_col:
            sp = str(entry.get(spec_col, '')).strip()
            if sp: return sp
        return code

    # ── 템플릿 복사해서 작업본 만들기 (서식 보존)
    shutil.copy2(template_path, output_path)
    wb = openpyxl.load_workbook(output_path)
    ws = wb.active

    # ── 행 8 (첫 데이터 행)의 서식 + 행 높이 캐싱 → 데이터 행 추가 시 복제용
    ref_row = DATA_START_ROW
    ref_height = ws.row_dimensions[ref_row].height
    ref_styles = {}  # {col_idx: (font, fill, border, alignment, number_format)}
    last_styled_col = max(ws.max_column, MATRIX_START_COL + len(spec['vcs']) + 10)
    for c in range(1, last_styled_col + 1):
        cell = ws.cell(ref_row, c)
        if cell.has_style:
            ref_styles[c] = (
                _copy_style(cell.font),
                _copy_style(cell.fill),
                _copy_style(cell.border),
                _copy_style(cell.alignment),
                cell.number_format,
            )
    # L열(P/NAME) 기본값 — 양식의 placeholder 그대로 사용
    default_pname = str(ws.cell(ref_row, 12).value or 'SEAT ASSY-FR, LH')

    def _apply_ref_style(row_idx: int):
        """ref_row 의 서식을 row_idx 에 통째로 복제 (행 높이 포함)."""
        if row_idx == ref_row: return
        if ref_height is not None:
            ws.row_dimensions[row_idx].height = ref_height
        for c, (font, fill, border, align, fmt) in ref_styles.items():
            cell = ws.cell(row_idx, c)
            cell.font = _copy_style(font)
            cell.fill = _copy_style(fill)
            cell.border = _copy_style(border)
            cell.alignment = _copy_style(align)
            cell.number_format = fmt

    stats = {
        'vc_count': len(spec['vcs']),
        'opt_count': spec['opt_count'],
        'pel_total': 0, 'matched': 0, 'unmatched': 0,
        'unmatched_codes': set(),
        'fully_matched_vc': 0, 'partial_vc': 0,
        'level1_pno': spec['level1_pno'],
        'level1_pno_missing': not bool(spec['level1_pno']),
        'no_region_vc': 0,
    }

    bad_fill = PatternFill('solid', fgColor='FFCDD2')
    warn_fill = PatternFill('solid', fgColor='FFF8E1')

    # ── VC별로 양식 채우기
    for idx, vc_block in enumerate(spec['vcs']):
        row = DATA_START_ROW + idx
        col = MATRIX_START_COL + idx
        vc = vc_block['vc']
        region = vc_block['region']

        # DESCRIPTION 만들기
        names, unmatched_in_vc = [], []
        for op in vc_block['opts']:
            pel = op['pel_code']
            entry = master.get(pel)
            if entry:
                stats['matched'] += 1
                names.append(pel_to_name(pel))
            else:
                stats['unmatched'] += 1
                stats['unmatched_codes'].add(pel)
                unmatched_in_vc.append(pel)
                names.append(f'?{pel}?')
            stats['pel_total'] += 1
        description = ' + '.join(names) if names else '(빈 VC)'

        if not region: stats['no_region_vc'] += 1
        if unmatched_in_vc: stats['partial_vc'] += 1
        else: stats['fully_matched_vc'] += 1

        # 좌측 데이터 행
        _apply_ref_style(row)                                    # 서식 + 행 높이 복제
        ws.cell(row, 1).value = vc                              # A: VC 번호
        ws.cell(row, 2).value = 1                                # B: LEVEL = 1
        if spec['level1_pno']:
            ws.cell(row, 10).value = spec['level1_pno']          # J: 1레벨 P/NO
        ws.cell(row, 12).value = default_pname                   # L: P/NAME (양식 placeholder)
        ws.cell(row, 14).value = description                     # N: DESCRIPTION
        if region:
            ws.cell(row, 21).value = region                      # U: 지역
        ws.cell(row, 22).value = 'ASSY'                          # V: MATERIAL

        if unmatched_in_vc:
            ws.cell(row, 14).fill = warn_fill

        # 매트릭스 상단 (W4, W5, W6)
        ws.cell(MATRIX_VC_ROW,     col).value = vc
        if region:
            ws.cell(MATRIX_REGION_ROW, col).value = region
        if spec['level1_pno']:
            ws.cell(MATRIX_LV1_ROW,    col).value = spec['level1_pno']

        # 교차점 QTY 마커 (대각선)
        ws.cell(row, col).value = 1

    # ── 템플릿이 갖고 있던 placeholder 행 정리 (실제 VC 수보다 많을 때)
    last_used = DATA_START_ROW + len(spec['vcs']) - 1
    for r in range(last_used + 1, ws.max_row + 1):
        v = ws.cell(r, 12).value  # L열 placeholder 확인
        if v is None: continue
        # 데이터 값만 비우고 서식은 유지
        for c in (1, 2, 10, 12, 14, 21, 22):
            ws.cell(r, c).value = None

    wb.save(output_path)

    return {
        'total': stats['pel_total'],
        'matched': stats['matched'],
        'unmatched': stats['unmatched'],
        'unmatched_codes': sorted(stats['unmatched_codes']),
        'unmatched_unique_count': len(stats['unmatched_codes']),
        'vc_count': stats['vc_count'],
        'opt_count': stats['opt_count'],
        'fully_matched_vc': stats['fully_matched_vc'],
        'partial_vc': stats['partial_vc'],
        'opt_labels': spec['opt_labels'],
        'vehicle_info': spec['vehicle_info'],
        'level1_pno': stats['level1_pno'],
        'level1_pno_missing': stats['level1_pno_missing'],
        'no_region_vc': stats['no_region_vc'],
    }


# ════════════════════════════════════════════════════════════════════════════
# 4. 호환용 진입점 — main.py가 부르는 generate_bom(...)
# ════════════════════════════════════════════════════════════════════════════
def generate_bom(part_spec_path: str, pel_path: str, output_path: str,
                 template_path: str = None) -> dict:
    """활성 표준화 양식이 있으면 그걸 기반으로 채움.
    없으면 명시적 에러 (사용자가 admin에게 양식 등록 요청해야 함)."""
    if not template_path or not os.path.exists(template_path):
        raise FileNotFoundError(
            '활성 표준화 BOM 양식이 등록되지 않았습니다. '
            '관리자에게 [📚 리비전 관리] 메뉴에서 양식 등록을 요청하세요.'
        )
    return generate_bom_from_template(part_spec_path, pel_path,
                                       template_path, output_path)
