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
                  update_bom_template_note,
                  add_ccc_upload, save_ccc_items, get_ccc_uploads, get_ccc_upload,
                  get_ccc_items, get_ccc_items_by_vehicle, delete_ccc_upload,
                  upsert_sales_price, get_sales_prices,
                  add_ebom_upload, save_ebom_items, get_ebom_uploads,
                  get_ebom_items, get_ebom_items_by_vehicle, delete_ebom_upload,
                  get_all_country_codes, upsert_country_code, delete_country_code, get_country_code,
                  get_all_dev_stages, get_dev_stage_codes, upsert_dev_stage, delete_dev_stage,
                  get_all_part_names, upsert_part_name, delete_part_name,
                  add_mbom_history, add_mbom_file, get_mbom_history, get_mbom_history_post,
                  get_mbom_file, delete_mbom_history,
                  list_country_ppt_revisions, add_country_ppt_revision,
                  delete_country_ppt_revision, get_country_ppt_revision,
                  MATERIAL_TYPES, get_ccc_matrix, upsert_ccc_matrix, delete_ccc_matrix,
                  get_ccc_codes_for_dropdown,
                  upsert_sales_price_v2, get_sales_prices_v2,
                  add_pel_history, update_pel_history, get_pel_history, get_pel_history_item,
                  delete_pel_history, PEL_STAGE_ORDER, PEL_COLUMN_DIVS)

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
    mfg_code = str(item.get('mfg_code', '')).strip().upper()
    if not code or not name:
        return JSONResponse({'error': '코드와 차종명은 필수입니다.'}, status_code=400)
    if get_vehicle_code_by_code(code):
        return JSONResponse({'error': f'이미 존재하는 차종 코드: {code}'}, status_code=400)
    add_vehicle_code(code, name, memo, mfg_code=mfg_code)
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
    mfg_code = str(item.get('mfg_code', '')).strip().upper()
    if not new_code or not name:
        return JSONResponse({'error': '코드와 차종명은 필수입니다.'}, status_code=400)
    if not get_vehicle_code_by_code(code):
        return JSONResponse({'error': f'차종 코드 {code}을(를) 찾을 수 없습니다.'}, status_code=404)
    if new_code != code.strip().upper() and get_vehicle_code_by_code(new_code):
        return JSONResponse({'error': f'이미 존재하는 코드로 변경 불가: {new_code}'}, status_code=400)
    result = update_vehicle_code_by_code(code, new_code, name, memo, mfg_code=mfg_code)
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


# ── CCC 게시판 ────────────────────────────────────────────────────────────────
CCC_FILES: dict = {}   # file_id -> (path, original_filename)

@app.get('/ccc', response_class=HTMLResponse)
async def ccc_page(request: Request, vehicle: str = '', stage: str = ''):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    vcodes = get_all_vehicle_codes()
    uploads = get_ccc_uploads(vehicle or None, stage or None)
    return templates.TemplateResponse(request=request, name='ccc.html',
                                      context={'me': me, 'vcodes': vcodes,
                                               'uploads': uploads,
                                               'sel_vehicle': vehicle,
                                               'sel_stage': stage,
                                               'stages': get_dev_stage_codes()})


@app.post('/ccc/upload')
async def ccc_upload(request: Request,
                     vehicle_code: str = Form(...),
                     stage: str = Form(...),
                     revision: str = Form('VER.1'),
                     description: str = Form(''),
                     file: UploadFile = File(...)):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.xlsx', '.xlsm', '.pptx'):
        return JSONResponse({'error': 'xlsx, xlsm, pptx 파일만 지원합니다.'}, status_code=400)

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        from ccc_parser import parse_ccc_file
        items = parse_ccc_file(tmp_path, ext.lstrip('.'))

        file_id = uuid.uuid4().hex[:12]
        CCC_FILES[file_id] = (tmp_path, file.filename)

        upload_id = add_ccc_upload(vehicle_code, stage, revision, description,
                                   file.filename, file_id, ext.lstrip('.'), me['username'])
        if items:
            save_ccc_items(upload_id, items)

        return JSONResponse({'ok': True, 'upload_id': upload_id,
                             'item_count': len(items), 'items': items, 'file_id': file_id})
    except Exception as ex:
        import traceback
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return JSONResponse({'error': str(ex), 'trace': traceback.format_exc()}, status_code=500)


