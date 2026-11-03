// Local UI regression. Seeds only an explicitly named synthetic /tmp fixture;
// model, connection and generation routes are denied throughout the browser run.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {mkdirSync,readFileSync,realpathSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
const root=process.env.KEYFX_UI_URL||'http://127.0.0.1:8778',pid=process.env.KEYFX_UI_PROJECT,data=process.env.KEYFX_UI_DATA;
assert(pid&&data,'Set KEYFX_UI_PROJECT and KEYFX_UI_DATA to an empty synthetic fixture.');
const origin=new URL(root);assert.equal(origin.protocol,'http:');assert(['127.0.0.1','localhost','[::1]'].includes(origin.hostname));assert.notEqual(origin.port,'8766');
const directory=realpathSync(data);assert(/^\/(?:private\/)?tmp\//.test(directory));
const projectFile=resolve(directory,'projects',pid,'project.json'),original=readFileSync(projectFile,'utf8'),disk=JSON.parse(original);
assert.equal(disk.id,pid);assert(/fixture/i.test(disk.name));assert.equal(disk.mode,'plate');assert.equal(disk.effects.length,0);assert.equal(disk.renders.length,0);assert(!(disk.flashes??[]).length);
// Also restore if dependency loading or browser launch fails before the main try.
process.once('exit',()=>writeFileSync(projectFile,original));
execFileSync(process.env.KEYFX_PYTHON||'python',['-c',`
import hashlib,json,sys
from pathlib import Path
import cv2,numpy as np
root=Path(sys.argv[1]);path=root/'project.json';p=json.loads(path.read_text())
assert '/tmp/' in str(root.resolve()) and 'fixture' in p['name'].lower()
depth=np.full((180,320),40,np.uint8)
depth[57:60,60:240]=173
depth[60:70,60:240]=160;depth[115:125,60:240]=160
depth[60:125,60:68]=160;depth[60:125,232:240]=160
depth[100:115,275:290]=160
relative=Path('geometry')/'de7d00000001'/'depth.png';asset=root/relative;asset.parent.mkdir(parents=True,exist_ok=True)
assert cv2.imwrite(str(asset),depth)
entry={'id':'de7d00000001','file':str(relative),'kind':'inverse','reference_frame':0,'width':320,'height':180,'sha256':hashlib.sha256(asset.read_bytes()).hexdigest(),'units':'synthetic relative depth'}
p['geometry']={'reference_frame':0,'focal_pixels':900.,'depth_scale':4.,'depth_bias':.05,'depth':entry,'mattes':[]}
p['settings'].update(pipeline='ltx_elements',legacy_h3=False)
path.write_text(json.dumps(p))
`,resolve(directory,'projects',pid)],{cwd:resolve(new URL('../..',import.meta.url).pathname),stdio:'pipe'});
const runtime=createRequire(process.env.KEYFX_NODE_PACKAGE||new URL('../package.json',import.meta.url));
const {chromium}=runtime('playwright');
const out=process.env.KEYFX_UI_OUTPUT||'/tmp/bang-depth-selection';mkdirSync(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.KEYFX_CHROME||undefined,headless:true,args:process.platform==='darwin'?['--use-angle=metal']:[]});
const report={pass:false,errors:[],failed:[],blocked:[],writes:[]};let page;
try{
 const context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1});page=await context.newPage();page.setDefaultTimeout(60000);
 const base=`${root}/api/projects/${pid}`;
 page.on('pageerror',e=>report.errors.push(String(e)));
 page.on('response',r=>{if(r.status()>=400)report.failed.push({url:r.url(),status:r.status()});});
 page.on('requestfailed',r=>{if(r.resourceType()!=='media'||r.failure()?.errorText!=='net::ERR_ABORTED')report.failed.push({url:r.url(),error:r.failure()?.errorText});});
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),method=request.method();
  if(url.origin!==root){report.blocked.push({url:request.url(),method});return route.abort();}
  const localWrite=method==='PATCH'&&[base,`${base}/geometry`].includes(request.url())||method==='POST'&&[`${base}/effects`,`${base}/geometry/depth-selection/preview`,`${base}/geometry/depth-selection`].includes(request.url());
  if(url.pathname.endsWith('/elements/capabilities')||!['GET','HEAD'].includes(method)&&!localWrite){report.blocked.push({url:request.url(),method});return route.fulfill({status:409,json:{detail:'Provider and model operations are blocked in depth-selection QA.'}});}
  if(localWrite)report.writes.push({url:request.url(),method,body:request.postDataJSON()});
  return route.continue();
 });
 await page.goto(`${root}/#${pid}`,{waitUntil:'networkidle'});await page.waitForFunction(id=>window.__keyfx?.project?.id===id,pid);
 if(await page.getByRole('button',{name:'Set up later',exact:true}).isVisible())await page.getByRole('button',{name:'Set up later',exact:true}).click();
 await page.locator('nav.steps button').nth(1).click();await page.getByRole('button',{name:'Add an effect',exact:true}).click();
 await page.getByRole('button',{name:'Smoke grenade',exact:true}).click();
 const canvas=page.locator('.viewer canvas'),bounds=await canvas.boundingBox();assert(bounds);
 const placed=page.waitForResponse(r=>r.url()===`${base}/effects`&&r.request().method()==='POST');
 await canvas.click({position:{x:bounds.width*.4,y:bounds.height*.5}});assert.equal((await placed).status(),200);
 await page.locator('.interaction-step>summary').click();
 await page.getByRole('button',{name:'Select object from depth…',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'Select a depth object',exact:true});assert(await dialog.isVisible());
 const picker=page.getByRole('button',{name:'Pick a surface on the plate',exact:true}),rect=await picker.boundingBox();assert(rect);
 const waitPreview=()=>page.waitForResponse(r=>r.url()===`${base}/geometry/depth-selection/preview`&&r.request().method()==='POST');
 let response=waitPreview();await picker.click({position:{x:rect.width*.4,y:rect.height*.35}});
 let preview=await response;assert.equal(preview.status(),200);const initial=await preview.json();assert(initial.coverage>0&&initial.coverage<.2);assert.equal(initial.points.length,1);
 response=waitPreview();await page.getByLabel('Depth tolerance',{exact:true}).evaluate(node=>{node.value='.08';node.dispatchEvent(new Event('input',{bubbles:true}));});
 preview=await response;assert.equal(preview.status(),200);const expanded=await preview.json();assert(expanded.coverage>initial.coverage,'Tolerance must expand the connected depth selection.');
 response=waitPreview();await picker.click({position:{x:rect.width*.88,y:rect.height*.6},modifiers:['Shift']});
 preview=await response;assert.equal(preview.status(),200);const combined=await preview.json();assert.equal(combined.points.length,2);assert(combined.coverage>expanded.coverage);
 await page.getByLabel('Object name',{exact:true}).fill('QA depth rack');
 const saved=page.waitForResponse(r=>r.url()===`${base}/geometry/depth-selection`&&r.request().method()==='POST');
 await page.getByRole('button',{name:'Add selected object',exact:true}).click();const created=await saved;assert.equal(created.status(),200);const object=(await created.json()).asset;
 await dialog.waitFor({state:'hidden'});
 await page.waitForFunction(id=>window.__keyfx.project.effects[0].element.colliders.includes(id),object.id);
 assert(await page.getByRole('checkbox',{name:'Use QA depth rack for this element',exact:true}).isChecked(),'The new depth collider must be assigned in the live selected element.');
 await page.locator('summary').filter({hasText:'QA depth rack · controls and preview'}).click();
 const blur=page.getByLabel('Collider blur for QA depth rack',{exact:true});
 const geometrySaved=page.waitForResponse(r=>r.url()===`${base}/geometry`&&r.request().method()==='PATCH');
 await blur.evaluate(node=>{node.value='24';node.dispatchEvent(new Event('input',{bubbles:true}));});assert.equal((await geometrySaved).status(),200);
 await page.waitForFunction(id=>window.__keyfx.project.geometry.mattes.find(m=>m.id===id).colliderBlur===24,object.id);
 assert.equal(await page.getByRole('img',{name:'Original holdout matte',exact:true}).getAttribute('src'),await page.getByRole('img',{name:'Collider blur setup preview',exact:true}).getAttribute('src'));
 assert.match(await page.getByRole('img',{name:'Collider blur setup preview',exact:true}).getAttribute('style'),/blur\(/);
 await page.screenshot({path:`${out}/depth-object.png`,fullPage:true});
 await page.locator('nav.steps button').nth(2).click();assert(await page.getByRole('heading',{name:'Integration',exact:true}).isVisible());
 assert(await page.getByRole('button',{name:'Update composite from cached elements',exact:true}).isEnabled());
 assert(await page.getByLabel(/Room light/).isVisible());assert(await page.getByLabel(/Room shadow/).isVisible());
 await page.screenshot({path:`${out}/finishing-integration.png`,fullPage:true});
 assert.deepEqual(report.errors,[]);assert.deepEqual(report.failed,[]);assert.deepEqual(report.blocked,[]);
 report.depth={initialCoverage:initial.coverage,expandedCoverage:expanded.coverage,combinedCoverage:combined.coverage,object:object.id,assigned:true,colliderBlur:24};report.integration=true;report.pass=true;
}catch(error){report.failure=String(error);if(page){report.body=await page.locator('body').innerText().catch(()=>'?');await page.screenshot({path:`${out}/failure.png`,fullPage:true}).catch(()=>{});}throw error;}
finally{await browser.close();writeFileSync(projectFile,original);writeFileSync(`${out}/report.json`,JSON.stringify(report,null,2));console.log(JSON.stringify({pass:report.pass,report:`${out}/report.json`,errors:report.errors,failed:report.failed}));}
