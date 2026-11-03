/* Exact solution of dv/dt = gravity - drag*v. The old gravity-then-damping Euler step
   changed terminal speed with frame rate and over-damped falling grit. Coordinates may
   be screen (positive Y down) or world (positive Y up); callers supply signed gravity. */
export function integrateParticle(p, dt, drag, gravity, spatial3D=false){
  const k=Math.exp(-drag*dt), q=drag>1e-6 ? -Math.expm1(-drag*dt)/drag : dt;
  const accelTime=drag>1e-6 ? (dt-q)/drag : dt*dt*0.5;
  p.x+=p.vx*q; p.y+=p.vy*q+gravity*accelTime;
  p.vx*=k; p.vy=p.vy*k+gravity*q;
  if (spatial3D){ p.z+=p.vz*q; p.vz*=k; }
  p.age+=dt;
}
// A finite burst is one cohort. Density added while the source expands must
// inherit the cohort's current position and speed instead of restarting it.
export function burstFall(D,t,gravity=1){
  if(!D.bulkFall||D.bulkFall.follow===false)return null;
  const initial=-D.bulkFall.up*gravity,terminal=D.bulkFall.gravity*.5*gravity;
  const decay=Math.exp(-2*Math.max(0,t));
  return {speed:terminal+(initial-terminal)*decay,terminal,
    offset:terminal*t+(initial-terminal)*(1-decay)*.5};
}
