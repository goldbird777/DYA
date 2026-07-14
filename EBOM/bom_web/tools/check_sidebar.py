#!/usr/bin/env python3
"""사이드바 단일 소스 검사.

사이드바는 templates/_sidebar.html 하나로 통합됐고, 각 페이지는
{% include '_sidebar.html' %} 로 가져온다. 메뉴 변경은 _sidebar.html
한 곳만 고치면 되고, 페이지별 드리프트는 구조적으로 불가능하다.

이 가드는 그 규칙이 깨졌는지 검사한다:
  - 어떤 페이지가 사이드바를 인라인으로 하드코딩하면(=include 대신 <nav>) 실패.
  - _sidebar.html 에서 메뉴 항목 수를 리포트.

    python tools/check_sidebar.py

위반 시 종료코드 1.
"""
import glob
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

TPL_DIR = os.path.join(os.path.dirname(__file__), '..', 'templates')
PARTIAL = '_sidebar.html'
INLINE_NAV = re.compile(r'<nav class="sidebar" id="sidebar">')


def main():
    partial_path = os.path.join(TPL_DIR, PARTIAL)
    if not os.path.exists(partial_path):
        print(f'❌ {PARTIAL} 이 없습니다. 사이드바 단일 소스가 사라졌습니다.')
        return 1
    menu = re.findall(r'class="menu-item[^"]*" href="([^"]+)"',
                      open(partial_path, encoding='utf-8').read())
    inline = []
    for path in sorted(glob.glob(os.path.join(TPL_DIR, '*.html'))):
        name = os.path.basename(path)
        if name == PARTIAL:
            continue
        if INLINE_NAV.search(open(path, encoding='utf-8').read()):
            inline.append(name)
    print(f'{PARTIAL}: 메뉴 {len(menu)}개 (단일 소스)')
    if inline:
        print('\n❌ 사이드바를 인라인 하드코딩한 페이지(드리프트 위험):')
        for f in inline:
            print(f'  - {f}  →  <nav>...</nav> 대신 {{% include \'{PARTIAL}\' %}} 사용')
        return 1
    print('✅ 모든 페이지가 단일 사이드바 partial 사용. 드리프트 위험 없음.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
