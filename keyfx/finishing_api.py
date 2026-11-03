import json,re
from fastapi import APIRouter,HTTPException,Request
from starlette.concurrency import run_in_threadpool
from . import projects,finish_approval,lighting_integration
from .element_api import _project,_id

router=APIRouter(prefix='/api/projects/{pid}',tags=['finishing'])

@router.get('/elements/{eid}/matte-support/plan')
def matte_plan(pid:str,eid:str,take_id:str):
    _project(pid);_id(eid);_id(take_id)
    from . import matte_support
    try:return matte_support.plan(projects.load(pid),eid,take_id)
    except (ValueError,KeyError,OSError) as exc:raise HTTPException(400,str(exc)) from None

@router.post('/elements/{eid}/matte-support/{tid}')
async def prepare_matte(pid:str,eid:str,tid:str,request:Request):
    _project(pid);_id(eid);_id(tid)
    from . import matte_support
    from .element_api import _length,MAX_PREPARE_BYTES,MAX_IMAGE_BYTES
    _length(request,MAX_PREPARE_BYTES)
    try:
        form=await request.form(max_files=121,max_fields=1,max_part_size=MAX_IMAGE_BYTES);frames={};meta=None;total=0;seen=set()
        for name,val in form.multi_items():
            if name in seen:raise ValueError('Each matte support frame must appear only once.')
            seen.add(name)
            if name=='metadata':
                if not isinstance(val,str) or len(val)>20000:raise ValueError('Supply frozen matte support metadata.')
                meta=json.loads(val);continue
            match=re.fullmatch(r'alpha_(\d{4})\.png',name)
            if not match or not hasattr(val,'read'):raise ValueError('Unexpected matte support upload field.')
            data=await val.read(MAX_IMAGE_BYTES+1);total+=len(data)
            if len(data)>MAX_IMAGE_BYTES or total>MAX_PREPARE_BYTES:raise ValueError('Matte support upload exceeds the capture limit.')
            frames[int(match.group(1))]=data
        if not isinstance(meta,dict):raise ValueError('Supply frozen matte support metadata.')
        return await run_in_threadpool(matte_support.prepare,pid,eid,tid,meta,frames)
    except (ValueError,TypeError,KeyError,OSError) as exc:raise HTTPException(400,str(exc)) from None

@router.post('/finishing/approve-preview')
async def approve_preview(pid:str,body:dict):
    _project(pid)
    if set(body)!={'render_id'} or not isinstance(body.get('render_id'),str):raise HTTPException(400,'Choose the combined LTX preview being approved.')
    _id(body['render_id'])
    try:return await run_in_threadpool(finish_approval.approve_preview,pid,body['render_id'])
    except (ValueError,KeyError,OSError) as exc:raise HTTPException(400,str(exc)) from None

@router.get('/lighting')
def lighting_status(pid:str):
    p=_project(pid)
    return {'takes':lighting_integration.records(p),'settings':p.get('light_integration',{})}

@router.post('/lighting/run')
def run_lighting(pid:str,body:dict):
    _project(pid)
    if set(body)-{'new_version'} or not isinstance(body.get('new_version',False),bool):raise HTTPException(400,'Choose whether to create a new lighting version.')
    try:return lighting_integration.start(pid,new_version=body.get('new_version',False))
    except (ValueError,KeyError,OSError) as exc:raise HTTPException(400,str(exc)) from None

@router.patch('/lighting')
def configure_lighting(pid:str,body:dict):
    _project(pid)
    try:return lighting_integration.configure(pid,body)
    except (ValueError,KeyError,OSError) as exc:raise HTTPException(400,str(exc)) from None
