
/* ---------- presets (sizes are fractions of plate height, speeds plate-heights/sec) ---------- */
export const PRESETS = {
  // Wall hit: a compact camera-facing burst, ballistic chips, a falling grit curtain, then
  // suspended mineral fines with a slower, independent fade. Matched to Wall_Hit_Front_1_0282.
  // detail3d: scales the 3D render detail; its erosion carves a thin, long-lived dust haze into clumps
  concrete:{label:'Wall hit', group:'Impacts', kind:'impact', swatch:'#ead0b5', breakup2d:0.8, detail3d:1.1, detailErode3d:0.0005, breakup3d:1.0, detailEdge3d:0.8, detailShade3d:0.20,
    detailCoreErode3d:.04,detailCoreScale3d:2.5,detailCoreShade3d:.6,detailCoreWarp3d:.8,
    defaults:{dissipate:2.7,linger:0.2,turb:0.20,curl:1.2,angle:-10,color:'#ead0b5',floor:false,flash:false,sparksMul:0,facing:.85},
    // dust n: splats per substep, grow: s for the puff to swell to full size; fines: the dust that hangs, spreads and
    // settles for seconds (n parcels, dur s, drifting spread H/s over tau s, falling up to ~2x fall H/s, fading over fade s)
    dust:{clusters2d:6,puffRate:20,puffLinger:.1,bulkFall:{up:.15,gravity:.8},drift:-.04,lift:.015,lift3d:.015,initialSettle:.35,initialSettle3d:.35,settleTime:.45,settle:.02,radius2d:.70,amount2d:2.4,radius:.064,speed:1.4,spread:90,tau:.19,dur:10,temp:0,amount:.50,n:12,grow:.035,
      fines:{analytic:true,distribution:true,cohortWidth:.55,fallKick3d:.4,settle:.025,drift:-.05,n:288,dur:6.5,src:.025,spread:.064,tau:1.3,fall:.09,wander:.030,r:.018,amount:21.333333,fade:2.5,rate:.75,linger:.5}},
    grains:{count:2200,size:.00065,life:1.25,gravity:4.2,drag:10,alpha:.18},
    fineGrains:{count:14000,size:.00042,alpha:.43,fade:1.5,shutter:.2},
    debris:{count:28,shutter:.2,speed:1.6,spread:150,size:.0045,frontDepth:.04,color:'#8b7d6b',gravity:4,drag:2,life:1.0,trail:0,bounce:.25},
    // grit: many small grains raining out of the puff (src: half-size of the source, H). gravity/drag set the terminal fall speed,
    // spreadK spreads it (finer grains fall slower). trail: dust laid along the path (radius trailR), ramping in over delay s and
    // shed over shed s, dissipating at rate x the effect's Dissipate. show: fraction drawn as specks (the rest only carry dust)
    grit:{count:180, speed:.45, spread:90, size:.0009, gravity:5, drag:6, life:1.25, trail:1, delay:.06, shed:.45, spreadK:2.5, rate:3.5, linger:0, shedUntil:.65, shedReach:.30,settle:.30,terminalSettle:.05, trailR:.0065, src:.034, show:.3, alpha:.7},
    sparks:{count:14,speed:2.1,spread:85,life:.18,color:'#ffb060',intensity:7,width:.0011,gravity:.8,drag:1.2},
    flash:{size:.035,intensity:5,color:'#ffd9a8',frames:2}},
  // Dirt hit (matched against a practical dust hit): a dense, light-grey burst along Direction that keeps rolling out
  // and hangs for seconds; a few heavy clods that land close by.
  dirt:{label:'Dirt hit', group:'Impacts', kind:'impact', swatch:'#b8b2a8',
    defaults:{dissipate:0.5,linger:1.5,turb:0.7,curl:10,angle:0,color:'#b8b2a8',floor:true,flash:false},
    dust:{settle:.025,radius:.018,speed:1.1,spread:25,tau:.25,dur:14,temp:0,amount:.6,reach:.2,push:.4,
      fines:{n:30,dur:6,src:.04,spread:.05,tau:2,fall:.01,wander:.02,r:.04,amount:2,fade:3}},
    debris:{count:8,speed:.8,spread:42,size:.0026,color:'#3f3325',gravity:3,drag:2,life:1,trail:.15,bounce:.2},
    sparks:null, flash:null},
  metal:{label:'Metal sparks', group:'Impacts', kind:'impact', swatch:'#ffb060',
    defaults:{dissipate:1.2,linger:0.2,turb:0.3,curl:12,angle:0,color:'#6f6e6a',floor:true,flash:true},
    dust:{settle:.008,radius:.01,speed:.5,spread:90,tau:.08,dur:3,temp:.2,amount:.45},
    debris:{count:6,speed:.9,spread:80,size:.0018,color:'#3a3a3a',gravity:1.5,drag:.8,life:1.2,trail:0,bounce:.3},
    sparks:{count:90,speed:1.8,spread:100,life:.42,color:'#ffc070',intensity:9,width:.0013,gravity:1.7,drag:1.4},
    flash:{size:.05,intensity:8,color:'#fff0d0',frames:2}},
  // Fireball (fire), no dust. Debris, sparks and the flash are there but off by default, like the reference element.
  explosion:{label:'Explosion', group:'Impacts', kind:'impact', swatch:'#ff8a3a',
    defaults:{dissipate:1.6,linger:1.5,turb:0.25,curl:40,angle:0,color:'#3a3531',floor:false,flash:false,scale:1,debrisMul:0,sparksMul:0},
    fire:{radius:.12,grow:.12,dur:.35,after:.8,swell:.4,tau:.15,speed:.9,aim:.55,temp:1,heat:3,aheat:2,cool:.4,amount:1.5},
    dust:null,
    debris:{count:120,speed:1.8,spread:115,size:.004,color:'#2e2a26',gravity:1.5,drag:.5,life:2.5,trail:.6,bounce:.2},
    sparks:{count:70,speed:2.2,spread:150,life:.6,color:'#ffa040',intensity:8,width:.0016,gravity:.7,drag:.9},
    flash:{size:.18,intensity:10,color:'#ffc27a',frames:3}},
  // smoke: push = how hard the source jet sets velocity. draft = the warm channel that keeps a plume rising (emitSmoke):
  // temp/k its heat and blend rate, rise (H/s) and height (H, x sqrt(scale)), base (source radii) and cone (width per H),
  // wander/grow/lambda/f the slow meander, tail (s) the cool-off after the source stops, boxW the 3D box half-width (H)
  plume:{label:'Plume smoke', group:'Smoke', kind:'smoke', swatch:'#969696', breakup3d:0.8,detail3d:.85, detailShade3d:.25, detailEdge3d:1,detailErode3d:.002,
    defaults:{dissipate:0.28,linger:0.7,turb:0.32,curl:8.0,angle:0,color:'#969696',duration:120},
    smoke:{sheet:{amount:.63,initialAmount:6,dissipate:.65,fragment:.55,turbBase:.70,turbGrowth:3.0,entrain:.04,entrainStart:5,entrainTime:8,sourceTempCurve:[[0,.6],[6,.6],[12,.35],[18,.25]],draftKCurve:[[0,.008],[6,.008],[16,.003]],curl:1.5},radius:.038,speed:.14,spread:15,push:.12,temp:.10,amount:.065,ramp:8,
      sourceGrowth:4.5,growthPower:3,sourceWidthGrowth:.25,growthTime:18,
      volume:{cachedDetail:true,opticalMacro:3,densityScale:1.4,refineTurb:.18,flowPeriod:1,projectAfter:true,draftK:0,sourceTempCurve:[[0,.35],[2,.35],[4,1.4]],radius:1.0,amount:1.2,puff:.45,initialAmount:.5,sourceGrowth:1.3,growthPower:2,dissipate:.72,entrain:0,turb:1.4,turbGrowth:2.5,curl:0.94},
      draft:{temp:.06,heatGrowth:0,k:.03,rise:.085,height:.85,lead:2,n:5,base:2.5,cone:.30,wander:2.2,grow:16,lambda:.35,f:.05,tail:3,boxW:.38}}},
  column:{label:'Black smoke column', group:'Smoke', kind:'smoke', swatch:'#3a3633',
    defaults:{dissipate:0.18,linger:1.5,turb:0.25,curl:3.5,angle:0,color:'#4d4a47',duration:200,scale:1.4},
    smoke:{radius:.045,speed:.15,spread:20,push:.1,temp:.15,amount:.14,ramp:12,fire:{heat:2.1},
      draft:{temp:.12,k:.03,rise:.1,height:.83,lead:2,n:5,base:2.5,cone:.2,wander:1,grow:20,lambda:.4,f:.04,tail:4,boxW:.32}}},
  // Ground fire (matched against a practical ground-fire element): flame sites along a line (Fire line length), each
  // flickering on its own. The hot dye glows and clears quickly once it cools, so it reads as flame rather than smoke.
  groundfire:{label:'Ground fire', group:'Fire', kind:'smoke', swatch:'#ffa040',
    defaults:{dissipate:5,linger:0.3,turb:0.6,curl:20,angle:0,color:'#2a2522',duration:240,width:.32},
    // curl3d: vorticity confinement churns thin 3D flame jets in place, so 3D gets much less of it than the 2D licks
    smoke:{radius:.018,speed:1,spread:0,push:.7,temp:1.6,amount:1.1,ramp:6,line:true,sites:10,curl3d:.15,fire:{heat:2.4}}},
  // Snow kinds are analytic (a pure function of frame), rendered straight into the snow layer, never simulated.
  snowfall:{label:'Blowing snow', group:'Snow', kind:'snow', swatch:'#e8eef4',
    defaults:{angle:100,color:'#f4f7fa',curl:20,full:true,area:.8,depth:.7,gust:.6},
    // matched against real snowfall (Snow 003): ~250 flakes in a 2K frame, mostly small bright dots drifting ~0.1 H/s,
    // the odd big defocused one near camera. Faster wind (Wind speed) turns them into streaks.
    flakes:{count:520, size:.0015, speed:.13, fall:.09, ramp:8}},
  spindrift:{label:'Ridge spindrift', group:'Snow', kind:'drift', swatch:'#cfd9e4',
    defaults:{angle:80,color:'#f4f7fa',curl:25,ridge:20,width:.35,length:.9,lift:.15,haze:.35},
    drift:{speed:.15, thick:.018, spread:.12, amount:1.6, grains:36000, grainSize:.0009, grainAlpha:1.6}},
};

