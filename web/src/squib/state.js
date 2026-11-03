
/* ---------- app state ---------- */
export const state = {
  plate:{kind:'sample', w:1920, h:1080, first:1001, last:1096, files:null, frames:null, name:'sample'},
  fps:24, frame:1001, playing:false, loop:true,
  instances:[], nextId:1, selectedId:null, armed:null,
  scene:{smokeMode:'live',depth2d:3,mc:true,rk2:true,multigrid:true,projectAfter:true,quality:768,buoyancy:1,cooling:.4,wind:0,drag:.5,fineTurb:1,density:2.2,lightAngle:40,lightElev:20,lightCol:'#fff4e8',ambCol:'#d6e0ea',shadow:1,ambient:.4,glow:1,detail2d:.10,shape2d:.55,opacitySoft2d:.60,matteUse:'collide'},
  view:{layer:0, plate:true, handles:true, collider:false},
  matte:null,
  cam:null,   // tracked 3D camera (src/io/camera.js), with the 3D sim settings
};


/* mutable runtime handles shared across modules */
export const app = { dirty:true, sim:null, PT:null, exporting:false, cancelExport:false, dpr:1, plateTexKey:null, matteTexKey:null, sampleCanvas:null, sim3d:null };
