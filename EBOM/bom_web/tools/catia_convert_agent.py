# -*- coding: utf-8 -*-
"""
카티아 자동 변환 프로그램 (사내 CATIA PC 에서 실행)

왜 필요한가
  서버(리눅스)는 CATPart 를 못 읽는다. 다쏘시스템 전용 비공개 형식이라 오픈소스 변환기가
  없기 때문이다. 그래서 «CATIA 가 깔린 사내 PC 한 대»가 변환기 역할을 한다.

무엇을 하나
  ① 서버에 «변환할 게 있나» 물어본다        (GET  /catia/convert/queue)
  ② 원본을 받아                              (GET  /catia/convert/source/{id})
  ③ CATIA 로 열어 STEP·PDF 로 내보낸 뒤
  ④ 변환본을 서버에 올린다                    (POST /catia/convert/result/{id})
  → 게시판의 «변환본» 칸이 자동으로 채워지고, 웹 뷰어에서 바로 열린다.

  CATPart·CATProduct → STEP(.stp)      3D 뷰어에서 회전·확대해 볼 수 있다
  CATDrawing         → PDF             브라우저가 그대로 연다

준비
  1) 이 PC 에 CATIA V5 가 설치되어 있어야 한다(라이선스 포함).
  2) pip install requests pywin32
  3) 관리자 화면에서 «변환 열쇠»를 복사해 아래 AGENT_KEY 에 넣는다.

실행
    python catia_convert_agent.py                 # 한 번만 처리하고 끝
    python catia_convert_agent.py --loop 300      # 5분마다 계속 확인
    python catia_convert_agent.py --dry           # CATIA 없이 연결만 시험

주의
  CATIA 자동화는 이 PC 의 CATIA 를 실제로 조작한다. 사람이 CATIA 로 작업 중인 PC 보다는
  «변환 전용 PC»에서 돌리는 편이 안전하다.
"""
import os
import sys
import time
import tempfile
import argparse

try:
    import requests
except ImportError:
    print('requests 가 필요합니다:  pip install requests')
    sys.exit(1)

# ── 설정 ──────────────────────────────────────────────────────────────────────
SERVER = os.environ.get('DYA_SERVER', 'https://dyaerp.cloud')
AGENT_KEY = os.environ.get('DYA_AGENT_KEY', '')      # 관리자 화면의 «변환 열쇠»
TIMEOUT = 1800


def api(path):
    return SERVER.rstrip('/') + path


def headers():
    return {'X-Agent-Key': AGENT_KEY}


# ── CATIA 조작 ────────────────────────────────────────────────────────────────
def catia_convert(src_path: str, out_path: str) -> str:
    """CATIA 로 열어 내보낸다. 성공하면 '' , 실패하면 사유를 돌려준다."""
    try:
        import win32com.client
    except ImportError:
        return 'pywin32 가 없습니다 (pip install pywin32)'
    try:
        cat = win32com.client.Dispatch('CATIA.Application')
        cat.Visible = False
    except Exception as ex:
        return f'CATIA 를 실행하지 못했습니다: {ex}'

    doc = None
    try:
        doc = cat.Documents.Open(src_path)
        # ExportData 는 확장자로 형식을 정한다. STEP 은 stp, 도면은 pdf 로 저장된다.
        stem, ext = os.path.splitext(out_path)
        fmt = ext.replace('.', '')
        doc.ExportData(stem, fmt)
        made = stem + ext
        if not os.path.exists(made):
            # 일부 버전은 확장자를 다르게 붙인다 — 같은 이름의 산출물을 찾아 준다
            folder = os.path.dirname(stem) or '.'
            base = os.path.basename(stem)
            for f in os.listdir(folder):
                if f.startswith(base) and f.lower().endswith(ext):
                    made = os.path.join(folder, f)
                    break
        if not os.path.exists(made):
            return '내보내기 결과 파일을 찾지 못했습니다'
        if made != out_path:
            os.replace(made, out_path)
        return ''
    except Exception as ex:
        return f'변환 실패: {ex}'
    finally:
        try:
            if doc is not None:
                doc.Close()
        except Exception:
            pass


# ── 한 바퀴 처리 ──────────────────────────────────────────────────────────────
def run_once(dry=False, limit=20) -> int:
    try:
        r = requests.get(api('/catia/convert/queue?limit=%d' % limit),
                         headers=headers(), timeout=60)
    except Exception as ex:
        print('서버에 연결하지 못했습니다:', ex)
        return 0
    if r.status_code != 200:
        print('서버 응답 오류:', r.status_code, r.text[:200])
        return 0
    d = r.json()
    items = d.get('items') or []
    st = d.get('stats') or {}
    print('변환 대기 %s건 / 전체 원본 %s건 (완료 %s건)'
          % (st.get('pending'), st.get('orig'), st.get('done')))
    if not items:
        return 0
    if dry:
        for it in items[:10]:
            print('   [시험] %s → %s' % (it['filename'], it['want']))
        return 0

    tmp = tempfile.mkdtemp(prefix='dya_conv_')
    done = 0
    for it in items:
        src = os.path.join(tmp, it['filename'])
        out = os.path.splitext(src)[0] + it['want']
        print('\n▶ %s  (%.0f MB)' % (it['filename'], (it.get('size_no') or 0) / 1048576))
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

        err = catia_convert(src, out)
        if err:
            print('   ', err)
            continue

        try:
            with open(out, 'rb') as fh:
                up = requests.post(api('/catia/convert/result/%d' % it['id']),
                                   headers=headers(),
                                   files={'file': (os.path.basename(out), fh)},
                                   timeout=TIMEOUT)
            if up.status_code == 200 and (up.json().get('ok')):
                print('   올림 완료 →', os.path.basename(out))
                done += 1
            else:
                print('   올리기 실패:', up.status_code, up.text[:160])
        except Exception as ex:
            print('   올리기 실패:', ex)
        finally:
            for f in (src, out):
                try:
                    os.remove(f)
                except OSError:
                    pass
    return done


def main():
    ap = argparse.ArgumentParser(description='카티아 자동 변환 프로그램')
    ap.add_argument('--loop', type=int, default=0, help='초 단위 반복 간격 (0=한 번만)')
    ap.add_argument('--limit', type=int, default=20, help='한 번에 처리할 개수')
    ap.add_argument('--dry', action='store_true', help='CATIA 없이 연결·목록만 확인')
    a = ap.parse_args()

    if not AGENT_KEY:
        print('변환 열쇠가 없습니다.')
        print('  관리자 화면에서 열쇠를 복사한 뒤, 이 창에서 다음을 실행하세요:')
        print('    set DYA_AGENT_KEY=복사한열쇠')
        sys.exit(1)
    print('서버:', SERVER)

    while True:
        n = run_once(dry=a.dry, limit=a.limit)
        if n:
            print('\n%d건 변환했습니다.' % n)
        if not a.loop:
            break
        time.sleep(a.loop)


if __name__ == '__main__':
    main()