@app.get('/ccc/items/{upload_id}')
async def ccc_items_api(request: Request, upload_id: int):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    upload = get_ccc_upload(upload_id)
    if not upload:
        return JSONResponse({'error': '없음'}, status_code=404)
    items = get_ccc_items(upload_id)
    return JSONResponse({'upload': upload, 'items': items})


@app.post('/ccc/delete/{upload_id}')
async def ccc_delete(request: Request, upload_id: int):
    redir = require_login(request)
    if redir: return redir
    delete_ccc_upload(upload_id)
    return RedirectResponse('/ccc', status_code=302)


@app.get('/ccc/download/{file_id}')
async def ccc_download(request: Request, file_id: str):
    redir = require_login(request)
    if redir: return redir
    if not re.fullmatch(r'[a-f0-9]{12}', file_id):
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    entry = CCC_FILES.get(file_id)
    if not entry or not os.path.exists(entry[0]):
        return JSONResponse({'error': '파일을 찾을 수 없습니다.'}, status_code=404)
    path, name = entry
    return FileResponse(path, filename=name)


# ── 영업 단가 입력 ─────────────────────────────────────────────────────────────
@app.get('/sales/price', response_class=HTMLResponse)
async def sales_price_page(request: Request, vehicle: str = '', stage: str = ''):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    vcodes = get_all_vehicle_codes()
    return templates.TemplateResponse(request=request, name='sales_price.html',
                                      context={'me': me, 'vcodes': vcodes,
                                               'sel_vehicle': vehicle,
                                               'sel_stage': stage,
                                               'stages': get_dev_stage_codes()})


@app.post('/sales/price/save')
async def sales_price_save(request: Request):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)
    body = await request.json()
    errors = []
    saved = 0
    for row in body.get('rows', []):
        try:
            price_val = row.get('unit_price')
            if price_val is None or str(price_val).strip() == '':
                continue
            price_f = float(str(price_val).replace(',', ''))
            upsert_sales_price(
                row['vehicle_code'], row['stage'],
                row['part_no_10'], row['ccc_code'],
                price_f, row.get('currency', 'KRW'),
                row.get('effective_date', ''), me['username']
            )
            saved += 1
        except Exception as ex:
            errors.append(f"{row.get('part_no_13','?')}: {ex}")
    return JSONResponse({'ok': True, 'saved': saved, 'errors': errors})


@app.get('/sales/price/data')
async def sales_price_data(request: Request, vehicle: str = '', stage: str = ''):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    prices = get_sales_prices(vehicle or None, stage or None)
    ccc_items = get_ccc_items_by_vehicle(vehicle, stage or None) if vehicle else []
    return JSONResponse({'prices': prices, 'ccc_items': ccc_items})


# ── E-BOM 게시판 ──────────────────────────────────────────────────────────────
EBOM_BOARD_FILES: dict = {}   # file_id -> (path, original_filename)

@app.get('/ebom-board', response_class=HTMLResponse)
async def ebom_board_page(request: Request, vehicle: str = '', stage: str = ''):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    vcodes = get_all_vehicle_codes()
    uploads = get_ebom_uploads(vehicle or None, stage or None)
    return templates.TemplateResponse(request=request, name='ebom_board.html', context={
        'me': me, 'vcodes': vcodes, 'uploads': uploads,
        'sel_vehicle': vehicle, 'sel_stage': stage,
        'stages': get_dev_stage_codes(),
    })


