"""keyfx serve | new <video> | effect <project> ... | render <project> | list"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(prog='keyfx')
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('serve'); s.add_argument('--port', type=int, default=8765); s.add_argument('--host', default='127.0.0.1')
    n = sub.add_parser('new'); n.add_argument('video'); n.add_argument('--name', default=''); n.add_argument('--mode', default='plate')
    n.add_argument('--start', type=float, default=0.0); n.add_argument('--length', type=float, default=None)
    e = sub.add_parser('effect', help='add an effect at a frame and normalised position')
    e.add_argument('project'); e.add_argument('preset'); e.add_argument('frame', type=int); e.add_argument('x', type=float); e.add_argument('y', type=float)
    e.add_argument('--where', default=None); e.add_argument('--scale', type=float, default=None, help='multiply every stamp size')
    e.add_argument('--flip', action='store_true')
    r = sub.add_parser('render'); r.add_argument('project'); r.add_argument('--kind', default='effect', help='finish (effects, muzzle frames, splice) | effect | environment | location | dressing | keys')
    r.add_argument('--wait', action='store_true'); r.add_argument('--polish', choices=['on', 'off'], default=None, help='AI polish pass on the keys')
    sub.add_parser('list')
    approve = sub.add_parser('approve-environment', help='select a reviewed environment candidate')
    approve.add_argument('project'); approve.add_argument('render')
    a = ap.parse_args()

    if a.cmd == 'serve':
        import uvicorn
        uvicorn.run('keyfx.server:app', host=a.host, port=a.port)
        return
    from . import projects
    if a.cmd == 'approve-environment':
        p = projects.approve_environment(a.project, a.render)
        print('approved environment', p['environment']); return
    if a.cmd == 'list':
        print(json.dumps(projects.list_all(), indent=1)); return
    if a.cmd == 'new':
        p = projects.create(Path(a.video), a.name, a.mode, a.start, a.length)
        print(p['id'], p['frames'], 'frames'); return
    if a.cmd == 'effect':
        p = projects.add_effect(a.project, a.preset, a.frame, a.x, a.y)
        def fn(q):
            eff = q['effects'][-1]
            if a.where: eff['where'] = a.where
            eff['flip'] = a.flip
            if a.scale:
                for k in eff['keys']:
                    if 'scale' in k: k['scale'] *= a.scale
        p = projects.update(a.project, fn)
        print(json.dumps(p['effects'][-1], indent=1)); return
    if a.cmd == 'render':
        if a.polish:
            projects.update(a.project, lambda q: q['settings'].update(polish=a.polish == 'on'))
        r = projects.start_render(a.project, a.kind)
        print('render', r['id'])
        if a.wait:
            last = None
            while True:
                time.sleep(5)
                cur = next(x for x in projects.load(a.project)['renders'] if x['id'] == r['id'])
                if cur['status'] != last:
                    print(time.strftime('%H:%M:%S'), cur['status'], cur.get('error', '')); last = cur['status']
                if cur['status'] in ('done', 'error'):
                    break


if __name__ == '__main__':
    main()
