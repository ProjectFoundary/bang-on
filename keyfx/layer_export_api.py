"""Local native layer-package endpoints; no model or provider operations."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from . import layer_exports
from .element_api import _project, _idle

router=APIRouter(prefix='/api/projects/{pid}/layer-exports',tags=['layer-exports'])


def _public(pid,value):
    if value.get('status')=='done':value['url']=f'/api/projects/{pid}/layer-exports/{value["id"]}/bundle.zip'
    return value


@router.post('')
async def start_export(pid:str,body:dict|None=None):
    p=_project(pid);_idle(p)
    try:return _public(pid,await run_in_threadpool(layer_exports.start,pid,body))
    except (ValueError,TypeError,KeyError,OSError) as exc:raise HTTPException(400,str(exc)) from None


@router.get('/{jid}')
def export_status(pid:str,jid:str):
    _project(pid)
    try:return _public(pid,layer_exports.status(pid,jid))
    except (ValueError,TypeError,KeyError,OSError):raise HTTPException(404,'Export package not found.') from None


@router.get('/{jid}/bundle.zip')
def export_archive(pid:str,jid:str):
    _project(pid)
    try:path=layer_exports.archive(pid,jid)
    except (ValueError,TypeError,KeyError,OSError):raise HTTPException(404,'Export package is missing, unfinished or changed.') from None
    return FileResponse(path,media_type='application/zip',filename=f'bang-on-layers-{jid[:12]}.zip',headers={'Cache-Control':'private, no-cache'})
