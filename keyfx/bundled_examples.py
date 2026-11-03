"""Install editable, offline sample shots once without replacing user projects."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import threading
import zipfile

ASSETS=Path(__file__).with_name('example_projects')
_lock=threading.RLock()


def _relative(value):
    if not isinstance(value,str) or '\\' in value or ':' in value or value=='.':
        raise ValueError('Invalid bundled example path.')
    path=PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('Invalid bundled example path.')
    return Path(*path.parts)


def _install(entry,data):
    pid=entry['id']
    if not re.fullmatch(r'[a-z0-9-]+',pid):raise ValueError('Invalid bundled example ID.')
    destination=data/'projects'/pid
    if destination.exists():return
    source=ASSETS/_relative(entry['archive'])
    if hashlib.sha256(source.read_bytes()).hexdigest()!=entry['sha256']:
        raise ValueError('A bundled example archive failed its integrity check.')
    data.mkdir(parents=True,exist_ok=True);destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.example-',dir=data) as td:
        stage=Path(td)/'shot';stage.mkdir()
        with zipfile.ZipFile(source) as archive:
            manifest=json.loads(archive.read('files.json'))
            if sum(item.file_size for item in archive.infolist())>512*1024*1024:
                raise ValueError('Bundled example exceeds its size limit.')
            for name,digest in manifest.items():
                rel=_relative(name)
                if not re.fullmatch(r'[0-9a-f]{64}',digest):raise ValueError('Invalid example asset hash.')
                raw=archive.read('blobs/'+digest)
                if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Bundled example asset changed.')
                target=stage/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        project=json.loads((stage/'project.json').read_text())
        if project['id']!=pid or project.get('renders') or project.get('element_takes') or project.get('element_approvals'):
            raise ValueError('Bundled examples must be editable source setups without generation or approval state.')
        try:os.rename(stage,destination)
        except OSError:
            if not (destination/'project.json').is_file():raise


def install_defaults(data=None):
    if os.environ.get('KEYFX_SKIP_EXAMPLES')=='1':return []
    from . import projects
    data=Path(data) if data is not None else projects.DATA
    if not (ASSETS/'catalog.json').is_file():return []
    with _lock:
        marker=data/'.bundled-examples.json'
        installed=json.loads(marker.read_text()) if marker.is_file() else []
        added=[]
        for entry in reversed(json.loads((ASSETS/'catalog.json').read_text())['examples']):
            if entry['id'] in installed:continue
            _install(entry,data);installed.append(entry['id']);added.append(entry['id'])
            data.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w',prefix='.examples-',dir=data,delete=False) as f:
                f.write(json.dumps(installed)+'\n');temp=Path(f.name)
            os.replace(temp,marker)
        return added