/* preset keys renamed since older setups were saved */
export const LEGACY_PRESETS = {grenade:'plume'};

/* kinds that run through the fluid sim (the rest are render-only) */
export const SIMULATED = k => k==='impact' || k==='smoke';
/* kinds with a start frame + duration rather than a single impact frame */
export const HAS_DURATION = k => k!=='impact';

export const FIELDS = {
  impact:[
    {k:'frame',label:'Impact frame',type:'int'},
    {k:'pos',label:'Position px',type:'pos'},
    {k:'scale',label:'Scale',type:'range',min:.2,max:4,step:.01},
    {k:'angle',label:'Direction',type:'range',min:-180,max:180,step:1,unit:'°'},
    {k:'facing',label:'Facing camera',type:'range',min:0,max:1,step:.01},
    {k:'intensity',label:'Dust',type:'range',min:0,max:3,step:.01},
    {k:'gravityMul',label:'Gravity',type:'range',min:0,max:2,step:.01},
    {k:'debrisMul',label:'Debris',type:'range',min:0,max:3,step:.05},
    {k:'fineGrain',label:'Fine grains',type:'range',min:0,max:2,step:.05,render:true,showIf:i=>!!PRESETS[i.preset].fineGrains},
    {k:'sparksMul',label:'Sparks',type:'range',min:0,max:3,step:.05},
    {k:'heat',label:'Heat',type:'range',min:0,max:2,step:.01},
    {k:'curl',label:'Curl detail',type:'range',min:0,max:60,step:.5},
    {k:'turb',label:'Turbulence',type:'range',min:0,max:3,step:.01},
    {k:'linger',label:'Linger',type:'range',min:0,max:10,step:.1,unit:'s'},
    {k:'dissipate',label:'Dissipate',type:'range',min:0,max:4,step:.01},
    {k:'color',label:'Dust colour',type:'color'},
    {k:'flash',label:'Flash frame',type:'bool'},
    {k:'floor',label:'Debris lands at hit height',type:'bool'},
    {k:'seed',label:'Seed',type:'seed'},
  ],
  smoke:[
    {k:'frame',label:'Start frame',type:'int'},
    {k:'duration',label:'Duration',type:'int',unit:'fr'},
    {k:'pos',label:'Position px',type:'pos'},
    {k:'scale',label:'Scale',type:'range',min:.2,max:4,step:.01},
    {k:'angle',label:'Direction',type:'range',min:-180,max:180,step:1,unit:'°'},
    {k:'intensity',label:'Density',type:'range',min:0,max:3,step:.01},
    {k:'speedMul',label:'Push',type:'range',min:0,max:3,step:.01},
    {k:'heat',label:'Heat (rise)',type:'range',min:0,max:3,step:.01},
    {k:'width',label:'Fire line length',type:'range',min:.02,max:2,step:.01,showIf:i=>!!PRESETS[i.preset].smoke?.line},
    {k:'curl',label:'Curl detail',type:'range',min:0,max:60,step:.5},
    {k:'turb',label:'Turbulence',type:'range',min:0,max:3,step:.01},
    {k:'linger',label:'Linger',type:'range',min:0,max:10,step:.1,unit:'s'},
    {k:'dissipate',label:'Dissipate',type:'range',min:0,max:4,step:.01},
    {k:'color',label:'Smoke colour',type:'color'},
    {k:'seed',label:'Seed',type:'seed'},
  ],
  snow:[
    {k:'frame',label:'Start frame',type:'int'},
    {k:'duration',label:'Duration',type:'int',unit:'fr'},
    {k:'pos',label:'Region centre',type:'pos'},
    {k:'full',label:'Fill the frame',type:'bool'},
    {k:'area',label:'Region size',type:'range',min:.05,max:2,step:.01,showIf:i=>!i.full},
    {k:'angle',label:'Wind dir',type:'range',min:-180,max:180,step:1,unit:'°'},
    {k:'intensity',label:'Density',type:'range',min:0,max:3,step:.01},
    {k:'speedMul',label:'Wind speed',type:'range',min:0,max:4,step:.01},
    {k:'gust',label:'Gusts',type:'range',min:0,max:2,step:.01},
    {k:'curl',label:'Turbulence',type:'range',min:0,max:60,step:.5},
    {k:'sizeMul',label:'Flake size',type:'range',min:.2,max:4,step:.01},
    {k:'depth',label:'Depth / defocus',type:'range',min:0,max:1,step:.01},
    {k:'color',label:'Snow colour',type:'color'},
    {k:'seed',label:'Seed',type:'seed'},
  ],
  drift:[
    {k:'frame',label:'Start frame',type:'int'},
    {k:'duration',label:'Duration',type:'int',unit:'fr'},
    {k:'pos',label:'Ridge centre',type:'pos'},
    {k:'ridge',label:'Ridge angle',type:'range',min:-90,max:90,step:1,unit:'°'},
    {k:'width',label:'Ridge length',type:'range',min:0,max:1.5,step:.01},
    {k:'angle',label:'Wind dir',type:'range',min:-180,max:180,step:1,unit:'°'},
    {k:'intensity',label:'Snow grains',type:'range',min:0,max:3,step:.01},
    {k:'haze',label:'Haze',type:'range',min:0,max:2,step:.01},
    {k:'sizeMul',label:'Grain size',type:'range',min:.3,max:3,step:.01},
    {k:'speedMul',label:'Wind speed',type:'range',min:0,max:3,step:.01},
    {k:'length',label:'Plume length',type:'range',min:.1,max:3,step:.01},
    {k:'spreadMul',label:'Fan out',type:'range',min:0,max:3,step:.01},
    {k:'lift',label:'Lift / droop',type:'range',min:-1,max:1,step:.01},
    {k:'curl',label:'Wisp detail',type:'range',min:0,max:60,step:.5},
    {k:'color',label:'Snow colour',type:'color'},
    {k:'seed',label:'Seed',type:'seed'},
  ],
};

