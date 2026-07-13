#!/usr/bin/env python3
"""사이드바 메뉴 일관성 검사.

하드코딩된 사이드바가 페이지마다 복제돼 있어, 새 메뉴를 추가할 때
일부 페이지(특히 menu-item에 active/open이 붙은 변형)를 빠뜨리기 쉽다.
이 스크립트는 모든 템플릿의 사이드바 메뉴 href 집합을 비교해
누락된 페이지를 찾아낸다. 사이드바를 건드린 뒤/배포 전에 실행할 것.

    python tools/check_sidebar.py

drift가 있으면 종료코드 1.
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
# 조건부(권한/페이지별)라 페이지마다 있을 수도 없을 수도 있는 항목 — 비교에서 제외
OPTIONAL = {'/logout', '/admin'}


def sidebar_hrefs(text):
    return set(re.findall(r'class="menu-item[^"]*" href="([^"]+)"', text))


def main():
    pages = {}
    for path in sorted(glob.glob(os.path.join(TPL_DIR, '*.html'))):
        hrefs = sidebar_hrefs(open(path, encoding='utf-8').read())
        if hrefs:
            pages[os.path.basename(path)] = hrefs
    if not pages:
        print('사이드바를 가진 템플릿을 찾지 못했습니다.')
        return 0
    canonical = set().union(*pages.values()) - OPTIONAL
    drift = {f: sorted(canonical - h) for f, h in pages.items() if canonical - h}
    print(f'페이지 {len(pages)}개 · 핵심 메뉴 {len(canonical)}개')
    if drift:
        print('\n❌ 사이드바 메뉴 누락(drift) 발견:')
        for f, missing in drift.items():
            print(f'  - {f}: {missing}')
        print('\n→ 위 페이지에 누락 메뉴를 추가하세요.')
        return 1
    print('✅ 모든 페이지 사이드바 메뉴셋 일치.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