def _parse_ebom_xlsx(path: str) -> list:
    """
    E-BOM xlsx 파싱 — DYA 사내 BOM 고정 컬럼 구조 기반
    (bom_parser.py 와 동일 구조: A열=VC, B~I열=레벨, J열=품번, L열=품명, N열=설명)
    데이터 시작: 6행 (index 5)
    """
    import re
    import pandas as pd

    pno_pattern = re.compile(r'\d{5}-[A-Z0-9]{5}')

    try:
        df = pd.read_excel(path, header=None, sheet_name=0)
    except Exception:
        return []

    items = []
    for ri in range(5, len(df)):          # 6행부터 (0-index=5)
        row = df.iloc[ri].tolist()

        # 품번: J열(index 9)
        pno = str(row[9]).strip() if pd.notna(row[9]) else ''
        if not pno_pattern.search(pno):
            continue

        # 레벨: B~I열(index 1~8) 중 첫 번째 정수값
        level = None
        for lc in range(1, 9):
            if pd.notna(row[lc]) and str(row[lc]).strip() not in ('nan', ''):
                try:
                    level = int(float(row[lc]))
                    break
                except Exception:
                    pass
        if level is None:
            level = 0

        # 품명: L열(index 11), 설명: N열(index 13)
        pname = str(row[11]).strip() if pd.notna(row[11]) else ''
        desc  = str(row[13]).strip() if pd.notna(row[13]) else ''
        description = pname or desc

        # VC: A열(index 0)
        vc = str(row[0]).strip() if pd.notna(row[0]) else ''

        # 수량: variant_cols 처리 대신 단순 qty는 첫 번째 variant 컬럼 활용 생략
        items.append({
            'level': level,
            'pno': pno,
            'description': description,
            'qty': '',
            'variant_code': vc,
        })

    return items


@app.post('/ebom-board/upload')
async def ebom_board_upload(
    request: Request,
    vehicle_code: str = Form(...),
    stage: str = Form(...),
    revision: str = Form('VER.1'),
    description: str = Form(''),
    file: UploadFile = File(...),
):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.xlsx', '.xls'):
        return JSONResponse({'error': 'xlsx/xls 파일만 업로드 가능합니다.'}, status_code=400)

    import tempfile, uuid
    file_id = uuid.uuid4().hex[:12]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    content = await file.read()
    tmp.write(content); tmp.flush(); tmp.close()

    EBOM_BOARD_FILES[file_id] = (tmp.name, file.filename)

    items = _parse_ebom_xlsx(tmp.name)
    upload_id = add_ebom_upload(vehicle_code, stage, revision, description,
                                file.filename, file_id, me['username'])
    if items:
        save_ebom_items(upload_id, items)

    return JSONResponse({
        'ok': True,
        'upload_id': upload_id,
        'items_count': len(items),
        'message': f'{len(items)}개 품목이 등록되었습니다.' if items else '파일은 저장되었으나 품번을 찾지 못했습니다. BOM 구조를 확인해주세요.'
    })


@app.get('/ebom-board/items/{upload_id}')
async def ebom_board_items(request: Request, upload_id: int):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    items = get_ebom_items(upload_id)
    return JSONResponse({'items': items})


@app.get('/api/ebom/parts')
async def api_ebom_parts(request: Request, vehicle: str = '', stage: str = ''):
    """영업단가 입력에서 호출하는 E-BOM 품목 API"""
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    if not vehicle:
        return JSONResponse({'error': '차종을 선택하세요.'}, status_code=400)
    items = get_ebom_items_by_vehicle(vehicle, stage or None)
    return JSONResponse({'vehicle': vehicle, 'stage': stage, 'items': items, 'count': len(items)})


@app.post('/ebom-board/delete/{upload_id}')
async def ebom_board_delete(request: Request, upload_id: int):
    redir = require_login(request)
    if redir: return redir
    delete_ebom_upload(upload_id)
    return RedirectResponse('/ebom-board', status_code=303)


# ── 국가코드 게시판 ────────────────────────────────────────────────────────────
COUNTRY_PPT_DIR = os.path.join(DATA_DIR, 'country_ppt')
os.makedirs(COUNTRY_PPT_DIR, exist_ok=True)
COUNTRY_PPT_FILES: dict = {}  # rev_id -> path (세션 캐시)


