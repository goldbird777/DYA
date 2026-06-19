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
                  get_all_vehicle_codes, add_vehicle_code, update_vehicle_code, delete_vehicle_code)

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, 'reports')
os.makedirs(REPORTS_DIR, exist_ok=True)

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
