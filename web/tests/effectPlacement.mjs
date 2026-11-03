// Placement regression for legacy original-plate shots. All writes are limited
// to an explicitly selected synthetic /tmp fixture; every model route is denied.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync,writeFileSync,mkdirSync,realpathSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const local=createRequire(new URL('../package.json',import.meta.url));
const {build}=local('esbuild');
const bundle=await build({entryPoints:[fileURLToPath(new URL('../src/effectAvailability.ts',import.meta.url))],bundle:true,write:false,format:'esm'});
const availability=await import('data:text/javascript;base64,'+Buffer.from(bundle.outputFiles[0].text).toString('base64'));
for(const id of ['smoke_white','smoke_plume','smoke_plume_hq','smoke_grenade','fire']){
 assert(availability.effectPlaceable(id,'plate',false),`${id} must be placeable in an existing original-plate shot`);
 assert(availability.effectPlaceable(id,'plate',true));
 assert(availability.placementNeedsElements(id,'plate',false));
 assert(!availability.placementNeedsElements(id,'plate',true));
}
assert(availability.effectPlaceable('explosion','plate',false));
assert(!availability.effectPlaceable('sparks','plate',false));
assert(!availability.placementNeedsElements('fire','greenscreen',false));
console.log('Placement availability: legacy plate smoke/Fire and LTX adoption eligibility pass.');
if(process.env.KEYFX_PLACEMENT_PURE==='1')process.exit(0);

