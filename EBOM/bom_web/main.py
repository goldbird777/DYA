"""
DYA BOM 검증 웹 서버 — FastAPI
"""
import os, shutil, tempfile, uuid, re, json
from fastapi import FastAPI, UploadFile, File, Request, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from auth import (init_db, create_user, get_user, verify_pw, create_token,
                  current_user, require_login, require_admin,
                  get_all_users, approve_user, reject_user, delete_user, set_role,
                  get_all_vehicle_codes, add_vehicle_code, update_vehicle_code, delete_vehicle_code,
                  get_vehicle_code_by_code, update_vehicle_code_by_code, delete_vehicle_code_by_code,
                  get_vehicle_by_id, get_vehicle_by_code_mfg,
                  save_stored_bom, list_stored_boms, get_stored_bom, delete_stored_bom,
                  update_stored_bom_meta, find_duplicate_by_hash,
                  list_bom_template_revisions, get_active_bom_template,
                  get_bom_template_revision, add_bom_template_revision,
                  activate_bom_template_revision, delete_bom_template_revision,
                  update_bom_template_note,
                  add_ccc_upload, save_ccc_items, get_ccc_uploads, get_ccc_upload,
                  get_ccc_items, get_ccc_items_by_vehicle, delete_ccc_upload,
                  upsert_sales_price, get_sales_prices,
                  add_ebom_upload, save_ebom_items, replace_ebom_items, get_ebom_uploads, get_ebom_upload,
                  get_ebom_items, get_ebom_items_by_vehicle, get_ebom_board_revisions,
                  delete_ebom_upload,
                  get_all_country_codes, upsert_country_code, delete_country_code, get_country_code,
                  get_all_dev_stages, get_dev_stage_codes, upsert_dev_stage, delete_dev_stage,
                  get_all_part_names, upsert_part_name, delete_part_name,
                  get_all_fabric_codes, upsert_fabric_code, delete_fabric_code,
                  add_mbom_history, add_mbom_file, get_mbom_history, get_mbom_history_post,
                  get_mbom_file, get_mbom_files_by_post, delete_mbom_history,
                  list_country_ppt_revisions, add_country_ppt_revision,
                  delete_country_ppt_revision, get_country_ppt_revision,
                  MATERIAL_TYPES, get_ccc_matrix, upsert_ccc_matrix, delete_ccc_matrix,
                  get_ccc_codes_for_dropdown,
                  upsert_sales_price_v2, get_sales_prices_v2,
                  add_pel_history, update_pel_history, get_pel_history, get_pel_history_item,
                  delete_pel_history, PEL_STAGE_ORDER, PEL_COLUMN_DIVS,
                  add_pel_spec, get_pel_spec_list, get_pel_spec, delete_pel_spec,
                  get_pel_spec_latest_by_factory,
                  add_sales_file, get_sales_file_list, get_sales_file, delete_sales_file,
                  update_sales_file_edits)

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
                                      context={'me': me, 'stages': get_dev_stage_codes()})


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
                                      context={'me': me, 'stages': get_dev_stage_codes()})


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
    rows = get_all_vehicle_codes(distinct=False)  # 마스터 편집: 생산코드별 전체 행
    return templates.TemplateResponse(request=request, name='vehicles.html',
                                      context={'me': me, 'rows': rows})


@app.get('/vehicles/api/list')
async def vehicles_api_list(request: Request):
    """드롭다운/자동완성용 차종 JSON"""
    redir = require_login(request)
    if redir:
        return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
    rows = get_all_vehicle_codes()  # distinct: 차종당 1개
    return JSONResponse({'vehicles': [{'code': r['code'], 'name': r['name'], 'memo': r.get('memo', ''),
                                       'mfg_code': r.get('mfg_code', ''), 'powertrain': r.get('powertrain', '')}
                                      for r in rows]})


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
    powertrain = str(item.get('powertrain', '')).strip()
    if not code or not name:
        return JSONResponse({'error': '코드와 차종명은 필수입니다.'}, status_code=400)
    if get_vehicle_by_code_mfg(code, mfg_code):
        pair = f'{code} / {mfg_code}' if mfg_code else code
        return JSONResponse({'error': f'이미 등록된 조합: {pair}'}, status_code=400)
    r = add_vehicle_code(code, name, memo, mfg_code=mfg_code, powertrain=powertrain)
    if not r.get('ok'):
        return JSONResponse({'error': r.get('msg', '저장 실패')}, status_code=400)
    v = get_vehicle_by_code_mfg(code, mfg_code)
    return JSONResponse({'ok': True, 'code': code, 'id': v['id'] if v else None})


@app.post('/vehicles/row/{item_id:int}')
async def vehicles_update_row(request: Request, item_id: int):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    try:
        item = await request.json()
    except Exception:
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    cur = get_vehicle_by_id(item_id)
    if not cur:
        return JSONResponse({'error': '차종 행을 찾을 수 없습니다.'}, status_code=404)
    new_code = str(item.get('code', cur['code'])).strip().upper()
    name = str(item.get('name', '')).strip()
    memo = str(item.get('memo', '')).strip()
    mfg_code = str(item.get('mfg_code', '')).strip().upper()
    powertrain = str(item.get('powertrain', '')).strip()
    if not new_code or not name:
        return JSONResponse({'error': '코드와 차종명은 필수입니다.'}, status_code=400)
    dup = get_vehicle_by_code_mfg(new_code, mfg_code)
    if dup and dup['id'] != item_id:
        pair = f'{new_code} / {mfg_code}' if mfg_code else new_code
        return JSONResponse({'error': f'이미 등록된 조합으로 변경 불가: {pair}'}, status_code=400)
    result = update_vehicle_code(item_id, new_code, name, memo, mfg_code=mfg_code, powertrain=powertrain)
    if not result.get('ok'):
        return JSONResponse({'error': result.get('msg', '저장 실패')}, status_code=400)
    return JSONResponse({'ok': True, 'code': new_code, 'id': item_id})


@app.post('/vehicles/row/{item_id:int}/delete')
async def vehicles_delete_row(request: Request, item_id: int):
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
    if not get_vehicle_by_id(item_id):
        return JSONResponse({'error': '차종 행을 찾을 수 없습니다.'}, status_code=404)
    delete_vehicle_code(item_id)
    return JSONResponse({'ok': True})