@app.get('/country-codes', response_class=HTMLResponse)
async def country_codes_page(request: Request):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    codes = get_all_country_codes()
    ppt_revs = list_country_ppt_revisions()
    for r in ppt_revs:
        r['file_exists'] = os.path.exists(r.get('file_path', ''))
    return templates.TemplateResponse(request=request, name='country_codes.html',
                                      context={'me': me, 'codes': codes, 'ppt_revs': ppt_revs})


@app.get('/api/country-codes')
async def api_country_codes(request: Request):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    codes = get_all_country_codes()
    return JSONResponse({'codes': codes})


@app.post('/country-codes/save')
async def country_codes_save(request: Request):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    rows = body.get('rows', [])
    for row in rows:
        code = str(row.get('code', '')).strip().upper()
        if not code:
            continue
        upsert_country_code(
            code,
            str(row.get('region', '')).strip(),
            str(row.get('countries', '')).strip(),
            int(row.get('display_order', 0)),
            code1=str(row.get('code1', '')).strip(),
            code2=str(row.get('code2', '')).strip(),
            hkmc_code=str(row.get('hkmc_code', '')).strip(),
        )
    return JSONResponse({'ok': True, 'saved': len(rows)})


@app.post('/country-codes/delete/{code}')
async def country_codes_delete(request: Request, code: str):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    delete_country_code(code)
    return JSONResponse({'ok': True})


# ── 개발단계 마스터 ────────────────────────────────────────────────────────────
@app.get('/dev-stages', response_class=HTMLResponse)
async def dev_stages_page(request: Request):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    return templates.TemplateResponse(request=request, name='dev_stages.html',
                                      context={'me': me, 'stages': get_all_dev_stages()})


@app.get('/api/dev-stages')
async def api_dev_stages(request: Request):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    return JSONResponse({'stages': get_all_dev_stages()})


@app.post('/dev-stages/save')
async def dev_stages_save(request: Request):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    body = await request.json()
    rows = body.get('rows', [])
    for row in rows:
        code = str(row.get('code', '')).strip()
        if not code:
            continue
        upsert_dev_stage(code, str(row.get('name', '')).strip(), int(row.get('display_order', 0)))
    return JSONResponse({'ok': True, 'saved': len(rows)})


@app.post('/dev-stages/delete/{code}')
async def dev_stages_delete(request: Request, code: str):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    delete_dev_stage(code)
    return JSONResponse({'ok': True})


# ── 파트 네임 정의 ─────────────────────────────────────────────────────────────
@app.get('/part-names', response_class=HTMLResponse)
async def part_names_page(request: Request):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    return templates.TemplateResponse(request=request, name='part_names.html',
                                      context={'me': me, 'rows': get_all_part_names(),
                                               'vcodes': get_all_vehicle_codes()})


@app.get('/api/part-names')
async def api_part_names(request: Request, vehicle: str = ''):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    return JSONResponse({'rows': get_all_part_names(vehicle)})


@app.post('/part-names/save')
async def part_names_save(request: Request):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    body = await request.json()
    saved = 0
    for row in body.get('rows', []):
        key = str(row.get('part_key', '')).strip()
        if not key:
            continue
        upsert_part_name(key, str(row.get('part_name', '')).strip(),
                         vehicle_code=str(row.get('vehicle_code', '')).strip(),
                         display_order=int(row.get('display_order', 0)))
        saved += 1
    return JSONResponse({'ok': True, 'saved': saved})


@app.post('/part-names/delete/{row_id}')
async def part_names_delete(request: Request, row_id: int):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    delete_part_name(row_id)
    return JSONResponse({'ok': True})


# ── M-BOM 시스템 ──────────────────────────────────────────────────────────────
MBOM_HISTORY_DIR = os.path.join(DATA_DIR, 'mbom_history')
os.makedirs(MBOM_HISTORY_DIR, exist_ok=True)