const root=process.env.KEYFX_UI_URL||'http://127.0.0.1:8778',pid=process.env.KEYFX_UI_PROJECT,data=process.env.KEYFX_UI_DATA;
if(!pid||!data)throw Error('Set KEYFX_UI_PROJECT and KEYFX_UI_DATA to the isolated synthetic fixture.');
const origin=new URL(root);
assert.equal(origin.protocol,'http:');assert(['127.0.0.1','localhost','[::1]'].includes(origin.hostname));
assert.notEqual(origin.port,'8766','Production Bang On must never be mutated by this test.');
const directory=realpathSync(data);assert(/^\/(?:private\/)?tmp\//.test(directory),'Fixture directory must be under /tmp.');
const disk=JSON.parse(readFileSync(resolve(directory,'projects',pid,'project.json'),'utf8'));
assert.equal(disk.id,pid);assert(/fixture/i.test(disk.name));assert.equal(disk.mode,'plate');assert(!disk.native_profile,'The regression must begin with a legacy original-plate shot.');
assert.equal(disk.effects.length,0,'Use a fresh empty fixture, not a copied working shot.');
assert.equal(disk.renders.length,0);assert(!(disk.flashes??[]).length);
const runtime=createRequire(process.env.KEYFX_NODE_PACKAGE||new URL('../package.json',import.meta.url));
const {chromium}=runtime('playwright');
const out=process.env.KEYFX_UI_OUTPUT||'/tmp/bang-effect-placement';mkdirSync(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.KEYFX_CHROME||undefined,headless:true,args:process.platform==='darwin'?['--use-angle=metal']:[]});
const report={pass:false,errors:[],failed:[],blocked:[],writes:[],cases:[]};
let context,page;
const base=`${root}/api/projects/${pid}`;
try{
 context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1});page=await context.newPage();page.setDefaultTimeout(60000);
 page.on('pageerror',e=>report.errors.push(String(e)));
 page.on('response',r=>{if(r.status()>=400)report.failed.push({url:r.url(),status:r.status()});});
 page.on('requestfailed',r=>{if(r.resourceType()!=='media'||r.failure()?.errorText!=='net::ERR_ABORTED')report.failed.push({url:r.url(),error:r.failure()?.errorText});});
 await page.route('**/*',async route=>{
  const r=route.request(),url=new URL(r.url());
  if(url.origin!==root){report.blocked.push({url:r.url(),method:r.method()});return route.abort();}
  if(url.pathname.endsWith('/elements/capabilities')){report.blocked.push({url:r.url(),method:r.method()});return route.fulfill({status:409,json:{detail:'Model checks blocked by isolated placement QA.'}});}
  if(!['GET','HEAD'].includes(r.method())){
   const allowed=r.method()==='PATCH'&&url.pathname===`/api/projects/${pid}`||r.method()==='POST'&&url.pathname===`/api/projects/${pid}/effects`;
   if(!allowed){report.blocked.push({url:r.url(),method:r.method()});return route.fulfill({status:409,json:{detail:'Only synthetic fixture placement/edit writes are allowed.'}});}
   report.writes.push({url:r.url(),method:r.method(),body:r.postDataJSON()});
  }
  return route.continue();
 });
 const request=async(method,url,body)=>{const response=await context.request.fetch(url,{method,data:body});assert.equal(response.status(),200,await response.text());return response.json();};
 const fixture=await request('GET',base);assert.equal(fixture.id,pid);assert.equal(fixture.name,disk.name);assert(!fixture.native_profile);
 const initialSettings={...fixture.settings,pipeline:'h3',legacy_h3:false};
 const cases=[['smoke_plume_hq','Smoke plume (HQ)',5],['smoke_grenade','Smoke grenade',3],['fire','Fire',3]];
 for(const [kind,label,duration]of cases){
  await request('PATCH',base,{settings:initialSettings,effects:[]});
  await page.goto('about:blank');
  await page.goto(`${root}/#${pid}`,{waitUntil:'networkidle'});
  await page.waitForFunction(id=>window.__keyfx?.project?.id===id,pid);
  if(await page.getByRole('button',{name:'Set up later',exact:true}).isVisible())await page.getByRole('button',{name:'Set up later',exact:true}).click();
  assert.equal(await page.locator('.action-draft').count(),0,'Original-plate Scene must skip action analysis.');
  assert.equal(await page.getByRole('button',{name:'Draft action from source clip',exact:true}).count(),0);
  assert.equal(await page.evaluate(()=>window.__keyfx.project.settings.pipeline),'h3');
  const adoption=page.waitForResponse(r=>r.url()===base&&r.request().method()==='PATCH'&&r.request().postDataJSON()?.settings?.pipeline==='ltx_elements');
  await page.locator('nav.steps button').nth(1).click();
  assert.equal((await adoption).status(),200);
  await page.waitForFunction(()=>window.__keyfx.project.settings.pipeline==='ltx_elements');
  assert.equal(await page.getByRole('button',{name:'Preview',exact:true}).getAttribute('aria-pressed'),'true','Scene→Effects must show Preview without an effect result.');
  assert(await page.getByRole('button',{name:'Result',exact:true}).isDisabled());
  assert.equal(await page.locator('.no-result').count(),0);
  assert(await page.locator('.viewer canvas').isVisible(),'The placement plate must be visible.');
  if(kind===cases[0][0]){
   await page.locator('nav.steps button').nth(0).click();
   await page.getByRole('button',{name:'Continue to effects',exact:true}).click();
   assert.equal(await page.getByRole('button',{name:'Preview',exact:true}).getAttribute('aria-pressed'),'true','Footer navigation must also show Preview.');
  }
  const picker=page.getByRole('button',{name:'Add an effect',exact:true});
  assert.equal(await picker.getAttribute('aria-expanded'),'false');
  await picker.click();assert.equal(await picker.getAttribute('aria-expanded'),'true');
  assert(await page.getByRole('region',{name:'Effect library',exact:true}).isVisible());
  await picker.click();assert.equal(await picker.getAttribute('aria-expanded'),'false');
  await picker.click();
  for(const [,name]of cases)assert(await page.getByRole('button',{name,exact:true}).isEnabled(),`${name} is still disabled on a legacy plate`);
  await page.getByRole('button',{name:label,exact:true}).click();
  assert.equal(await picker.getAttribute('aria-expanded'),'false','Choosing an effect must collapse the dropdown.');
  await page.locator('.viewer.armed').waitFor();
  const canvas=page.locator('.viewer canvas'),bounds=await canvas.boundingBox();assert(bounds);
  const saved=page.waitForResponse(r=>r.url()===`${base}/effects`&&r.request().method()==='POST'&&r.request().postDataJSON()?.preset===kind);
  await canvas.click({position:{x:bounds.width*.6,y:bounds.height*.65}});
  const response=await saved;assert.equal(response.status(),200);const updated=await response.json();
  assert.equal(updated.effects.length,1);const effect=updated.effects[0];assert.equal(effect.preset,kind);assert.equal(effect.element.duration_s,duration);
  await page.waitForFunction(id=>window.__keyfx.project.effects.some(e=>e.id===id),effect.id);
  const tracking=page.getByRole('combobox',{name:/^Tracking/});assert.equal(await tracking.inputValue(),'camera');
  const trackingSave=page.waitForResponse(r=>r.url()===base&&r.request().method()==='PATCH'&&r.request().postDataJSON()?.effects?.some(e=>e.id===effect.id&&e.tracking==='screen'));
  await tracking.selectOption('screen');assert.equal((await trackingSave).status(),200);
  const row={kind,pipeline:updated.settings.pipeline,duration:effect.element.duration_s,position:effect.anchors[0],title:await page.locator('.panel-title').innerText()};
  report.cases.push(row);await page.screenshot({path:`${out}/${kind}.png`,fullPage:true});console.log(JSON.stringify(row));
 }
 // H3 remains an explicit secondary choice, including after a stage change.
 await request('PATCH',base,{settings:{...initialSettings,pipeline:'ltx_elements'},effects:[]});
 await page.goto('about:blank');await page.goto(`${root}/#${pid}`,{waitUntil:'networkidle'});
 await page.waitForFunction(id=>window.__keyfx?.project?.id===id,pid);
 if(await page.getByRole('button',{name:'Set up later',exact:true}).isVisible())await page.getByRole('button',{name:'Set up later',exact:true}).click();
 await page.locator('nav.steps button').nth(1).click();
 await page.locator('.legacy-workflow summary').click();
 const legacy=page.waitForResponse(r=>r.url()===base&&r.request().method()==='PATCH'&&r.request().postDataJSON()?.settings?.legacy_h3===true);
 await page.getByRole('button',{name:'Use legacy H3 video workflow',exact:true}).click();assert.equal((await legacy).status(),200);
 await page.waitForFunction(()=>window.__keyfx.project.settings.pipeline==='h3'&&window.__keyfx.project.settings.legacy_h3);
 await page.locator('nav.steps button').nth(0).click();await page.locator('nav.steps button').nth(1).click();
 assert.equal(await page.evaluate(()=>window.__keyfx.project.settings.pipeline),'h3','An explicit H3 choice must survive Scene→Effects.');
 assert.equal(await page.getByRole('button',{name:'Preview',exact:true}).getAttribute('aria-pressed'),'true');
 await page.getByRole('button',{name:'Add an effect',exact:true}).click();
 assert(await page.getByRole('button',{name:'White smoke',exact:true}).isEnabled(),'Legacy White smoke stays accessible in the secondary workflow.');
 await page.getByRole('button',{name:'Add an effect',exact:true}).click();
 const primary=page.waitForResponse(r=>r.url()===base&&r.request().method()==='PATCH'&&r.request().postDataJSON()?.settings?.legacy_h3===false);
 await page.getByRole('button',{name:'Use LTX elements',exact:true}).click();assert.equal((await primary).status(),200);
 await page.waitForFunction(()=>window.__keyfx.project.settings.pipeline==='ltx_elements'&&!window.__keyfx.project.settings.legacy_h3);
 report.workflow={noActionAnalysis:true,previewOnNavigation:true,dropdown:true,legacyChoiceRetained:true,primaryRestored:true};
 await page.screenshot({path:`${out}/workflow.png`,fullPage:true});
 assert.deepEqual(report.errors,[]);assert.deepEqual(report.failed,[]);assert.deepEqual(report.blocked,[]);
 assert.equal(report.writes.filter(w=>w.url.endsWith('/effects')).length,cases.length);
 report.pass=true;
}catch(error){
 report.failure=String(error);if(page){report.body=await page.locator('body').innerText().catch(()=>'?');await page.screenshot({path:`${out}/failure.png`,fullPage:true}).catch(()=>{});}throw error;
}finally{
 if(context)await context.request.patch(base,{data:{settings:{...disk.settings,pipeline:'h3'},effects:[]}}).catch(()=>{});
 writeFileSync(`${out}/report.json`,JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({pass:report.pass,report:`${out}/report.json`,errors:report.errors,failed:report.failed}));
}
