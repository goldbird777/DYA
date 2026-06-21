"""
DYA BOM 검증 웹 서버 — FastAPI
"""
import os, shutil, tempfile, uuid, re
from fastapi import FastAPI, UploadFile, File, Request, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from auth import (init_db, create_user, get_user, verify_pw, create_token,
                  current_user, require_login, require_admin,
                  get_all_users, approve_user, reject_user, delete_user, set_role,
                  get_all_vehicle_codes, add_vehicle_code, update_vehicle_code, delete_vehicle_code,
                  get_vehicle_code_by_code, update_vehicle_code_by_code, delete_vehicle_code_by_code,
                  save_stored_bom, list_stored_boms, get_stored_bom, delete_stored_bom,
                  update_stored_bom_meta, find_duplicate_by_hash,
                  list_bom_template_revisions, get_active_bom_template,
                  get_bom_template_revision, add_bom_template_revision,
                  activate_bom_template_revision, delete_bom_template_revision,
                  update_bom_template_note)

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, 'reports')
DATA_DIR    = os.path.join(BASE_DIR, 'data')
os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

PEL_CODE_PATH = os.path.join(DATA_DIR, 'pel_code_master.xlsx')
STORED_BOM_DIR = os.path.join(DATA_DIR, 'stored_boms')
os.makedirs(STORED_BOM_DIR, exist_ok=True)

init_db()

STATIC_DIR = os.path.join(BASE_DIR, 'static')
os.makedirs(STATIC_DIR, exist_ok=True)

app       = FastAPI(title='DYA BOM 검증 시스템')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, 'templates'))


# ── 인증 페이지 ───────────────────────────────────────────────────────────────
@app.get('/login', response_class=HTMLResponse)
async def login_page(request: Request, next: str = '/'):
    user = current_user(request)
    if user:
        return RedirectResponse('/', status_code=302)
    return templates.TemplateResponse(request=request, name='login.html',
                                      context={'next': next, 'error': ''})


@app.post('/login', response_class=HTMLResponse)
async def login_post(request: Request,
                     username: str = Form(...),
                     password: str = Form(...),
                     next: str    = Form('/login')):
    user = get_user(username)
    error = ''
    if not user or not verify_pw(password, user['hashed_pw']):
        error = '아이디 또는 비밀번호가 올바르지 않습니다.'
    elif user['role'] == 'pending':
        error = '관리자 승인 대기 중입니다. 승인 후 이용하실 수 있습니다.'
    elif user['role'] == 'rejected':
        error = '승인이 거부된 계정입니다. 관리자에게 문의하세요.'

    if error:
        return templates.TemplateResponse(request=request, name='login.html',
                                          context={'next': next, 'error': error})

    token = create_token(username)
    redirect_url = next if next.startswith('/') else '/'
    resp = RedirectResponse(redirect_url, status_code=302)
    resp.set_cookie('bom_token', token, httponly=True, max_age=60*60*8, samesite='lax')
    return resp


@app.get('/logout')
async def logout():
    resp = RedirectResponse('/login', status_code=302)
    resp.delete_cookie('bom_token')
    return resp


@app.get('/register', response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request=request, name='register.html',
                                      context={'error': '', 'success': False})


@app.post('/register', response_class=HTMLResponse)
async def register_post(request: Request,
                        username: str = Form(...),
                        email:    str = Form(...),
                        password: str = Form(...),
                        password2:str = Form(...),
                        dept:     str = Form('')):
    error = ''
    if password != password2:
        error = '비밀번호가 일치하지 않습니다.'
    elif len(password) < 6:
        error = '비밀번호는 6자 이상이어야 합니다.'
    elif len(username) < 3:
        error = '아이디는 3자 이상이어야 합니다.'

    if not error:
        result = create_user(username, email, password, dept)
        if not result['ok']:
            error = result['msg']

    if error:
        return templates.TemplateResponse(request=request, name='register.html',
                                          context={'error': error, 'success': False})
    return templates.TemplateResponse(request=request, name='register.html',
                                      context={'error': '', 'success': True})


# ── 관리자 페이지 ─────────────────────────────────────────────────────────────
@app.get('/admin', response_class=HTMLResponse)
async def admin_page(request: Request):
    redir = require_admin(request)
    if redir: return redir
    users = get_all_users()
    me = current_user(request)
    vcodes = get_all_vehicle_codes()
    return templates.TemplateResponse(request=request, name='admin.html',
                                      context={'users': users, 'me': me, 'vcodes': vcodes})


@app.post('/admin/approve/{user_id}')
async def admin_approve(request: Request, user_id: int):
    if require_admin(request): return RedirectResponse('/login', status_code=302)
    approve_user(user_id)
    return RedirectResponse('/admin', status_code=302)


@app.post('/admin/reject/{user_id}')
async def admin_reject(request: Request, user_id: int):
    if require_admin(request): return RedirectResponse('/login', status_code=302)
    reject_user(user_id)
    return RedirectResponse('/admin', status_code=302)


@app.post('/admin/delete/{user_id}')
async def admin_delete(request: Request, user_id: int):
    if require_admin(request): return RedirectResponse('/login', status_code=302)
    delete_user(user_id)
    return RedirectResponse('/admin', status_code=302)