MBOM_FILE_SLOTS = ['Q파트 종합', 'FRT LH', 'FRT RH', 'RR BACK LH', 'RR CUSH', 'RR BACK RH']


@app.get('/mbom-history', response_class=HTMLResponse)
async def mbom_history_page(request: Request, vehicle: str = ''):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    return templates.TemplateResponse(request=request, name='mbom_history.html', context={
        'me': me, 'vcodes': get_all_vehicle_codes(), 'sel_vehicle': vehicle,
        'stages': get_dev_stage_codes(), 'slots': MBOM_FILE_SLOTS,
    })


@app.get('/mbom-history/list')
async def mbom_history_list(request: Request, vehicle: str = ''):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    if not vehicle:
        return JSONResponse({'items': []})
    return JSONResponse({'items': get_mbom_history(vehicle)})


@app.post('/mbom-history/upload')
async def mbom_history_upload(request: Request):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)
    form = await request.form()
    vehicle_code = str(form.get('vehicle_code', '')).strip().upper()
    title = str(form.get('title', '')).strip()
    if not vehicle_code or not title:
        return JSONResponse({'error': '차종과 제목은 필수입니다.'}, status_code=400)
    post_id = add_mbom_history(
        vehicle_code, str(form.get('stage', '')).strip(),
        str(form.get('revision', 'VER.1')).strip() or 'VER.1',
        title, str(form.get('description', '')).strip(), me['username'])
    saved_files = 0
    for i, slot in enumerate(MBOM_FILE_SLOTS):
        f = form.get(f'file{i}')
        if f is None or not getattr(f, 'filename', ''):
            continue
        ext = os.path.splitext(f.filename)[1].lower()
        fid = uuid.uuid4().hex[:16]
        path = os.path.join(MBOM_HISTORY_DIR, f'{fid}{ext}')
        with open(path, 'wb') as out:
            shutil.copyfileobj(f.file, out)
        add_mbom_file(post_id, slot, f.filename, fid, path)
        saved_files += 1
    return JSONResponse({'ok': True, 'id': post_id, 'files': saved_files,
                         'uploaded_by': me['username']})


@app.get('/mbom-history/download/{file_row_id}')
async def mbom_history_download(request: Request, file_row_id: int):
    redir = require_login(request)
    if redir: return redir
    f = get_mbom_file(file_row_id)
    if not f or not os.path.exists(f['file_path']):
        return JSONResponse({'error': '파일이 없습니다.'}, status_code=404)
    return FileResponse(f['file_path'], filename=f['filename'])


@app.post('/mbom-history/delete/{post_id}')
async def mbom_history_delete(request: Request, post_id: int):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)
    # 삭제 전 권한 확인 (본인 또는 관리자)
    posts_all = get_mbom_history_post(post_id)
    if not posts_all:
        return JSONResponse({'error': '찾을 수 없습니다.'}, status_code=404)
    if me['role'] != 'admin' and posts_all['uploaded_by'] != me['username']:
        return JSONResponse({'error': '본인 또는 관리자만 삭제할 수 있습니다.'}, status_code=403)
    result = delete_mbom_history(post_id)
    for p in result.get('paths', []):
        try:
            if os.path.exists(p): os.unlink(p)
        except Exception:
            pass
    return JSONResponse({'ok': True})


@app.get('/mbom-alc-convert', response_class=HTMLResponse)
async def mbom_alc_convert_page(request: Request):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    return templates.TemplateResponse(request=request, name='mbom_placeholder.html', context={
        'me': me, 'page_key': 'convert',
        'page_title': 'HKMC ALC 코드집 변환',
        'page_icon': '🔁',
    })


@app.get('/mbom-alc2-gen', response_class=HTMLResponse)
async def mbom_alc2_gen_page(request: Request):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    return templates.TemplateResponse(request=request, name='mbom_placeholder.html', context={
        'me': me, 'page_key': 'gen',
        'page_title': 'DYA ALC-2 코드 생성',
        'page_icon': '🧬',
    })


