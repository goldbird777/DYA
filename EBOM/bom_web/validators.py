"""
BOM 검증 로직 — 5가지 오류 유형
  ① 오사양 누락   — 1레벨 DESC 사양이 하위 부품에 없음 (ERROR)
  ② 사양 불일치   — 하위 부품 사양이 1레벨 DESC에 없음 (WARNING)
  ③ 수량 오기입   — 소수·음수·비정상 대수량 (WARNING)
  ④ 레벨 중복     — 동일 variant 동일 ASSY 2개 이상 (ERROR)
  ⑤ P/NO 오류     — 동일 ASSY 하위 중복 or 누락 (WARNING)
"""
import re

# ── 사양 키워드 매핑 (PEL_CODE 기준 전체 반영) ────────────────────────────────
SPEC_KEYWORDS = {
    # ─ 에어백 ─────────────────────────────────────────────
    'SAB':       ['SAB', 'SIDE AIR', 'AIRBAG', 'AIR BAG',
                  'THORAX', 'T&P', 'T & P'],          # THORAX / T&P = SIDE AIR BAG
    'CSAB':      ['CSAB', 'CENTER SAB', 'CENTER AIR'],

    # ─ 시트 히터 / 통풍 ──────────────────────────────────
    'HTR':       ['HTR', 'HEATER', 'HEAT'],
    'VENT':      ['VENT', 'VENTIL'],
    'WMR':       ['WMR', 'WARMER', 'WARM'],             # 워머 (2열 시트 등)

    # ─ 파워 / 전동 ───────────────────────────────────────
    'PWR':       ['PWR', 'POWER SEAT', 'PWR SEAT'],
    'IMS':       ['IMS'],                               # 통합 메모리 (PWR 계열)
    'L/SUPT':    ['L/SUPT', 'LUMBAR', 'LUMB', '2WAY L/SUPT'],
    'P_EXT':     ['P_EXT', 'P/EXT', 'EXTENDABLE', 'EXT CUSH'],  # 파워 연장 쿠션

    # ─ 암레스트 / 워크인 / 릴렉스 ───────────────────────
    'ARMREST':   ['ARMREST', 'ARM REST', 'A/REST', 'ARMRST'],
    'W/IN':      ['W/IN', 'WALK-IN', 'WALK IN', 'WALKIN'],
    'RLX':       ['RLX', 'RELAX'],
    'VIBRO':     ['VIBRO', 'VIBRAT'],

    # ─ 안전벨트 리마인더 ─────────────────────────────────
    'SBR':       ['SBR', 'BELT REM', 'BELT REMIND'],   # 물리 BUCKLE/S/BELT 제외

    # ─ 원단 ──────────────────────────────────────────────
    'CLOTH':     ['CLOTH'],
    'A/CLOTH':   ['A/CLOTH'],
    'A/LEATHER': ['A/LEATHER', 'A/LEA', 'LEATHER'],    # A/LEA 추가
    'P/LEA':     ['P/LEA', 'P/LEATHER', 'PURE LEATHER', 'P/LEATHER'],  # 순정가죽

    # ─ 기타 구조·편의 사양 ──────────────────────────────
    'B/BOARD':   ['B/BOARD'],
    'USB':       ['USB'],
    'TABLE':     ['TABLE', 'TRAY', 'FOLD UP TRAY'],     # 폴드업 트레이
    'EASY_ACC':  ['EASY ACCESS', 'EASY-ACCESS'],         # Easy Access
    'ODS':       ['ODS'],                                # 승객감지 센서 (SAB 연동)
}

MEANINGS = {
    'SAB': '사이드 에어백', 'CSAB': '센터 에어백',
    'HTR': '시트 히터', 'VENT': '시트 통풍', 'WMR': '워머',
    'PWR': '전동 시트', 'IMS': '통합 메모리', 'L/SUPT': '럼버 서포트',
    'P_EXT': '파워 연장 쿠션',
    'ARMREST': '암레스트', 'W/IN': '워크인', 'RLX': '릴렉세이션',
    'VIBRO': '진동 마사지', 'SBR': '시트벨트 리마인더',
    'CLOTH': '패브릭 원단', 'A/CLOTH': '인조 패브릭',
    'A/LEATHER': '인조가죽', 'P/LEA': '순정가죽',
    'B/BOARD': '백보드', 'USB': 'USB 충전',
    'TABLE': '폴드업 트레이', 'EASY_ACC': 'Easy Access', 'ODS': '승객감지센서',
}

# 사양 불일치 체크 제외 — 선택 사양이 아닌 안전·구조 부품
SKIP_SPEC_KEYS = {'B/BOARD', 'USB', 'CLOTH', 'A/CLOTH', 'A/LEATHER', 'P/LEA',
                  'ODS', 'IMS'}

# 하드웨어/부자재 패턴 — 여러 ASSY에 공통 사용되므로 사양 검증 제외
HW_PAT = re.compile(
    r'(SCREW|BOLT|NUT|CLIP|RIVET|WASHER|PIN|BAND|RING|BUSH'
    r'|WIRE|CABLE|HARNESS|BRACKET|BRKT|BUCKLE|RETRACT|ANCHOR'
    r'|HOG.?RING|T/SCREW|SNAP)', re.IGNORECASE)

