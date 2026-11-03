"""Cheap local review hints; never alter or approve generated media."""
import json
from pathlib import Path

import cv2
import numpy as np


def _green_fraction(im: np.ndarray) -> float:
    h,w=im.shape[:2]
    if w>384:im=cv2.resize(im,(384,max(1,round(h*384/w))),interpolation=cv2.INTER_AREA)
    hsv=cv2.cvtColor(im,cv2.COLOR_BGR2HSV)
    return float(((hsv[...,0]>35)&(hsv[...,0]<90)&(hsv[...,1]>90)&(hsv[...,2]>50)).mean())


def review_scene(video: Path, opening: Path, out: Path) -> dict:
    """Flag large green areas absent from a clean opening; not a semantic matte.

    Natural green scenes are skipped when the opening already contains much
    green. A warning asks for review, since later reveals can legitimately be
    green too. This analysis-only thumbnail never enters generation/export.
    """
    image=cv2.imread(str(opening))
    if image is None:raise ValueError('The opening reference is missing for scene review.')
    baseline=_green_fraction(image);suspect=[];maximum=0.;count=0
    cap=cv2.VideoCapture(str(video));fps=cap.get(cv2.CAP_PROP_FPS) or 24
    try:
        while True:
            ok,frame=cap.read()
            if not ok:break
            green=_green_fraction(frame);maximum=max(maximum,green)
            if baseline<.08 and green>max(.10,baseline+.08):suspect.append(count)
            count+=1
    finally:cap.release()
    if not count:raise ValueError('The generated video could not be read for scene review.')
    warnings=[]
    if len(suspect)>=2:
        warnings.append(f'Possible source green screen: large green areas absent from the opening appear in '
                        f'{len(suspect)} of {count} frames, starting at frame {suspect[0]} ({suspect[0]/fps:.2f}s). '
                        'Check the ground and frame edges before approving.')
    data={'check':'unexpected-green-v1','reference_green_fraction':baseline,'max_green_fraction':maximum,
          'frames_checked':count,'suspect_frames':suspect,'warnings':warnings,
          'limitation':'Colour-based review hint; not a guarantee of background replacement or absence of visual effects.'}
    out.write_text(json.dumps(data,indent=2))
    return data
