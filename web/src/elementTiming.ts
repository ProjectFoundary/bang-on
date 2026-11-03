import type {Effect,ElementSettings} from './api';
import {elementOnset} from './elementOptions';
import {frameTime} from './nativeTiming';

export const MIN_ELEMENT_SECONDS=.1,MAX_ELEMENT_SECONDS=5;
export type ElementTimeline={frames:number;fps?:number;timestamps?:number[];clipDuration?:number};
const epsilon=1e-9;
export function elementClipEnd(t:ElementTimeline):number{
  return t.clipDuration&&Number.isFinite(t.clipDuration)?t.clipDuration:frameTime(t.frames,t.fps,t.timestamps);
}
/** Last included source frame. The duration's end is an exclusive time bound. */
function beforeTime(time:number,t:ElementTimeline):number{
  let lo=0,hi=Math.max(1,t.frames);
  while(lo<hi){const mid=(lo+hi)>>>1;if(frameTime(mid,t.fps,t.timestamps)<time-epsilon)lo=mid+1;else hi=mid;}
  return Math.max(0,Math.min(t.frames-1,lo-1));
}
export function elementRange(effect:Effect,t:ElementTimeline,duration=effect.element?.duration_s??2){
  const startFrame=Math.max(0,Math.min(t.frames-1,elementOnset(effect))),start=frameTime(startFrame,t.fps,t.timestamps);
  const end=Math.min(elementClipEnd(t),start+duration);
  return {startFrame,start,end,endFrame:Math.max(startFrame,beforeTime(end,t)),duration,
    minEndFrame:Math.max(startFrame,beforeTime(Math.min(elementClipEnd(t),start+MIN_ELEMENT_SECONDS),t)),
    maxEndFrame:Math.max(startFrame,beforeTime(Math.min(elementClipEnd(t),start+MAX_ELEMENT_SECONDS),t))};
}
/** Drag to an inclusive source end frame, retaining onset, anchors and all look settings. */
export function durationThroughFrame(effect:Effect,frame:number,t:ElementTimeline):number{
  const range=elementRange(effect,t),last=Math.max(range.minEndFrame,Math.min(range.maxEndFrame,Math.round(frame)));
  if(last===range.minEndFrame)return MIN_ELEMENT_SECONDS;
  const end=last>=t.frames-1?elementClipEnd(t):frameTime(last+1,t.fps,t.timestamps);
  const duration=end-range.start;
  if(duration<=MIN_ELEMENT_SECONDS+epsilon)return MIN_ELEMENT_SECONDS;
  if(duration>=MAX_ELEMENT_SECONDS-epsilon)return MAX_ELEMENT_SECONDS;
  return duration;
}
export function setElementDuration(effect:Effect,duration:number){
  // Older adopted shots can have no per-element settings yet.
  const defaults:ElementSettings={duration_s:2,cadence:1,blur:2,shading:1,seed:42,opacity:1,gain:1,light:0,shadow:0,depth_offset:0,colliders:[],wind_dispersion:1};
  effect.element={...defaults,...effect.element,duration_s:Math.max(MIN_ELEMENT_SECONDS,Math.min(MAX_ELEMENT_SECONDS,duration))};
}