QTY_WARN_THRESHOLD = 50


def _parse_spec(text: str) -> set:
    u = str(text).upper()
    return {k for k, kws in SPEC_KEYWORDS.items() if any(kw in u for kw in kws)}


def _base_pname(s: str) -> str:
    return re.sub(r'\s*[-–]\s*\d+.*$', '', s).strip()


def validate_bom(rows: list, variant_cols: dict):
    errors = []
    valid_rows = [r for r in rows if not r['is_pno_struck']]

    lv1_by_vc = {
        r['vc']: r for r in valid_rows
        if r['level'] == 1 and r['vc'] and not r['is_section']
    }
    sub_rows = [r for r in valid_rows if r['level'] is not None and r['level'] >= 2]

    # ────────────────────────────────────────────────────────────────────────
    # ① 오사양 누락 — 1레벨 DESC 사양 키워드가 하위 부품에 없음
    # ────────────────────────────────────────────────────────────────────────
    for vc_code, lv1 in lv1_by_vc.items():
        lv1_spec = _parse_spec(lv1['desc'])
        vc_sub   = [r for r in sub_rows if vc_code in r['qtys']]

        sub_spec_found = set()
        for part in vc_sub:
            sub_spec_found |= _parse_spec(part['pname'])
            sub_spec_found |= _parse_spec(part['desc'])

        for spec_key in lv1_spec:
            if spec_key in SKIP_SPEC_KEYS:
                continue
            if spec_key not in sub_spec_found:
                errors.append({
                    'type': '🔴 오사양 누락', 'severity': 'ERROR',
                    'variant': vc_code, 'level': 1,
                    'pno': lv1['pno'], 'pname': '(1레벨)',
                    'desc': lv1['desc'][:60],
                    'excel_rows': [lv1['row_idx']],
                    'message': f"[{spec_key}] 1레벨에 사양 기재 → 하위 부품 없음",
                    'lv1_desc': lv1['desc'],
                    'mismatch_keys': [spec_key],
                    'detail': (f"1레벨 DESCRIPTION에 '{spec_key}({MEANINGS.get(spec_key, spec_key)})' 사양이 "
                               f"명시되어 있으나, 하위 레벨에 해당 사양 부품이 배정되지 않았습니다."),
                    'fix_target': f"행 {lv1['row_idx']} → N열(DESCRIPTION) 수정 또는 하위 사양 부품 추가",
                })

    # ────────────────────────────────────────────────────────────────────────
    # ② 사양 불일치 — 하위 부품 사양이 1레벨 DESC에 없음
    # ────────────────────────────────────────────────────────────────────────
    for vc_code, lv1 in lv1_by_vc.items():
        lv1_spec = _parse_spec(lv1['desc'])
        for part in [r for r in sub_rows if vc_code in r['qtys']]:
            if '공용' in part['desc'] or '공용' in part['pname']:
                continue
            if HW_PAT.search(part['pname']):
                continue
            req  = _parse_spec(part['pname'])
            miss = {k for k in req if k not in lv1_spec and k not in SKIP_SPEC_KEYS}
            if miss:
                errors.append({
                    'type': '⚠️ 사양 불일치', 'severity': 'WARNING',
                    'variant': vc_code, 'level': part['level'],
                    'pno': part['pno'], 'pname': part['pname'],
                    'desc': part['desc'][:50],
                    'excel_rows': [part['row_idx']],
                    'message': f"[{', '.join(sorted(miss))}] 하위 부품 사양 → 1레벨 DESC 미기재",
                    'lv1_desc': lv1['desc'],
                    'mismatch_keys': sorted(miss),
                    'detail': (f"하위 부품에 '{', '.join(sorted(miss))}' 사양이 있으나 "
                               f"1레벨 DESCRIPTION에 해당 사양이 없습니다."),
                    'fix_target': f"행 {lv1['row_idx']} → N열(DESCRIPTION) 또는 하위 부품 배정 확인",
                })

    # ────────────────────────────────────────────────────────────────────────
    # ③ 수량 오기입 — 소수점 · 음수 · 비정상 대수량
    # ────────────────────────────────────────────────────────────────────────
    for vc_code in variant_cols.values():
        for part in [r for r in sub_rows if vc_code in r['qtys']]:
            qty    = part['qtys'][vc_code]
            issues = []
            if qty != int(qty):
                issues.append(f"소수점 수량 ({qty})")
            elif qty <= 0:
                issues.append(f"음수/0 수량 ({qty})")
            elif qty > QTY_WARN_THRESHOLD:
                issues.append(f"비정상 대수량 ({int(qty)})")
            if issues:
                lv1 = lv1_by_vc.get(vc_code, {})
                errors.append({
                    'type': '🟡 수량 오기입', 'severity': 'WARNING',
                    'variant': vc_code, 'level': part['level'],
                    'pno': part['pno'], 'pname': part['pname'],
                    'desc': part['desc'][:50],
                    'excel_rows': [part['row_idx']],
                    'message': f"{part['pno']} 수량 이상 — {', '.join(issues)}",
                    'lv1_desc': lv1.get('desc', ''),
                    'mismatch_keys': [],
                    'detail': ', '.join(issues),
                    'fix_target': f"행 {part['row_idx']} → Variant {vc_code} 수량 셀 확인",
                })

    # ────────────────────────────────────────────────────────────────────────
    # ④ 레벨 중복 — 동일 variant 동일 ASSY 타입 2개 이상
    # ────────────────────────────────────────────────────────────────────────
    for vc_code in variant_cols.values():
        assigned_l2 = [r for r in sub_rows if r['level'] == 2 and vc_code in r['qtys']]
        groups: dict[str, list] = {}
        for r in assigned_l2:
            groups.setdefault(_base_pname(r['pname']), []).append(r)
        for bn, grp in groups.items():
            if len(grp) > 1 and any('ASSY' in g['pname'].upper() for g in grp):
                lv1 = lv1_by_vc.get(vc_code, {})
                errors.append({
                    'type': '❌ 레벨 중복', 'severity': 'ERROR',
                    'variant': vc_code, 'level': 2,
                    'pno': ' / '.join(g['pno'] for g in grp),
                    'pname': bn,
                    'desc': ' / '.join(g['desc'][:25] for g in grp),
                    'excel_rows': [g['row_idx'] for g in grp],
                    'message': f"[{bn}] 어셈블리 {len(grp)}개 중복 배정",
                    'lv1_desc': lv1.get('desc', ''),
                    'mismatch_keys': [],
                    'detail': f"동일 어셈블리 타입 [{bn}]이 같은 variant에 {len(grp)}개 배정되었습니다.",
                    'fix_target': f"행 {[g['row_idx'] for g in grp]} → 중복 ASSY 확인",
                })

    # ────────────────────────────────────────────────────────────────────────
    # ⑤ P/NO 오류 — 동일 ASSY 하위에서만 중복 체크
    #    ※ 다른 레벨2 ASSY 하위에 같은 P/NO(스크류 등 하드웨어)가 있는 것은 정상
    # ────────────────────────────────────────────────────────────────────────
    for vc_code in variant_cols.values():
        # 해당 variant 부품을 행 순서대로 정렬
        vc_parts = sorted(
            [r for r in sub_rows if vc_code in r['qtys']],
            key=lambda r: r['row_idx']
        )

        current_l2_pno  = None   # 현재 레벨2 ASSY P/NO
        current_l2_row  = None   # 현재 레벨2 ASSY 행번호
        keyed: dict[tuple, list] = {}   # (pno, parent_l2_pno) → [parts]

        for part in vc_parts:
            if part['level'] == 2:
                current_l2_pno = part['pno']
                current_l2_row = part['row_idx']
            # 하드웨어 부품은 중복 체크 제외
            # (동일 ASSY 내 여러 위치에 같은 P/NO 사용 가능 — DESC로 용도 구분)
            if part['pno'] and not HW_PAT.search(part['pname']):
                key = (part['pno'], current_l2_pno)
                keyed.setdefault(key, []).append(part)

        for (pno, parent_l2), parts in keyed.items():
            if len(parts) > 1:
                lv1 = lv1_by_vc.get(vc_code, {})
                errors.append({
                    'type': '🔵 P/NO 중복', 'severity': 'WARNING',
                    'variant': vc_code, 'level': parts[0]['level'],
                    'pno': pno,
                    'pname': parts[0]['pname'],
                    'desc': f"상위 ASSY P/NO: {parent_l2 or '(없음)'}",
                    'excel_rows': [p['row_idx'] for p in parts],
                    'message': f"P/NO [{pno}] 동일 ASSY 하위 {len(parts)}회 중복",
                    'lv1_desc': lv1.get('desc', ''),
                    'mismatch_keys': [],
                    'detail': (f"P/NO [{pno}]이 상위 ASSY [{parent_l2}] 하위에 "
                               f"{len(parts)}번 중복 등록되었습니다. "
                               f"(행: {', '.join(str(p['row_idx']) for p in parts)})"),
                    'fix_target': f"행 {[p['row_idx'] for p in parts]} → 동일 ASSY 내 중복 여부 확인",
                })

    # P/NO 누락 (수량 있는데 P/NO 없음)
    for part in sub_rows:
        if not part['pno'] and not part['is_section'] and part['qtys']:
            vc_code = next(iter(part['qtys']))
            errors.append({
                'type': '🟠 P/NO 누락', 'severity': 'WARNING',
                'variant': vc_code, 'level': part['level'],
                'pno': '(없음)', 'pname': part['pname'],
                'desc': part['desc'][:50],
                'excel_rows': [part['row_idx']],
                'message': f"행 {part['row_idx']}: 수량 있으나 P/NO 누락",
                'lv1_desc': '',
                'mismatch_keys': [],
                'detail': '수량이 기입되어 있으나 P/NO가 비어있습니다.',
                'fix_target': f"행 {part['row_idx']} → J열(P/NO) 입력 필요",
            })

    return errors, lv1_by_vc
