"""
부품사양서 → BOM 자동 생성기
- "옵션제약" 영역의 PEL CODE를 PEL 마스터에서 룩업하여 1레벨 BOM 생성
- 헤더 위치 / OPT 컬럼 수 동적 감지 (양식 변경 대응)
"""
import os
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
import pandas as pd
from datetime import datetime


def parse_part_spec(filepath: str) -> dict:
    """부품사양서를 파싱해서 VC별 PEL CODE 매트릭스 추출."""
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb.active

    # "UPG VC" 가 있는 행을 찾아 헤더 시작점 자동 감지
    group_row = None
    for r in range(1, min(ws.max_row + 1, 30)):
        v = ws.cell(r, 1).value
        if v and 'UPG VC' in str(v):
            group_row = r
            break
    if not group_row:
        raise ValueError('헤더를 찾을 수 없습니다 (1열에 "UPG VC" 없음)')

    sub_row    = group_row + 1       # COMB / EXCL 등 서브 섹션
    detail_row = group_row + 2       # OPT1, OPT2 ... 라벨 행
    data_start = group_row + 3

    # "옵션제약" 그룹의 시작 컬럼 찾기 (group_row에서)
    opt_start_col = None
    for c in range(1, ws.max_column + 1):
        v = ws.cell(group_row, c).value
        if v and '옵션제약' in str(v):
            opt_start_col = c
            break
    if not opt_start_col:
        raise ValueError('"옵션제약" 그룹을 찾을 수 없습니다')

    # 옵션제약 안에서도 첫 서브 섹션 (보통 COMB = 적용 옵션) 만 추출.
    # EXCL (제외) 섹션은 BOM 1레벨 생성 대상이 아니므로 제외.
    initial_sub = ws.cell(sub_row, opt_start_col).value
    initial_sub = str(initial_sub).strip() if initial_sub else ''

    opt_cols = []
    for c in range(opt_start_col, ws.max_column + 1):
        # group_row 헤더가 새로 나오면 다른 그룹으로 넘어간 것 → 종료
        grp = ws.cell(group_row, c).value
        if c > opt_start_col and grp and str(grp).strip():
            break
        # sub_row 가 초기값과 다른 값으로 바뀌면 같은 그룹 내 다른 섹션(EXCL) → 종료
        sub = ws.cell(sub_row, c).value
        if c > opt_start_col and sub and str(sub).strip() and str(sub).strip() != initial_sub:
            break
        lbl = ws.cell(detail_row, c).value
        if not lbl or not str(lbl).upper().startswith('OPT'):
            break
        opt_cols.append((c, str(lbl).strip()))
    if not opt_cols:
        raise ValueError('OPT 컬럼을 찾을 수 없습니다')

    # 데이터 추출
    rows = []
    for r in range(data_start, ws.max_row + 1):
        vc_raw = ws.cell(r, 1).value
        if vc_raw is None:
            continue
        vc = str(vc_raw).strip()
        if not vc:
            continue

        # OPT 슬롯별 PEL CODE
        opt_pels = []
        for col_idx, opt_label in opt_cols:
            pel = ws.cell(r, col_idx).value
            if pel is not None and str(pel).strip():
                opt_pels.append({'opt': opt_label, 'pel_code': str(pel).strip()})
        if opt_pels:
            rows.append({'vc': vc, 'opts': opt_pels})

    return {
        'vehicle_info': str(ws.cell(2, 1).value or ''),
        'opt_count': len(opt_cols),
        'opt_labels': [lbl for _, lbl in opt_cols],
        'vcs': rows,
    }


def load_pel_master(pel_path: str) -> dict:
    """PEL 마스터 엑셀 → {pel_code: {fields...}} 딕셔너리."""
    if not os.path.exists(pel_path):
        return {}
    df = pd.read_excel(pel_path, sheet_name=0).fillna('')
    cols = [str(c) for c in df.columns]

    # CODE 컬럼 찾기 (대소문자 무관)
    code_col = None
    for c in cols:
        if str(c).strip().upper() == 'CODE':
            code_col = c
            break
    if not code_col:
        # fallback: 두 번째 컬럼을 CODE로 가정
        code_col = cols[1] if len(cols) >= 2 else cols[0]

    master = {}
    for _, row in df.iterrows():
        code = str(row[code_col]).strip()
        if not code:
            continue
        master[code] = {c: str(row[c]) for c in cols}
    return {'data': master, 'columns': cols, 'code_col': code_col}


