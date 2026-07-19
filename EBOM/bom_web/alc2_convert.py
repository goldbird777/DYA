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
import os
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


_MASTER_CACHE = {}   # path -> (mtime, size, master)


def read_master(path):
    """통합 ALC2 대장 → {KMC코드: {alc2(5자리)}}. 헤더로 DYA/KMC 열 자동탐지.
       대장(3천행×283열)은 매 실행마다 바뀌지 않으므로 mtime 기준으로 캐시한다."""
    try:
        st = os.stat(path)
        ck = (st.st_mtime, st.st_size)
        hit = _MASTER_CACHE.get(path)
        if hit and hit[0] == ck:
            return hit[1]
    except OSError:
        ck = None
    rows = _load_rows(path)   # read_only 스트리밍
    hi = _find_hdr_idx(rows, ['ALC-2 CODE', 'KMC ALC-2'])
    master = {}
    if hi is None:
        return master
    hd = {_t(v).upper(): i for i, v in enumerate(rows[hi])}
    dcol = next((i for k, i in hd.items() if 'ALC-2 CODE' in k and 'KMC' not in k), None)
    kcol = next((i for k, i in hd.items() if 'KMC ALC-2' in k), None)
    if dcol is None or kcol is None:
        return master
    for r in range(hi + 1, len(rows)):
        row = rows[r]
        if kcol >= len(row) or dcol >= len(row):
            continue
        a = _t(row[dcol]).upper()
        k = re.sub(r'\s', '', _t(row[kcol])).upper()
        # KMC 코드는 차종별 가변길이(20·28자)이고 미적용 좌석은 '*'로 표기된다
        if re.fullmatch(r'[A-Z0-9]{5}', a) and re.fullmatch(r'[A-Z0-9*]{12,40}', k):
            master[k] = {'alc2': a, 'row': r + 1}
    if ck:
        _MASTER_CACHE[path] = (ck, master)
    return master