# ── 저장된 BOM 관리 (Step 2/3) ────────────────────────────────────────────────
@app.post('/bom-storage/save')
async def bom_storage_save(request: Request,
                            file: UploadFile = File(...),
                            vehicle_code: str = Form(...),
                            row_num: str = Form(...),
                            position: str = Form(...),
                            kind: str = Form(...),
                            stage: str = Form(''),
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
        file_hash=file_hash, stage=stage.strip(),
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
    if 'stage' in item:        fields['stage']        = str(item['stage']).strip()
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
PEL_STD_COLS = ['구분', 'CODE', '사양', '설명', '비고', '옵션그룹', '표시순서']  # 표준 7컬럼
PEL_COL_ALIASES = {
    '사양': ['사양', '명칭', 'NAME', 'SPEC'],
    '비고': ['비고', '분류', 'CATEGORY', 'CLASS', 'NOTE', 'REMARK'],
    '구분': ['구분', 'TYPE', 'KIND'],
    'CODE': ['CODE', 'PEL', 'PELCODE'],
    '설명': ['설명', 'DESCRIPTION', 'DESC'],
    '옵션그룹': ['옵션그룹', '옵션 그룹', 'GROUP', 'OPTGROUP', '그룹'],
    '표시순서': ['표시순서', '표시 순서', 'ORDER', 'SORT', '순서'],
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
        def _cell(c):
            s = str(c)
            if s.endswith('.0') and s[:-2].lstrip('-').isdigit():  # 10.0 → 10 (표시순서 등)
                return s[:-2]
            return '' if s == 'nan' else s
        rows = [[_cell(c) for c in row] for row in df.values.tolist()]
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
        '옵션그룹': str(item.get('옵션그룹', '')).strip(),
        '표시순서': str(item.get('표시순서', '')).strip(),
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
    if '옵션그룹' in df.columns: df.at[idx, '옵션그룹'] = str(item.get('옵션그룹', df.at[idx, '옵션그룹'])).strip()
    if '표시순서' in df.columns: df.at[idx, '표시순서'] = str(item.get('표시순서', df.at[idx, '표시순서'])).strip()
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
EBOM_BOARD_DIR = os.path.join(DATA_DIR, 'ebom_board')
os.makedirs(EBOM_BOARD_DIR, exist_ok=True)

@app.get('/ebom-board', response_class=HTMLResponse)
async def ebom_board_page(request: Request, vehicle: str = '', stage: str = ''):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    vcodes = get_all_vehicle_codes()
    return templates.TemplateResponse(request=request, name='ebom_board.html', context={
        'me': me, 'vcodes': vcodes,
        'sel_vehicle': vehicle, 'sel_stage': stage,
        'stages': get_dev_stage_codes(),
    })


_EBOM_PNO_PAT = re.compile(r'^\d{5}-?[A-Z0-9]{5}$')


def _ebom_norm(x):
    return re.sub(r'\s', '', str(x)) if x is not None else ''


def _detect_pno_col(df):
    """변경후 품번 컬럼 자동 탐지 — 헤더 '변경후' 우선, 없으면 10자리 최다 컬럼(최좌측=변경후)."""
    import pandas as pd
    for ri in range(min(25, df.shape[0])):
        for ci in range(min(30, df.shape[1])):
            if str(df.iat[ri, ci]).strip() == '변경후':
                return ci
    counts = {}
    for ci in range(min(30, df.shape[1])):
        n = sum(1 for ri in range(df.shape[0]) if _EBOM_PNO_PAT.match(_ebom_norm(df.iat[ri, ci])))
        if n >= 2:
            counts[ci] = n
    if not counts:
        return None
    mx = max(counts.values())
    return min(ci for ci, n in counts.items() if n >= mx * 0.6)


def _parse_ebom_xlsx(path: str, position: str = '') -> list:
    """
    E-BOM xlsx 파싱 (양식 변동 대응):
      · 모든 시트 스캔 → 위치(운전석/조수석)로 BOM 시트 필터, 이력/사양현황 시트 제외
      · '변경후' 품번 컬럼 자동 탐지 (파일마다 J/L열 등으로 다름)
      · 레벨 = B~I(1~8) 중 첫 non-empty 컬럼 위치 (1레벨 = B열 표시)
      · 여러 변형 시트(LHD/RHD·기본차/환경차)의 품번은 합집합·중복제거
    """
    import pandas as pd
    try:
        xl = pd.ExcelFile(path)
    except Exception:
        return []

    p = position or ''
    if '운전' in p:
        kws = ['운전']
    elif ('조수' in p) or ('동승' in p):
        kws = ['동승', '조수']
    else:
        kws = None
    SKIP = ('EXP', '이력', '사양 현황', '사양현황', '변경 이력', '표지', 'COVER')

    seen = {}
    for sh in xl.sheet_names:
        if kws and not any(k in sh for k in kws):
            continue
        if any(s in sh for s in SKIP):
            continue
        try:
            df = pd.read_excel(path, header=None, sheet_name=sh)
        except Exception:
            continue
        if df.shape[0] < 5 or df.shape[1] < 6:
            continue
        pno_col = _detect_pno_col(df)
        if pno_col is None:
            continue
        for ri in range(df.shape[0]):
            raw = df.iat[ri, pno_col]
            if pd.isna(raw):
                continue
            pno = str(raw).strip()
            if not _EBOM_PNO_PAT.match(_ebom_norm(pno)):
                continue
            level = None
            for lc in range(1, 9):
                if lc < df.shape[1]:
                    v = df.iat[ri, lc]
                    if pd.notna(v) and _ebom_norm(v) not in ('', 'nan'):
                        level = lc
                        break
            if level is None:
                continue
            desc = ''
            for dc in range(pno_col + 1, min(pno_col + 5, df.shape[1])):
                dv = df.iat[ri, dc]
                if pd.notna(dv):
                    s = str(dv).strip()
                    if s and not _EBOM_PNO_PAT.match(_ebom_norm(s)):
                        desc = s
                        break
            key = re.sub(r'[\s\-]', '', pno).upper()
            if key not in seen:
                seen[key] = {'level': level, 'pno': pno, 'description': desc,
                             'qty': '', 'variant_code': ''}
    items = list(seen.values())

    return items


@app.get('/ebom-board/list')
async def ebom_board_list(request: Request, vehicle: str = '', row_num: str = '', position: str = ''):
    """특정 (차종,열,위치)의 리비전 이력 — PEL 이력관리와 동일한 게시판형 조회"""
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    if not (vehicle and row_num and position):
        return JSONResponse({'items': []})
    items = get_ebom_board_revisions(vehicle, row_num, position)
    return JSONResponse({'items': items})


@app.post('/ebom-board/upload')
async def ebom_board_upload(
    request: Request,
    vehicle_code: str = Form(...),
    stage: str = Form(''),
    row_num: str = Form(...),
    position: str = Form(...),
    variant: str = Form(''),
    revision: str = Form('VER.1'),
    description: str = Form(''),
    file: UploadFile = File(...),
):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)

    if not row_num.strip() or not position.strip():
        return JSONResponse({'error': '열과 위치를 선택하세요.'}, status_code=400)

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.xlsx', '.xls'):
        return JSONResponse({'error': 'xlsx/xls 파일만 업로드 가능합니다.'}, status_code=400)

    import uuid
    file_id = uuid.uuid4().hex[:12]
    saved_path = os.path.join(EBOM_BOARD_DIR, f'{file_id}{ext}')
    content = await file.read()
    with open(saved_path, 'wb') as f:
        f.write(content)

    items = _parse_ebom_xlsx(saved_path, position=position.strip())
    upload_id = add_ebom_upload(vehicle_code, stage, revision, description,
                                file.filename, file_id, me['username'],
                                row_num=row_num.strip(), position=position.strip(),
                                file_path=saved_path, variant=variant.strip())
    if items:
        save_ebom_items(upload_id, items)

    lv1 = sum(1 for it in items if it.get('level') == 1)
    return JSONResponse({
        'ok': True,
        'upload_id': upload_id,
        'items_count': len(items),
        'lv1_count': lv1,
        'uploaded_by': me['username'],
        'message': f'{len(items)}개 품목이 등록되었습니다. (1레벨 {lv1}개)' if items else '파일은 저장되었으나 품번을 찾지 못했습니다. BOM 구조를 확인해주세요.'
    })


@app.get('/ebom-board/items/{upload_id}')
async def ebom_board_items(request: Request, upload_id: int):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    items = get_ebom_items(upload_id)
    return JSONResponse({'items': items})


@app.get('/ebom-board/download/{upload_id}')
async def ebom_board_download(request: Request, upload_id: int):
    redir = require_login(request)
    if redir: return redir
    up = get_ebom_upload(upload_id)
    if not up or not up.get('file_path') or not os.path.exists(up['file_path']):
        return JSONResponse({'error': '첨부 파일이 없습니다.'}, status_code=404)
    return FileResponse(up['file_path'], filename=up['filename'])


@app.post('/ebom-board/reparse/{upload_id}')
async def ebom_board_reparse(request: Request, upload_id: int):
    """저장된 원본 파일을 새 파서로 다시 파싱 (구버전 파서로 0개였던 파일 복구용)."""
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)
    up = get_ebom_upload(upload_id)
    if not up:
        return JSONResponse({'error': '찾을 수 없습니다.'}, status_code=404)
    if me['role'] != 'admin' and up['uploaded_by'] != me['username']:
        return JSONResponse({'error': '본인 또는 관리자만 재파싱할 수 있습니다.'}, status_code=403)
    if not up.get('file_path') or not os.path.exists(up['file_path']):
        return JSONResponse({'error': '원본 파일이 없어 재파싱할 수 없습니다.'}, status_code=404)
    items = _parse_ebom_xlsx(up['file_path'], position=up.get('position', ''))
    replace_ebom_items(upload_id, items)
    lv1 = sum(1 for it in items if it.get('level') == 1)
    return JSONResponse({'ok': True, 'items_count': len(items), 'lv1_count': lv1,
                         'message': f'{len(items)}개 재파싱 (1레벨 {lv1}개)'})


@app.get('/api/ebom/parts')
async def api_ebom_parts(request: Request, vehicle: str = '', stage: str = ''):
    """영업단가 입력/M-BOM비교에서 호출하는 E-BOM 품목 API — 열/위치별 최신 업로드를 합쳐서 반환"""
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    if not vehicle:
        return JSONResponse({'error': '차종을 선택하세요.'}, status_code=400)
    items = get_ebom_items_by_vehicle(vehicle)   # 차종 단독 (단계 무관)
    return JSONResponse({'vehicle': vehicle, 'stage': stage, 'items': items, 'count': len(items)})


@app.post('/ebom-board/delete/{upload_id}')
async def ebom_board_delete(request: Request, upload_id: int):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)
    up = get_ebom_upload(upload_id)
    if not up:
        return JSONResponse({'error': '찾을 수 없습니다.'}, status_code=404)
    if me['role'] != 'admin' and up['uploaded_by'] != me['username']:
        return JSONResponse({'error': '본인 또는 관리자만 삭제할 수 있습니다.'}, status_code=403)
    delete_ebom_upload(upload_id)
    return JSONResponse({'ok': True})


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
        'alc2_masters': _alc2_all_info(), 'alc2_kinds': ALC2_MASTER_KINDS,
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


# ── 통합 ALC2 마스터 (마스터 데이터 저장 · ALC-2 채번 기준) ─────────────────────
ALC2_MASTER_DIR = os.path.join(DATA_DIR, 'alc2_master')
os.makedirs(ALC2_MASTER_DIR, exist_ok=True)
# 3종 마스터: 표준(매칭·채번) / 열별사양 / 최종 산출물 서식(★REV)
ALC2_MASTER_KINDS = {
    'standard': '표준 마스터 (MES OX표기용 · 전체열 통합)',
    'spec': '열별 사양 마스터 (ALC코드 집 생성용)',
    'format': '최종 산출물 서식 (★통합 ALC2 코드 REV)',
}
ALC2_MASTER_PATH = os.path.join(ALC2_MASTER_DIR, 'alc2_standard.xlsx')  # 매칭용(하위호환)