export const SCENE_FIELDS = [
  {k:'depth2d',label:'Smoke depth (2D)',type:'select',options:[[1,'Single sheet'],[3,'Three flow layers'],[5,'Five flow layers']]},
  {k:'mc',label:'Sharp advection (2D)',type:'bool'},
  {k:'quality',label:'Sim grid',type:'select',options:[[256,'Draft · 256'],[384,'Standard · 384'],[512,'High · 512'],[768,'Final · 768'],[1024,'Hero · 1024']]},
  {k:'buoyancy',label:'Buoyancy',type:'range',min:0,max:3,step:.01},
  {k:'cooling',label:'Air cooling (2D)',type:'range',min:0,max:3,step:.01},
  {k:'wind',label:'Wind',type:'range',min:-.5,max:.5,step:.005},
  {k:'drag',label:'Air drag',type:'range',min:0,max:3,step:.01},
  {k:'density',label:'Opacity',type:'range',min:.2,max:6,step:.05,render:true},
  {k:'lightAngle',label:'Light dir',type:'range',min:-180,max:180,step:1,unit:'°',render:true},
  {k:'lightElev',label:'Front / back',type:'range',min:-90,max:90,step:1,unit:'°',render:true},
  {k:'lightCol',label:'Key colour',type:'color',render:true},
  {k:'ambCol',label:'Sky colour',type:'color',render:true},
  {k:'shadow',label:'Self-shadow',type:'range',min:0,max:3,step:.01,render:true},
  {k:'ambient',label:'Ambient',type:'range',min:0,max:1,step:.01,render:true},
  {k:'glow',label:'Fire glow',type:'range',min:0,max:3,step:.01,render:true},
  {k:'shape2d',label:'Light shaping (2D)',type:'range',min:0,max:1,step:.01,render:true},
  {k:'opacitySoft2d',label:'Smoke softness (2D)',type:'range',min:0,max:1,step:.01,render:true},
  {k:'detail2d',label:'Detail (2D)',type:'range',min:0,max:2,step:.01,render:true},
  // what a loaded matte does: block the smoke (collider), hide the effects behind it (holdout), or both
  {k:'matteUse',label:'Matte',type:'select',options:[['collide','Collider'],['holdout','Holdout'],['both','Collider + holdout']]},
];

