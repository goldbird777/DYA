# -*- coding: utf-8 -*-
"""E-BOM ↔ M-BOM 사양 대조 엔진.

설계(E-BOM N열)와 생관(M-BOM ALC 코드집)이 같은 품번에 대해 적은 사양이 일치하는지
**축(옵션그룹) 단위**로 비교한다. 목적은 오류 적발보다 1레벨 BOM 자동화 준비 —
두 문서의 기준정보가 어디서 어긋나는지, 무엇을 보완해야 하는지 드러내는 것.

왜 축 단위인가: 사양 집합을 통째로 비교하면 실측 완전일치가 **0%** 나온다. 원인은
셋 다 진짜 오류가 아니다.
  ① 계열 vs 특정값 — E-BOM «A/LEATHER»(상위) ↔ M-BOM «A/LEA(1)»(하위)
  ② 표기 세밀도   — «USB» ↔ «USB(27W)», «L/SUPT» ↔ «L/SUPT(4CELL)»
  ③ 마스터 미등록 — E-BOM의 «2WAY H/REST STD»가 PEL 마스터에 없어 해석 실패
①은 validators._spec_index 가 어휘→사양키 **집합**을 주므로 교집합 판정으로 흡수되고,
②③은 축별로 나눠 사유를 구분해 표기한다(고칠 곳이 서로 다르기 때문).

핵심 원칙: **«PEL 마스터를 고쳐라»와 «BOM 파일을 고쳐라»를 구분해 알려준다.**
"""
import re

import validators

# 축(옵션그룹) → 사람이 읽는 이름. 없으면 옵션그룹 문자열을 그대로 쓴다.
AXIS_LABEL = {
    "COVER'G": '원단',
    'Airbag': '에어백',
    'H/REST': '헤드레스트',
    'LUMBAR': '럼버',
    'VENT': '통풍',
    'ODS': '승객감지',
    '안전부품': '안전부품',
    'OPTION': '기타 옵션',
}

# 판정 코드
OK          = 'OK'            # 양쪽 값 있고 교집합 있음
MISMATCH    = 'MISMATCH'      # 양쪽 값 있는데 교집합 없음 → 진짜 어긋남
NEED_MASTER = 'NEED_MASTER'   # E-BOM에 글자는 있는데 PEL 마스터에 없어 해석 실패
NEED_EBOM   = 'NEED_EBOM'     # E-BOM N열에 그 축 자체가 없음


def axis_label(group: str) -> str:
    return AXIS_LABEL.get(group, group or '(그룹없음)')


def _axes_from_specs(spec_keys, vocab):
    """사양키 집합 → {옵션그룹: {사양키}}"""
    out = {}
    for k in spec_keys:
        grp = (vocab.get(k) or {}).get('group') or '(그룹없음)'
        out.setdefault(grp, set()).add(k)
    return out


def resolve_text(text, vocab, idx):
    """E-BOM N열 텍스트 → (축별 사양키, 해석 실패한 원문 토큰들).
       실패 토큰은 «PEL 마스터에 없는 용어»라 보완 대상 목록이 된다."""
    keys, unresolved = set(), []
    for tok in validators._tokens(text):
        n = validators._norm(tok)
        if not n:
            continue
        hit = idx.get(n)
        if hit:
            keys |= set(hit) if isinstance(hit, (set, list)) else {hit}
        elif validators._norm(tok).upper() not in validators.PEL_SPEC_SKIP \
                and tok.strip().upper() not in validators.PEL_SPEC_SKIP:
            unresolved.append(tok.strip())
    return _axes_from_specs(keys, vocab), unresolved


def resolve_pel_codes(pel_codes, master, vocab):
    """M-BOM ALC의 PEL 코드들 → 축별 사양키."""
    keys = set()
    for pc in pel_codes:
        m = master.get(pc)
        if not m:
            continue
        sp = str(m.get('사양', '')).strip().upper()
        if sp and sp not in validators.PEL_SPEC_SKIP:
            keys.add(sp)
    return _axes_from_specs(keys, vocab)