@app.get('/ebom-mbom-compare', response_class=HTMLResponse)
async def ebom_mbom_compare_page(request: Request):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    return templates.TemplateResponse(request=request, name='mbom_placeholder.html', context={
        'me': me, 'page_key': 'compare',
        'page_title': 'E-BOM & M-BOM 비교',
        'page_icon': '⚖️',
    })


@app.post('/country-codes/ppt/upload')
async def country_ppt_upload(request: Request,
                              file: UploadFile = File(...),
                              note: str = Form('')):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    me = current_user(request)
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.pptx', '.png', '.jpg', '.jpeg', '.gif', '.webp'):
        return JSONResponse({'error': 'pptx, png, jpg, jpeg, gif, webp 파일만 지원합니다.'}, status_code=400)

    existing = list_country_ppt_revisions()
    next_rev = (max((r['rev_num'] for r in existing), default=0)) + 1
    save_path = os.path.join(COUNTRY_PPT_DIR, f'rev_{next_rev:03d}{ext}')
    with open(save_path, 'wb') as f:
        shutil.copyfileobj(file.file, f)

    result = add_country_ppt_revision(
        filename=file.filename,
        file_path=save_path,
        file_ext=ext.lstrip('.'),
        uploaded_by=me['username'],
        note=(note or '').strip()
    )
    return JSONResponse({'ok': True, 'rev_num': result['rev_num']})


@app.get('/country-codes/ppt/{rev_id}/download')
async def country_ppt_download(request: Request, rev_id: int):
    redir = require_login(request)
    if redir: return redir
    info = get_country_ppt_revision(rev_id)
    if not info or not os.path.exists(info['file_path']):
        return JSONResponse({'error': '파일을 찾을 수 없습니다.'}, status_code=404)
    return FileResponse(info['file_path'], filename=info['filename'])


@app.get('/country-codes/ppt/{rev_id}/view')
async def country_ppt_view(request: Request, rev_id: int):
    """이미지 파일 인라인 표시"""
    redir = require_login(request)
    if redir: return redir
    info = get_country_ppt_revision(rev_id)
    if not info or not os.path.exists(info['file_path']):
        return JSONResponse({'error': '파일을 찾을 수 없습니다.'}, status_code=404)
    ext = info.get('file_ext', '').lower()
    mime_map = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                'gif': 'image/gif', 'webp': 'image/webp'}
    if ext not in mime_map:
        return JSONResponse({'error': '이미지 파일이 아닙니다.'}, status_code=400)
    return FileResponse(info['file_path'], media_type=mime_map[ext])


@app.post('/country-codes/ppt/{rev_id}/delete')
async def country_ppt_delete(request: Request, rev_id: int):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    info = delete_country_ppt_revision(rev_id)
    if not info:
        return JSONResponse({'error': '찾을 수 없습니다.'}, status_code=404)
    fp = info.get('file_path')
    if fp and os.path.exists(fp):
        try: os.unlink(fp)
        except Exception: pass
    return JSONResponse({'ok': True})


# ── CCC 매트릭스 API ──────────────────────────────────────────────────────────
@app.get('/ccc/matrix')
async def ccc_matrix_get(request: Request, vehicle: str = '', stage: str = ''):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    if not vehicle:
        return JSONResponse({'matrix': [], 'material_types': MATERIAL_TYPES})
    matrix = get_ccc_matrix(vehicle, stage)
    return JSONResponse({'matrix': matrix, 'material_types': MATERIAL_TYPES})


@app.post('/ccc/matrix/save')
async def ccc_matrix_save(request: Request):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    vehicle = str(body.get('vehicle_code', '')).strip()
    stage = str(body.get('stage', '')).strip()
    cells = body.get('cells', [])
    if not vehicle:
        return JSONResponse({'error': '차종을 선택하세요.'}, status_code=400)
    saved = 0
    for cell in cells:
        mat = str(cell.get('material_type', '')).strip()
        ctry = str(cell.get('country_code', '')).strip()
        ccc = str(cell.get('ccc_code', '')).strip()
        if mat and ctry:
            upsert_ccc_matrix(vehicle, stage, mat, ctry, ccc, me['username'])
            saved += 1
    return JSONResponse({'ok': True, 'saved': saved})