// R13 live-particle calibration. R12 remains available as a frozen comparison.
const candidatePatch={"concrete":{"dust":{"fines":{"volume":{"particles":true,"particleBirthBreakup":1,"particleMassCurve":[[0,0],[0.0417,0.4],[0.0833,0.65],[0.1667,0.85],[0.3333,1.1],[0.75,1.5],[1.5,1.3],[3,0.9],[6,0.3],[10,0]],"src":0.02,"spread":0.064,"tau":0.9,"fallKick":0.28,"particleTurb":0.2,"particleCurl":2.0,"particleDiffusion":2e-06,"birthWidth":0.5,"cohorts":16,"particleStir":0.07,"particleEddyScale":0.04,"particleFallModes":[0.5,0.08,0.5,0.1],"particleFastTau":0.45,"particleFastSpread":0.65},"sheet":{"particles":true,"particleBirthBreakup":1,"particleMassCurve":[[0,0.0],[0.0417,0.32000000000000006],[0.0833,0.52],[0.1667,0.68],[0.3333,0.8800000000000001],[0.75,1.2000000000000002],[1.5,1.04],[3,0.7200000000000001],[6,0.24],[10,0.0]],"src":0.02,"spread":0.064,"tau":0.9,"fallKick":0.28,"particleTurb":0.15,"particleCurl":1.2,"particleDiffusion":8e-06,"birthWidth":0.5,"cohorts":16,"particleStir":0.07,"particleEddyScale":0.04,"particleFallModes":[0.5,0.08,0.5,0.1],"particleFastTau":0.45,"particleFastSpread":0.65}},"particleCoreAmount":0.12}},"plume":{"smoke":{"sheet":{"sourceTempCurve":[[0,0.6],[6,0.6],[12,0.14],[18,0.05]],"draftKCurve":[[0,0.008],[6,0.008],[16,0.001]],"dissipate":0.78}}}};
function applyCandidate(a,b){for(const[k,v]of Object.entries(b)){if(v&&typeof v==='object'&&!Array.isArray(v))applyCandidate(a[k]??={},v);else a[k]=v;}}
applyCandidate(PRESETS,candidatePatch);
// R14 keeps the matched particle trajectories and larger chips, while reducing
// the soft dense core and decorative grain. Fine 3D detail uses the persistent
// air map so its surface does not renew with the render clock.
applyCandidate(PRESETS,{
  concrete:{fineGrains:{alpha:.20},dust:{particleCoreAmount:.06}},
  plume:{smoke:{sheet:{dissipate:.90},volume:{persistentFine:true}}}
});
// R15: continuous live smoke mass follows the scalar carrier. Render Detail
// blends this conserved density with grid density; it does not renew noise.
applyCandidate(PRESETS,{plume:{smoke:{volume:{particles:true,particleCount:524288,particleBlend:.8,
  sourceTempCurve:[[0,.35],[2,.35],[4,1.4],[6,1.4],[12,.6],[18,.3]],sourceGrowth:2.5}}}});