@app.post('/admin/role/{user_id}')
async def admin_role(request: Request, user_id: int, role: str = Form(...)):
    if require_admin(request): return RedirectResponse('/login', status_code=302)
    set_role(user_id, role)
    return RedirectResponse('/admin', status_code=302)


@app.post('/admin/vehicle-code/add')
async def admin_vehicle_add(request: Request,
                             code: str = Form(...), name: str = Form(...), memo: str = Form('')):
    if require_admin(request): return RedirectResponse('/login', status_code=302)
    add_vehicle_code(code, name, memo)
    return RedirectResponse('/admin#tab-vehicle', status_code=302)


@app.post('/admin/vehicle-code/delete/{code_id}')
async def admin_vehicle_delete(request: Request, code_id: int):
    if require_admin(request): return RedirectResponse('/login', status_code=302)
    delete_vehicle_code(code_id)
    return RedirectResponse('/admin#tab-vehicle', status_code=302)


@app.post('/admin/vehicle-code/edit/{code_id}')
async def admin_vehicle_edit(request: Request, code_id: int,
                              code: str = Form(...), name: str = Form(...), memo: str = Form('')):
    if require_admin(request): return RedirectResponse('/login', status_code=302)
    update_vehicle_code(code_id, code, name, memo)
    return RedirectResponse('/admin#tab-vehicle', status_code=302)


# ── 메인 페이지 ───────────────────────────────────────────────────────────────
@app.get('/', response_class=HTMLResponse)
async def index(request: Request):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    return templates.TemplateResponse(request=request, name='index.html',
                                      context={'me': me})


# ── BOM 검증 API ──────────────────────────────────────────────────────────────
@app.post('/validate')
async def validate(request: Request, file: UploadFile = File(...)):
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)

    if not file.filename.endswith(('.xlsx', '.xlsm')):
        return JSONResponse({'error': 'xlsx 또는 xlsm 파일만 지원합니다.'}, status_code=400)

    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        from bom_parser import parse_bom
        from validators import validate_bom
        from report import make_report

        rows, variant_cols, struck_parts, highlighted_parts = parse_bom(tmp_path)
        errors, lv1_by_vc = validate_bom(rows, variant_cols)

        report_id   = uuid.uuid4().hex[:10]
        report_path = os.path.join(REPORTS_DIR, f'BOM_검증_{report_id}.xlsx')
        make_report(file.filename, errors, lv1_by_vc, variant_cols,
                    struck_parts, highlighted_parts, report_path)

        return JSONResponse({
            'filename':          file.filename,
            'variant_count':     len(lv1_by_vc),
            'struck_count':      len(struck_parts),
            'highlighted_count': len(highlighted_parts),
            'err_count':         sum(1 for e in errors if e['severity'] == 'ERROR'),
            'warn_count':        sum(1 for e in errors if e['severity'] == 'WARNING'),
            'report_id':         report_id,
            'errors':            errors,
            'lv1_variants': [
                {
                    'vc':          vc,
                    'pno':         r['pno'],
                    'desc':        r['desc'],
                    'has_error':   any(e['variant'] == vc and e['severity'] == 'ERROR'   for e in errors),
                    'has_warning': any(e['variant'] == vc and e['severity'] == 'WARNING' for e in errors),
                }
                for vc, r in sorted(lv1_by_vc.items())
            ],
        })

    except Exception as ex:
        import traceback
        return JSONResponse({'error': f'파싱 오류: {str(ex)}',
                             'trace': traceback.format_exc()}, status_code=500)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.get('/viewer', response_class=HTMLResponse)
async def viewer_page(request: Request):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    return templates.TemplateResponse(request=request, name='index.html',
                                      context={'me': me})


VIEWER_FILES: dict = {}  # file_id -> (path, original_filename)

@app.post('/view-excel')
async def view_excel(request: Request, file: UploadFile = File(...)):
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)

    if not file.filename.endswith(('.xlsx', '.xlsm')):
        return JSONResponse({'error': 'xlsx 또는 xlsm 파일만 지원합니다.'}, status_code=400)

    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        from excel_viewer import parse_excel
        sheets = parse_excel(tmp_path)
        file_id = uuid.uuid4().hex[:12]
        VIEWER_FILES[file_id] = (tmp_path, file.filename)
        return JSONResponse({'filename': file.filename, 'sheets': sheets, 'file_id': file_id})
    except Exception as ex:
        import traceback
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return JSONResponse({'error': str(ex), 'trace': traceback.format_exc()}, status_code=500)


