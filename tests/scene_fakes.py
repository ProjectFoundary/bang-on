"""Local stand-in for two sequential Cloud video jobs; never submits work."""
import cv2

from keyfx import media


def scene_cloud(source, seen=None):
    def run(graph, uploads, out_node, out_dir, **kwargs):
        draft = 'proxy_video' in uploads
        if draft and seen is not None:
            seen.update(graph=graph, uploads=uploads)
        frames = media.read_video(source if draft else uploads['source'][1])
        cfg = graph['cond']['inputs']
        out_dir.mkdir(parents=True, exist_ok=True)
        result = out_dir / 'save_0.mp4'
        writer = media._writer(result, cfg['width'], cfg['height'], True)
        for i in range(cfg['length']):
            frame = frames[min(i, len(frames)-1)]
            writer.stdin.write(cv2.resize(frame, (cfg['width'], cfg['height'])).tobytes())
        writer.stdin.close()
        assert writer.wait() == 0
        if kwargs.get('on_status'):
            kwargs['on_status']('draft-job' if draft else 'hd-job', 'running')
            kwargs['on_status']('draft-job' if draft else 'hd-job', 'done')
        return [result]
    return run
