# -*- coding: utf-8 -*-
"""카티아 자동 변환 — 더블클릭용 메뉴.

CATIA 가 깔린 사내 PC 에서 `catia_convert.bat` 을 더블클릭하면 이 화면이 뜬다.
서버(리눅스)는 CATIA 를 못 돌리므로 이 PC 가 변환을 대신한다.

  쓰기 전에 확인할 것
    1) CATIA 가 실행돼 있어야 한다(평소 쓰는 CATSTART 로 띄워 두면 된다).
    2) 처음 한 번만:  pip install requests pywin32

«변환 열쇠»는 소스에 적지 않는다 — 저장소가 공개라 같이 올라가면 안 된다.
처음 실행할 때 한 번 물어보고 옆의 agent_key.txt 에 저장한다(git 제외 대상).
열쇠는 사이트 «관리자 → 변환 열쇠»에서 볼 수 있고, 새로 발급했으면
agent_key.txt 를 지우고 다시 실행하면 된다.

메뉴 글자를 이 파이썬 쪽에 둔 이유: cmd.exe 는 배치 파일을 «콘솔 코드페이지»로
읽어서, 파일 인코딩이 다르면 한글이 명령어로 잘못 해석된다. 배치는 영문만 두고
사람이 읽는 글자는 전부 여기에 모았다.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(HERE, 'agent_key.txt')
AGENT = os.path.join(HERE, 'catia_convert_agent.py')
SERVER = 'https://dyaerp.cloud'
BAR = '=' * 60


def read_key():
    """열쇠를 읽는다. 없으면 한 번 물어보고 저장한다."""
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, encoding='utf-8') as fp:
            key = fp.read().strip()
        if key:
            return key
    print()
    print('  변환 열쇠가 아직 없습니다.')
    print('  사이트 [관리자] 화면의 «변환 열쇠»를 복사해서 붙여 넣으세요.')
    print()
    key = input('  변환 열쇠: ').strip()
    if not key:
        print('\n  열쇠를 입력하지 않아 종료합니다.')
        return ''
    with open(KEY_FILE, 'w', encoding='utf-8') as fp:
        fp.write(key + '\n')
    print('  agent_key.txt 에 저장했습니다. 다음부터는 묻지 않습니다.')
    return key


MENU = (
    ('1', '한 번만 변환하고 끝내기', ['--limit', '100']),
    ('2', '계속 감시하기 (5분마다, 창을 닫으면 멈춤)', ['--loop', '300', '--limit', '100']),
    ('3', '연결만 확인하기 (CATIA 를 건드리지 않음)', ['--check']),
)


def main():
    key = read_key()
    if not key:
        return 1

    print()
    print(BAR)
    print('  카티아 자동 변환      서버: %s' % SERVER)
    print(BAR)
    print()
    for num, label, _ in MENU:
        print('   %s. %s' % (num, label))
    print()
    sel = input('  번호를 고르세요 [1-3, 기본 1]: ').strip() or '1'

    args = next((a for n, _, a in MENU if n == sel), None)
    if args is None:
        print('  1~3 중에서 고르세요.')
        return 1

    env = dict(os.environ,
               DYA_SERVER=SERVER,
               DYA_AGENT_KEY=key,
               PYTHONIOENCODING='utf-8',
               PYTHONUNBUFFERED='1')
    print()
    rc = subprocess.call([sys.executable, AGENT] + args, cwd=HERE, env=env)
    print()
    print(BAR)
    print('  끝났습니다. 창을 닫으셔도 됩니다.' if rc == 0
          else '  오류로 끝났습니다(코드 %d). 위 내용을 확인하세요.' % rc)
    print(BAR)
    return rc


if __name__ == '__main__':
    sys.exit(main())
