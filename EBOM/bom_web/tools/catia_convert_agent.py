# -*- coding: utf-8 -*-
"""
카티아 자동 변환 프로그램  (CATIA 가 깔린 사내 PC 에서 실행)

■ 왜 별도 프로그램이 필요한가
  서버는 리눅스 클라우드라 CATIA 를 못 돌린다. CATPart 는 다쏘시스템 전용 비공개 형식이라
  오픈소스 변환기도 없고, CATIA 는 윈도우 + 라이선스가 있어야 실행된다.
  그래서 «서버가 변환»하는 건 불가능하고, CATIA 가 있는 PC 가 대신 변환해 주는 수밖에 없다.
  다만 사용자 입장에서는 자동이다 — 올려 두면 이 프로그램이 알아서 변환해 올린다.

■ 동작
  ① 서버에 «변환할 게 있나» 물어본다        (GET  /catia/convert/queue)
  ② 원본을 받아                              (GET  /catia/convert/source/{id})
  ③ 실행 중인 CATIA 에 붙어 내보낸다
  ④ 변환본을 올린다                          (POST /catia/convert/result/{id})
  → 게시판의 «STP 파일 / PDF 파일» 칸이 X 에서 O 로 바뀌고 웹뷰어로 열린다.

■ 실측(이 PC, CATIA V5 R34 / 2026-08-02)
  · CATPart 47MB  → STEP 64.6MB   여는 데 3.9초 + 내보내기 31초
  · CATDrawing 55MB → PDF          내보내기 8.0초
    ※ 도면은 «시트마다 따로» 나온다(시트_1 4.44MB, DAYOU 0.08, REVISION 0.00 …).
      그중 «가장 큰 것»만 본 도면이라 그것만 올리고 나머지(양식·리비전·디테일)는 버린다.

■ 준비
  1) CATIA V5 가 실행돼 있어야 한다(라이선스 필요). 평소 쓰시는 CATSTART 로 띄워 두면 된다.
  2) pip install requests pywin32
  3) 관리자 화면의 «변환 열쇠»를 환경변수에 넣는다.

■ 실행
    set DYA_AGENT_KEY=열쇠
    python catia_convert_agent.py --check       CATIA 연결·서버 연결만 확인
    python catia_convert_agent.py               한 번 처리하고 종료
    python catia_convert_agent.py --loop 120     2분마다 계속 (사실상 자동)

■ 주의
  이 프로그램은 «실행 중인 CATIA»에 붙어 문서를 열고 닫는다. 사람이 작업 중인 CATIA 에서
  돌리면 방해가 되므로 «변환 전용 PC»를 권한다. 화면 상태(Visible)는 건드리지 않는다.
"""
import os
import re
import sys
import time
import shutil
import tempfile
import argparse

try:
    import requests
except ImportError:
    print('requests 가 필요합니다:  pip install requests')
    sys.exit(1)

SERVER = os.environ.get('DYA_SERVER', 'https://dyaerp.cloud')
AGENT_KEY = os.environ.get('DYA_AGENT_KEY', '')
TIMEOUT = 3600

# 평소 쓰시는 CATIA 실행 명령(R34). --launch 를 줬을 때만 사용한다.
CATIA_LAUNCH = (
    r'"C:\Program Files\Dassault Systemes\B34\win_b64\code\bin\CATSTART.exe" '
    r'-run "CNEXT.exe" -env CATIA.V5-6R2024.B34_DP2 '
    r'-direnv "C:\ProgramData\DassaultSystemes\CATEnv\ENV\R34" -nowindow'
)


def api(p):
    return SERVER.rstrip('/') + p


def headers():
    return {'X-Agent-Key': AGENT_KEY}


# ── CATIA ────────────────────────────────────────────────────────────────────
def get_catia(launch=False):
    """실행 중인 CATIA 에 «붙는다». 없으면(옵션 시) 띄운다.
       Visible 을 건드리지 않는다 — 사람이 쓰던 창을 숨겨 버리면 안 되기 때문."""
    import win32com.client
    try:
        return win32com.client.GetActiveObject('CATIA.Application'), ''
    except Exception:
        pass
    if not launch:
        return None, ('실행 중인 CATIA 가 없습니다. 평소처럼 CATIA 를 띄운 뒤 다시 실행하거나 '
                      '--launch 를 붙이세요.')
    os.system('start "" ' + CATIA_LAUNCH)
    for _ in range(60):                       # 최대 2분 기다린다
        time.sleep(2)
        try:
            return win32com.client.GetActiveObject('CATIA.Application'), ''
        except Exception:
            continue
    return None, 'CATIA 를 띄웠지만 연결하지 못했습니다.'


def pick_main_pdf(folder, stem, out_path) -> str:
    """도면은 시트마다 PDF 가 따로 나온다(시트_1 · DAYOU · REVISION · Default_Detail …).
       그중 «본 도면»만 남기고 나머지는 버린다 — 나머지는 도면 양식·리비전 표·디테일이라
       따로 볼 필요가 없다(사용자 확정 2026-08-02).
       판별은 «용량이 가장 큰 것» — 실측에서 본 도면 4.44MB vs 나머지 0.00~0.08MB 로
       차이가 뚜렷했다."""
    base = os.path.basename(stem)
    parts = [f for f in os.listdir(folder)
             if f.startswith(base) and f.lower().endswith('.pdf')]
    if not parts:
        return '도면 PDF 가 만들어지지 않았습니다'
    sized = sorted(((os.path.getsize(os.path.join(folder, f)), f) for f in parts),
                   reverse=True)
    main = sized[0][1]
    if len(sized) > 1:
        print('   시트 %d개 중 본 도면 선택: %s (%.2f MB)'
              % (len(sized), main, sized[0][0] / 1048576))
    src = os.path.join(folder, main)
    if os.path.abspath(src) != os.path.abspath(out_path):
        os.replace(src, out_path)
    for _sz, f in sized[1:]:                   # 나머지 조각 정리
        try:
            os.remove(os.path.join(folder, f))
        except OSError:
            pass
    return ''


