"""Opt-in, fail-closed input QC and submission ceiling for development sessions.

Nothing is enabled without BANG_ON_DEVELOPMENT_GATE. The reviewer, not the
generation worker, writes qc.json. Reservations count even when upload/submit
fails; they are never refunded or silently retried.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time
import uuid


ENV = 'BANG_ON_DEVELOPMENT_GATE'


class GateError(RuntimeError):
    pass


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def _read(path):
    return json.loads(Path(path).read_text())


def _write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
    os.replace(temp, path)


def _absolute(value, label):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise GateError(f'Development gate needs an absolute {label}.')
    return Path(value).resolve()


def _config():
    path = os.environ.get(ENV)
    if not path: return None
    try:
        c = _read(_absolute(path, 'configuration path'))
        if c.get('version') != 1 or not isinstance(c.get('session_id'), str) or not c['session_id'].strip():
            raise GateError('Invalid development gate session.')
        maximum = c.get('max_submissions')
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 4:
            raise GateError('Development sessions permit at most four submissions.')
        c['project_root'] = str(_absolute(c.get('project_root'), 'project_root'))
        c['audit_dir'] = str(_absolute(c.get('audit_dir'), 'audit_dir'))
        stages = c.get('allowed_stages')
        if (not isinstance(stages, list) or not 1 <= len(stages) <= 4
                or any(not isinstance(s, dict) or set(s) != {'effect_id', 'stage'}
                       or not isinstance(s['effect_id'], str) or not s['effect_id']
                       or s['stage'] not in ('rgb', 'alpha') for s in stages)
                or len({(s['effect_id'], s['stage']) for s in stages}) != len(stages)):
            raise GateError('Choose unique ordered development effect/stage allowances.')
        # The allowance/scope cannot be expanded by editing config mid-session.
        binding = {k: c[k] for k in ('session_id', 'project_root', 'max_submissions', 'allowed_stages')}
        c['scope_sha256'] = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
        poll = c.get('poll_seconds', .5)
        if isinstance(poll, bool) or not isinstance(poll, (int, float)) or not .01 <= poll <= 5:
            raise GateError('Invalid development gate poll interval.')
        c['poll_seconds'] = poll
        return c
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, GateError): raise
        raise GateError('Could not read a valid development gate configuration.') from exc


@contextmanager
def _ledger_lock(root):
    path = root / 'ledger.lock'; root.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 5
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd); break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise GateError('Development ledger is locked; inspect it before any further submission.')
            time.sleep(.02)
    try: yield
    finally: path.unlink()


def _ledger(c):
    path = Path(c['audit_dir']) / 'ledger.json'
    if not path.exists():
        return {'version': 1, 'session_id': c['session_id'], 'scope_sha256': c['scope_sha256'],
                'maximum': c['max_submissions'], 'attempts': []}
    try: ledger = _read(path)
    except (OSError, ValueError) as exc: raise GateError('Development ledger cannot be verified.') from exc
    if (ledger.get('version') != 1 or ledger.get('session_id') != c['session_id']
            or ledger.get('scope_sha256') != c['scope_sha256'] or ledger.get('maximum') != c['max_submissions']
            or not isinstance(ledger.get('attempts'), list)):
        raise GateError('Development allowance/scope changed; the existing ledger remains authoritative.')
    return ledger


def _check_allowance(c, index):
    attempts = _ledger(c)['attempts']
    if any(a['status'] not in ('done', 'failed') for a in attempts):
        raise GateError('An outstanding/uncertain development attempt blocks further inference.')
    if len(attempts) >= c['max_submissions']:
        raise GateError('Development submission allowance exhausted; continue local tests only.')
    if any(a['allowance_index'] >= index for a in attempts):
        raise GateError('This development stage already attempted or stage order changed; no retry allowed.')


@dataclass
class Permit:
    config: dict
    pending: Path
    pending_sha: str
    qc_sha: str
    manifest: dict
    index: int
    attempt_id: str | None = None

    def verify(self):
        current = _config()
        if (current is None or current['scope_sha256'] != self.config['scope_sha256']
                or current['audit_dir'] != self.config['audit_dir']):
            raise GateError('Development configuration changed after input review.')
        if _sha(self.pending) != self.pending_sha or _sha(self.pending.with_name('qc.json')) != self.qc_sha:
            raise GateError('Reviewed development QC/input manifest changed.')
        for item in self.manifest['files']:
            if _sha(item['path']) != item['sha256']:
                raise GateError('A reviewed development graph/request/input changed; no submission allowed.')
        if (Path(self.config['audit_dir']) / 'cancel').exists():
            raise GateError('Development session was cancelled; no submission allowed.')

    def reserve(self, idempotency_key):
        """Called immediately before executor/upload, after the normal stage claim."""
        self.verify()
        c = self.config; root = Path(c['audit_dir'])
        with _ledger_lock(root):
            _check_allowance(c, self.index)
            ledger = _ledger(c); attempts = ledger['attempts']
            attempt_id = str(uuid.uuid4())
            attempts.append({'id': attempt_id, 'status': 'reserved', 'allowance_index': self.index,
                             'effect_id': self.manifest['effect_id'], 'stage': self.manifest['stage'],
                             'take_id': self.manifest['take_id'], 'pending': str(self.pending),
                             'pending_sha256': self.pending_sha, 'qc_sha256': self.qc_sha,
                             'input_sha256': self.manifest['input_sha256'],
                             'graph_sha256': self.manifest['graph_sha256'],
                             'idempotency_key': idempotency_key, 'reserved_at': time.time()})
            _write(root / 'ledger.json', ledger)
            self.attempt_id = attempt_id

    def update(self, status, *, job=None):
        if not self.attempt_id: return
        root = Path(self.config['audit_dir'])
        with _ledger_lock(root):
            ledger = _ledger(self.config)
            row = next(a for a in ledger['attempts'] if a['id'] == self.attempt_id)
            row.update(status=status, updated_at=time.time())
            if job: row['job'] = job
            _write(root / 'ledger.json', ledger)


def before_stage(request, stage, graph, uploads, output, cache_dir, *, awaiting=None):
    """Freeze and wait for independent QC before *any* upload or inference."""
    c = _config()
    if c is None: return None
    project = Path(c['project_root']); td = Path(request['take_dir']).resolve()
    pair = {'effect_id': request['effect_id'], 'stage': stage}
    if (Path(request['takes_root']).resolve() != project / 'elements'
            or not td.is_relative_to(project / 'elements' / 'takes') or pair not in c['allowed_stages']):
        raise GateError('This generation is outside the configured development project/effect/stage scope.')
    index = c['allowed_stages'].index(pair)
    root = Path(c['audit_dir'])
    with _ledger_lock(root): _check_allowance(c, index)
    pending_dir = root / 'pending' / request['effect_id'] / request['id'] / stage / Path(cache_dir).name
    graph_path = pending_dir / 'graph.json'
    pending = pending_dir / 'pending.json'
    # Stable files let the independent reviewer bind approval to exact bytes.
    if not graph_path.exists(): _write(graph_path, graph)
    elif _read(graph_path) != graph: raise GateError('A frozen development graph differs; no submission allowed.')
    files = [{'path': str(graph_path), 'sha256': _sha(graph_path)},
             {'path': str(td / 'request.json'), 'sha256': _sha(td / 'request.json')}]
    frozen_uploads = {}
    for node, (field, source) in uploads.items():
        source = Path(source).resolve()
        if not source.is_relative_to(project): raise GateError('A development upload is outside the scoped project.')
        entry = {'path': str(source), 'sha256': _sha(source)}
        files.append(entry); frozen_uploads[node] = {'field': field, **entry}
    manifest = {'version': 1, 'session_id': c['session_id'], 'scope_sha256': c['scope_sha256'],
                'effect_id': request['effect_id'], 'take_id': request['id'], 'stage': stage,
                'backend': request.get('backend'), 'recipe': request['recipe'],
                'geometry': request['geometry'], 'timing': request['timing'],
                'provenance': request['provenance'], 'output_node': output,
                'input_sha256': files[-1]['sha256'], 'graph_sha256': files[0]['sha256'],
                'files': files, 'uploads': frozen_uploads, 'cache_dir': str(cache_dir)}
    if not pending.exists(): _write(pending, manifest)
    elif _read(pending) != manifest: raise GateError('Frozen development pending inputs changed; require a new reviewed input.')
    pending_sha = _sha(pending); qc_path = pending.with_name('qc.json')
    if awaiting: awaiting('awaiting_' + stage + '_qc')
    while not qc_path.exists():
        if (root / 'cancel').exists(): raise GateError('Development session cancelled while awaiting input QC.')
        if _sha(pending) != pending_sha: raise GateError('Development pending manifest changed while awaiting QC.')
        time.sleep(c['poll_seconds'])
    try: qc = _read(qc_path)
    except (OSError, ValueError) as exc: raise GateError('Independent development QC is not valid JSON.') from exc
    if (qc.get('verdict') != 'pass' or qc.get('pending_sha256') != pending_sha
            or not isinstance(qc.get('reviewer'), str) or not qc['reviewer'].strip()
            or not isinstance(qc.get('checked_at'), str) or not qc['checked_at'].strip()
            or qc_path.stat().st_mtime_ns < pending.stat().st_mtime_ns):
        raise GateError('Independent development QC did not pass for these exact pending inputs.')
    permit = Permit(c, pending, pending_sha, _sha(qc_path), manifest, index)
    permit.verify()
    return permit


def note_local_recovery(request, stage, receipt, asset, recovery_record):
    """Annotate an already consumed terminal attempt; never creates/refunds one."""
    c = _config()
    if c is None: return
    if (Path(request['takes_root']).resolve() != Path(c['project_root']) / 'elements'
            or {'effect_id': request['effect_id'], 'stage': stage} not in c['allowed_stages']):
        raise GateError('Local recovery is outside the configured development scope.')
    root = Path(c['audit_dir'])
    with _ledger_lock(root):
        ledger = _ledger(c)
        rows = [a for a in ledger['attempts'] if a['take_id'] == request['id'] and a['stage'] == stage
                and a['effect_id'] == request['effect_id'] and a.get('job') == receipt['job']]
        if (len(rows) != 1 or rows[0]['status'] not in ('failed', 'done')
                or rows[0]['input_sha256'] != receipt['input_sha256']
                or rows[0]['graph_sha256'] != receipt['graph_sha256']):
            raise GateError('Local recovery does not match a known terminal consumed allowance.')
        rows[0].update(status='done', local_recovery=str(recovery_record),
                       recovered_output_sha256=asset['sha256'], local_recovery_at=time.time())
        _write(root / 'ledger.json', ledger)
