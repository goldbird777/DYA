"""★통합 ALC2 코드 대장(REV) 파일에 신규 코드 행을 '빠르게' 이어붙인다.

openpyxl로 load_workbook→save 하면 3,111행×283열(88만 셀)을 전부 파싱·재직렬화하느라
15초가 걸린다. 이 모듈은 원본 xlsx(zip)를 그대로 복사하면서 대장 시트의 XML에만
<row> 몇 개를 끼워 넣으므로 1초 이내로 끝난다. 서식은 마지막 데이터 행의 스타일
인덱스(s=)를 그대로 물려받으므로 원본 서식이 100% 보존된다.
"""
import re
import shutil
import zipfile
from xml.sax.saxutils import escape

LEDGER_SHEET = '통합 ALC2 코드'
# 헤더명 → 논리 필드 (열 순서가 바뀌어도 헤더로 찾는다)
HEADER_MAP = {'NO': 'no', '차종': 'vehicle', 'ALC-2 CODE': 'alc2', 'KMC ALC-2 CODE': 'kmc'}


def _col_letter(ref):
    return re.match(r'([A-Z]+)', ref).group(1)


def _sheet_part(zf, sheet_name):
    """시트 이름 → xl/worksheets/sheetN.xml 경로."""
    wbx = zf.read('xl/workbook.xml').decode('utf-8')
    rid = None
    for m in re.finditer(r'<sheet\b[^>]*?name="([^"]+)"[^>]*?r:id="([^"]+)"[^>]*/>', wbx):
        if m.group(1) == sheet_name:
            rid = m.group(2)
            break
    if rid is None:  # 이름이 바뀐 경우 첫 시트
        m = re.search(r'<sheet\b[^>]*?r:id="([^"]+)"[^>]*/>', wbx)
        if not m:
            return None
        rid = m.group(1)
    rels = zf.read('xl/_rels/workbook.xml.rels').decode('utf-8')
    m = re.search(r'Id="%s"[^>]*Target="([^"]+)"' % re.escape(rid), rels)
    if not m:
        return None
    tgt = m.group(1).lstrip('/')
    return tgt if tgt.startswith('xl/') else 'xl/' + tgt


def find_columns(path, sheet_name=LEDGER_SHEET):
    """헤더 행을 찾아 {필드: 열문자} 와 마지막 NO 값을 돌려준다."""
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet_name] if sheet_name in wb.sheetnames else wb.worksheets[0]
    cols, hdr_row, ncol = {}, 0, 0
    for r, row in enumerate(ws.iter_rows(min_row=1, max_row=12, max_col=30, values_only=True), 1):
        vals = {str(v).strip().upper(): i for i, v in enumerate(row, 1) if v is not None}
        if 'KMC ALC-2 CODE' in vals:
            hdr_row = r
            for want, key in HEADER_MAP.items():
                if want in vals:
                    cols[key] = get_column_letter(vals[want])
                    if key == 'no':
                        ncol = vals[want]
            break
    last_no = 0
    if hdr_row and ncol:
        for row in ws.iter_rows(min_row=hdr_row + 1, min_col=ncol, max_col=ncol, values_only=True):
            try:
                last_no = int(str(row[0]).strip())
            except (TypeError, ValueError):
                pass
    wb.close()
    return cols, last_no


def _cell_xml(ref, style, value):
    s = ' s="%s"' % style if style else ''
    if value is None or value == '':
        return '<c r="%s"%s/>' % (ref, s)
    if isinstance(value, (int, float)):
        return '<c r="%s"%s><v>%s</v></c>' % (ref, s, value)
    return '<c r="%s"%s t="inlineStr"><is><t>%s</t></is></c>' % (ref, s, escape(str(value)))


def append_rows(src, dst, values_by_row, sheet_name=LEDGER_SHEET):
    """values_by_row: [{열문자: 값}, ...] — 마지막 데이터 행 아래에 순서대로 추가.
       반환: 추가된 행 수."""
    if not values_by_row:
        shutil.copy2(src, dst)
        return 0
    zin = zipfile.ZipFile(src)
    part = _sheet_part(zin, sheet_name)
    if part is None:
        zin.close(); shutil.copy2(src, dst); return 0
    xml = zin.read(part).decode('utf-8')

    end = xml.find('</sheetData>')
    if end < 0:
        zin.close(); shutil.copy2(src, dst); return 0
    start = xml.rfind('<row ', 0, end)
    if start < 0:
        zin.close(); shutil.copy2(src, dst); return 0
    last = xml[start:xml.index('</row>', start) + 6]

    m = re.match(r'<row\b([^>]*)>', last)
    attrs = m.group(1)
    last_no = int(re.search(r'r="(\d+)"', attrs).group(1))
    attrs_tpl = re.sub(r'\br="\d+"', 'r="{R}"', attrs)
    # 마지막 행의 셀별 스타일 인덱스 (서식 상속용)
    styles = {}
    for cm in re.finditer(r'<c\b([^>]*)/?>', last):
        a = cm.group(1)
        rm = re.search(r'r="([A-Z]+\d+)"', a)
        if not rm:
            continue
        sm = re.search(r's="(\d+)"', a)
        styles[_col_letter(rm.group(1))] = sm.group(1) if sm else ''
    order = sorted(styles, key=lambda c: (len(c), c))

    out = []
    for i, vals in enumerate(values_by_row, 1):
        rn = last_no + i
        cells = [_cell_xml('%s%d' % (c, rn), styles.get(c, ''), vals.get(c)) for c in order]
        for c in vals:  # 마지막 행에 없던 열도 채운다
            if c not in styles:
                cells.append(_cell_xml('%s%d' % (c, rn), '', vals[c]))
        out.append('<row%s>%s</row>' % (attrs_tpl.format(R=rn), ''.join(cells)))
    xml = xml[:end] + ''.join(out) + xml[end:]
    # dimension 갱신 (Excel이 관대하지만 맞춰준다)
    xml = re.sub(r'(<dimension ref="[A-Z]+\d+:[A-Z]+)\d+"',
                 lambda mm: '%s%d"' % (mm.group(1), last_no + len(values_by_row)), xml, count=1)

    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        for it in zin.infolist():
            data = xml.encode('utf-8') if it.filename == part else zin.read(it.filename)
            zout.writestr(it, data)
    zin.close()
    return len(values_by_row)