def compare_axes(e_axes, m_axes, unresolved):
    """한 품번의 축별 판정. 반환: [{axis, label, verdict, ebom, mbom, hint}]

    unresolved 가 있으면 «E-BOM에 글자는 있으나 마스터에 없음»이므로, M-BOM에만 있는
    축은 NEED_EBOM 이 아니라 NEED_MASTER 로 본다 — 실측상 헤드레스트가 정확히 이 경우다
    (E-BOM에 «2WAY H/REST STD»가 23회 적혀 있는데 마스터에 그 용어가 없어 해석 실패)."""
    results = []
    for axis in sorted(set(e_axes) | set(m_axes)):
        e = e_axes.get(axis, set())
        m = m_axes.get(axis, set())
        if e and m:
            verdict = OK if (e & m) else MISMATCH
        elif m and not e:
            verdict = NEED_MASTER if unresolved else NEED_EBOM
        else:
            verdict = NEED_EBOM      # E-BOM에만 있는 축 — M-BOM ALC 확인 대상
        results.append({
            'axis': axis, 'label': axis_label(axis), 'verdict': verdict,
            'ebom': sorted(e), 'mbom': sorted(m),
        })
    return results


def verdict_text(axis_results, unresolved):
    """비고란 문구. 초보자도 «어디를 고쳐야 하는지» 알 수 있게 조치 위치를 함께 쓴다."""
    bad = [a for a in axis_results if a['verdict'] != OK]
    if not bad:
        return 'OK', '일치'

    parts = []
    worst = 'NEED'
    for a in bad:
        if a['verdict'] == MISMATCH:
            worst = 'MISMATCH'
            parts.append(f"{a['label']}: E-BOM «{'/'.join(a['ebom'])}» ↔ "
                         f"M-BOM «{'/'.join(a['mbom'])}»")
        elif a['verdict'] == NEED_MASTER:
            parts.append(f"{a['label']}: M-BOM «{'/'.join(a['mbom'])}» 에 대응하는 "
                         f"E-BOM 용어가 PEL CODE 마스터에 없음")
        else:
            side = f"M-BOM «{'/'.join(a['mbom'])}»" if a['mbom'] else f"E-BOM «{'/'.join(a['ebom'])}»"
            parts.append(f"{a['label']}: {side} 만 있음")

    if worst == 'MISMATCH':
        return 'MISMATCH', '불일치 — ' + ' · '.join(parts) + ' · BOM 파일 또는 ALC 확인 필요'
    if unresolved:
        return 'NEED_MASTER', ('보완필요 — ' + ' · '.join(parts)
                               + f" · E-BOM 미등록 용어: {', '.join(sorted(set(unresolved))[:4])}"
                               + ' · PEL CODE 마스터 설명란에 추가')
    return 'NEED_EBOM', '보완필요 — ' + ' · '.join(parts) + ' · E-BOM 파일 N열 확인'


def summarize(all_axis_results):
    """축별 요약 — 개별 행보다 «무엇을 보완해야 하는가»를 먼저 보여주기 위한 집계.
       반환: {axis: {'label','OK','MISMATCH','NEED_MASTER','NEED_EBOM','total'}}"""
    agg = {}
    for results in all_axis_results:
        for a in results:
            e = agg.setdefault(a['axis'], {'label': a['label'], OK: 0, MISMATCH: 0,
                                           NEED_MASTER: 0, NEED_EBOM: 0, 'total': 0,
                                           'samples': []})
            e[a['verdict']] += 1
            e['total'] += 1
            if a['verdict'] == MISMATCH and len(e['samples']) < 3:
                s = f"E-BOM «{'/'.join(a['ebom'])}» ↔ M-BOM «{'/'.join(a['mbom'])}»"
                if s not in e['samples']:
                    e['samples'].append(s)
    return agg
