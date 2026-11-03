import {createRequire} from 'node:module';
import {writeFileSync,mkdirSync} from 'node:fs';
import assert from 'node:assert/strict';
// Run against an explicitly selected synthetic /tmp fixture, never a real shot.
// KEYFX_UI_PROJECT=<id> node web/tests/elementUI.mjs
// The local server must already be running with a separate KEYFX_DATA directory.
const runtime=createRequire(process.env.KEYFX_NODE_PACKAGE||new URL('../package.json',import.meta.url));
const {chromium}=runtime('playwright');
const root=process.env.KEYFX_UI_URL||'http://127.0.0.1:8778',pid=process.env.KEYFX_UI_PROJECT,base=`${root}/api/projects/${pid}`,out=process.env.KEYFX_UI_OUTPUT||'/tmp/bang-ui-qa';
if(!pid)throw Error('Set KEYFX_UI_PROJECT to an isolated synthetic fixture ID.');
const url=new URL(root);if(url.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(url.hostname))throw Error('Only a local isolated QA server is permitted.');
assert.notEqual(url.port,'8766','UI QA must not mutate production Bang On.');
mkdirSync(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.KEYFX_CHROME||undefined,headless:true,args:process.platform==='darwin'?['--use-angle=metal']:[]});
const report={errors:[],failed:[],mediaCancelled:[],blocked:[],requests:[],console:[],stages:[]};
let stage='open',mockGeneration=false,mockTake='',mockPoll=0,watcher;
try{
 const context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1});const page=await context.newPage();page.setDefaultTimeout(60000);
 const fixture=await(await context.request.get(base)).json();
 assert(/fixture/i.test(fixture.name),'UI QA requires a named synthetic fixture');
 assert(/^\/(?:private\/)?tmp\//.test(fixture.native_profile?.source_format_metadata?.filename??''),'UI QA requires isolated /tmp data');
 report.profile={width:fixture.native_profile.width,height:fixture.native_profile.height,fps:fixture.native_profile.fps,frames:fixture.frames};
 page.on('pageerror',e=>report.errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')report.console.push(m.text());});
 page.on('response',r=>{if(r.status()>=400)report.failed.push({url:r.url(),status:r.status()});});page.on('requestfailed',r=>{const item={url:r.url(),error:r.failure()?.errorText};if(r.resourceType()==='media'&&item.error==='net::ERR_ABORTED')report.mediaCancelled.push(item);else report.failed.push(item);});
 await page.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url());
  if(url.origin!==root){report.blocked.push({url:req.url(),method:req.method()});return route.abort();}
  if(url.pathname==='/api/connection'&&req.method()==='GET')return route.fulfill({json:{configured:true,mode:'local',local_url:'http://127.0.0.1:9',comfyui_url:'http://127.0.0.1:9',has_cloud_key:false,has_local_token:false,allow_cloud_partner:false,nano_available:true,source:'saved',report:{connected:true,mode:'local',message:'Synthetic UI connection. No provider is contacted.',checks:[]},error:null}});
  if(/\/element-takes\/[^/]+\/generate$/.test(url.pathname)&&req.method()==='POST'){
   report.blocked.push({url:req.url(),method:req.method(),mocked:true});mockGeneration=true;mockTake=url.pathname.split('/').at(-2);return route.fulfill({json:{id:mockTake,status:'queued',mocked:true}});
  }
  const localWrite=url.pathname===`/api/projects/${pid}`||url.pathname===`/api/projects/${pid}/geometry`||url.pathname===`/api/projects/${pid}/flashes`||new RegExp(`^/api/projects/${pid}/elements/[^/]+/prepare$`).test(url.pathname);
  if(!['GET','HEAD'].includes(req.method())&&!localWrite){report.blocked.push({url:req.url(),method:req.method()});return route.fulfill({status:409,json:{detail:'Blocked by isolated QA: only local fixture edits and guide preparation are allowed.'}});}
  if(/\/elements\/capabilities/.test(url.pathname)){report.blocked.push({url:req.url(),method:req.method()});return route.fulfill({status:409,json:{detail:'Capabilities check disabled in isolated QA.'}});}
  if(mockGeneration&&req.method()==='GET'&&url.pathname===`/api/projects/${pid}`){const response=await route.fetch(),data=await response.json(),take=data.element_takes.find(t=>t.id===mockTake);if(take){take.status=['rgb','alpha','done'][Math.min(mockPoll++,2)];report.stages.push(take.status);}return route.fulfill({response,json:data});}
  if(req.method()!=='GET')report.requests.push({url:req.url(),method:req.method(),bytes:req.postDataBuffer()?.length??0});return route.continue();
 });
 await page.goto(`${root}/#${pid}`,{waitUntil:'networkidle'});await page.waitForFunction(id=>window.__keyfx?.project?.id===id,pid);
 if(await page.getByRole('button',{name:'Set up later',exact:true}).isVisible())await page.getByRole('button',{name:'Set up later',exact:true}).click();
 report.initial=await page.locator('body').innerText();await page.screenshot({path:`${out}/01-open.png`,fullPage:true});console.log(JSON.stringify({stage,body:report.initial.slice(0,5000)}));
 stage='effects';await page.locator('nav.steps button').nth(1).click();await page.locator('.shot-items button').first().click();await page.getByText('Simulation preview',{exact:true}).waitFor();
 const canvas=page.locator('.viewer canvas');report.viewport=await canvas.evaluate(c=>{const r=c.getBoundingClientRect();return{width:r.width,height:r.height,ratio:r.width/r.height,intrinsic:[c.width,c.height],viewport:[innerWidth,innerHeight]};});
 assert(Math.abs(report.viewport.ratio-report.profile.width/report.profile.height)<.001);assert(report.viewport.height<1000);
 await page.evaluate(()=>window.__keyfx.setFrame(12));await page.waitForTimeout(1000);report.timeline=await page.locator('.timeline .tc').innerText();
 report.nativeTimeline={phaseDiamonds:await page.locator('.timeline .key').count(),guideSamples:await page.locator('.timeline .ruler .kf[title^="Image guide"]').count(),label:await page.locator('.timeline .transport span[title^="Isolated"]').innerText()};
 assert.equal(report.nativeTimeline.phaseDiamonds,0);assert.equal(report.nativeTimeline.guideSamples,48);assert.match(report.nativeTimeline.label,/^48\s+guides/);
 await page.screenshot({path:`${out}/02-preview.png`,fullPage:true});console.log(JSON.stringify({stage,viewport:report.viewport,timeline:report.timeline}));
 stage='native-end-retime';const start=page.getByLabel('Start frame',{exact:true}),originalStart=Number(await start.inputValue()),effectId=fixture.effects[0].id,target=fixture.frames-2;
 const changeStart=async target=>{const saved=page.waitForResponse(r=>r.url()===base&&r.request().method()==='PATCH'&&r.request().postDataJSON()?.effects?.some(e=>e.id===effectId&&e.keys.some(k=>k.phase==='onset'&&k.frame===target)));await start.fill(String(target));await start.press('Tab');const response=await saved;assert.equal(response.status(),200);return response.json();};
 const endProject=await changeStart(target),endEffect=endProject.effects.find(e=>e.id===effectId);
 report.nativeEndRetime={onset:endEffect.keys.find(k=>k.phase==='onset').frame,lastLegacyPhase:Math.max(...endEffect.keys.map(k=>k.frame)),duration:endEffect.element.duration_s,barLeft:await page.locator('.timeline .lane .bar').evaluate(e=>e.style.left)};
 assert.equal(report.nativeEndRetime.onset,target);assert(report.nativeEndRetime.lastLegacyPhase>fixture.frames-1);assert.equal(report.nativeEndRetime.duration,2);await page.screenshot({path:`${out}/02b-near-end.png`,fullPage:true});
 const restored=await changeStart(originalStart);assert.equal(restored.effects.find(e=>e.id===effectId).keys.find(k=>k.phase==='onset').frame,originalStart);console.log(JSON.stringify({stage,...report.nativeEndRetime}));
 stage='collision-controls';await page.locator('.interaction-step>summary').click();await page.getByText('Shelf · controls and preview',{exact:true}).click();const role=page.getByRole('combobox',{name:'Role for Shelf',exact:true});
 await Promise.all([page.waitForResponse(r=>r.url()===`${base}/geometry`&&r.request().method()==='PATCH'),role.selectOption('holdout')]);
 report.holdoutRole=(await (await context.request.get(base)).json()).geometry.mattes[0].role;
 await Promise.all([page.waitForResponse(r=>r.url()===`${base}/geometry`&&r.request().method()==='PATCH'),role.selectOption('both')]);
 report.restoredRole=(await (await context.request.get(base)).json()).geometry.mattes[0].role;
 assert.equal(report.holdoutRole,'holdout');assert.equal(report.restoredRole,'both');
 const assigned=page.getByRole('checkbox',{name:'Use Shelf for this element',exact:true});report.colliderAssigned=await assigned.isChecked();
 await page.screenshot({path:`${out}/03-controls.png`,fullPage:true});console.log(JSON.stringify({stage,holdout:report.holdoutRole,restored:report.restoredRole,assigned:report.colliderAssigned}));
 stage='muzzle-preview';await page.getByRole('button',{name:'Add an effect',exact:true}).click();await page.getByRole('button',{name:'Muzzle flash',exact:true}).click();await page.evaluate(()=>window.__keyfx.setFrame(12));await page.waitForTimeout(1000);await page.evaluate(()=>window.scrollTo(0,0));
 const mc=page.locator('.viewer canvas'),existing=await page.evaluate(()=>JSON.parse(JSON.stringify(window.__keyfx.project.flashes??[])));
 if(!existing.some(f=>f.frame===12&&Math.hypot(f.tip.x-f.origin.x,f.tip.y-f.origin.y)>.04)){
  const box=await mc.boundingBox(),mx=box.x+box.width*.12,my=box.y+Math.min(box.height*.1,260);await page.mouse.move(mx,my);await page.mouse.down();await page.mouse.move(mx+box.width*.12,my+20,{steps:5});await page.mouse.up();
  await page.waitForFunction(n=>(window.__keyfx.project.flashes?.length??0)>n,existing.length);
 }
 await page.waitForTimeout(800);report.flashes=await page.evaluate(()=>JSON.parse(JSON.stringify(window.__keyfx.project.flashes)));assert(report.flashes.some(f=>f.frame===12&&Math.hypot(f.tip.x-f.origin.x,f.tip.y-f.origin.y)>.04));await page.screenshot({path:`${out}/03b-muzzle.png`,fullPage:true});report.muzzleBody=await page.locator('body').innerText();console.log(JSON.stringify({stage,flashes:report.flashes}));
 await page.locator('.shot-items button').first().click();
 // Force a fresh local capture even when this fixture already has a prepared take.
 await page.getByText('Advanced guide settings',{exact:true}).click();const seed=page.getByLabel('Seed',{exact:true});await seed.fill(String(Number(await seed.inputValue())+1));await seed.press('Tab');
 stage='prepare-guide';const started=Date.now();let progress=[];watcher=setInterval(async()=>{try{const text=await page.locator('body').innerText();const match=text.match(/Capturing[^\n]*|\d+\s*\/\s*\d+\s*keyframes prepared/g);if(match){const value=match.join(' ');if(progress.at(-1)!==value){progress.push(value);console.log(JSON.stringify({captureProgress:value}));}}}catch{}},1000);
 const previous=await page.evaluate(()=>window.__keyfx.project.element_takes?.map(t=>t.id)??[]);
 await page.getByRole('button',{name:'Prepare guide video · no model spend',exact:true}).click();
 await page.waitForFunction(ids=>window.__keyfx?.project?.element_takes?.some(t=>t.status==='prepared'&&t.current&&!ids.includes(t.id)),previous,{timeout:240000});clearInterval(watcher);report.prepareMs=Date.now()-started;report.progress=progress;
 report.prepared=await page.evaluate(()=>JSON.parse(JSON.stringify(window.__keyfx.project.element_takes)));assert.equal(report.prepared.find(t=>t.current).backend_current,true);await page.screenshot({path:`${out}/04-prepared.png`,fullPage:true});console.log(JSON.stringify({stage,ms:report.prepareMs,takes:report.prepared.map(t=>({id:t.id,status:t.status,current:t.current,backend_current:t.backend_current,frames:t.plan.active_frames,width:t.plan.width,height:t.plan.height,seed:t.plan.settings.seed}))}));
 const prepared=report.prepared.find(t=>t.current),guideUrl=`${base}/element-takes/${prepared.id}/guide.mp4`,guideResponse=await context.request.get(guideUrl);assert.equal(guideResponse.status(),200);writeFileSync(`${out}/guide.mp4`,await guideResponse.body());
 report.guide=await page.evaluate(src=>new Promise((resolve,reject)=>{const video=document.createElement('video');video.preload='auto';video.onerror=()=>reject(new Error('Prepared guide failed to decode.'));video.onloadedmetadata=()=>{video.currentTime=Math.min(.5,video.duration/2);};video.onseeked=()=>{const canvas=document.createElement('canvas');canvas.width=video.videoWidth;canvas.height=video.videoHeight;const g=canvas.getContext('2d');g.drawImage(video,0,0);const data=g.getImageData(0,0,canvas.width,canvas.height).data,w=canvas.width,h=canvas.height,corners=[[0,0],[w-1,0],[0,h-1],[w-1,h-1]].map(([x,y])=>Array.from(data.slice((y*w+x)*4,(y*w+x)*4+3)));let sum=0;for(let i=0;i<data.length;i++)if(i%4!==3)sum+=data[i];resolve({width:w,height:h,duration:video.duration,sum,corners});};video.src=src;}),guideUrl);
 assert.equal(report.guide.width,prepared.plan.width);assert.equal(report.guide.height,prepared.plan.height);assert(report.guide.sum>0);assert(report.guide.corners.every(c=>Math.max(...c)<8));console.log(JSON.stringify({guide:report.guide}));
 stage='mock-generation';await page.getByRole('button',{name:'Generate selected · LTX + alpha',exact:true}).click();await page.waitForFunction(id=>window.__keyfx?.project?.element_takes?.some(t=>t.id===id&&t.status==='done'),prepared.id,{timeout:30000});
 await page.screenshot({path:`${out}/05-mocked-status.png`,fullPage:true});report.mockDone=await page.locator('body').innerText();console.log(JSON.stringify({stage,blocked:report.blocked,stages:report.stages}));
 assert.deepEqual(report.errors,[]);assert.deepEqual(report.failed,[]);assert(report.stages.includes('rgb')&&report.stages.includes('alpha')&&report.stages.includes('done'));assert(report.blocked.some(r=>r.mocked));assert(report.requests.some(r=>r.url.endsWith('/prepare')));
 report.pass=true;
}catch(error){report.pass=false;report.failureStage=stage;report.failure=String(error);const pages=browser.contexts().flatMap(c=>c.pages());if(pages[0]){report.failureBody=await pages[0].locator('body').innerText().catch(()=>'?');await pages[0].screenshot({path:`${out}/failure.png`,fullPage:true}).catch(()=>{});}console.log(JSON.stringify({stage,error:String(error),body:report.failureBody?.slice(0,6000)}));}
finally{clearInterval(watcher);writeFileSync(`${out}/report.json`,JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({report:`${out}/report.json`,pass:report.pass,errors:report.errors,failed:report.failed,blocked:report.blocked}));if(!report.pass)process.exitCode=1;}
