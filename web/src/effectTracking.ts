import type { Anchor, Tracker } from './track';

/** Respect explicit feature/screen tracking instead of forcing every LTX element
 * onto the camera path. Screen mode still permits manually authored anchors. */
export function effectTrackingPath(anchors:Anchor[],frames:number,mode:'camera'|'features'|'screen'|undefined,tracker:Pick<Tracker,'path'>|null,cameraDefault:boolean,width:number,height:number):Float32Array {
  if(tracker&&mode!=='screen')return tracker.path(anchors,mode==='camera'||mode==null&&cameraDefault);
  const sorted=[...anchors].sort((a,b)=>a.frame-b.frame),out=new Float32Array(frames*2);
  if(!sorted.length)return out;
  for(let f=0;f<frames;f++){
    const right=sorted.findIndex(a=>a.frame>f);
    const a=right<=0?sorted[right===0?0:sorted.length-1]:sorted[right-1];
    const b=right>0?sorted[right]:a;
    const u=a===b?0:(f-a.frame)/Math.max(1,b.frame-a.frame),blend=u*u*(3-2*u);
    out[f*2]=(a.x+(b.x-a.x)*blend)*width;
    out[f*2+1]=(a.y+(b.y-a.y)*blend)*height;
  }
  return out;
}