@app.get('/api/ccc/matrix')
async def api_ccc_matrix(request: Request, vehicle: str = '', stage: str = ''):
    """영업단가 게시판에서 호출 — CCC 매트릭스 조회"""
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    matrix = get_ccc_matrix(vehicle, stage) if vehicle else []
    return JSONResponse({'matrix': matrix, 'material_types': MATERIAL_TYPES})


# ── 영업단가 v2 (매트릭스 형식) ────────────────────────────────────────────────
@app.get('/sales/price/v2', response_class=HTMLResponse)
async def sales_price_v2_page(request: Request, vehicle: str = '', stage: str = ''):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    vcodes = get_all_vehicle_codes()
    return templates.TemplateResponse(request=request, name='sales_price.html',
                                      context={'me': me, 'vcodes': vcodes,
                                               'sel_vehicle': vehicle, 'sel_stage': stage})


@app.get('/sales/price/v2/data')
async def sales_price_v2_data(request: Request, vehicle: str = '', stage: str = ''):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    ebom_items = get_ebom_items_by_vehicle(vehicle, stage or None) if vehicle else []
    ccc_matrix = get_ccc_matrix(vehicle, stage) if vehicle else []
    prices = get_sales_prices_v2(vehicle or None, stage or None)
    country_codes = get_all_country_codes()
    return JSONResponse({
        'ebom_items': ebom_items,
        'ccc_matrix': ccc_matrix,
        'prices': prices,
        'country_codes': country_codes,
        'material_types': MATERIAL_TYPES,
    })


@app.post('/sales/price/v2/save')
async def sales_price_v2_save(request: Request):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)
    body = await request.json()
    errors = []
    saved = 0
    for row in body.get('rows', []):
        try:
            price_val = row.get('unit_price')
            if price_val is None or str(price_val).strip() == '':
                price_f = None
            else:
                price_f = float(str(price_val).replace(',', ''))
            upsert_sales_price_v2(
                vehicle_code=row['vehicle_code'],
                stage=row['stage'],
                part_no=row['part_no'],
                part_name=row.get('part_name', ''),
                material_type=row['material_type'],
                country_code=row.get('country_code', '-'),
                ccc_code=row.get('ccc_code', ''),
                unit_price=price_f,
                currency=row.get('currency', 'KRW'),
                effective_date=row.get('effective_date', ''),
                username=me['username'],
                compare_pno=row.get('compare_pno', '')
            )
            saved += 1
        except Exception as ex:
            errors.append(f"{row.get('part_no','?')}-{row.get('material_type','?')}-{row.get('country_code','?')}: {ex}")
    return JSONResponse({'ok': True, 'saved': saved, 'errors': errors})


# ── PEL 이력 관리 게시판 ───────────────────────────────────────────────────────
PEL_HISTORY_DIR = os.path.join(DATA_DIR, 'pel_history')
os.makedirs(PEL_HISTORY_DIR, exist_ok=True)


@app.get('/pel-history', response_class=HTMLResponse)
async def pel_history_page(request: Request, vehicle: str = ''):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    vcodes = get_all_vehicle_codes()
    return templates.TemplateResponse(request=request, name='pel_history.html', context={
        'me': me, 'vcodes': vcodes, 'sel_vehicle': vehicle,
        'stages': get_dev_stage_codes(), 'column_divs': PEL_COLUMN_DIVS,
    })


@app.get('/pel-history/list')
async def pel_history_list(request: Request, vehicle: str = ''):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    if not vehicle:
        return JSONResponse({'items': []})
    items = get_pel_history(vehicle)
    return JSONResponse({'items': items})