def _load_rows(path):
    """read_only + values_only 로 전 행을 리스트로 (대용량 ALC 가속)."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return rows


def _t(v):
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _find_hdr_idx(rows, keys, maxr=20):
    keys = [k.upper() for k in keys]
    for i in range(min(maxr, len(rows))):
        vals = [_t(x).upper() for x in rows[i]]
        if all(any(k in v for v in vals) for k in keys):
            return i
    return None


def read_alc_full(path):
    """ALC 코드집 1회 로드 → {CODE(4자리): [PEL코드(6자리)...]}. (codes = result.keys())"""
    rows = _load_rows(path)
    hi = _find_hdr_idx(rows, ['CODE', 'PART NO'])
    if hi is None:
        raise ValueError('ALC 코드집 헤더(CODE/PART NO)를 찾지 못했습니다.')
    hdr = {_t(x).upper(): j for j, x in enumerate(rows[hi])}
    cc = hdr.get('CODE')
    result = {}
    for i in range(hi + 1, len(rows)):
        r = rows[i]
        code = _t(r[cc]).upper() if (cc is not None and cc < len(r)) else ''
        if not re.fullmatch(r'[A-Z0-9]{4}', code):
            continue
        result[code] = [_t(x).upper() for x in r if re.fullmatch(r'[0-9A-Z]{6}', _t(x).upper())]
    return result


def analyze(qpart_path, alc_paths, master_path, master_pel):
    """6파일 1회씩만 읽어 판정 + O·X 를 함께 산출 (최적화 경로)."""
    qrows = read_qpart(qpart_path)
    alc_full = {}
    for slot in ALC_SLOTS:
        p = alc_paths.get(slot)
        alc_full[slot] = read_alc_full(p) if p else {}
    master = read_master(master_path) if master_path else {}
    used = {v['alc2'] for v in master.values()}
    seq = 1
    col_defs, jrows, oxrows = {}, [], []
    unknown_pel = {}      # PEL 코드 -> {발견된 슬롯}
    for q in qrows:
        # 판정
        missing = []
        for i in range(min(len(ALC_SLOTS), len(q['keys']))):
            k = q['keys'][i]
            if k == '****':
                continue
            if alc_full[ALC_SLOTS[i]] and k not in alc_full[ALC_SLOTS[i]]:
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
        jrows.append({'vehicle': q['vehicle'], 'key01': q.get('key01', ''), 'keys': q['keys'],
                      'kmc20': q['kmc20'], 'alc2': alc2, 'status': status, 'detail': detail})
        # O·X (재사용된 PEL 코드로)
        marks = set()
        for i in range(min(len(ALC_SLOTS), len(q['keys']))):
            k = q['keys'][i]
            if k == '****':
                continue
            for pc in alc_full[ALC_SLOTS[i]].get(k, []):
                m = master_pel.get(pc)
                if not m:
                    # PEL CODE 마스터에 없는 코드 → O 표기가 누락되므로 경고 대상
                    unknown_pel.setdefault(pc, set()).add(ALC_SLOTS[i])
                    continue
                sp = _spec_label(m)
                if not sp:
                    continue
                grp = str(m.get('옵션그룹', '')).strip() or 'OPTION'
                try:
                    order = float(m.get('표시순서') or 9999)
                except Exception:
                    order = 9999.0
                if sp not in col_defs or order < col_defs[sp]['order']:
                    col_defs[sp] = {'group': grp, 'order': order}
                marks.add(sp)
        oxrows.append({'vehicle': q['vehicle'], 'key01': q.get('key01', ''),
                       'kmc20': q['kmc20'], 'marks': sorted(marks)})
    columns = [{'spec': sp, 'group': d['group'], 'order': d['order']}
               for sp, d in sorted(col_defs.items(), key=lambda kv: (kv[1]['order'], kv[0]))]
    groups = []
    for c in columns:
        if groups and groups[-1]['group'] == c['group']:
            groups[-1]['span'] += 1
        else:
            groups.append({'group': c['group'], 'span': 1})
    stats = {'total': len(jrows),
             'matched': sum(r['status'] == '기존매칭' for r in jrows),
             'new': sum(r['status'] == '신규승인필요' for r in jrows),
             'missing': sum(r['status'] == '원본누락' for r in jrows)}
    return {'rows': jrows, 'stats': stats,
            'unknown_pel': [{'code': pc, 'slots': sorted(sl)}
                            for pc, sl in sorted(unknown_pel.items())],
            'ox': {'columns': columns, 'groups': groups, 'rows': oxrows}}


def read_alc_pel(path):
    """ALC 코드집 → {CODE(4자리): [PEL코드(6자리) list]}.
       라벨 없는 확산 열에 흩어진 6자리 PEL 코드를 각 CODE 행에서 수집."""
    ws = openpyxl.load_workbook(path, data_only=True).active
    hr = _find_header(ws, ['CODE', 'PART NO'])
    if hr is None:
        raise ValueError('ALC 코드집에서 헤더(CODE/PART NO)를 찾지 못했습니다.')
    hd = {_cell(ws, hr, c).upper(): c for c in range(1, ws.max_column + 1)}
    ccode = hd.get('CODE')
    result = {}
    for r in range(hr + 1, ws.max_row + 1):
        code = _cell(ws, r, ccode).upper()
        if not re.fullmatch(r'[A-Z0-9]{4}', code):
            continue
        pels = []
        for c in range(1, ws.max_column + 1):
            v = _cell(ws, r, c).upper()
            if re.fullmatch(r'[0-9A-Z]{6}', v):
                pels.append(v)
        result[code] = pels
    return result


def _norm(s):
    """비교용 정규화 — 대문자화 + 영숫자만 남김(공백·기호 무시)."""
    return re.sub(r'[^A-Z0-9]', '', str(s or '').upper())


# ALC_SLOTS(업로드 6파일 중 5개) → «★통합 ALC2 코드» 서식의 좌석위치(top) 블록.
# 후석 3개(2열 LH/CTR·CUSH/RH)는 좌우가 물리적 위치라 핸들방향과 무관하게 고정 매핑 가능.
# 전석(FRT LH/RH)은 DRIVER/PASSENGER가 LHD/RHD에 따라 바뀌므로 규칙이 확정되기 전까지는
# 일부러 매핑하지 않는다 — 잘못 채우면 에어백 등 안전 관련 열이 틀릴 수 있다.
SLOT_TOP_MAP = {
    'RR BACK LH': 'Rr 2ND LH',
    'RR CUSH': 'Rr 2ND CTR or CUSH',
    'RR BACK RH': 'Rr 2ND RH',
}


def match_option_columns(option_cols, m, top_filter=None):
    """PEL 마스터 항목(m: 사양/설명)이 서식의 어느 옵션 열(«★통합 ALC2 코드» 템플릿의
       ERGO/LUMBAR SUPPORT/THORAX... 같은 고정 열)에 해당하는지 col_letter 집합으로 반환.
       option_cols: alc2_ledger.find_option_columns()의 결과 {col_letter: {'top','group','label'}}.
       top_filter가 있으면 그 좌석위치(top) 열만 후보로 본다 — 같은 리프 라벨(LUMBAR SUPPORT,
       THORAX...)이 좌석위치마다 반복되므로 좌석을 모르는 채로는 매칭하면 안 된다.
       매칭은 정규화 후 완전일치만 인정한다(오탐 방지) — «사양» 또는 «설명»의 콤마 구분
       용어 중 하나가 열 리프 라벨과 정확히 같아야 한다. 같은 좌석위치 안에서 리프 라벨이
       중복되면(POWER/MANUAL 둘 다 «LUMBAR SUPPORT») 그룹명 첫 단어도 용어 중 어딘가에
       포함되어 있어야 확정한다 — 없으면 모호하므로 매칭하지 않는다."""
    phrases = [m.get('사양', '')] + str(m.get('설명', '')).split(',')
    phrase_norms = [_norm(p) for p in phrases if str(p).strip()]
    phrase_norms = [p for p in phrase_norms if p]
    if not phrase_norms:
        return set()
    cand = option_cols
    if top_filter is not None:
        tf = _norm(top_filter)
        cand = {c: info for c, info in option_cols.items() if _norm(info.get('top', '')) == tf}
    label_cols = {}
    for col, info in cand.items():
        label_cols.setdefault(_norm(info['label']), []).append(col)
    hit = set()
    for col, info in cand.items():
        ln = _norm(info['label'])
        if not ln or ln not in phrase_norms:
            continue
        if len(label_cols[ln]) > 1:
            gword = _norm(info['group'].split()[0]) if info.get('group') else ''
            if not gword or not any(gword in p for p in phrase_norms):
                continue
        hit.add(col)
    return hit


def build_option_marks(qpart_path, alc_paths, master_pel, option_cols):
    """«★통합 ALC2 코드» 서식의 고정 옵션 열(ERGO/LUMBAR SUPPORT/THORAX...)에 대해
       각 생산조합(kmc20)이 어느 열에 O가 찍혀야 하는지 계산한다.
       SLOT_TOP_MAP에 없는 슬롯(현재 FRT LH/FRT RH)은 좌석위치를 단정할 수 없어 건너뛴다.
       반환: {kmc20: set(col_letter)}."""
    if not option_cols:
        return {}
    qrows = read_qpart(qpart_path)
    alc_full = {}
    for slot in ALC_SLOTS:
        p = alc_paths.get(slot)
        alc_full[slot] = read_alc_full(p) if p else {}
    col_cache = {}
    out = {}
    for q in qrows:
        hit_cols = set()
        for i in range(min(len(ALC_SLOTS), len(q['keys']))):
            slot = ALC_SLOTS[i]
            top = SLOT_TOP_MAP.get(slot)
            if not top:
                continue
            k = q['keys'][i]
            if k == '****':
                continue
            for pc in alc_full[slot].get(k, []):
                m = master_pel.get(pc)
                if not m:
                    continue
                ck = (pc, top)
                if ck not in col_cache:
                    col_cache[ck] = match_option_columns(option_cols, m, top_filter=top)
                hit_cols |= col_cache[ck]
        out[q['kmc20']] = hit_cols
    return out


def _spec_label(m):
    """PEL 마스터 항목의 O/X 열 이름. «사양»이 비어 있으면 «설명»의 콤마 구분 용어 중
       첫 번째로 대체 — 설계 사양명이 없어 O 표기가 통째로 누락되던 것을 방지."""
    sp = str(m.get('사양', '')).strip()
    if sp:
        return sp
    for part in str(m.get('설명', '')).split(','):
        part = part.strip()
        if part:
            return part
    return ''


def build_ox(qpart_path, alc_paths, master_pel):
    """6파일 + PEL CODE 마스터 → O/X 통합코드집 (PEL 사양변경 패턴).
       행=생산조합(KMC ALC-2), 열=옵션(사양), 값=O(●)/X.
       master_pel: {PEL코드: {'사양','설명','옵션그룹','표시순서'}} (load_pel_master.data).
       열 이름은 _spec_label()로 결정 — «사양»이 비어 있으면 «설명»의 콤마 구분 용어로 대체."""
    qrows = read_qpart(qpart_path)
    alc_pel = {}
    for slot in ALC_SLOTS:
        p = alc_paths.get(slot)
        alc_pel[slot] = read_alc_pel(p) if p else {}
    col_defs, rows = {}, []
    for q in qrows:
        marks = set()
        for i, slot in enumerate(ALC_SLOTS):
            if i >= len(q['keys']):
                break
            key = q['keys'][i]
            if key == '****':
                continue
            for pc in alc_pel[slot].get(key, []):
                m = master_pel.get(pc)
                if not m:
                    continue
                sp = _spec_label(m)
                if not sp:
                    continue
                grp = str(m.get('옵션그룹', '')).strip() or 'OPTION'
                try:
                    order = float(m.get('표시순서') or 9999)
                except Exception:
                    order = 9999.0
                if sp not in col_defs or order < col_defs[sp]['order']:
                    col_defs[sp] = {'group': grp, 'order': order}
                marks.add(sp)
        rows.append({'vehicle': q['vehicle'], 'key01': q.get('key01', ''),
                     'kmc20': q['kmc20'], 'marks': sorted(marks)})
    columns = [{'spec': sp, 'group': d['group'], 'order': d['order']}
               for sp, d in sorted(col_defs.items(), key=lambda kv: (kv[1]['order'], kv[0]))]
    groups = []
    for c in columns:
        if groups and groups[-1]['group'] == c['group']:
            groups[-1]['span'] += 1
        else:
            groups.append({'group': c['group'], 'span': 1})
    return {'columns': columns, 'groups': groups, 'rows': rows, 'vc_count': len(rows)}


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
