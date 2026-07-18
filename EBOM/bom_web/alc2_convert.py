# -*- coding: utf-8 -*-
"""DYA ALC-2 변환 엔진 (DYA_ALC2_LOCAL/app.py 웹 포팅).

핵심: HKMC Q파트 종합 + ALC 원본 5종(FRT LH/RH, RR BACK LH/CUSH/RH) + 통합 ALC2 마스터
 → 각 생산행(KEY02~06 = KMC20)을 판정:
    · 원본누락 : KEY가 해당 시트 ALC 코드집에 없음
    · 기존매칭 : KMC20이 통합 마스터에 있음 → 기존 DYA ALC-2 재사용
    · 신규승인필요 : 마스터에 없음 → 임시코드(AAxxJ) 부여, 수동승인 대상

app.py는 config.json의 열 위치(A,B,C)를 썼지만, 여기서는 **헤더 이름 자동인식**으로
열을 찾으므로 HKMC 양식의 열이 바뀌어도 동작한다.
"""
import re
import openpyxl

# ALC 원본 슬롯 순서 = KEY02~KEY06 순서와 1:1 대응
ALC_SLOTS = ['FRT LH', 'FRT RH', 'RR BACK LH', 'RR CUSH', 'RR BACK RH']


def _cell(ws, r, c):
    if c is None:
        return ''
    v = ws.cell(r, c).value
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _find_header(ws, keys, maxr=20):
    """지정 키워드가 모두 들어있는 헤더 행 번호를 찾는다(대소문자 무시, 부분일치)."""
    keys = [k.upper() for k in keys]
    for r in range(1, min(maxr, ws.max_row) + 1):
        rowvals = [_cell(ws, r, c).upper() for c in range(1, ws.max_column + 1)]
        if all(any(k in v for v in rowvals) for k in keys):
            return r
    return None


def read_qpart(path):
    """Q파트 종합 → [{vehicle, line, key01(국가), keys[가변], kmc20(가변길이), excel_row}].
       KEY02부터 값 있는 마지막 KEY열까지 조합. X 표기 좌석은 '****'로 치환(행 유지).
       예) [507S,607S,604S,X,704S] → '507S607S604S****704S' (20). NQ5 7그룹이면 28자리."""
    ws = openpyxl.load_workbook(path, data_only=True).active
    hr = _find_header(ws, ['차종', 'KEY01', 'KEY02'])
    if hr is None:
        raise ValueError('Q파트에서 헤더(차종/KEY01~)를 찾지 못했습니다.')
    hdr = {_cell(ws, hr, c).upper(): c for c in range(1, ws.max_column + 1)}
    cvehicle, cline, ckey01 = hdr.get('차종'), hdr.get('라인'), hdr.get('KEY01')
    key_cols = [hdr.get('KEY%02d' % i) for i in range(2, 10)]
    key_cols = [c for c in key_cols if c]
    rows = []
    for r in range(hr + 1, ws.max_row + 1):
        vehicle = _cell(ws, r, cvehicle)
        if not vehicle:
            continue
        raw = [_cell(ws, r, c) for c in key_cols]
        while raw and raw[-1] == '':          # 후미 빈칸 제거 → 차종별 그룹 수
            raw.pop()
        if len(raw) < 2:
            continue
        groups, ok = [], True
        for v in raw:
            vu = v.strip().upper()
            if vu == 'X':
                groups.append('****')          # X = 해당 좌석 미해당 → ****
            elif re.fullmatch(r'[A-Za-z0-9]{4}', vu):
                groups.append(vu)
            else:
                ok = False; break              # 예상외 값(중간 빈칸 등) → 제외
        if not ok:
            continue
        rows.append({'vehicle': vehicle, 'line': _cell(ws, r, cline),
                     'key01': _cell(ws, r, ckey01), 'keys': groups,
                     'kmc20': ''.join(groups), 'excel_row': r})
    return rows


def read_alc(path):
    """ALC 코드집 → 4자리 CODE 집합."""
    ws = openpyxl.load_workbook(path, data_only=True).active
    hr = _find_header(ws, ['CODE', 'PART NO'])
    if hr is None:
        raise ValueError('ALC 코드집에서 헤더(CODE/PART NO)를 찾지 못했습니다.')
    hd = {_cell(ws, hr, c).upper(): c for c in range(1, ws.max_column + 1)}
    cc = hd.get('CODE')
    codes = set()
    for r in range(hr + 1, ws.max_row + 1):
        cd = _cell(ws, r, cc).upper()
        if re.fullmatch(r'[A-Z0-9]{4}', cd):
            codes.add(cd)
    return codes


def read_master(path):
    """통합 ALC2 마스터 → {KMC20(20자리): {alc2(5자리)}}. 헤더로 DYA/KMC 열 자동탐지."""
    ws = openpyxl.load_workbook(path, data_only=True).active
    hr = _find_header(ws, ['ALC-2 CODE', 'KMC ALC-2'])
    if hr is None:
        return {}
    hd = {_cell(ws, hr, c).upper(): c for c in range(1, ws.max_column + 1)}
    dcol = next((c for k, c in hd.items() if 'ALC-2 CODE' in k and 'KMC' not in k), None)
    kcol = next((c for k, c in hd.items() if 'KMC ALC-2' in k), None)
    master = {}
    if not dcol or not kcol:
        return master
    for r in range(hr + 1, ws.max_row + 1):
        a = _cell(ws, r, dcol).upper()
        k = re.sub(r'\s', '', _cell(ws, r, kcol)).upper()
        if re.fullmatch(r'[A-Z0-9]{5}', a) and re.fullmatch(r'[A-Z0-9]{20}', k):
            master[k] = {'alc2': a, 'row': r}
    return master


def convert(qpart_path, alc_paths, master_path):
    """alc_paths: {slot명: path} (ALC_SLOTS 5종). master_path: 통합 마스터.
       반환: {rows:[...판정...], stats:{...}}."""
    qrows = read_qpart(qpart_path)
    maps = []
    for slot in ALC_SLOTS:
        p = alc_paths.get(slot)
        maps.append(read_alc(p) if p else set())
    master = read_master(master_path) if master_path else {}
    used = {v['alc2'] for v in master.values()}
    seq = 1
    results = []
    for q in qrows:
        # 앞 5개 KEY만 5개 ALC 코드집과 대응(**** 좌석·ALC없는 뒤 KEY는 검사 제외)
        missing = []
        for i in range(min(len(ALC_SLOTS), len(q['keys']))):
            k = q['keys'][i]
            if k == '****':
                continue
            if maps[i] and k not in maps[i]:
                missing.append(k)
        hit = master.get(q['kmc20'])
        if missing:
            status, alc2, detail = '원본누락', '', ', '.join(missing)
        elif hit:
            status, alc2, detail = '기존매칭', hit['alc2'], '정상'
        else:
            while ('AA%02dJ' % seq) in used:
                seq += 1
            alc2 = 'AA%02dJ' % seq
            used.add(alc2); seq += 1
            status, detail = '신규승인필요', '마스터 신규 조합(원단/사양 확인 후 확정)'
        results.append({'vehicle': q['vehicle'], 'line': q['line'], 'key01': q.get('key01', ''),
                        'keys': q['keys'], 'kmc20': q['kmc20'], 'alc2': alc2,
                        'status': status, 'detail': detail})
    stats = {
        'total': len(results),
        'matched': sum(r['status'] == '기존매칭' for r in results),
        'new': sum(r['status'] == '신규승인필요' for r in results),
        'missing': sum(r['status'] == '원본누락' for r in results),
    }
    return {'rows': results, 'stats': stats}