def generate_bom(part_spec_path: str, pel_path: str, output_path: str) -> dict:
    """부품사양서 + PEL 마스터 → BOM 엑셀 생성.

    구조:
      - 메인 시트: VC당 1행. 조합 명칭은 OPT 순서대로 '+' 로 연결.
        예) VC 001 → "SAB + CLOTH1 + H_P_COVER + H_UP/DN + HTR + USB(27W)"
      - 상세 시트: 검증용으로 VC × OPT 별 1행씩.
    """
    spec = parse_part_spec(part_spec_path)
    master_info = load_pel_master(pel_path)
    master = master_info.get('data', {})
    master_cols = master_info.get('columns', [])

    def pick_col(candidates):
        for c in master_cols:
            cs = str(c)
            cu = cs.strip().upper()
            for cand in candidates:
                if cand in cu or cand in cs:
                    return c
        return None

    name_col = pick_col(['사양', '명칭', 'NAME', 'SPEC']) or (master_cols[2] if len(master_cols) > 2 else None)
    desc_col = pick_col(['설명', 'DESCRIPTION', 'DESC']) or (master_cols[3] if len(master_cols) > 3 else None)
    category_col = pick_col(['비고', '분류', 'CATEGORY', 'CLASS', 'NOTE', 'REMARK']) or (master_cols[4] if len(master_cols) > 4 else None)

    wb = openpyxl.Workbook()
    thin = Side(border_style='thin', color='CCCCCC')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    bad_fill = PatternFill('solid', fgColor='FFCDD2')
    warn_fill = PatternFill('solid', fgColor='FFF8E1')

    stats = {'pel_total': 0, 'matched': 0, 'unmatched': 0,
             'unmatched_codes': set(),
             'vc_count': len(spec['vcs']), 'opt_count': spec['opt_count'],
             'fully_matched_vc': 0, 'partial_vc': 0}

    # ── 1. 메인 시트: VC별 조합 BOM ─────────────────────────────────
    ws_main = wb.active
    ws_main.title = 'BOM (VC별)'
    headers = ['Level', 'VC', '조합 사양 (명칭)', 'PEL CODE 목록', '구성 OPT', 'PEL 개수', '매칭 상태', '미매칭 코드']
    for c, h in enumerate(headers, 1):
        cell = ws_main.cell(1, c, h)
        cell.font = Font(bold=True, color='FFFFFF', size=11)
        cell.fill = PatternFill('solid', fgColor='002C5F')
        cell.alignment = Alignment(horizontal='center', vertical='center')

    r = 2
    for vc_block in spec['vcs']:
        vc = vc_block['vc']
        names, codes, opts_used, unmatched_in_vc = [], [], [], []
        for op in vc_block['opts']:
            pel = op['pel_code']
            opts_used.append(op['opt'])
            codes.append(pel)
            entry = master.get(pel)
            if entry:
                # cascade fallback: 명칭 → 설명 → 코드
                nm = str(entry.get(name_col, '') if name_col else '').strip()
                if not nm:
                    nm = str(entry.get(desc_col, '') if desc_col else '').strip()
                names.append(nm if nm else pel)
                stats['matched'] += 1
            else:
                names.append(f'?{pel}?')
                unmatched_in_vc.append(pel)
                stats['unmatched'] += 1
                stats['unmatched_codes'].add(pel)
            stats['pel_total'] += 1

        combined_desc = ' + '.join(names) if names else '(빈 VC)'
        combined_codes = ', '.join(codes)
        opts_list = ', '.join(opts_used)
        if not unmatched_in_vc:
            status = '✅ 완전매칭'
            row_fill = None
            stats['fully_matched_vc'] += 1
        else:
            status = f'⚠ 부분매칭 ({len(unmatched_in_vc)}개 누락)'
            row_fill = warn_fill
            stats['partial_vc'] += 1

        row_data = [1, vc, combined_desc, combined_codes, opts_list,
                    len(codes), status, ', '.join(unmatched_in_vc)]
        for c, val in enumerate(row_data, 1):
            cell = ws_main.cell(r, c, val)
            cell.border = border
            cell.alignment = Alignment(vertical='center', wrap_text=(c == 3))
            if row_fill:
                cell.fill = row_fill
        r += 1

    widths = [8, 12, 70, 50, 28, 10, 22, 28]
    for i, w in enumerate(widths, 1):
        ws_main.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    ws_main.row_dimensions[1].height = 28
    ws_main.freeze_panes = 'C2'

    # ── 2. 상세 시트: VC × OPT 슬롯별 (검증용) ──────────────────────
    ws_detail = wb.create_sheet('상세 (슬롯별)')
    detail_headers = ['VC', 'OPT', 'PEL CODE', '명칭', '설명', '분류', '매칭']
    for c, h in enumerate(detail_headers, 1):
        cell = ws_detail.cell(1, c, h)
        cell.font = Font(bold=True, color='FFFFFF', size=11)
        cell.fill = PatternFill('solid', fgColor='002C5F')
        cell.alignment = Alignment(horizontal='center', vertical='center')

    r = 2
    for vc_block in spec['vcs']:
        vc = vc_block['vc']
        for op in vc_block['opts']:
            pel = op['pel_code']
            entry = master.get(pel)
            if entry:
                name = entry.get(name_col, '') if name_col else ''
                desc = entry.get(desc_col, '') if desc_col else ''
                category = entry.get(category_col, '') if category_col else ''
                status = '매칭'
            else:
                name = '(미매칭)'
                desc = ''
                category = ''
                status = '미매칭'
            row_data = [vc, op['opt'], pel, name, desc, category, status]
            for c, val in enumerate(row_data, 1):
                cell = ws_detail.cell(r, c, val)
                cell.border = border
                cell.alignment = Alignment(vertical='center')
                if status == '미매칭':
                    cell.fill = bad_fill
            r += 1

    widths = [12, 10, 14, 30, 38, 16, 10]
    for i, w in enumerate(widths, 1):
        ws_detail.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    ws_detail.freeze_panes = 'A2'

    # ── 3. 요약 시트 (맨 앞) ────────────────────────────────────────
    ws_sum = wb.create_sheet('요약', 0)
    ws_sum['A1'] = 'BOM 자동 생성 결과'
    ws_sum['A1'].font = Font(bold=True, size=16, color='002C5F')
    ws_sum['A3'] = '생성일시:';      ws_sum['B3'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ws_sum['A4'] = '원본 부품사양서:'; ws_sum['B4'] = os.path.basename(part_spec_path)
    ws_sum['A5'] = '차종/UPG:';      ws_sum['B5'] = spec.get('vehicle_info', '')
    ws_sum['A7'] = 'VC 수 (=BOM 행 수):'; ws_sum['B7'] = stats['vc_count']
    ws_sum['A8'] = 'OPT 슬롯 수:';        ws_sum['B8'] = stats['opt_count']
    ws_sum['A9'] = '사용된 PEL 인스턴스:'; ws_sum['B9'] = stats['pel_total']

    ws_sum['A11'] = '완전매칭 VC:';   ws_sum['B11'] = stats['fully_matched_vc']
    ws_sum['A11'].font = Font(bold=True, color='2E7D32')
    ws_sum['B11'].font = Font(bold=True, color='2E7D32')
    ws_sum['A12'] = '부분매칭 VC:';   ws_sum['B12'] = stats['partial_vc']
    if stats['partial_vc']:
        ws_sum['A12'].font = Font(bold=True, color='E65100')
        ws_sum['B12'].font = Font(bold=True, color='E65100')

    ws_sum['A14'] = 'PEL 매칭 성공:'; ws_sum['B14'] = stats['matched']
    ws_sum['A15'] = 'PEL 매칭 실패:'; ws_sum['B15'] = stats['unmatched']
    if stats['unmatched']:
        ws_sum['A15'].font = Font(bold=True, color='C62828')
        ws_sum['B15'].font = Font(bold=True, color='C62828')
    ws_sum['A16'] = '누락 코드 종류:'; ws_sum['B16'] = len(stats['unmatched_codes'])

    if stats['unmatched_codes']:
        ws_sum['A18'] = '미매칭 PEL CODE 목록:'
        ws_sum['A18'].font = Font(bold=True, color='C62828')
        for i, code in enumerate(sorted(stats['unmatched_codes']), start=19):
            ws_sum.cell(i, 1, code).fill = bad_fill

    for col_letter, w in zip(['A', 'B'], [24, 60]):
        ws_sum.column_dimensions[col_letter].width = w

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
    }