@app.post('/pel-history/upload')
async def pel_history_upload(
    request: Request,
    vehicle_code: str = Form(...),
    stage: str = Form(''),
    column_div: str = Form(''),
    revision: str = Form('VER.1'),
    title: str = Form(...),
    description: str = Form(''),
    file: UploadFile = File(None),
):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)

    if not vehicle_code.strip():
        return JSONResponse({'error': '차종을 선택하세요.'}, status_code=400)
    if not title.strip():
        return JSONResponse({'error': '제목을 입력하세요.'}, status_code=400)

    filename = file_id = file_path = ''
    if file is not None and file.filename:
        ext = os.path.splitext(file.filename)[1].lower()
        file_id = uuid.uuid4().hex[:16]
        file_path = os.path.join(PEL_HISTORY_DIR, f'{file_id}{ext}')
        with open(file_path, 'wb') as f:
            shutil.copyfileobj(file.file, f)
        filename = file.filename

    new_id = add_pel_history(
        vehicle_code.strip().upper(), stage.strip(), revision.strip(),
        title.strip(), description.strip(),
        filename, file_id, file_path, me['username'],
        column_div=column_div.strip()
    )
    return JSONResponse({'ok': True, 'id': new_id, 'uploaded_by': me['username']})


@app.post('/pel-history/update/{item_id}')
async def pel_history_update(
    request: Request, item_id: int,
    stage: str = Form(''),
    column_div: str = Form(''),
    revision: str = Form('VER.1'),
    title: str = Form(...),
    description: str = Form(''),
    file: UploadFile = File(None),
):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)
    item = get_pel_history_item(item_id)
    if not item:
        return JSONResponse({'error': '찾을 수 없습니다.'}, status_code=404)
    if me['role'] != 'admin' and item['uploaded_by'] != me['username']:
        return JSONResponse({'error': '본인 또는 관리자만 수정할 수 있습니다.'}, status_code=403)
    if not title.strip():
        return JSONResponse({'error': '제목을 입력하세요.'}, status_code=400)

    filename = file_id = file_path = None
    if file is not None and file.filename:
        # 새 파일 첨부 → 기존 파일 삭제 후 교체
        if item.get('file_path') and os.path.exists(item['file_path']):
            try: os.unlink(item['file_path'])
            except Exception: pass
        ext = os.path.splitext(file.filename)[1].lower()
        file_id = uuid.uuid4().hex[:16]
        file_path = os.path.join(PEL_HISTORY_DIR, f'{file_id}{ext}')
        with open(file_path, 'wb') as f:
            shutil.copyfileobj(file.file, f)
        filename = file.filename

    update_pel_history(
        item_id, stage.strip(), column_div.strip(), revision.strip(),
        title.strip(), description.strip(),
        filename=filename, file_id=file_id, file_path=file_path
    )
    return JSONResponse({'ok': True})


@app.get('/pel-history/download/{item_id}')
async def pel_history_download(request: Request, item_id: int):
    redir = require_login(request)
    if redir: return redir
    item = get_pel_history_item(item_id)
    if not item or not item.get('file_path') or not os.path.exists(item['file_path']):
        return JSONResponse({'error': '첨부 파일이 없습니다.'}, status_code=404)
    return FileResponse(item['file_path'], filename=item['filename'])


@app.post('/pel-history/delete/{item_id}')
async def pel_history_delete(request: Request, item_id: int):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)
    item = get_pel_history_item(item_id)
    if not item:
        return JSONResponse({'error': '찾을 수 없습니다.'}, status_code=404)
    # 관리자 또는 작성자 본인만 삭제 가능
    if me['role'] != 'admin' and item['uploaded_by'] != me['username']:
        return JSONResponse({'error': '본인 또는 관리자만 삭제할 수 있습니다.'}, status_code=403)
    info = delete_pel_history(item_id)
    if info and info.get('file_path') and os.path.exists(info['file_path']):
        try: os.unlink(info['file_path'])
        except Exception: pass
    return JSONResponse({'ok': True})