def _alc2_path(kind):
    return os.path.join(ALC2_MASTER_DIR, f'alc2_{kind}.xlsx')


def _alc2_meta(kind):
    return os.path.join(ALC2_MASTER_DIR, f'meta_{kind}.json')


def _alc2_master_info(kind='standard'):
    p = _alc2_path(kind)
    if not os.path.exists(p):
        return {'exists': False, 'kind': kind, 'label': ALC2_MASTER_KINDS.get(kind, kind)}
    meta = {}
    try:
        meta = json.load(open(_alc2_meta(kind), encoding='utf-8'))
    except Exception:
        pass
    return {'exists': True, 'kind': kind, 'label': ALC2_MASTER_KINDS.get(kind, kind),
            'filename': meta.get('filename', os.path.basename(p)),
            'uploaded_by': meta.get('uploaded_by', ''), 'uploaded': meta.get('uploaded', '')}


def _alc2_all_info():
    return {k: _alc2_master_info(k) for k in ALC2_MASTER_KINDS}


@app.get('/alc2-master/info')
async def alc2_master_info(request: Request):
    if require_login(request):
        return JSONResponse({'error': '로그인 필요'}, status_code=401)
    return JSONResponse({'masters': _alc2_all_info()})


@app.post('/alc2-master/upload/{kind}')
async def alc2_master_upload(request: Request, kind: str, file: UploadFile = File(...)):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    if kind not in ALC2_MASTER_KINDS:
        return JSONResponse({'error': '잘못된 마스터 종류'}, status_code=400)
    me = current_user(request)
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.xlsx', '.xls'):
        return JSONResponse({'error': 'xlsx/xls만 가능합니다.'}, status_code=400)
    with open(_alc2_path(kind), 'wb') as f:
        shutil.copyfileobj(file.file, f)
    import datetime
    json.dump({'filename': file.filename, 'uploaded_by': me['username'],
               'uploaded': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')},
              open(_alc2_meta(kind), 'w', encoding='utf-8'), ensure_ascii=False)
    return JSONResponse({'ok': True, **_alc2_master_info(kind)})


@app.get('/alc2-master/download/{kind}')
async def alc2_master_download(request: Request, kind: str):
    if require_login(request):
        return RedirectResponse('/login')
    if kind not in ALC2_MASTER_KINDS or not os.path.exists(_alc2_path(kind)):
        return JSONResponse({'error': '등록된 마스터가 없습니다.'}, status_code=404)
    info = _alc2_master_info(kind)
    return FileResponse(_alc2_path(kind), filename=info.get('filename', f'alc2_{kind}.xlsx'))


ALC2_LEDGER_CACHE = {}   # path -> (mtime, size, cols, hdr_row)


def _alc2_ledger_cols(path):
    """대장의 열 위치를 헤더명으로 탐색 (mtime 캐시 — 매번 2.4초 걸리므로)."""
    import alc2_ledger
    try:
        st = os.stat(path); ck = (st.st_mtime, st.st_size)
    except OSError:
        ck = None
    hit = ALC2_LEDGER_CACHE.get(path)
    if hit and hit[0] == ck:
        return hit[1], hit[2]
    cols, last_no = alc2_ledger.find_columns(path)
    if ck:
        ALC2_LEDGER_CACHE[path] = (ck, cols, last_no)
    return cols, last_no


def _alc2_write_ledger(src, dst, rows):
    """★REV 서식(헤더 색상·열 구조)만 물려받고, 데이터 영역은 이번 변환 결과로 교체한다.
       기존 대장 이력을 그대로 두면 이전 파일과 비교가 안 되므로 8행부터 새로 채운다.
       openpyxl 왕복(15초) 대신 zip+XML 직접 조작(0.6초)."""
    import alc2_ledger
    cols, first_row = _alc2_ledger_cols(src)
    if 'kmc' not in cols:
        shutil.copy2(src, dst)
        return 0
    vals = []
    for i, r in enumerate(rows, 1):
        v = {cols['kmc']: r.get('kmc20', '')}
        if 'no' in cols:
            v[cols['no']] = i
        if 'vehicle' in cols:
            v[cols['vehicle']] = r.get('vehicle', '')
        if 'alc2' in cols:
            v[cols['alc2']] = r.get('alc2', '')
        vals.append(v)
    return alc2_ledger.replace_rows(src, dst, vals, first_row)


# ── mbom-history 게시글에서 ALC-2 생성 실행 ───────────────────────────────────
@app.post('/mbom-history/alc2-run/{post_id}')
async def mbom_history_alc2_run(request: Request, post_id: int):
    if require_login(request):
        return JSONResponse({'error': '로그인 필요'}, status_code=401)
    import alc2_convert
    # 매칭·채번 원본 = ★최종 산출물 서식(통합 ALC2 코드 대장) 우선, 없으면 표준 마스터
    master_path = _alc2_path('format') if os.path.exists(_alc2_path('format')) else ALC2_MASTER_PATH
    if not os.path.exists(master_path):
        return JSONResponse({'error': '먼저 이 화면 상단 [기준 마스터 3종]에서 «★최종 산출물 서식» 또는 «표준 마스터»를 등록하세요.'}, status_code=400)
    files = get_mbom_files_by_post(post_id)
    by_slot = {f['slot']: f['file_path'] for f in files if f.get('file_path') and os.path.exists(f['file_path'])}
    qpart = by_slot.get('Q파트 종합')
    if not qpart:
        return JSONResponse({'error': "'Q파트 종합' 파일이 이 게시글에 없습니다."}, status_code=400)
    alc_paths = {s: by_slot.get(s) for s in alc2_convert.ALC_SLOTS}
    missing_slots = [s for s in alc2_convert.ALC_SLOTS if not alc_paths.get(s)]
    from bom_generator import load_pel_master
    mpel = load_pel_master(PEL_CODE_PATH).get('data', {})
    try:
        full = alc2_convert.analyze(qpart, alc_paths, master_path, mpel)  # 6파일 1회씩만 로드
    except Exception as ex:
        return JSONResponse({'error': f'변환 오류: {ex}'}, status_code=500)
    res = {'rows': full['rows'], 'stats': full['stats']}
    _ox = full['ox']
    rid = uuid.uuid4().hex[:10]
    # ① ★통합 ALC2 코드 대장 — 원본 서식 그대로 복사 + 신규 코드만 이어붙임
    fmt_path = _alc2_path('format')
    tpl_used, ledger_added = '', 0
    if os.path.exists(fmt_path):
        try:
            lout = os.path.join(REPORTS_DIR, f'ALC2LEDGER_{rid}.xlsx')
            ledger_added = _alc2_write_ledger(fmt_path, lout, res['rows'])
            tpl_used = _alc2_master_info('format').get('filename', '★통합 ALC2 코드')
            ALC2_LEDGERS[rid] = lout
        except Exception:
            tpl_used, ledger_added = '', 0
    # ② 판정결과 · O·X 통합코드집 리포트
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook(); ws = wb.active; ws.title = 'ALC2 판정결과'
    hdr = ['NO', '차종', '국가(KEY01)', 'KMC ALC-2 CODE', 'DYA ALC-2', '판정', '상세']
    ws.append(hdr)
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF'); c.fill = PatternFill('solid', start_color='1A237E')
    yellow = PatternFill('solid', start_color='FFF9C4'); red = PatternFill('solid', start_color='FFCDD2')
    for i, r in enumerate(res['rows'], 1):
        ws.append([i, r['vehicle'], r.get('key01', ''), r['kmc20'], r['alc2'], r['status'], r['detail']])
        if r['status'] == '신규승인필요':
            for c in ws[i + 1]: c.fill = yellow
        elif r['status'] == '원본누락':
            for c in ws[i + 1]: c.fill = red
    # O/X 통합코드집 시트 (PEL CODE 마스터 기반) — 6파일만으로 생성
    ox_cols = 0
    try:
        from openpyxl.styles import Alignment
        ox = _ox
        ox_cols = len(ox['columns'])
        a2 = {r['kmc20']: r['alc2'] for r in res['rows']}
        ws2 = wb.create_sheet('O·X 통합코드집')
        center = Alignment(horizontal='center', vertical='center')
        fx = ['NO', '차종', '국가', 'KMC ALC-2', 'DYA ALC-2']
        ws2.append(fx + sum([[g['group']] + [''] * (g['span'] - 1) for g in ox['groups']], []))
        ws2.append([''] * len(fx) + [c['spec'] for c in ox['columns']])
        gfill = PatternFill('solid', start_color='283593'); hfill = PatternFill('solid', start_color='1A237E')
        hfont = Font(bold=True, color='FFFFFF')
        for cc in ws2[1]: cc.fill = gfill; cc.font = hfont; cc.alignment = center
        for cc in ws2[2]: cc.fill = hfill; cc.font = hfont; cc.alignment = center
        cidx = len(fx) + 1
        for g in ox['groups']:
            if g['span'] > 1:
                ws2.merge_cells(start_row=1, start_column=cidx, end_row=1, end_column=cidx + g['span'] - 1)
            cidx += g['span']
        for k in range(1, len(fx) + 1):
            ws2.merge_cells(start_row=1, start_column=k, end_row=2, end_column=k)
            ws2.cell(1, k).value = fx[k - 1]; ws2.cell(1, k).fill = hfill
        for i, r in enumerate(ox['rows'], 1):
            mk = set(r['marks'])
            ws2.append([i, r['vehicle'], r.get('key01', ''), r['kmc20'], a2.get(r['kmc20'], '')]
                       + ['O' if c['spec'] in mk else '' for c in ox['columns']])
            for cc in range(len(fx) + 1, len(fx) + 1 + len(ox['columns'])):
                ws2.cell(i + 2, cc).alignment = center
    except Exception:
        pass
    out = os.path.join(REPORTS_DIR, f'ALC2RES_{rid}.xlsx')
    wb.save(out); ALC2_RESULTS[rid] = out
    from datetime import datetime
    day = datetime.now().strftime('%Y%m%d')
    ALC2_RESULT_NAMES[rid] = 'ALC2_판정결과·OX통합코드집_%s.xlsx' % day
    ALC2_LEDGER_NAMES[rid] = '★통합 ALC2 코드_%s_REV(변환 %d건).xlsx' % (day, ledger_added)
    return JSONResponse({'ok': True, 'result_id': rid, 'stats': res['stats'], 'template': tpl_used,
                         'ledger_added': ledger_added, 'has_ledger': bool(tpl_used),
                         'ox_cols': ox_cols, 'missing_slots': missing_slots, 'rows': res['rows'][:200]})


@app.get('/mbom-history/alc2-result/{result_id}')
async def mbom_history_alc2_result(request: Request, result_id: str):
    if require_login(request):
        return RedirectResponse('/login')
    if not re.fullmatch(r'[a-f0-9]{10}', result_id):
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    path = ALC2_RESULTS.get(result_id)
    if not path or not os.path.exists(path):
        return JSONResponse({'error': '결과 만료 — 다시 실행하세요.'}, status_code=404)
    return FileResponse(path, filename=ALC2_RESULT_NAMES.get(result_id, 'DYA_ALC2_판정결과.xlsx'),
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.get('/mbom-history/alc2-ledger/{result_id}')
async def mbom_history_alc2_ledger(request: Request, result_id: str):
    """★통합 ALC2 코드 대장 (원본 서식 + 신규코드 반영)."""
    if require_login(request):
        return RedirectResponse('/login')
    if not re.fullmatch(r'[a-f0-9]{10}', result_id):
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    path = ALC2_LEDGERS.get(result_id)
    if not path or not os.path.exists(path):
        return JSONResponse({'error': '결과 만료 — 다시 실행하세요.'}, status_code=404)
    return FileResponse(path, filename=ALC2_LEDGER_NAMES.get(result_id, '★통합 ALC2 코드_REV.xlsx'),
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


# ── 원단코드 마스터 (마스터 데이터) ────────────────────────────────────────────
@app.get('/fabric-master', response_class=HTMLResponse)
async def fabric_master_page(request: Request):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    return templates.TemplateResponse(request=request, name='fabric_master.html', context={
        'me': me, 'fabrics': get_all_fabric_codes(),
    })


# ── 원단코드 마스터 API ────────────────────────────────────────────────────────
@app.get('/api/fabric-codes')
async def api_fabric_codes(request: Request):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    return JSONResponse({'fabrics': get_all_fabric_codes()})


@app.post('/fabric-codes/save')
async def fabric_codes_save(request: Request):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    body = await request.json()
    n = 0
    for row in body.get('rows', []):
        code = str(row.get('code', '')).strip()
        if not code:
            continue
        upsert_fabric_code(code, str(row.get('fabric_code', '')).strip(),
                           str(row.get('name', '')).strip(), str(row.get('stitch_color', '')).strip(),
                           str(row.get('base_color', '')).strip(), int(row.get('display_order', 0)),
                           hkmc_code=str(row.get('hkmc_code', '')).strip())
        n += 1
    return JSONResponse({'ok': True, 'saved': n})


@app.post('/fabric-codes/delete/{code}')
async def fabric_codes_delete(request: Request, code: str):
    if require_admin(request):
        return JSONResponse({'error': '관리자 권한이 필요합니다.'}, status_code=403)
    delete_fabric_code(code)
    return JSONResponse({'ok': True})


# ── DYA ALC-2 코드 생성 ────────────────────────────────────────────────────────

def _parse_qpart_xlsx(path: str) -> list:
    """GY 종합(Q파트) 파싱 — 차종|구분|KEY01~KEY06 (KEY02~06 4자리 코드 행만)"""
    import pandas as pd
    df = pd.read_excel(path, header=None, sheet_name=0)
    rows = []
    for i in range(1, len(df)):
        r = df.iloc[i]
        def s(c):
            v = r[c] if c < len(r) else None
            return str(v).strip() if pd.notna(v) else ''
        keys = [s(c) for c in range(3, 8)]   # KEY02~KEY06
        if not all(keys) or any(len(k) != 4 for k in keys):
            continue
        rows.append({
            'vehicle': s(0), 'gubun': s(1), 'key01': s(2),
            'keys': keys, 'kmc20': ''.join(keys),
        })
    del df
    return rows


def _parse_alc2_master_xlsx(path: str) -> dict:
    """통합 ALC2 코드 마스터 파싱 — {KMC 20자리: {'alc2':5자리, 'vehicle':차종}}"""
    import pandas as pd
    df = pd.read_excel(path, sheet_name=0, header=None)
    m = {}
    for i in range(len(df)):
        try:
            a2 = str(df.iat[i, 2]).strip()
            k = str(df.iat[i, 3]).strip()
            veh = str(df.iat[i, 1]).strip()
        except Exception:
            continue
        if len(k) == 20 and k.isalnum() and len(a2) == 5:
            m[k] = {'alc2': a2, 'vehicle': veh}
    del df
    return m


@app.on_event('startup')
def _alc2_warmup():
    """대장(3천행×283열) 캐시를 기동 직후 백그라운드로 미리 적재.
       안 하면 재시작 후 첫 변환이 30초대가 된다."""
    import threading

    def run():
        try:
            p = _alc2_path('format')
            if not os.path.exists(p):
                p = ALC2_MASTER_PATH
            if os.path.exists(p):
                import alc2_convert
                alc2_convert.read_master(p)
                _alc2_ledger_cols(p)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


ALC2_RESULTS: dict = {}        # result_id -> 판정·O·X 리포트 path
ALC2_RESULT_NAMES: dict = {}   # result_id -> 리포트 다운로드 파일명
ALC2_LEDGERS: dict = {}        # result_id -> ★통합 ALC2 코드 대장 path
ALC2_LEDGER_NAMES: dict = {}   # result_id -> 대장 다운로드 파일명


@app.post('/mbom-alc2-gen/run')
async def mbom_alc2_run(request: Request,
                        qpart: UploadFile = File(...),
                        master: UploadFile = File(...)):
    """Q파트 종합 + 기존 통합 ALC2 마스터 → 행별 DYA ALC-2 매칭/신규 판정"""
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)

    import tempfile
    tmp_q = tmp_m = None
    try:
        # 순차 처리 (서버 메모리 보호): 파일 하나씩 저장→파싱→해제
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as t:
            shutil.copyfileobj(qpart.file, t); tmp_q = t.name
        q_rows = _parse_qpart_xlsx(tmp_q)
        os.unlink(tmp_q); tmp_q = None

        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as t:
            shutil.copyfileobj(master.file, t); tmp_m = t.name
        master_map = _parse_alc2_master_xlsx(tmp_m)
        os.unlink(tmp_m); tmp_m = None

        if not q_rows:
            return JSONResponse({'error': 'Q파트 파일에서 KEY02~KEY06(4자리×5) 행을 찾지 못했습니다.'}, status_code=400)
        if not master_map:
            return JSONResponse({'error': '통합 ALC2 마스터에서 코드(5자리+20자리) 행을 찾지 못했습니다.'}, status_code=400)

        # 국가코드 해석 (HKMC 코드 → 지역/1순위)
        cmap = {}
        for c in get_all_country_codes():
            hk = (c.get('hkmc_code') or '').strip().upper()
            if hk:
                cmap[hk] = {'region': c.get('region', ''), 'code1': (c.get('code1') or '').strip().upper(),
                            'kcode': c.get('code', '')}

        # 신규 채번 준비: 마스터에 이미 쓰인 ALC2 수집 → (지역+차종)별 다음 순번
        used = set(v['alc2'] for v in master_map.values())

        results, matched, newcnt = [], 0, 0
        seen_new = {}
        for row in q_rows:
            k20 = row['kmc20']
            hit = master_map.get(k20)
            key01 = row['key01'].upper()
            cinfo = cmap.get(key01, {})
            veh_letter = row['keys'][0][-1] if row['keys'][0] else '?'
            if hit:
                matched += 1
                results.append({**row, 'alc2': hit['alc2'], 'status': 'OK',
                                'region': cinfo.get('region', ''), 'kcode': cinfo.get('kcode', '')})
            else:
                # 신규 — 제안 채번: [지역1순위][원단?][순번2][차종문자] (원단코드는 사양 확인 후 확정)
                if k20 in seen_new:
                    prop = seen_new[k20]
                else:
                    r1 = cinfo.get('code1') or '?'
                    nn = 1
                    while True:
                        cand = f"{r1}?{nn:02d}{veh_letter}"
                        if cand not in used:
                            used.add(cand); break
                        nn += 1
                    prop = cand
                    seen_new[k20] = prop
                    newcnt += 1
                results.append({**row, 'alc2': prop, 'status': 'NEW',
                                'region': cinfo.get('region', ''), 'kcode': cinfo.get('kcode', '')})

        # 결과 엑셀 생성
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        wb = Workbook(); ws = wb.active; ws.title = 'ALC2 생성 결과'
        hdr = ['NO', '차종', '구분', 'KEY01', '국가', 'K코드',
               'KEY02', 'KEY03', 'KEY04', 'KEY05', 'KEY06',
               'KMC ALC-2 (20자리)', 'DYA ALC-2', '판정']
        ws.append(hdr)
        for c in ws[1]:
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = PatternFill('solid', start_color='1A237E')
        new_fill = PatternFill('solid', start_color='FFF9C4')
        for i, r in enumerate(results, 1):
            ws.append([i, r['vehicle'], r['gubun'], r['key01'], r['region'], r['kcode'],
                       *r['keys'], r['kmc20'], r['alc2'],
                       '기존 매칭' if r['status'] == 'OK' else '신규 채번 필요(원단? 확인)'])
            if r['status'] == 'NEW':
                for c in ws[i + 1]:
                    c.fill = new_fill
        result_id = uuid.uuid4().hex[:10]
        out_path = os.path.join(REPORTS_DIR, f'ALC2_{result_id}.xlsx')
        wb.save(out_path)
        ALC2_RESULTS[result_id] = out_path

        return JSONResponse({
            'ok': True, 'result_id': result_id,
            'total': len(results), 'matched': matched, 'new_codes': newcnt,
            'rows': results[:300],
            'truncated': len(results) > 300,
        })
    except Exception as ex:
        import traceback
        return JSONResponse({'error': f'처리 오류: {ex}', 'trace': traceback.format_exc()}, status_code=500)
    finally:
        for p in (tmp_q, tmp_m):
            if p and os.path.exists(p):
                try: os.unlink(p)
                except Exception: pass


@app.get('/mbom-alc2-gen/download/{result_id}')
async def mbom_alc2_download(request: Request, result_id: str):
    redir = require_login(request)
    if redir: return redir
    if not re.fullmatch(r'[a-f0-9]{10}', result_id):
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    path = ALC2_RESULTS.get(result_id)
    if not path or not os.path.exists(path):
        return JSONResponse({'error': '결과가 만료되었습니다. 다시 실행해주세요.'}, status_code=404)
    return FileResponse(path, filename='DYA_ALC2_생성결과.xlsx',
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.get('/ebom-mbom-compare', response_class=HTMLResponse)
async def ebom_mbom_compare_page(request: Request):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    return templates.TemplateResponse(request=request, name='mbom_placeholder.html', context={
        'me': me, 'page_key': 'compare',
        'page_title': 'E-BOM & M-BOM 비교',
        'page_icon': '⚖️',
        'vcodes': get_all_vehicle_codes(),
        'stages': get_dev_stage_codes(),
    })


def _norm_pno(p: str) -> str:
    """품번 정규화 — 하이픈/공백 제거, 대문자 (88005 P1000 == 88005-P1000)"""
    return re.sub(r'[\s\-]', '', str(p or '')).upper()


def _parse_alc_partnos(path: str) -> dict:
    """열별 ALC 코드집에서 13자리 품번 추출 → {10자리: {'names':set, 'cccs':set, 'rows':n}}"""
    import pandas as pd
    df = pd.read_excel(path, header=None, sheet_name=0)
    pat = re.compile(r'^(\d{5})([A-Z0-9]{5})([A-Z0-9]{3})?$')
    out = {}
    for i in range(len(df)):
        raw = str(df.iat[i, 2]).strip() if df.shape[1] > 2 and pd.notna(df.iat[i, 2]) else ''
        m = pat.match(_norm_pno(raw))
        if not m:
            continue
        base = m.group(1) + m.group(2)
        ccc = m.group(3) or ''
        name = ''
        if df.shape[1] > 6 and pd.notna(df.iat[i, 6]):
            name = str(df.iat[i, 6]).strip()
        e = out.setdefault(base, {'names': set(), 'cccs': set(), 'rows': 0})
        e['rows'] += 1
        if ccc: e['cccs'].add(ccc)
        if name: e['names'].add(name)
    del df
    return out


COMPARE_RESULTS: dict = {}


@app.post('/ebom-mbom-compare/run')
async def ebom_mbom_compare_run(request: Request):
    """E-BOM 1레벨(등록본) vs M-BOM ALC 파일(들) 10자리 품번 대조"""
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    import tempfile
    form = await request.form()
    vehicle = str(form.get('vehicle', '')).strip().upper()
    stage = str(form.get('stage', '')).strip()
    if not vehicle:
        return JSONResponse({'error': '차종을 선택하세요.'}, status_code=400)

    # E-BOM 측: 등록된 1레벨 품번 (차종 단독 — 열/위치별 최신 리비전 합산)
    ebom_items = get_ebom_items_by_vehicle(vehicle)
    ebom = {}
    for it in ebom_items:
        base = _norm_pno(it.get('pno'))
        if len(base) == 10:
            ebom.setdefault(base, set()).add((it.get('description') or '').strip())

    # M-BOM 측: 업로드된 ALC 파일들 순차 파싱
    mbom = {}
    file_names = []
    for key in form.keys():
        if not key.startswith('alc'):
            continue
        f = form.get(key)
        if f is None or not getattr(f, 'filename', ''):
            continue
        file_names.append(f.filename)
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as t:
            shutil.copyfileobj(f.file, t); tmp = t.name
        try:
            part = _parse_alc_partnos(tmp)
        finally:
            try: os.unlink(tmp)
            except Exception: pass
        for base, e in part.items():
            m = mbom.setdefault(base, {'names': set(), 'cccs': set(), 'rows': 0, 'files': set()})
            m['rows'] += e['rows']; m['cccs'] |= e['cccs']; m['names'] |= e['names']
            m['files'].add(f.filename)

    if not mbom:
        return JSONResponse({'error': 'ALC 파일에서 품번을 찾지 못했습니다. 파일 형식을 확인하세요.'}, status_code=400)

    def fmt(b): return b[:5] + '-' + b[5:]
    both   = sorted(set(ebom) & set(mbom))
    e_only = sorted(set(ebom) - set(mbom))
    m_only = sorted(set(mbom) - set(ebom))

    rows = []
    for b in both:
        rows.append({'pno': fmt(b), 'status': 'OK',
                     'ebom_name': ' / '.join(sorted(n for n in ebom[b] if n))[:60],
                     'mbom_name': ' / '.join(sorted(mbom[b]['names']))[:60],
                     'ccc_count': len(mbom[b]['cccs']),
                     'cccs': ','.join(sorted(mbom[b]['cccs'])[:15])})
    for b in e_only:
        rows.append({'pno': fmt(b), 'status': 'EBOM_ONLY',
                     'ebom_name': ' / '.join(sorted(n for n in ebom[b] if n))[:60],
                     'mbom_name': '', 'ccc_count': 0, 'cccs': ''})
    for b in m_only:
        rows.append({'pno': fmt(b), 'status': 'MBOM_ONLY', 'ebom_name': '',
                     'mbom_name': ' / '.join(sorted(mbom[b]['names']))[:60],
                     'ccc_count': len(mbom[b]['cccs']),
                     'cccs': ','.join(sorted(mbom[b]['cccs'])[:15])})

    # 결과 엑셀
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook(); ws = wb.active; ws.title = 'E-BOM vs M-BOM'
    ws.append(['NO', '품번(10자리)', '판정', 'E-BOM 품명', 'M-BOM PART-NAME', 'CCC 수', 'CCC 목록'])
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF'); c.fill = PatternFill('solid', start_color='1A237E')
    fills = {'OK': None, 'EBOM_ONLY': PatternFill('solid', start_color='FFCDD2'),
             'MBOM_ONLY': PatternFill('solid', start_color='FFF9C4')}
    label = {'OK': '일치(OK)', 'EBOM_ONLY': 'E-BOM에만 존재 — 확인 필요', 'MBOM_ONLY': 'M-BOM에만 존재 — 확인 필요'}
    for i, r in enumerate(rows, 1):
        ws.append([i, r['pno'], label[r['status']], r['ebom_name'], r['mbom_name'], r['ccc_count'], r['cccs']])
        if fills[r['status']]:
            for c in ws[i + 1]: c.fill = fills[r['status']]
    result_id = uuid.uuid4().hex[:10]
    out = os.path.join(REPORTS_DIR, f'COMPARE_{result_id}.xlsx')
    wb.save(out)
    COMPARE_RESULTS[result_id] = out

    return JSONResponse({'ok': True, 'result_id': result_id,
                         'vehicle': vehicle, 'stage': stage, 'files': file_names,
                         'ebom_count': len(ebom), 'mbom_count': len(mbom),
                         'ok_count': len(both), 'ebom_only': len(e_only), 'mbom_only': len(m_only),
                         'rows': rows[:400], 'truncated': len(rows) > 400})


@app.get('/ebom-mbom-compare/download/{result_id}')
async def ebom_mbom_compare_download(request: Request, result_id: str):
    redir = require_login(request)
    if redir: return redir
    if not re.fullmatch(r'[a-f0-9]{10}', result_id):
        return JSONResponse({'error': '잘못된 요청'}, status_code=400)
    path = COMPARE_RESULTS.get(result_id)
    if not path or not os.path.exists(path):
        return JSONResponse({'error': '결과가 만료되었습니다.'}, status_code=404)
    return FileResponse(path, filename='EBOM_MBOM_비교결과.xlsx',
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


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
    matrix = get_ccc_matrix(vehicle)   # 차종 단독 (단계 무관)
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
    cells = body.get('cells', [])
    if not vehicle:
        return JSONResponse({'error': '차종을 선택하세요.'}, status_code=400)
    saved = 0
    for cell in cells:
        mat = str(cell.get('material_type', '')).strip()
        ctry = str(cell.get('country_code', '')).strip()
        ccc = str(cell.get('ccc_code', '')).strip()
        if mat and ctry:
            upsert_ccc_matrix(vehicle, mat, ctry, ccc, me['username'])
            saved += 1
    return JSONResponse({'ok': True, 'saved': saved})


@app.get('/api/ccc/matrix')
async def api_ccc_matrix(request: Request, vehicle: str = '', stage: str = ''):
    """영업단가 게시판에서 호출 — CCC 매트릭스 조회 (차종 단독)"""
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    matrix = get_ccc_matrix(vehicle) if vehicle else []
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
    # 연결은 차종 단독 — E-BOM은 (열,위치)별 최신, CCC는 차종별 최신 (단계 무관)
    ebom_items = get_ebom_items_by_vehicle(vehicle) if vehicle else []
    ccc_matrix = get_ccc_matrix(vehicle) if vehicle else []
    prices = get_sales_prices_v2(vehicle or None, None)
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


# ── PEL 사양변경 게시판 (부품사양서 → 사양수현황 그리드) ────────────────────────
PEL_SPEC_DIR = os.path.join(DATA_DIR, 'pel_spec')
os.makedirs(PEL_SPEC_DIR, exist_ok=True)


def _pt_mark(v) -> bool:
    return str(v).strip() in ('○', '●', 'O', 'o', '*', 'V', 'v', '√', '1')


def _extract_my_code(part_path: str) -> str:
    """부품사양서에서 '차종년식 : XX YY' 공장구분 코드 추출 (행/열 위치 무관하게 검색)."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(part_path, data_only=True)
        ws = wb.active
        for r in range(1, min(14, ws.max_row + 1)):
            for c in range(1, min(24, ws.max_column + 1)):
                v = ws.cell(r, c).value
                if v and isinstance(v, str):
                    m = re.search(r'차종\s*년?식\s*[:：]\s*([0-9A-Za-z]+\s+[0-9A-Za-z]+)', v)
                    if m:
                        wb.close()
                        return m.group(1).strip()
        wb.close()
    except Exception:
        pass
    return ''


def _build_pt_map(part_path: str) -> dict:
    """VC → 기본차/환경차/공용 (BASE/EV1 열 스캔)."""
    import openpyxl
    pt_map = {}
    try:
        wb = openpyxl.load_workbook(part_path, data_only=True)
        ws = wb.active
        base_col = ev_col = None
        for r in range(1, min(14, ws.max_row + 1)):
            for c in range(1, min(70, ws.max_column + 1)):
                s = str(ws.cell(r, c).value or '').strip().upper()
                if s == 'BASE': base_col = c
                elif s in ('EV1', 'EV'): ev_col = c
            if base_col and ev_col: break
        if base_col or ev_col:
            for r in range(1, ws.max_row + 1):
                vraw = ws.cell(r, 1).value
                if vraw is None: continue
                vc = str(vraw).strip()
                if not vc or not vc[0].isdigit(): continue
                b = _pt_mark(ws.cell(r, base_col).value) if base_col else False
                e = _pt_mark(ws.cell(r, ev_col).value) if ev_col else False
                pt_map[vc] = '공용' if (b and e) else ('기본차' if b else ('환경차' if e else ''))
        wb.close()
    except Exception:
        pass
    return pt_map


def _transform_pel_spec_multi(sources: list) -> dict:
    """여러 부품사양서(공장별)를 하나의 사양수현황 그리드로 병합.
       sources: [{'path':..., 'factory':...}] — factory가 각 행의 공장 태그로 들어감.
       열은 모든 소스의 사양 합집합(옵션그룹/표시순서 정렬)."""
    from bom_generator import parse_part_spec, load_pel_master
    master = load_pel_master(PEL_CODE_PATH).get('data', {})
    col_defs, all_rows = {}, []
    for src in sources:
        path = src.get('path'); factory = src.get('factory', '') or ''
        try:
            spec = parse_part_spec(path)
        except Exception:
            continue
        pt_map = _build_pt_map(path)
        for vcrow in spec.get('vcs', []):
            marks, spec_names = set(), []
            for o in vcrow.get('opts', []):
                m = master.get(str(o.get('pel_code', '')).strip())
                if not m: continue
                sp = str(m.get('사양', '')).strip()
                if not sp: continue
                grp = str(m.get('옵션그룹', '')).strip() or 'OPTION'
                try: order = float(m.get('표시순서') or 9999)
                except Exception: order = 9999.0
                if sp not in col_defs or order < col_defs[sp]['order']:
                    col_defs[sp] = {'group': grp, 'order': order}
                marks.add(sp); spec_names.append(sp)
            all_rows.append({
                'vc': vcrow.get('vc', ''), 'region': vcrow.get('region', ''),
                'powertrain': pt_map.get(str(vcrow.get('vc', '')).strip(), ''),
                'factory': factory,
                'spec_text': '+'.join(spec_names),
                'marks': sorted(marks),
            })
    columns = [{'spec': sp, 'group': d['group'], 'order': d['order']}
               for sp, d in sorted(col_defs.items(), key=lambda kv: (kv[1]['order'], kv[0]))]
    groups = []
    for c in columns:
        if groups and groups[-1]['group'] == c['group']:
            groups[-1]['span'] += 1
        else:
            groups.append({'group': c['group'], 'span': 1})
    return {'columns': columns, 'groups': groups, 'rows': all_rows, 'vc_count': len(all_rows)}


def _transform_pel_spec(part_path: str, factory: str = '') -> dict:
    """단일 부품사양서 → 사양수현황 그리드."""
    return _transform_pel_spec_multi([{'path': part_path, 'factory': factory}])


@app.get('/pel-spec', response_class=HTMLResponse)
async def pel_spec_page(request: Request, vehicle: str = ''):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    return templates.TemplateResponse(request=request, name='pel_spec.html', context={
        'me': me, 'vcodes': get_all_vehicle_codes(), 'sel_vehicle': vehicle,
    })


@app.get('/pel-spec/list')
async def pel_spec_list(request: Request, vehicle: str = ''):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    if not vehicle:
        return JSONResponse({'items': []})
    return JSONResponse({'items': get_pel_spec_list(vehicle)})


FACTORY_OPTIONS = ['공통', '광주', '화성']


@app.post('/pel-spec/detect-code')
async def pel_spec_detect_code(request: Request, file: UploadFile = File(...)):
    """업로드 전 미리보기 — 파일에서 차종년식 코드 추출(공장 자동제안용)."""
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    ext = os.path.splitext(file.filename)[1].lower()
    tmp = os.path.join(PEL_SPEC_DIR, f'_tmp_{uuid.uuid4().hex[:10]}{ext}')
    with open(tmp, 'wb') as f:
        shutil.copyfileobj(file.file, f)
    try:
        code = _extract_my_code(tmp)
    finally:
        try: os.unlink(tmp)
        except Exception: pass
    return JSONResponse({'my_code': code})


@app.post('/pel-spec/upload')
async def pel_spec_upload(
    request: Request,
    vehicle_code: str = Form(...),
    powertrain: str = Form('전체'),
    factory: str = Form('공통'),
    revision: str = Form('VER.1'),
    title: str = Form(...),
    description: str = Form(''),
    file: UploadFile = File(...),
):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)
    if not vehicle_code.strip() or not title.strip():
        return JSONResponse({'error': '차종과 제목은 필수입니다.'}, status_code=400)
    factory = factory.strip() or '공통'
    if factory not in FACTORY_OPTIONS:
        factory = '공통'
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.xlsx', '.xls'):
        return JSONResponse({'error': 'xlsx/xls 파일만 업로드 가능합니다.'}, status_code=400)
    file_id = uuid.uuid4().hex[:16]
    saved_path = os.path.join(PEL_SPEC_DIR, f'{file_id}{ext}')
    with open(saved_path, 'wb') as f:
        shutil.copyfileobj(file.file, f)
    my_code = _extract_my_code(saved_path)
    # 업로드 즉시 변환 시도 (오류나도 저장은 유지)
    try:
        grid = _transform_pel_spec(saved_path, factory)
        vc_count = grid['vc_count']; col_count = len(grid['columns'])
        msg = f'{vc_count}개 VC · {col_count}개 사양열 변환됨 · 공장={factory}'
    except Exception as ex:
        vc_count = 0; col_count = 0
        msg = f'파일 저장됨 (변환 오류: {ex})'
    new_id = add_pel_spec(vehicle_code.strip().upper(), powertrain.strip() or '전체',
                          revision.strip() or 'VER.1', title.strip(), description.strip(),
                          file.filename, file_id, saved_path, me['username'],
                          factory=factory, my_code=my_code)
    return JSONResponse({'ok': True, 'id': new_id, 'uploaded_by': me['username'],
                         'vc_count': vc_count, 'col_count': col_count,
                         'factory': factory, 'my_code': my_code, 'message': msg})


@app.get('/pel-spec/grid/{item_id}')
async def pel_spec_grid(request: Request, item_id: int):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    item = get_pel_spec(item_id)
    if not item or not item.get('file_path') or not os.path.exists(item['file_path']):
        return JSONResponse({'error': '원본 파일이 없습니다.'}, status_code=404)
    try:
        grid = _transform_pel_spec(item['file_path'], item.get('factory', ''))
        grid['meta'] = {'vehicle': item['vehicle_code'], 'powertrain': item['powertrain'],
                        'factory': item.get('factory', ''), 'my_code': item.get('my_code', ''),
                        'revision': item['revision'], 'title': item['title'],
                        'filename': item['filename'], 'merged': False}
        return JSONResponse(grid)
    except Exception as ex:
        import traceback
        return JSONResponse({'error': f'변환 오류: {ex}', 'trace': traceback.format_exc()}, status_code=500)


@app.get('/pel-spec/grid-merged/{vehicle}')
async def pel_spec_grid_merged(request: Request, vehicle: str):
    """차종의 공장별 최신 PEL을 하나의 사양수현황 그리드로 병합."""
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    latest = get_pel_spec_latest_by_factory(vehicle)
    sources = [{'path': it['file_path'], 'factory': it.get('factory', '') or '공통'}
               for it in latest if it.get('file_path') and os.path.exists(it['file_path'])]
    if not sources:
        return JSONResponse({'error': '병합할 PEL이 없습니다.'}, status_code=404)
    try:
        grid = _transform_pel_spec_multi(sources)
        parts = [f"{it.get('factory','공통')} {it['revision']}({it.get('my_code','') or '-'})" for it in latest]
        grid['meta'] = {'vehicle': vehicle, 'merged': True,
                        'sources': parts, 'factory_count': len(sources),
                        'title': f'{vehicle} 통합', 'revision': '통합'}
        return JSONResponse(grid)
    except Exception as ex:
        import traceback
        return JSONResponse({'error': f'변환 오류: {ex}', 'trace': traceback.format_exc()}, status_code=500)


def _extract_header_style(part_path):
    """원본 부품사양서의 고정틀 헤더 색을 추출 (UPG VC 헤더셀 기준).
       반환: {'fill', 'gfill'(그룹헤더용 약간 진하게), 'font', 'bold'}."""
    style = {'fill': '1A237E', 'gfill': '283593', 'font': 'FFFFFF', 'bold': True}
    try:
        import openpyxl
        wb = openpyxl.load_workbook(part_path)
        ws = wb.active
        for r in range(1, 13):
            for c in range(1, 7):
                if str(ws.cell(r, c).value or '').strip() == 'UPG VC':
                    cell = ws.cell(r, c)
                    rgb = getattr(getattr(cell.fill, 'fgColor', None), 'rgb', None)
                    if isinstance(rgb, str) and len(rgb) >= 6 and rgb[-6:] != '000000':
                        style['fill'] = rgb[-6:]
                        style['gfill'] = _darken_hex(rgb[-6:], 0.85)
                    frgb = getattr(getattr(cell.font, 'color', None), 'rgb', None)
                    if isinstance(frgb, str) and len(frgb) >= 6:
                        style['font'] = frgb[-6:]
                    style['bold'] = bool(cell.font.bold)
                    wb.close()
                    return style
        wb.close()
    except Exception:
        pass
    return style


def _darken_hex(hex6, factor):
    try:
        r = int(int(hex6[0:2], 16) * factor)
        g = int(int(hex6[2:4], 16) * factor)
        b = int(int(hex6[4:6], 16) * factor)
        return f'{r:02X}{g:02X}{b:02X}'
    except Exception:
        return hex6


def _pel_row_val(r, col_id):
    if col_id == 'factory': return r.get('factory', '') or '(없음)'
    if col_id == 'powertrain': return r.get('powertrain', '') or '(없음)'
    if col_id == 'region': return r.get('region', '') or '(없음)'
    if col_id.startswith('opt:'): return '●' if col_id[4:] in set(r.get('marks', [])) else '(없음)'
    return ''


def _pel_filter_generic(rows, filters_json, q):
    """웹 오토필터와 동일 — filters: {colId: [허용값...]} + 텍스트검색."""
    try:
        filters = json.loads(filters_json) if filters_json else {}
    except Exception:
        filters = {}
    ql = (q or '').strip().lower()
    out = []
    for r in rows:
        ok = True
        for col_id, allowed in filters.items():
            if _pel_row_val(r, col_id) not in allowed:
                ok = False; break
        if not ok:
            continue
        if ql:
            s = f"{r.get('vc','')} {r.get('region','')} {r.get('spec_text','')}".lower()
            if ql not in s:
                continue
        out.append(r)
    return out


def _pel_grid_to_excel(grid, filename, style=None):
    """사양수현황 그리드 dict → 엑셀 (공장 열 포함, 그룹헤더 병합, ●).
       style: 원본 부품사양서에서 추출한 고정틀 색(없으면 시스템 기본)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    st = style or {'fill': '1A237E', 'gfill': '283593', 'font': 'FFFFFF', 'bold': True}
    wb = Workbook(); ws = wb.active; ws.title = '사양수현황'
    center = Alignment(horizontal='center', vertical='center')
    hfill = PatternFill('solid', start_color=st['fill'])
    hfont = Font(bold=st.get('bold', True), color=st['font'])
    gfill = PatternFill('solid', start_color=st['gfill'])
    fixed = ['NO', 'VC', '공장', '파워트레인', '지역', 'SPEC']
    ws.append(fixed + sum([[g['group']] + [''] * (g['span'] - 1) for g in grid['groups']], []))
    ws.append([''] * len(fixed) + [c['spec'] for c in grid['columns']])
    for cell in ws[1]:
        cell.fill = gfill; cell.font = hfont; cell.alignment = center
    for cell in ws[2]:
        cell.fill = hfill; cell.font = hfont; cell.alignment = center
    cidx = len(fixed) + 1
    for g in grid['groups']:
        if g['span'] > 1:
            ws.merge_cells(start_row=1, start_column=cidx, end_row=1, end_column=cidx + g['span'] - 1)
        cidx += g['span']
    for i in range(1, len(fixed) + 1):
        ws.merge_cells(start_row=1, start_column=i, end_row=2, end_column=i)
        ws.cell(1, i).value = fixed[i - 1]; ws.cell(1, i).fill = hfill
    for i, r in enumerate(grid['rows'], 1):
        base = [i, r['vc'], r.get('factory', ''), r['powertrain'], r['region'], r['spec_text']]
        markset = set(r['marks'])
        line = base + ['●' if c['spec'] in markset else '' for c in grid['columns']]
        ws.append(line)
        for c in range(len(fixed) + 1, len(line) + 1):
            ws.cell(i + 2, c).alignment = center
    out_path = os.path.join(REPORTS_DIR, f'PELSPEC_{uuid.uuid4().hex[:10]}.xlsx')
    wb.save(out_path)
    return FileResponse(out_path, filename=filename,
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.get('/pel-spec/download/{item_id}')
async def pel_spec_download(request: Request, item_id: int, mode: str = 'grid',
                            filters: str = '', q: str = ''):
    redir = require_login(request)
    if redir: return redir
    item = get_pel_spec(item_id)
    if not item or not item.get('file_path') or not os.path.exists(item['file_path']):
        return JSONResponse({'error': '원본 파일이 없습니다.'}, status_code=404)
    if mode == 'original':
        return FileResponse(item['file_path'], filename=item['filename'])
    try:
        grid = _transform_pel_spec(item['file_path'], item.get('factory', ''))
    except Exception as ex:
        return JSONResponse({'error': f'변환 오류: {ex}'}, status_code=500)
    grid = dict(grid)
    grid['rows'] = _pel_filter_generic(grid['rows'], filters, q)
    base = os.path.splitext(item['filename'])[0]
    return _pel_grid_to_excel(grid, f'{base}_사양수현황.xlsx',
                              style=_extract_header_style(item['file_path']))


@app.get('/pel-spec/download-merged/{vehicle}')
async def pel_spec_download_merged(request: Request, vehicle: str, filters: str = '', q: str = ''):
    redir = require_login(request)
    if redir: return redir
    latest = get_pel_spec_latest_by_factory(vehicle)
    sources = [{'path': it['file_path'], 'factory': it.get('factory', '') or '공통'}
               for it in latest if it.get('file_path') and os.path.exists(it['file_path'])]
    if not sources:
        return JSONResponse({'error': '병합할 PEL이 없습니다.'}, status_code=404)
    grid = _transform_pel_spec_multi(sources)
    grid['rows'] = _pel_filter_generic(grid['rows'], filters, q)
    return _pel_grid_to_excel(grid, f'{vehicle}_통합_사양수현황.xlsx',
                              style=_extract_header_style(sources[0]['path']))


@app.post('/pel-spec/delete/{item_id}')
async def pel_spec_delete(request: Request, item_id: int):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)
    item = get_pel_spec(item_id)
    if not item:
        return JSONResponse({'error': '찾을 수 없습니다.'}, status_code=404)
    if me['role'] != 'admin' and item['uploaded_by'] != me['username']:
        return JSONResponse({'error': '본인 또는 관리자만 삭제할 수 있습니다.'}, status_code=403)
    info = delete_pel_spec(item_id)
    if info and info.get('file_path') and os.path.exists(info['file_path']):
        try: os.unlink(info['file_path'])
        except Exception: pass
    return JSONResponse({'ok': True})


# ── 영업 단가 원본 파일 (차종별 리비전 게시판) ─────────────────────────────────────
SALES_FILE_DIR = os.path.join(DATA_DIR, 'sales_files')
os.makedirs(SALES_FILE_DIR, exist_ok=True)


@app.get('/sales/price/files', response_class=HTMLResponse)
async def sales_files_page(request: Request, vehicle: str = ''):
    redir = require_login(request)
    if redir: return redir
    me = current_user(request)
    return templates.TemplateResponse(request=request, name='sales_files.html', context={
        'me': me, 'vcodes': get_all_vehicle_codes(), 'sel_vehicle': vehicle,
    })


@app.get('/sales/price/files/list')
async def sales_files_list(request: Request, vehicle: str = ''):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    if not vehicle:
        return JSONResponse({'items': []})
    return JSONResponse({'items': get_sales_file_list(vehicle)})


@app.post('/sales/price/files/upload')
async def sales_files_upload(
    request: Request,
    vehicle_code: str = Form(...),
    powertrain: str = Form('전체'),
    revision: str = Form('VER.1'),
    title: str = Form(...),
    description: str = Form(''),
    file: UploadFile = File(...),
):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)
    if not vehicle_code.strip() or not title.strip():
        return JSONResponse({'error': '차종과 제목은 필수입니다.'}, status_code=400)
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.xlsx', '.xls', '.xlsm'):
        return JSONResponse({'error': 'xlsx/xls 파일만 업로드 가능합니다.'}, status_code=400)
    file_id = uuid.uuid4().hex[:16]
    saved_path = os.path.join(SALES_FILE_DIR, f'{file_id}{ext}')
    with open(saved_path, 'wb') as f:
        shutil.copyfileobj(file.file, f)
    new_id = add_sales_file(vehicle_code.strip().upper(), powertrain.strip() or '전체',
                            revision.strip() or 'VER.1', title.strip(), description.strip(),
                            file.filename, file_id, saved_path, me['username'])
    return JSONResponse({'ok': True, 'id': new_id, 'uploaded_by': me['username'],
                         'message': '업로드 완료'})


# 영업단가 시트 열 매핑 (1-indexed) — KMC 품의 자료 서식
SALES_SHEET_COLS = {'no': 1, 'spec': 2, 'pno': 3, 'cmp': 4, 'color': 5,
                    'jeonga': 6, 'sagup': 7, 'hap': 8, 'after': 9,
                    'bigo': 10, 'sangak': 12, 'upcode': 15}
SALES_DATA_START = 8


def _sales_num(v):
    if v is None or str(v).strip() == '':
        return None
    try:
        f = float(str(v).replace(',', ''))
        return int(f) if f == int(f) else f
    except Exception:
        return None


def _parse_sales_sheet(path):
    """KMC 단가 집계표의 데이터 영역(8행~)을 파싱. A열이 숫자인 행만."""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    C = SALES_SHEET_COLS
    rows = []
    for r in range(SALES_DATA_START, ws.max_row + 1):
        a = ws.cell(r, C['no']).value
        if not isinstance(a, (int, float)):
            continue
        def g(k):
            return ws.cell(r, C[k]).value
        rows.append({
            'r': r, 'no': a,
            'spec': g('spec') or '', 'pno': g('pno') or '', 'cmp': g('cmp') or '',
            'color': g('color') or '', 'jeonga': _sales_num(g('jeonga')),
            'sagup': _sales_num(g('sagup')), 'hap': _sales_num(g('hap')),
            'after': _sales_num(g('after')), 'bigo': g('bigo') or '',
            'sangak': _sales_num(g('sangak')), 'upcode': g('upcode') or '',
        })
    return rows


@app.get('/sales/price/files/sheet/{item_id}')
async def sales_files_sheet(request: Request, item_id: int):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    item = get_sales_file(item_id)
    if not item or not item.get('file_path') or not os.path.exists(item['file_path']):
        return JSONResponse({'error': '원본 파일이 없습니다.'}, status_code=404)
    try:
        rows = _parse_sales_sheet(item['file_path'])
    except Exception as ex:
        return JSONResponse({'error': f'파싱 오류: {ex}'}, status_code=500)
    try:
        edits = json.loads(item.get('edits_json') or '{}')
    except Exception:
        edits = {}
    ccc = get_ccc_codes_for_dropdown(item['vehicle_code'])
    return JSONResponse({'rows': rows, 'edits': edits, 'ccc_codes': ccc,
                         'meta': {'vehicle': item['vehicle_code'], 'revision': item['revision'],
                                  'title': item['title'], 'filename': item['filename']}})


@app.post('/sales/price/files/save-edits/{item_id}')
async def sales_files_save_edits(request: Request, item_id: int):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)
    item = get_sales_file(item_id)
    if not item:
        return JSONResponse({'error': '찾을 수 없습니다.'}, status_code=404)
    if me['role'] != 'admin' and item['uploaded_by'] != me['username']:
        return JSONResponse({'error': '본인 또는 관리자만 수정할 수 있습니다.'}, status_code=403)
    body = await request.json()
    edits = body.get('edits', {})
    update_sales_file_edits(item_id, json.dumps(edits, ensure_ascii=False))
    return JSONResponse({'ok': True, 'count': len(edits)})


@app.get('/sales/price/files/download/{item_id}')
async def sales_files_download(request: Request, item_id: int):
    redir = require_login(request)
    if redir: return redir
    item = get_sales_file(item_id)
    if not item or not item.get('file_path') or not os.path.exists(item['file_path']):
        return JSONResponse({'error': '원본 파일이 없습니다.'}, status_code=404)
    try:
        edits = json.loads(item.get('edits_json') or '{}')
    except Exception:
        edits = {}
    if not edits:
        # 편집 없음 → 원본 그대로
        return FileResponse(item['file_path'], filename=item['filename'])
    # 원본을 템플릿으로 열어 편집값만 덮어쓰기 (서식/양식 100% 보존)
    import openpyxl
    C = SALES_SHEET_COLS
    wb = openpyxl.load_workbook(item['file_path'])
    ws = wb.active
    for rstr, e in edits.items():
        try:
            r = int(rstr)
        except Exception:
            continue
        if e.get('color') is not None and str(e.get('color')) != '':
            ws.cell(r, C['color']).value = e['color']
        f = _sales_num(e.get('jeonga'))
        g = _sales_num(e.get('sagup'))
        if f is not None:
            ws.cell(r, C['jeonga']).value = f
        if g is not None:
            ws.cell(r, C['sagup']).value = g
            ws.cell(r, C['hap']).value = g            # 합계 = 사급
        if f is not None and g is not None:
            ws.cell(r, C['after']).value = f + g      # 변경후 = 종전가 + 사급
    out_path = os.path.join(REPORTS_DIR, f'SALES_{uuid.uuid4().hex[:10]}.xlsx')
    wb.save(out_path)
    return FileResponse(out_path, filename=item['filename'],
                        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.post('/sales/price/files/delete/{item_id}')
async def sales_files_delete(request: Request, item_id: int):
    redir = require_login(request)
    if redir: return JSONResponse({'error': '로그인 필요'}, status_code=401)
    me = current_user(request)
    item = get_sales_file(item_id)
    if not item:
        return JSONResponse({'error': '찾을 수 없습니다.'}, status_code=404)
    if me['role'] != 'admin' and item['uploaded_by'] != me['username']:
        return JSONResponse({'error': '본인 또는 관리자만 삭제할 수 있습니다.'}, status_code=403)
    info = delete_sales_file(item_id)
    if info and info.get('file_path') and os.path.exists(info['file_path']):
        try: os.unlink(info['file_path'])
        except Exception: pass
    return JSONResponse({'ok': True})
