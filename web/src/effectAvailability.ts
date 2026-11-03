/** Placement availability is separate from a saved shot's older generation route. */
const smoke=new Set(['smoke_white','smoke_plume','smoke_plume_hq','smoke_grenade']);
export function effectPlaceable(id:string,mode:string,elementMode:boolean):boolean{
  if(id==='explosion'||id==='fire')return true;
  if(smoke.has(id))return mode==='plate'||elementMode||id==='smoke_white'||id==='smoke_plume';
  return elementMode&&['wall_hit','dirt_hit'].includes(id);
}
export function placementNeedsElements(id:string,mode:string,elementMode:boolean):boolean{
  return mode==='plate'&&!elementMode&&(id==='fire'||smoke.has(id));
}