@app.get('/download-excel/{file_id}')
async def download_excel(request: Request, file_id: str):
    redir = require_login(request)
    if redir: return redir
    if not re.fullmatch(r'[a-f0-9]{12}', file_id):
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    entry = VIEWER_FILES.get(file_id)
    if not entry or not os.path.exists(entry[0]):
        return JSONResponse({'error': '파일을 찾을 수 없습니다. 다시 업로드해 주세요.'}, status_code=404)
    path, original_name = entry
    media = ('application/vnd.ms-excel.sheet.macroEnabled.12'
             if original_name.endswith('.xlsm')
             else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    return FileResponse(path, filename=original_name, media_type=media)


# ── 차종 마스터 ───────────────────────────────────────────────────────────────
@app.get('/vehicles', response_class=HTMLResponse)
async def vehicles_page(request: Request):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    rows = get_all_vehicle_codes()
    return templates.TemplateResponse(request=request, name='vehicles.html',
                                      context={'me': me, 'rows': rows})


@app.get('/vehicles/api/list')
async def vehicles_api_list(request: Request):
    """드롭다운/자동완성용 차종 JSON"""
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    rows = get_all_vehicle_codes()
    return JSONResponse({'vehicles': [{'code': r['code'], 'name': r['name'], 'memo': r.get('memo', '')} for r in rows]})


@app.post('/vehicles/row')
async def vehicles_add_row(request: Request):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    try:
        item = await request.json()
    except Exception:
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    code = str(item.get('code', '')).strip().upper()
    name = str(item.get('name', '')).strip()
    memo = str(item.get('memo', '')).strip()
    if not code or not name:
        return JSONResponse({'error': '코드와 차종명은 필수입니다.'}, status_code=400)
    if get_vehicle_code_by_code(code):
        return JSONResponse({'error': f'이미 존재하는 차종 코드: {code}'}, status_code=400)
    add_vehicle_code(code, name, memo)
    return JSONResponse({'ok': True, 'code': code})


@app.post('/vehicles/row/{code}')
async def vehicles_update_row(request: Request, code: str):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    try:
        item = await request.json()
    except Exception:
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    new_code = str(item.get('code', code)).strip().upper()
    name = str(item.get('name', '')).strip()
    memo = str(item.get('memo', '')).strip()
    if not new_code or not name:
        return JSONResponse({'error': '코드와 차종명은 필수입니다.'}, status_code=400)
    if not get_vehicle_code_by_code(code):
        return JSONResponse({'error': f'차종 코드 {code}을(를) 찾을 수 없습니다.'}, status_code=404)
    if new_code != code.strip().upper() and get_vehicle_code_by_code(new_code):
        return JSONResponse({'error': f'이미 존재하는 코드로 변경 불가: {new_code}'}, status_code=400)
    result = update_vehicle_code_by_code(code, new_code, name, memo)
    if not result.get('ok'):
        return JSONResponse({'error': result.get('msg', '저장 실패')}, status_code=400)
    return JSONResponse({'ok': True, 'code': new_code})


@app.post('/vehicles/row/{code}/delete')
async def vehicles_delete_row(request: Request, code: str):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    try:
        body = await request.json()
        password = str(body.get('password', ''))
    except Exception:
        password = ''
    if not password:
        return JSONResponse({'error': '비밀번호를 입력해주세요.'}, status_code=400)
    me = current_user(request)
    if not me:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    user_data = get_user(me['username'])
    if not user_data or not verify_pw(password, user_data['hashed_pw']):
        return JSONResponse({'error': '비밀번호가 일치하지 않습니다.'}, status_code=403)
    if not get_vehicle_code_by_code(code):
        return JSONResponse({'error': f'차종 코드 {code}을(를) 찾을 수 없습니다.'}, status_code=404)
    delete_vehicle_code_by_code(code)
    return JSONResponse({'ok': True})


# ── 저장된 BOM 관리 (Step 2/3) ────────────────────────────────────────────────
@app.post('/bom-storage/save')
async def bom_storage_save(request: Request,
                            file: UploadFile = File(...),
                            vehicle_code: str = Form(...),
                            row_num: str = Form(...),
                            position: str = Form(...),
                            kind: str = Form(...),
                            memo: str = Form(''),
                            force: str = Form('false')):
    """업로드된 파일을 메타데이터와 함께 영구 저장.
       동일 해시 파일이 같은 (차종/열/위치/kind)에 이미 있으면 409 응답 (force=true 면 무시하고 저장)."""
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    me = current_user(request)

    if kind not in ('verify', 'viewer'):
        return JSONResponse({'error': '잘못된 kind 값'}, status_code=400)
    fname = (file.filename or '').lower()
    if not fname.endswith(('.xlsx', '.xlsm', '.xls')):
        return JSONResponse({'error': 'xlsx/xlsm/xls 파일만 지원합니다.'}, status_code=400)

    # 파일 내용을 한 번 메모리로 읽어 해시 계산
    import hashlib
    file_bytes = await file.read()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    vc = vehicle_code.strip().upper()
    rn = str(row_num).strip()
    ps = position.strip()
    force_flag = str(force).lower() in ('true', '1', 'yes')

    # 중복 검사
    if not force_flag:
        dup = find_duplicate_by_hash(vc, rn, ps, kind, file_hash)
        if dup:
            return JSONResponse({
                'duplicate': True,
                'existing': {
                    'file_id': dup['file_id'],
                    'version_num': dup['version_num'],
                    'filename': dup['filename'],
                    'uploaded_at': dup['uploaded_at'],
                    'uploader': dup['uploader'],
                    'memo': dup.get('memo', ''),
                },
                'message': '동일한 파일이 이미 같은 위치에 저장되어 있습니다.'
            }, status_code=409)

    file_id = uuid.uuid4().hex[:16]
    ext = os.path.splitext(file.filename or '')[1] or '.xlsx'
    saved_path = os.path.join(STORED_BOM_DIR, f'{file_id}{ext}')
    with open(saved_path, 'wb') as f:
        f.write(file_bytes)

    result = save_stored_bom(
        vehicle_code=vc, row_num=rn, position=ps, kind=kind,
        filename=file.filename or 'unknown.xlsx',
        file_id=file_id, file_path=saved_path,
        uploader=me['username'], memo=memo.strip(),
        file_hash=file_hash,
    )
    return JSONResponse({'ok': True, 'file_id': file_id, 'version': result.get('version')})


@app.post('/bom-storage/{file_id}/meta')
async def bom_storage_update_meta(request: Request, file_id: str):
    """저장본의 차종/열/위치/메모 사후 수정 (admin)."""
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    entry = get_stored_bom(file_id)
    if not entry:
        return JSONResponse({'error': '저장본을 찾을 수 없습니다.'}, status_code=404)
    try:
        item = await request.json()
    except Exception:
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    fields = {}
    if 'vehicle_code' in item: fields['vehicle_code'] = str(item['vehicle_code']).strip().upper()
    if 'row_num' in item:      fields['row_num']      = str(item['row_num']).strip()
    if 'position' in item:     fields['position']     = str(item['position']).strip()
    if 'memo' in item:         fields['memo']         = str(item['memo']).strip()
    if not fields:
        return JSONResponse({'error': '수정할 필드가 없습니다.'}, status_code=400)
    ok = update_stored_bom_meta(file_id, **fields)
    if not ok:
        return JSONResponse({'error': '수정 실패'}, status_code=400)
    return JSONResponse({'ok': True})


@app.get('/bom-storage/versions')
async def bom_storage_versions(request: Request,
                                vehicle_code: str = '',
                                row_num: str = '',
                                position: str = '',
                                kind: str = ''):
    """저장된 BOM 목록 (조회 카드용)"""
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    rows = list_stored_boms(
        vehicle_code=vehicle_code.strip().upper() if vehicle_code else None,
        row_num=row_num.strip() if row_num else None,
        position=position.strip() if position else None,
        kind=kind.strip() if kind else None,
    )
    return JSONResponse({'versions': rows})


@app.get('/bom-storage/load/{file_id}')
async def bom_storage_load(request: Request, file_id: str):
    """저장된 파일을 재분석. kind에 따라 validate 또는 view-excel 결과 반환."""
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    entry = get_stored_bom(file_id)
    if not entry:
        return JSONResponse({'error': '저장된 BOM을 찾을 수 없습니다.'}, status_code=404)
    path = entry['file_path']
    if not os.path.exists(path):
        return JSONResponse({'error': '파일이 손상되거나 삭제되었습니다.'}, status_code=404)

    if entry['kind'] == 'verify':
        try:
            from bom_parser import parse_bom
            from validators import validate_bom
            from report import make_report
            rows, variant_cols, struck_parts, highlighted_parts = parse_bom(path)
            errors, lv1_by_vc = validate_bom(rows, variant_cols)
            report_id   = uuid.uuid4().hex[:10]
            report_path = os.path.join(REPORTS_DIR, f'BOM_검증_{report_id}.xlsx')
            make_report(entry['filename'], errors, lv1_by_vc, variant_cols,
                        struck_parts, highlighted_parts, report_path)
            return JSONResponse({
                'kind': 'verify',
                'meta': entry,
                'filename':          entry['filename'],
                'variant_count':     len(lv1_by_vc),
                'struck_count':      len(struck_parts),
                'highlighted_count': len(highlighted_parts),
                'err_count':         sum(1 for e in errors if e['severity'] == 'ERROR'),
                'warn_count':        sum(1 for e in errors if e['severity'] == 'WARNING'),
                'report_id':         report_id,
                'errors':            errors,
                'lv1_variants': [
                    {'vc': vc, 'pno': r['pno'], 'desc': r['desc'],
                     'has_error':   any(e['variant'] == vc and e['severity'] == 'ERROR'   for e in errors),
                     'has_warning': any(e['variant'] == vc and e['severity'] == 'WARNING' for e in errors),}
                    for vc, r in sorted(lv1_by_vc.items())
                ],
            })
        except Exception as ex:
            import traceback
            return JSONResponse({'error': f'재분석 오류: {ex}', 'trace': traceback.format_exc()}, status_code=500)
    else:
        # viewer
        try:
            from excel_viewer import parse_excel
            sheets = parse_excel(path)
            view_id = uuid.uuid4().hex[:12]
            VIEWER_FILES[view_id] = (path, entry['filename'])
            return JSONResponse({
                'kind': 'viewer',
                'meta': entry,
                'filename': entry['filename'],
                'sheets': sheets,
                'file_id': view_id
            })
        except Exception as ex:
            import traceback
            return JSONResponse({'error': str(ex), 'trace': traceback.format_exc()}, status_code=500)


@app.post('/bom-storage/{file_id}/delete')
async def bom_storage_delete(request: Request, file_id: str):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    entry = get_stored_bom(file_id)
    if not entry:
        return JSONResponse({'error': '찾을 수 없습니다.'}, status_code=404)
    try:
        if os.path.exists(entry['file_path']):
            os.unlink(entry['file_path'])
    except Exception:
        pass
    delete_stored_bom(file_id)
    return JSONResponse({'ok': True})


# ── M-BOM 코드 변경 (생관 — 양식 수신 대기 중) ────────────────────────────────
@app.get('/m-bom', response_class=HTMLResponse)
async def m_bom_page(request: Request):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    return templates.TemplateResponse(request=request, name='m_bom.html',
                                      context={'me': me})


# ── BOM 자동 생성 (부품사양서 → BOM) ──────────────────────────────────────────
GENERATED_BOMS: dict = {}  # file_id -> (out_path, filename, spec_path)


@app.get('/bom-generate', response_class=HTMLResponse)
async def bom_generate_page(request: Request):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    return templates.TemplateResponse(request=request, name='auto_bom.html',
                                      context={'me': me})


@app.post('/bom-generate/upload')
async def bom_generate_upload(request: Request, file: UploadFile = File(...)):
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    fname = (file.filename or '').lower()
    if not fname.endswith(('.xlsx', '.xls')):
        return JSONResponse({'error': 'xlsx 또는 xls 파일만 지원합니다.'}, status_code=400)

    suffix = os.path.splitext(fname)[1]
    file_id = uuid.uuid4().hex[:12]
    spec_keep_path = os.path.join(REPORTS_DIR, f'spec_{file_id}{suffix}')
    with open(spec_keep_path, 'wb') as f:
        shutil.copyfileobj(file.file, f)

    out_name = f'BOM_자동생성_{file_id}.xlsx'
    out_path = os.path.join(REPORTS_DIR, out_name)

    active_tpl = get_active_bom_template()
    tpl_path = active_tpl['file_path'] if active_tpl else None

    try:
        from bom_generator import generate_bom
        result = generate_bom(spec_keep_path, PEL_CODE_PATH, out_path,
                              template_path=tpl_path)
        GENERATED_BOMS[file_id] = (out_path, file.filename or 'BOM.xlsx', spec_keep_path)
        result['file_id'] = file_id
        if active_tpl:
            result['template_rev'] = active_tpl.get('rev_num')
            result['template_filename'] = active_tpl.get('filename')
        return JSONResponse(result)
    except Exception as ex:
        import traceback
        if os.path.exists(spec_keep_path):
            try: os.unlink(spec_keep_path)
            except: pass
        return JSONResponse({'error': f'BOM 생성 오류: {ex}',
                             'trace': traceback.format_exc()}, status_code=500)


@app.post('/bom-generate/regenerate/{file_id}')
async def bom_generate_regenerate(request: Request, file_id: str):
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    if not re.fullmatch(r'[a-f0-9]{12}', file_id):
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    entry = GENERATED_BOMS.get(file_id)
    if not entry:
        return JSONResponse({'error': '원본 파일이 만료되었습니다. 다시 업로드해주세요.'}, status_code=404)
    out_path, orig_name, spec_path = entry
    if not os.path.exists(spec_path):
        return JSONResponse({'error': '원본 파일이 없습니다. 다시 업로드해주세요.'}, status_code=404)
    active_tpl = get_active_bom_template()
    tpl_path = active_tpl['file_path'] if active_tpl else None
    try:
        from bom_generator import generate_bom
        result = generate_bom(spec_path, PEL_CODE_PATH, out_path,
                              template_path=tpl_path)
        result['file_id'] = file_id
        if active_tpl:
            result['template_rev'] = active_tpl.get('rev_num')
            result['template_filename'] = active_tpl.get('filename')
        return JSONResponse(result)
    except Exception as ex:
        import traceback
        return JSONResponse({'error': f'재생성 오류: {ex}',
                             'trace': traceback.format_exc()}, status_code=500)


@app.get('/bom-generate/download/{file_id}')
async def bom_generate_download(request: Request, file_id: str):
    redir = require_login(request)
    if redir: return redir
    if not re.fullmatch(r'[a-f0-9]{12}', file_id):
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    entry = GENERATED_BOMS.get(file_id)
    if not entry or not os.path.exists(entry[0]):
        return JSONResponse({'error': '파일을 찾을 수 없습니다. 다시 생성해주세요.'}, status_code=404)
    path, orig = entry[0], entry[1]
    base = os.path.splitext(orig)[0]
    dl_name = f'{base}_BOM.xlsx'
    return FileResponse(path, filename=dl_name,
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


# ── 표준화 BOM 템플릿 (리비전 관리) ────────────────────────────────────────────
BOM_TEMPLATE_DIR = os.path.join(DATA_DIR, 'bom_templates')
os.makedirs(BOM_TEMPLATE_DIR, exist_ok=True)
# 호환용: 첫 업로드 때 만들어진 단일 템플릿이 있으면 자동 마이그레이션
_LEGACY_TPL = os.path.join(DATA_DIR, 'bom_template.xlsx')
_LEGACY_META = os.path.join(DATA_DIR, 'bom_template_meta.json')


def _migrate_legacy_template():
    if not os.path.exists(_LEGACY_TPL):
        return
    if list_bom_template_revisions():
        return  # 이미 리비전 있음 → 마이그레이션 불필요
    import json
    meta = {}
    if os.path.exists(_LEGACY_META):
        try:
            with open(_LEGACY_META, 'r', encoding='utf-8') as f:
                meta = json.load(f)
        except Exception:
            pass
    fname = meta.get('filename') or 'bom_template.xlsx'
    uploader = meta.get('uploaded_by') or 'admin'
    new_path = os.path.join(BOM_TEMPLATE_DIR, 'rev_001.xlsx')
    shutil.copy2(_LEGACY_TPL, new_path)
    add_bom_template_revision(fname, new_path, uploader,
                              note='최초 업로드 (자동 마이그레이션)')


_migrate_legacy_template()


@app.get('/bom-generate/template/info')
async def bom_template_info(request: Request):
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    active = get_active_bom_template()
    return JSONResponse({'exists': active is not None, 'info': active})


@app.get('/bom-generate/template/list')
async def bom_template_list(request: Request):
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    revs = list_bom_template_revisions()
    # 파일 존재 여부도 같이 알려줌
    for r in revs:
        r['file_exists'] = os.path.exists(r.get('file_path', ''))
    return JSONResponse({'revisions': revs})


@app.post('/bom-generate/template/upload')
async def bom_template_upload(request: Request,
                              file: UploadFile = File(...),
                              note: str = Form('')):
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    me = current_user(request)
    if not me or me.get('role') != 'admin':
        return JSONResponse({'error': '관리자만 업로드할 수 있습니다.'}, status_code=403)
    fname = (file.filename or '').lower()
    if not fname.endswith('.xlsx'):
        return JSONResponse({'error': 'xlsx 파일만 지원합니다.'}, status_code=400)

    # 다음 리비전 번호 계산
    existing = list_bom_template_revisions()
    next_rev = (max((r['rev_num'] for r in existing), default=0)) + 1
    save_path = os.path.join(BOM_TEMPLATE_DIR, f'rev_{next_rev:03d}.xlsx')
    with open(save_path, 'wb') as f:
        shutil.copyfileobj(file.file, f)

    info = add_bom_template_revision(
        filename=file.filename or f'bom_template_rev{next_rev}.xlsx',
        file_path=save_path,
        uploaded_by=me.get('username', ''),
        note=(note or '').strip(),
    )
    return JSONResponse({'ok': True, 'info': info})


@app.post('/bom-generate/template/{rev_id}/activate')
async def bom_template_activate(request: Request, rev_id: int):
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    me = current_user(request)
    if not me or me.get('role') != 'admin':
        return JSONResponse({'error': '관리자만 변경할 수 있습니다.'}, status_code=403)
    ok = activate_bom_template_revision(rev_id)
    if not ok:
        return JSONResponse({'error': '해당 리비전을 찾을 수 없습니다.'}, status_code=404)
    return JSONResponse({'ok': True, 'active': get_active_bom_template()})


@app.post('/bom-generate/template/{rev_id}/delete')
async def bom_template_delete(request: Request, rev_id: int):
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    me = current_user(request)
    if not me or me.get('role') != 'admin':
        return JSONResponse({'error': '관리자만 삭제할 수 있습니다.'}, status_code=403)
    info = delete_bom_template_revision(rev_id)
    if not info:
        return JSONResponse({'error': '해당 리비전을 찾을 수 없습니다.'}, status_code=404)
    # 물리 파일 삭제
    fp = info.get('file_path')
    if fp and os.path.exists(fp):
        try: os.unlink(fp)
        except Exception: pass
    return JSONResponse({'ok': True, 'deleted_rev': info.get('rev_num'),
                         'active': get_active_bom_template()})


@app.post('/bom-generate/template/{rev_id}/note')
async def bom_template_set_note(request: Request, rev_id: int, note: str = Form('')):
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    me = current_user(request)
    if not me or me.get('role') != 'admin':
        return JSONResponse({'error': '관리자만 변경할 수 있습니다.'}, status_code=403)
    ok = update_bom_template_note(rev_id, (note or '').strip())
    if not ok:
        return JSONResponse({'error': '해당 리비전을 찾을 수 없습니다.'}, status_code=404)
    return JSONResponse({'ok': True})


@app.get('/bom-generate/template/{rev_id}/download')
async def bom_template_download_rev(request: Request, rev_id: int):
    redir = require_login(request)
    if redir: return redir
    info = get_bom_template_revision(rev_id)
    if not info or not os.path.exists(info['file_path']):
        return JSONResponse({'error': '파일을 찾을 수 없습니다.'}, status_code=404)
    return FileResponse(info['file_path'], filename=info['filename'],
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.get('/bom-generate/template/download')
async def bom_template_download(request: Request):
    redir = require_login(request)
    if redir: return redir
    active = get_active_bom_template()
    if not active or not os.path.exists(active['file_path']):
        return JSONResponse({'error': '활성 템플릿이 없습니다.'}, status_code=404)
    return FileResponse(active['file_path'], filename=active['filename'],
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


# ── PEL CODE 마스터 ───────────────────────────────────────────────────────────
PEL_STD_COLS = ['구분', 'CODE', '사양', '설명', '비고']  # 표준 5컬럼
PEL_COL_ALIASES = {
    '사양': ['사양', '명칭', 'NAME', 'SPEC'],
    '비고': ['비고', '분류', 'CATEGORY', 'CLASS', 'NOTE', 'REMARK'],
    '구분': ['구분', 'TYPE', 'KIND'],
    'CODE': ['CODE', 'PEL', 'PELCODE'],
    '설명': ['설명', 'DESCRIPTION', 'DESC'],
}


def _normalize_pel_df(df):
    """기존 컬럼명을 표준 5컬럼으로 매핑/정렬. 누락 컬럼은 빈 컬럼 추가."""
    import pandas as pd
    rename = {}
    used = set()
    for std, aliases in PEL_COL_ALIASES.items():
        for c in df.columns:
            if c in used: continue
            cs = str(c).strip()
            cu = cs.upper()
            for al in aliases:
                if cs == al or cu == al.upper():
                    rename[c] = std
                    used.add(c)
                    break
            if c in used: break
    df = df.rename(columns=rename)
    # 표준 컬럼 외 잔여 컬럼은 그대로 유지(끝쪽), 표준 컬럼이 없으면 추가
    for std in PEL_STD_COLS:
        if std not in df.columns:
            df[std] = ''
    # 표준 컬럼 먼저, 나머지 뒤
    extras = [c for c in df.columns if c not in PEL_STD_COLS]
    df = df[PEL_STD_COLS + extras]
    return df


def _gubun_sort_key(v):
    """구분 정렬 키 (튜플 — hashable): '1열' → (1,''), 그 외는 큰 값."""
    s = str(v or '').strip()
    if not s: return (9999, '')
    m = re.match(r'^(\d+)\s*열', s)
    if m: return (int(m.group(1)), '')
    if s.isdigit(): return (int(s), '')
    return (9000, s)


def _code_sort_key(v):
    """CODE 자연정렬 키 (튜플 — hashable): 숫자는 (0, int), 문자는 (1, str)."""
    s = str(v or '').strip()
    parts = []
    for t in re.findall(r'\d+|\D+', s):
        if t.isdigit():
            parts.append((0, int(t)))
        else:
            parts.append((1, t.lower()))
    return tuple(parts)


def _load_pel_df():
    """PEL 마스터 DataFrame을 표준 컬럼으로 로드 + 기본 정렬 (구분→CODE). 없으면 빈 DF."""
    import pandas as pd
    if not os.path.exists(PEL_CODE_PATH):
        return pd.DataFrame(columns=PEL_STD_COLS)
    df = pd.read_excel(PEL_CODE_PATH, sheet_name=0).fillna('')
    df = _normalize_pel_df(df)
    # 기본 정렬: 구분(1열→2열→3열…) → CODE(영숫자 자연정렬)
    if '구분' in df.columns and 'CODE' in df.columns and len(df):
        order = sorted(
            range(len(df)),
            key=lambda i: (_gubun_sort_key(df['구분'].iloc[i]),
                           _code_sort_key(df['CODE'].iloc[i])),
        )
        df = df.iloc[order].reset_index(drop=True)
    return df


def _save_pel_df(df):
    df.to_excel(PEL_CODE_PATH, index=False)


def _read_pel_code():
    """기존 시그니처 호환: (cols, rows, mtime) 반환."""
    if not os.path.exists(PEL_CODE_PATH):
        return [], [], None
    try:
        df = _load_pel_df()
        cols = [str(c) for c in df.columns]
        rows = [[str(c) for c in row] for row in df.values.tolist()]
        from datetime import datetime
        mtime = datetime.fromtimestamp(os.path.getmtime(PEL_CODE_PATH)).strftime('%Y-%m-%d %H:%M')
        return cols, rows, mtime
    except Exception:
        return [], [], None


@app.get('/pel-code', response_class=HTMLResponse)
async def pel_code_page(request: Request):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    cols, rows, mtime = _read_pel_code()
    return templates.TemplateResponse(request=request, name='pel_code.html',
                                      context={'me': me, 'cols': cols, 'rows': rows,
                                               'mtime': mtime or '-'})


@app.post('/pel-code/upload')
async def pel_code_upload(request: Request, file: UploadFile = File(...)):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    fname = (file.filename or '').lower()
    if not fname.endswith(('.xls', '.xlsx')):
        return JSONResponse({'error': 'xls 또는 xlsx 파일만 지원합니다.'}, status_code=400)
    suffix = os.path.splitext(fname)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        import pandas as pd
        df = pd.read_excel(tmp_path, sheet_name=0)
        df.to_excel(PEL_CODE_PATH, index=False)
        return RedirectResponse('/pel-code', status_code=302)
    except Exception as ex:
        return JSONResponse({'error': f'파일 처리 오류: {ex}'}, status_code=400)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.get('/pel-code/download')
async def pel_code_download(request: Request):
    redir = require_login(request)
    if redir: return redir
    if not os.path.exists(PEL_CODE_PATH):
        return JSONResponse({'error': '파일이 없습니다.'}, status_code=404)
    return FileResponse(PEL_CODE_PATH, filename='PEL_CODE_마스터.xlsx',
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.get('/pel-code/api/list')
async def pel_code_api_list(request: Request, q: str = ''):
    """사이드 패널/검색용 PEL 마스터 JSON 조회"""
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    cols, rows, mtime = _read_pel_code()
    if q:
        ql = q.lower().strip()
        rows = [r for r in rows if any(ql in str(c).lower() for c in r)]
    total = len(rows)
    truncated = total > 300
    return JSONResponse({
        'cols': cols,
        'rows': rows[:300],
        'total': total,
        'truncated': truncated,
        'mtime': mtime or '',
    })


@app.post('/pel-code/row')
async def pel_code_add_row(request: Request):
    """단일 행 추가 — 관리자(admin) 만 가능."""
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    try:
        item = await request.json()
    except Exception:
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    code = str(item.get('CODE', item.get('code', ''))).strip()
    if not code:
        return JSONResponse({'error': 'CODE는 필수 입력입니다.'}, status_code=400)
    df = _load_pel_df()
    existing = set(str(x).strip() for x in df['CODE'].astype(str))
    if code in existing:
        return JSONResponse({'error': f'이미 존재하는 CODE: {code}'}, status_code=400)
    new_row = {
        '구분': str(item.get('구분', '')).strip(),
        'CODE': code,
        '사양': str(item.get('사양', item.get('명칭', ''))).strip(),
        '설명': str(item.get('설명', '')).strip(),
        '비고': str(item.get('비고', item.get('분류', ''))).strip(),
    }
    import pandas as pd
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    _save_pel_df(df)
    return JSONResponse({'ok': True, 'code': code, 'total': len(df)})


@app.post('/pel-code/row/{code}')
async def pel_code_update_row(request: Request, code: str):
    """단일 행 업데이트 — 코드 변경도 가능. 관리자만 가능."""
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    try:
        item = await request.json()
    except Exception:
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    old_code = code.strip()
    new_code = str(item.get('CODE', item.get('code', old_code))).strip()
    if not new_code:
        return JSONResponse({'error': 'CODE는 필수 입력입니다.'}, status_code=400)
    df = _load_pel_df()
    mask = df['CODE'].astype(str).str.strip() == old_code
    if not mask.any():
        return JSONResponse({'error': f'CODE {old_code}을(를) 찾을 수 없습니다.'}, status_code=404)
    # CODE 변경 시 중복 체크
    if new_code != old_code:
        if (df['CODE'].astype(str).str.strip() == new_code).any():
            return JSONResponse({'error': f'이미 존재하는 CODE로 변경 불가: {new_code}'}, status_code=400)
    idx = df.index[mask][0]
    df.at[idx, '구분'] = str(item.get('구분', df.at[idx, '구분'])).strip()
    df.at[idx, 'CODE'] = new_code
    df.at[idx, '사양'] = str(item.get('사양', item.get('명칭', df.at[idx, '사양']))).strip()
    df.at[idx, '설명'] = str(item.get('설명', df.at[idx, '설명'])).strip()
    df.at[idx, '비고'] = str(item.get('비고', item.get('분류', df.at[idx, '비고']))).strip()
    _save_pel_df(df)
    return JSONResponse({'ok': True, 'code': new_code})


@app.post('/pel-code/row/{code}/delete')
async def pel_code_delete_row(request: Request, code: str):
    """단일 행 삭제 — 관리자 + 비밀번호 재확인."""
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)

    # 본인 비밀번호 재확인
    try:
        body = await request.json()
        password = str(body.get('password', ''))
    except Exception:
        password = ''
    if not password:
        return JSONResponse({'error': '비밀번호를 입력해주세요.'}, status_code=400)

    me = current_user(request)
    if not me:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    user_data = get_user(me['username'])
    if not user_data or not verify_pw(password, user_data['hashed_pw']):
        return JSONResponse({'error': '비밀번호가 일치하지 않습니다.'}, status_code=403)

    df = _load_pel_df()
    mask = df['CODE'].astype(str).str.strip() == code.strip()
    if not mask.any():
        return JSONResponse({'error': f'CODE {code}을(를) 찾을 수 없습니다.'}, status_code=404)
    df = df[~mask].reset_index(drop=True)
    _save_pel_df(df)
    return JSONResponse({'ok': True, 'remaining': len(df)})


@app.post('/pel-code/bulk-add')
async def pel_code_bulk_add(request: Request):
    """누락 PEL 코드 일괄 추가 — 관리자만 가능."""
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({'error': '잘못된 요청 형식'}, status_code=400)
    items = body.get('items') or []
    if not isinstance(items, list) or not items:
        return JSONResponse({'error': '추가할 항목이 없습니다.'}, status_code=400)

    df = _load_pel_df()
    existing = set(str(x).strip() for x in df['CODE'].astype(str))

    def pick(item, keys):
        for k in keys:
            v = item.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        return ''

    new_rows, added, skipped = [], 0, 0
    for item in items:
        code = pick(item, ['code', 'CODE'])
        spec = pick(item, ['사양', 'name', '명칭', 'spec'])
        if not code or not spec:
            skipped += 1
            continue
        if code in existing:
            skipped += 1
            continue
        new_rows.append({
            '구분': pick(item, ['구분', 'gubun']),
            'CODE': code,
            '사양': spec,
            '설명': pick(item, ['설명', 'desc', 'description']),
            '비고': pick(item, ['비고', '분류', 'category', 'note', 'remark']),
        })
        existing.add(code)
        added += 1

    if new_rows:
        import pandas as pd
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
        _save_pel_df(df)

    return JSONResponse({'added': added, 'skipped': skipped, 'total': len(items)})


@app.get('/download/{report_id}')
async def download(request: Request, report_id: str):
    redir = require_login(request)
    if redir: return redir
    if not re.fullmatch(r'[a-f0-9]{10}', report_id):
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    path = os.path.join(REPORTS_DIR, f'BOM_검증_{report_id}.xlsx')
    if not os.path.exists(path):
        return JSONResponse({'error': '리포트를 찾을 수 없습니다.'}, status_code=404)
    return FileResponse(path, filename='BOM_검증_리포트.xlsx',
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