def convert(cat, src_path, out_path) -> str:
    """CATIA 로 열어 내보낸다. 성공 '' / 실패 사유."""
    folder = os.path.dirname(out_path)
    stem, ext = os.path.splitext(out_path)
    fmt = ext.replace('.', '')
    doc = None
    try:
        doc = cat.Documents.Open(src_path)
        doc.ExportData(stem, fmt)
    except Exception as ex:
        return '변환 실패: %s' % str(ex)[:160]
    finally:
        try:
            if doc is not None:
                doc.Close()
        except Exception:
            pass

    if fmt == 'pdf':
        return pick_main_pdf(folder, stem, out_path)
    if os.path.exists(out_path):
        return ''
    # 일부 버전은 이름을 조금 다르게 붙인다 — 같은 앞머리의 산출물을 찾아 준다
    base = os.path.basename(stem)
    for f in os.listdir(folder):
        if f.startswith(base) and f.lower().endswith(ext):
            os.replace(os.path.join(folder, f), out_path)
            return ''
    return '내보내기 결과 파일을 찾지 못했습니다'


# ── 한 바퀴 ──────────────────────────────────────────────────────────────────
def run_once(cat, limit=20) -> int:
    try:
        r = requests.get(api('/catia/convert/queue?limit=%d' % limit),
                         headers=headers(), timeout=60)
    except Exception as ex:
        print('서버 연결 실패:', ex)
        return 0
    if r.status_code != 200:
        print('서버 오류:', r.status_code, r.text[:200])
        return 0
    d = r.json()
    items, st = d.get('items') or [], d.get('stats') or {}
    print('대기 %s건 / 원본 %s건 (완료 %s건)'
          % (st.get('pending'), st.get('orig'), st.get('done')))
    if not items:
        return 0

    tmp = tempfile.mkdtemp(prefix='dya_conv_')
    done = 0
    try:
        for it in items:
            src = os.path.join(tmp, it['filename'])
            out = os.path.splitext(src)[0] + it['want']
            mb = (it.get('size_no') or 0) / 1048576
            print('\n▶ %s  (%.0f MB → %s)' % (it['filename'], mb, it['want']))
            t0 = time.time()
            try:
                with requests.get(api('/catia/convert/source/%d' % it['id']),
                                  headers=headers(), stream=True, timeout=TIMEOUT) as rr:
                    rr.raise_for_status()
                    with open(src, 'wb') as fh:
                        for chunk in rr.iter_content(1024 * 1024):
                            fh.write(chunk)
            except Exception as ex:
                print('   내려받기 실패:', ex)
                continue

            err = convert(cat, src, out)
            if err:
                print('  ', err)
                continue
            print('   변환 완료 %.0f초 → %.1f MB'
                  % (time.time() - t0, os.path.getsize(out) / 1048576))

            try:
                with open(out, 'rb') as fh:
                    up = requests.post(api('/catia/convert/result/%d' % it['id']),
                                       headers=headers(),
                                       files={'file': (os.path.basename(out), fh)},
                                       timeout=TIMEOUT)
                if up.status_code == 200 and up.json().get('ok'):
                    print('   올림 완료')
                    done += 1
                else:
                    print('   올리기 실패:', up.status_code, up.text[:160])
            except Exception as ex:
                print('   올리기 실패:', ex)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return done


def main():
    ap = argparse.ArgumentParser(description='카티아 자동 변환 프로그램')
    ap.add_argument('--loop', type=int, default=0, help='초 단위 반복 (0=한 번만)')
    ap.add_argument('--limit', type=int, default=20, help='한 번에 처리할 개수')
    ap.add_argument('--check', action='store_true', help='연결 확인만')
    ap.add_argument('--launch', action='store_true', help='CATIA 가 없으면 띄운다')
    a = ap.parse_args()

    if not AGENT_KEY:
        print('변환 열쇠가 없습니다. 관리자 화면에서 복사한 뒤:')
        print('    set DYA_AGENT_KEY=복사한열쇠')
        sys.exit(1)
    print('서버:', SERVER)

    cat, err = get_catia(launch=a.launch)
    if err:
        print(err)
        sys.exit(1)
    try:
        print('CATIA 연결됨 — V%s R%s / 열린 문서 %s개'
              % (cat.SystemConfiguration.Version, cat.SystemConfiguration.Release,
                 cat.Documents.Count))
    except Exception:
        print('CATIA 연결됨')

    if a.check:
        try:
            r = requests.get(api('/catia/convert/queue?limit=5'), headers=headers(), timeout=30)
            print('서버 응답:', r.status_code)
            if r.status_code == 200:
                d = r.json()
                print('현황:', d.get('stats'))
                for it in (d.get('items') or [])[:5]:
                    print('   대기:', it['filename'][:60], '→', it['want'])
        except Exception as ex:
            print('서버 연결 실패:', ex)
        return

    while True:
        n = run_once(cat, limit=a.limit)
        if n:
            print('\n%d건 변환했습니다.' % n)
        if not a.loop:
            break
        time.sleep(a.loop)


if __name__ == '__main__':
    main()
