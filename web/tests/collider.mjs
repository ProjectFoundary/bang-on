// Real WebGL regression checks. Uses esbuild from web/ and Playwright from KEYFX_NODE_PACKAGE.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const local = createRequire(new URL('../package.json', import.meta.url));
const runtime = process.env.KEYFX_NODE_PACKAGE ? createRequire(process.env.KEYFX_NODE_PACKAGE) : local;
const { chromium } = runtime('playwright'), { build } = local('esbuild');
const bundle = await build({ stdin: { contents: `
  import * as collider from './collider';
  import * as fx from './squibfx';
  import { renderLayers } from './mockup';
  import { Tracker } from './track';
  import { Sim } from './squib/sim/Sim.js';
  import { app, state } from './squib/state.js';
  import { gl } from './squib/gl/context.js';
  window.KFX = { ...collider, ...fx, Sim, app, state, gl, renderLayers, Tracker };
`, resolveDir: fileURLToPath(new URL('../src/', import.meta.url)), loader: 'ts' }, bundle: true, write: false, format: 'iife' });
const browser = await chromium.launch({ executablePath: process.env.KEYFX_CHROME || undefined,
  headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const report = {};
try {
  const page = await browser.newPage(), errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  let maskPNG, failMask = true, requests = 0;
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><body></body>' });
    if (url.pathname.includes('/people/frame/')) {
      requests++;
      assert.equal(url.searchParams.get('matte'), 'tracked');
      assert.equal(url.searchParams.get('trim'), '0');
      return route.fulfill(failMask && url.pathname.endsWith('/1') ? { status: 404, body: '' }
        : { contentType: 'image/png', body: maskPNG });
    }
    return route.fulfill({ status: 404, body: '' });
  });
  await page.goto('http://kfx.local/');
  await page.addScriptTag({ content: bundle.outputFiles[0].text });

  report.roomTracking=await page.evaluate(()=>{
    const tracker=new window.KFX.Tracker({n:3,w:960,h:540,q:1,camera:[[1,0,0,0],[1,0,10,-5],[1,0,20,-10]],
      tracks:[[0,480,270,500,270,520,270],[0,490,270,510,270,530,270],[0,470,280,490,280,510,280]]});
    const anchor=[{frame:0,x:.5,y:.5}],features=tracker.path(anchor),room=tracker.path(anchor,true);
    return {features:Array.from(features),room:Array.from(room)};
  });
  assert.deepEqual(report.roomTracking.room,[480,270,490,265,500,260]);
  assert.deepEqual(report.roomTracking.features,[480,270,500,270,520,270]);
  console.log('Room tracking ignores moving local features and follows camera motion only.');

  report.flow = await page.evaluate(() => {
    const { Sim, state, gl } = window.KFX, saved = { ...state.scene };
    const w = 64, h = 36, mask = new Uint8Array(w * h);
    for (let y = 9; y < 27; y++) for (let x = 30; x < 34; x++) mask[y*w+x] = 255;
    Object.assign(state.plate, { w, h, first: 0, last: 8 }); state.instances = [];
    // Keep the solver identical in A/B; an empty collider must match no collider.
    Object.assign(state.scene, { multigrid: false, mc: false, rk2: false, buoyancy: 0, cooling: 0, fineTurb: 0, drag: 0, wind: 0, projectAfter: true, matteUse: 'collide' });
    const run = obstacle => {
      const s = new Sim(w, h); s.reset();
      state.matte = obstacle ? { gw: w, gh: h, data: new Map([[0, obstacle]]), frames: [0], invert: false } : null;
      s.uploadObstacle(0);
      const velocity = new Float32Array(w*h*4), dye = new Float32Array(w*h*4);
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        const i = (y*w+x)*4, d = Math.exp(-((x-24)**2+(y-18)**2)/40);
        velocity[i] = 8;
        dye.set([d*.6, d*.7, d*.8, d], i);
      }
      for (const [field, values] of [[s.vel, velocity], [s.dye, dye]]) {
        gl.bindTexture(gl.TEXTURE_2D, field.read.tex); gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, w, h, gl.RGBA, gl.FLOAT, values);
      }
      for (let step = 0; step < 8; step++) s.fluid(1/24, [], step/24, step);
      const read = field => { const out = new Float32Array(w*h*4); gl.bindFramebuffer(gl.FRAMEBUFFER, field.read.fbo); gl.readPixels(0,0,w,h,gl.RGBA,gl.FLOAT,out); return out; };
      const out = { vel: read(s.vel), dye: read(s.dye) }; s.destroy(); return out;
    };
    const off = run(null), empty = run(new Uint8Array(w*h)), on = run(mask);
    let outsideDelta = 0, insideVelocity = 0, insideDye = 0, emptyDelta = 0;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = (y*w+x)*4, solid = mask[(h-1-y)*w+x] > 127;
      emptyDelta = Math.max(emptyDelta, Math.abs(off.vel[i]-empty.vel[i]), Math.abs(off.dye[i+3]-empty.dye[i+3]));
      if (solid) { insideVelocity = Math.max(insideVelocity, Math.abs(on.vel[i]), Math.abs(on.vel[i+1])); insideDye = Math.max(insideDye, on.dye[i+3]); }
      else outsideDelta += Math.abs(off.vel[i]-on.vel[i]) + Math.abs(off.vel[i+1]-on.vel[i+1]);
    }
    state.matte = null; Object.assign(state.scene, saved);
    const glError = gl.getError();
    return { outsideDelta, insideVelocity, insideDye, emptyDelta, glError };
  });
  assert.equal(report.flow.glError, 0);
  assert.ok(report.flow.outsideDelta > 1, JSON.stringify(report.flow));
  assert.equal(report.flow.insideVelocity, 0);
  assert.equal(report.flow.insideDye, 0);
  assert.equal(report.flow.emptyDelta, 0);
  console.log('Fluid obstacle changes flow outside silhouette; empty mask matches baseline.');

  report.motion=await page.evaluate(()=>{
    const {Sim,state,gl,colliderForSim}=window.KFX,saved={...state.scene},w=64,h=36,n=8;
    const masks=(vertical=false)=>Array.from({length:n},(_,f)=>{
      const m=new Uint8Array(w*h);
      for(let y=12+(vertical?f:0);y<24+(vertical?f:0);y++)for(let x=12+(vertical?0:f);x<18+(vertical?0:f);x++)m[y*w+x]=255;
      return m;
    });
    const sequence={key:'moving-wall',width:w,height:h,data:masks()};
    const moving=colliderForSim(sequence,w,h,undefined,0),motion=moving.motion(1,24);
    const vertical=colliderForSim({...sequence,data:masks(true)},w,h,undefined,0).motion(1,24);
    const cameraPath=new Float32Array(n*2);for(let f=0;f<n;f++)cameraPath[f*2]=f*960/w;
    const cameraCancelled=colliderForSim(sequence,w,h,cameraPath,0).motion(1,24).reduce((m,v)=>Math.max(m,Math.abs(v)),0);
    Object.assign(state.plate,{w,h,first:0,last:n-1});state.instances=[];
    Object.assign(state.scene,{multigrid:false,mc:false,rk2:false,buoyancy:0,cooling:0,fineTurb:0,drag:0,wind:0,projectAfter:true,matteUse:'collide'});
    const run=matte=>{
      const s=new Sim(w,h);s.reset();state.matte=matte;
      const dye=new Float32Array(w*h*4);
      for(let y=0;y<h;y++)for(let x=0;x<w;x++){const a=Math.exp(-((x-24)**2+(y-18)**2)/12);dye.set([a,a,a,a],(y*w+x)*4);}
      gl.bindTexture(gl.TEXTURE_2D,s.dye.read.tex);gl.texSubImage2D(gl.TEXTURE_2D,0,0,0,w,h,gl.RGBA,gl.FLOAT,dye);
      for(let f=1;f<7;f++){s.uploadObstacle(f);s.fluid(1/24,[],f/24,f);}
      const read=field=>{const a=new Float32Array(w*h*4);gl.bindFramebuffer(gl.FRAMEBUFFER,field.read.fbo);gl.readPixels(0,0,w,h,gl.RGBA,gl.FLOAT,a);return a;};
      const d=read(s.dye),v=read(s.vel);let mass=0,moment=0,frontVelocity=0;
      for(let y=0;y<h;y++)for(let x=0;x<w;x++){const i=(y*w+x)*4;mass+=d[i+3];moment+=x*d[i+3];if(x>=24&&x<32&&y>=14&&y<22)frontVelocity+=v[i];}
      s.destroy();return {center:moment/mass,mass,frontVelocity:frontVelocity/64};
    };
    const stationary=colliderForSim({...sequence,key:'static',data:sequence.data.map(()=>sequence.data[0])},w,h,undefined,0);
    const still=run(stationary),pushed=run(moving);
    Object.assign(state.scene,saved);state.matte=null;
    return {wallX:motion[(18*w+18)*2],wallDownY:vertical[(24*w+15)*2+1],cameraCancelled,still,pushed,centerShift:pushed.center-still.center,glError:gl.getError()};
  });
  assert.equal(report.motion.glError,0);
  assert.equal(report.motion.wallX,24);
  assert.equal(report.motion.wallDownY,-24);
  assert.equal(report.motion.cameraCancelled,0);
  assert.ok(report.motion.centerShift>.2,JSON.stringify(report.motion));
  assert.ok(report.motion.pushed.frontVelocity>.1);
  assert.ok(report.motion.pushed.mass>1);
  console.log('Moving wall pushes initially still dye forward; camera motion cancels out of the collider velocity.');

  report.softMotion=await page.evaluate(()=>{
    const {Sim,state,gl,colliderForSim,softCollider}=window.KFX,saved={...state.scene},w=64,h=36,n=8;
    const masks=Array.from({length:n},(_,f)=>{
      const m=new Uint8Array(w*h);
      for(let y=12;y<24;y++)for(let x=12+f;x<18+f;x++)m[y*w+x]=255;
      return m;
    });
    const source={key:'soft-test',width:w,height:h,data:masks},settings={sigma:22.5,drag:24};
    const soft=softCollider(source,settings),moving=colliderForSim(soft,w,h,undefined,0);
    const stationary=colliderForSim(softCollider({...source,key:'soft-static',data:masks.map(()=>masks[0])},settings),w,h,undefined,0);
    const path=new Float32Array(n*2);for(let f=0;f<n;f++)path[f*2]=f*960/w;
    const cancelled=colliderForSim(soft,w,h,path,0).motion(1,24).reduce((m,v)=>Math.max(m,Math.abs(v)),0);
    Object.assign(state.plate,{w,h,first:0,last:n-1});state.instances=[];
    Object.assign(state.scene,{multigrid:false,mc:false,rk2:false,buoyancy:0,cooling:0,fineTurb:0,drag:0,wind:0,projectAfter:true,matteUse:'collide'});
    const run=matte=>{
      const s=new Sim(w,h);s.reset();state.matte=matte;
      const dye=new Float32Array(w*h*4);
      for(let y=0;y<h;y++)for(let x=0;x<w;x++){const a=Math.exp(-((x-24)**2+(y-18)**2)/40);dye.set([a,a,a,a],(y*w+x)*4);}
      gl.bindTexture(gl.TEXTURE_2D,s.dye.read.tex);gl.texSubImage2D(gl.TEXTURE_2D,0,0,0,w,h,gl.RGBA,gl.FLOAT,dye);
      for(let f=1;f<7;f++){s.uploadObstacle(f);s.fluid(1/24,[],f/24,f);}
      const read=field=>{const a=new Float32Array(w*h*4);gl.bindFramebuffer(gl.FRAMEBUFFER,field.read.fbo);gl.readPixels(0,0,w,h,gl.RGBA,gl.FLOAT,a);return a;};
      const d=read(s.dye),v=read(s.vel);let mass=0,moment=0,speed=0;
      for(let y=0;y<h;y++)for(let x=0;x<w;x++){const i=(y*w+x)*4;mass+=d[i+3];moment+=x*d[i+3];speed=Math.max(speed,Math.hypot(v[i],v[i+1]));}
      const insideDye=d[(18*w+15)*4+3];s.destroy();return {mass,center:moment/mass,speed,insideDye};
    };
    const still=run(stationary),pushed=run(moving),encoded=moving.data.get(1);
    const maxEncoded=encoded.reduce((m,v)=>Math.max(m,v),0),partial=encoded.filter(v=>v>0&&v<127).length;
    state.matte=null;Object.assign(state.scene,saved);
    return {still,pushed,centerShift:pushed.center-still.center,maxEncoded,partial,cancelled,
      distinctKey:soft.key!==source.key,sourceUnchanged:source.data[0][18*w+15]===255,glError:gl.getError()};
  });
  assert.equal(report.softMotion.glError,0);
  assert.ok(report.softMotion.maxEncoded<=127&&report.softMotion.partial>0);
  assert.equal(report.softMotion.cancelled,0);
  assert.equal(report.softMotion.distinctKey,true);
  assert.equal(report.softMotion.sourceUnchanged,true);
  assert.ok(report.softMotion.still.insideDye>.01,'Soft collider must not cut a hard empty body silhouette');
  assert.ok(report.softMotion.centerShift>.1,JSON.stringify(report.softMotion));
  assert.ok(report.softMotion.pushed.speed>1);
  console.log('Soft blurred body influence retains dye, drags it forward, and cancels camera-only motion.');

  report.cache = await page.evaluate(() => {
    const { squibLayer, setSquibCollider, setSquibQuality, colliderForSim, softCollider, app, gl } = window.KFX;
    const n = 10, width = 512, height = 288, data = [];
    const path = new Float32Array(n*2);
    for (let f = 0; f < n; f++) {
      const m = new Uint8Array(width*height);
      for (let y = 55; y < 230; y++) for (let x = 230+f; x < 275+f; x++) m[y*width+x] = 255;
      data.push(m); path[f*2] = 480 + f*6; path[f*2+1] = 450 + f*3;
    }
    const sequence = { key: 'moving-a', width, height, data };
    const mapped = colliderForSim(sequence, width, height, path, 0);
    const padded = colliderForSim(sequence,640,352,path,0,{x:-120,y:-60,width:1200,height:660}).data.get(4);
    // The tracked draw is +6,+3 logical pixels/frame. Sampling must undo that translation.
    const pixel = mapped.data.get(4);
    const alignment = pixel[60*width+222] === 255 && pixel[60*width+280] === 0 && pixel[250*width+240] === 0
      && padded[132*640+307]===255 && padded[132*640+5]===0;
    const opts = { w: 320, h: 180, n, x: 160, y: 150, start: -8, end: n-1, size: .65, seed: 42, wind: 0, path };
    setSquibQuality('draft', 'collider-test');
    const read = () => { const s = app.sim, out = new Float32Array(s.gw*s.gh*4); gl.bindFramebuffer(gl.FRAMEBUFFER, s.dye.read.fbo); gl.readPixels(0,0,s.gw,s.gh,gl.RGBA,gl.FLOAT,out); return out; };
    const delta = (a,b) => a.reduce((m,v,i) => Math.max(m,Math.abs(v-b[i])),0);
    const render = (mask, f) => { setSquibCollider(mask); squibLayer('same-effect', 'kfx_white', opts, f); return read(); };
    const baseline = render(null, 0), first = render(sequence, 0);
    render(sequence, 4);
    const back = render(sequence, 0), restoredMask = app.sim.obsKey;
    const movingFirst=render(sequence,2);render(sequence,6);const movingBack=render(sequence,2);
    const soft=softCollider(sequence,{sigma:22.5,drag:24});
    const softFirst=render(soft,2);render(soft,6);const softBack=render(soft,2);
    const disabled = render(null, 0), disabledMask = app.sim.obsKey;
    const changed = { ...sequence, key: 'moving-b', data: data.map(() => new Uint8Array(width*height)) };
    render(changed, 0);
    const fbo = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, app.sim.obs, 0);
    const obstacle = new Uint8Array(app.sim.gw*app.sim.gh*4);
    gl.readPixels(0,0,app.sim.gw,app.sim.gh,gl.RGBA,gl.UNSIGNED_BYTE,obstacle); gl.deleteFramebuffer(fbo);
    let obstacleMax = 0; for (let i = 0; i < obstacle.length; i += 4) obstacleMax = Math.max(obstacleMax, obstacle[i]);
    return { alignment, scrubDelta: delta(first,back), movingScrubDelta:delta(movingFirst,movingBack), softScrubDelta:delta(softFirst,softBack), disabledDelta: delta(baseline,disabled), colliderDelta: delta(baseline,first), restoredMask, disabledMask, obstacleMax, glError: gl.getError() };
  });
  assert.equal(report.cache.alignment, true);
  assert.equal(report.cache.glError, 0);
  assert.equal(report.cache.scrubDelta, 0);
  assert.equal(report.cache.movingScrubDelta,0);
  assert.equal(report.cache.softScrubDelta,0);
  assert.equal(report.cache.disabledDelta, 0);
  assert.ok(report.cache.colliderDelta > .01);
  assert.equal(report.cache.restoredMask, '0');
  assert.equal(report.cache.disabledMask, 'none');
  assert.equal(report.cache.obstacleMax, 0);
  console.log('Tracking, backwards scrubbing, mode toggle and same-frame matte replacement passed.');

  report.native = await page.evaluate(() => {
    const { renderLayers, app, state } = window.KFX, width=512, height=288, n=5;
    const data = Array.from({length:n}, () => { const m=new Uint8Array(width*height); for(let y=55;y<230;y++)m.fill(255,y*width+230,y*width+275); return m; });
    const sequence={key:'native-guide',width,height,data}, path=new Float32Array(n*2);
    for(let f=0;f<n;f++){path[f*2]=480;path[f*2+1]=450;}
    const effect={id:'native-smoke',preset:'smoke_white',size:.5,strength:0,keys:[{phase:'start',frame:-2}]};
    const preset={id:'smoke_white',phases:[{phase:'start',stamp:'puff.png',persist:true}]};
    const fx=document.createElement('canvas'),glow=document.createElement('canvas');fx.width=glow.width=1920;fx.height=glow.height=1080;
    renderLayers(fx,glow,[effect],{smoke_white:preset},0,n,new Map([[effect.id,path]]),()=>{},'high','native-test',sequence);
    const sheets=app.sim.sheets.map(s=>({grid:[s.gw,s.gh],mask:s.obsKey}));
    const alpha=fx.getContext('2d').getImageData(0,0,fx.width,fx.height).data;
    let occupied=0;for(let i=3;i<alpha.length;i+=4)if(alpha[i])occupied++;
    const connected=state.matte!==null;
    renderLayers(fx,glow,[effect],{smoke_white:preset},0,n,new Map([[effect.id,path]]),()=>{},'high','native-test');
    const defaultOff=state.matte===null, physicalScale=state.instances[0].scale*state.plate.h;
    for(let f=0;f<n;f++){path[f*2]=480+40*f;path[f*2+1]=450-20*f;}
    renderLayers(fx,glow,[effect],{smoke_white:preset},4,n,new Map([[effect.id,path]]),()=>{},'high','native-test',sequence);
    const moving={canvas:[state.plate.w,state.plate.h],sheets:app.sim.sheets.map(s=>({grid:[s.gw,s.gh],mask:s.obsKey})),
      aligned:state.matte.data.get(4)[149*app.sim.gw+256]>127,scaleDelta:Math.abs(physicalScale-state.instances[0].scale*state.plate.h)};
    return {dimensions:[fx.width,fx.height],sheets,occupied,connected,defaultOff,moving};
  });
  assert.deepEqual(report.native.dimensions,[1920,1080]);
  assert.equal(report.native.sheets.length,3);
  assert.ok(report.native.sheets.every(s=>s.mask==='0'&&s.grid[0]===512&&s.grid[1]===288));
  assert.ok(report.native.occupied>100);
  assert.equal(report.native.connected,true);
  assert.equal(report.native.defaultOff,true);
  assert.deepEqual(report.native.moving.canvas,[2240,1240]);
  assert.ok(report.native.moving.sheets.every(s=>s.mask==='4'&&s.grid[0]===597&&s.grid[1]===330));
  assert.equal(report.native.moving.aligned,true);
  assert.ok(report.native.moving.scaleDelta<1e-5);
  console.log('Native 1080p guide capture connects the collider to all three high-quality sheets.');

  maskPNG = Buffer.from(await page.evaluate(() => { const c = document.createElement('canvas'); c.width = 512; c.height = 288; const g=c.getContext('2d'); g.fillStyle='white'; g.fillRect(200,50,100,200); return c.toDataURL('image/png').split(',')[1]; }), 'base64');
  await page.evaluate(async () => {
    const p = { id: 'loader', frames: 3, settings: { collider: true }, people: { selection: 'manual', feather: 0, trim: 0 }, people_matte: { id: 'tracked', current: true } };
    window.loaderProject = p;
    try { await window.KFX.loadCollider(p); throw new Error('Partial matte unexpectedly accepted'); }
    catch (e) { if (!String(e).includes('frame 1')) throw e; }
  });
  failMask = false;
  report.loader = await page.evaluate(async () => {
    const seq = await window.KFX.loadCollider(window.loaderProject);
    const again = await window.KFX.loadCollider(window.loaderProject);
    window.loaderProject.settings.collider = false;
    return { frames: seq.data.length, shared: seq === again, off: await window.KFX.loadCollider(window.loaderProject) };
  });
  assert.equal(requests, 6);
  assert.deepEqual(report.loader, { frames: 3, shared: true, off: null });
  assert.deepEqual(errors, []);
  console.log('Incomplete mask load rejected; retry succeeds; mode off makes no mask requests.');
  if (process.env.KEYFX_COLLIDER_REPORT) writeFileSync(process.env.KEYFX_COLLIDER_REPORT, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report));
} finally { await browser.close(); }
