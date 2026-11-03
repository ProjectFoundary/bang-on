"""A missed running-status message must not leave an executing job labelled queued."""
from comfy_sdk.events import Progress,StatusChange
from keyfx import comfy,projects


def setup_render(tmp_path,monkeypatch):
    monkeypatch.setattr(projects,'DATA',tmp_path)
    monkeypatch.setattr(projects,'_progress_time',{})
    monkeypatch.setattr(projects.time,'monotonic',lambda:100.)
    d=projects.pdir('queue-test');d.mkdir(parents=True)
    projects.save({'id':'queue-test','renders':[{'id':'take','kind':'effect','status':'rendering','progress':{}}]})


def test_first_execution_event_clears_queue_without_running_status(tmp_path,monkeypatch):
    setup_render(tmp_path,monkeypatch)
    class Job:
        def events(self):
            yield StatusChange('queued',queue_position=4)
            yield Progress(.25,current_node='sampler',step=1,steps=20)
    comfy._watch_progress(Job(),lambda v:projects._progress('queue-test','take',v))
    p=projects.load('queue-test')['renders'][0]['progress']
    assert p['cloud_status']=='running' and p['step']==1
    assert 'queue_position' not in p


def test_worker_resuming_after_requeue_is_reported_running(tmp_path,monkeypatch):
    setup_render(tmp_path,monkeypatch)
    class Job:
        def events(self):
            yield Progress(.1,nodes_done=1,nodes_total=5)
            yield StatusChange('queued',queue_position=2)
            yield Progress(.2,nodes_done=2,nodes_total=5)
    comfy._watch_progress(Job(),lambda v:projects._progress('queue-test','take',v))
    p=projects.load('queue-test')['renders'][0]['progress']
    assert p['cloud_status']=='running' and p['nodes_done']==2
    assert 'queue_position' not in p


def test_running_status_clears_old_queue_position(tmp_path,monkeypatch):
    setup_render(tmp_path,monkeypatch)
    projects._progress('queue-test','take',{'cloud_status':'queued','queue_position':5})
    projects._progress('queue-test','take',{'cloud_status':'running','queue_position':None})
    p=projects.load('queue-test')['renders'][0]['progress']
    assert p['cloud_status']=='running' and 'queue_position' not in p