// R17: generated flow only. The post-advection pressure solve needs a new
// sheet emission balance. Resolved vortex breakup and source heating remain
// simulation state; no reference image, density or motion enters the solver.
applyCandidate(PRESETS,{
  plume:{smoke:{
    sheet:{amount:2.3,initialAmount:.9,sourceTempCurve:[[0,.6],[6,.6],[12,.5],[18,.4]],draftKCurve:[[0,.008],[6,.008],[16,.003]]},
    volume:{curl:2.35,thermalBreakup:2.5,sourceTempCurve:[[0,.45],[2,.45],[4,1.9],[6,1.9],[12,.8],[18,.6]]}
  }},
  concrete:{fineGrains:{alpha:.12},dust:{fines:{sheet:{particleCount:65536},volume:{particleCount:65536,particleCountByGrid:true}}}}
});

// R18 candidate: compact slow haze and a smaller, separately fading heavy population.
applyCandidate(PRESETS,{concrete:{dust:{fines:{
 sheet:{particleFallModes:[.25,.14,-.10,.12],settle:.04,particleLateSettle:.03,particleFastSettle:.35,particleFastFade:[.3,1.8]},
 volume:{particleFallModes:[.25,.14,-.10,.12],settle:.04,particleLateSettle:.03,particleFastSettle:.35,particleFastFade:[.3,1.8]}
}}}});

