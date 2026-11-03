"""Explicit, atomic approval of the exact combined preview the user reviewed."""
import json
from . import projects, element_jobs


def approve_preview(pid, render_id):
    with projects._lock(pid):
        p=projects.load(pid)
        if projects.generation_active(p):raise ValueError('Wait for the current generation before approving the preview.')
        r=next((r for r in p['renders'] if r['id']==render_id),None)
        if not r or r.get('kind')!='effect' or r.get('status')!='done' or not projects.render_current(p,r):
            raise ValueError('This combined preview changed. Build and review a current composite first.')
        if r.get('render_profile')!='native-ltx-elements-v1':raise ValueError('Choose a combined LTX preview.')
        request=json.loads((projects.pdir(pid)/'renders'/render_id/'request.json').read_text())
        ids=request.get('elements',[])
        if not isinstance(ids,list) or len(set(ids))!=len(ids):raise ValueError('The combined preview selection is invalid.')
        if r.get('review_only'):
            if ids!=r.get('review_take_ids'):raise ValueError('The reviewed element selection changed; rebuild the preview.')
        elif not r.get('composite_request_sha256') or element_jobs.elements._sha(projects.pdir(pid)/'renders'/render_id/'request.json')!=r['composite_request_sha256']:
            raise ValueError('Rebuild this combined preview before approving its selection.')
        selected=[]
        for effect in p['effects']:
            candidates=[row for row in p.get('element_takes',[]) if row['id'] in ids and row['effect_id']==effect['id']]
            if len(candidates)!=1:raise ValueError('The preview does not contain exactly one current version of every effect.')
            row=candidates[0]
            if row.get('status')!='done' or row['generation_signature']!=element_jobs.generation_signature(p,effect):
                raise ValueError('An element changed after this preview. Build a current composite first.')
            element_jobs._validated_take(projects.pdir(pid),row)
            selected.append((effect['id'],row['id']))
        if len(ids)!=len(selected):raise ValueError('The preview contains an obsolete effect selection.')
        # All validation completes before a single pin is written.
        p.setdefault('element_approvals',{}).update(dict(selected));projects.save(p)
        return projects.public(p)