// R18 selected generated source and independent heavy/suspended mineral lifetimes.
applyCandidate(PRESETS,{"plume":{"smoke":{"sheet":{"sourceTempCurve":[[0,0.55],[1,0.55],[2,0.4],[4,0.21],[12,0.14],[18,0.13]],"draftKCurve":[[0,0]],"entrain":0,"thermalBreakup":2.0,"turbBase":0.5,"amount":2.5,"growthTime":14,"curl":1.0,"speed":0.7},"volume":{"growthTime":14,"turb":0.8,"curl":1.6,"substeps":3,"rk2":true}}},"concrete":{"dust":{"fines":{"volume":{"particleRenderKernel":1,"particleFallModes":[0.5,0.11,-0.3,0.14],"particleFastTau":0.3,"particleFastSettle":0.65,"particleFastFallSpread":1.2,"settle":0.0475,"particleMassCurve":[[0,0],[0.0417,0.4],[0.0833,0.65],[0.1667,0.85],[0.3333,1.1],[0.75,1.8],[1.5,1.8],[3,1.2],[6,0.3],[10,0]],"particleLateSettle":0.025,"particleEddyScale":0.02,"spread":0.056},"sheet":{"particleFallModes":[0.5,0.11,-0.3,0.14],"particleFastTau":0.3,"particleFastSettle":0.65,"particleFastFallSpread":1.2,"settle":0.0475,"particleMassCurve":[[0,0],[0.0417,0.32000000000000006],[0.0833,0.52],[0.1667,0.68],[0.3333,0.8800000000000001],[0.75,1.4400000000000002],[1.5,1.4400000000000002],[3,0.96],[6,0.24],[10,0]],"particleLateSettle":0.025,"particleEddyScale":0.02}},"densityScale":1.7}}});
