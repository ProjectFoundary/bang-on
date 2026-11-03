// Pure orchestration checks. No browser, provider connection, uploads or spend.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(new URL('../package.json',import.meta.url)),{build}=require('esbuild');
async function load(name){
  const bundle=await build({entryPoints:[fileURLToPath(new URL('../src/'+name,import.meta.url))],bundle:true,write:false,format:'esm'});
  return import('data:text/javascript;base64,'+Buffer.from(bundle.outputFiles[0].text).toString('base64'));
}
const B=await load('elementBatch.ts'),W=await load('elementWorkflow.ts'),clone=value=>structuredClone(value);
const effect=(id,preset='smoke_grenade')=>({id,preset,where:'test '+id,tracking:'camera',size:.4,keys:[{phase:'start',frame:0}],element:{duration_s:1,seed:42,colliders:[]}});
const take=(id,effect_id,extra={})=>({id,effect_id,status:'done',current:true,approved:false,backend_current:true,created:'2026-11-03 12:00:00',...extra});
function shot(effects=[effect('a')],takes=[]){return {
  id:'batch-shot',mode:'plate',frames:24,start_s:0,source:{file:'plate.mp4',width:960,height:540,fps:24},
  native_profile:{width:960,height:540,fps:24,duration:1,timestamps:Array.from({length:24},(_,i)=>i/24)},
  settings:{pipeline:'ltx_elements',seed:42},geometry:{collision_mode:'matte2d',reference_frame:0,depth:null,mattes:[]},
  effects:clone(effects),element_takes:clone(takes),element_approvals:{},renders:[]
};}
function harness(initial,options={}){
  const h={state:clone(initial),calls:[],updates:[],progress:[],valid:true,projectReads:0,active:null};
  h.hooks={valid:()=>h.valid,onUpdate:p=>h.updates.push(clone(p)),onProgress:p=>h.progress.push(clone(p))};
  h.deps={
    project:async pid=>{
      assert.equal(pid,initial.id);h.calls.push('project');h.projectReads++;
      options.beforeProject?.(h,h.projectReads);return clone(h.state);
    },
    prepare:async(p,id,paths,onProgress,valid)=>{
      assert.equal(p.id,initial.id);assert.equal(valid(),true);assert(paths instanceof Map);
      h.calls.push('prepare:'+id);onProgress(1,24);
      const t=take(id+'-new',id,{status:'prepared'});h.state.element_takes.unshift(t);
      options.afterPrepare?.(h,id);return clone(t);
    },
    start:async(pid,id)=>{
      assert.equal(pid,initial.id);h.calls.push('start:'+id);options.beforeStart?.(h,id);
      if(options.startError)throw Error('Provider start failed');
      const t=h.state.element_takes.find(t=>t.id===id);assert(t);t.status='queued';h.active=id;
      return {id:'job-'+id,kind:'element',status:'queued'};
    },
    wait:async(pid,predicate,onUpdate)=>{
      assert.equal(pid,initial.id);
      if(h.active){
        const id=h.active;h.calls.push('wait:'+id);
        const t=h.state.element_takes.find(t=>t.id===id);t.status='rgb';onUpdate(clone(h.state));
        t.status=options.failedTake===id?'error':'done';if(t.status==='error')t.error='Alpha failed';
        h.active=null;onUpdate(clone(h.state));assert.equal(predicate(clone(h.state)),true,'Element terminal predicate must recognise done/error.');
        options.afterWait?.(h,id);
      }else{
        h.calls.push('wait:composite');const r=h.state.renders.find(r=>r.status==='compositing');assert(r,'Expected only the review composite to be pending.');
        r.status=options.compositeError?'error':'done';if(r.status==='error')r.error='Composite failed';
        onUpdate(clone(h.state));assert.equal(predicate(clone(h.state)),true,'Composite terminal predicate must recognise done/error.');
      }
      return clone(h.state);
    },
    composite:async(pid,ids)=>{
      assert.equal(pid,initial.id);h.calls.push('composite:'+ids.join(','));h.compositeIds=clone(ids);
      const r={id:'review-batch',kind:'effect',status:'compositing',review_only:true,files:{},source_current:true};h.state.renders.unshift(r);return clone(r);
    }
  };
  h.run=()=>B.runEffectBatch(clone(initial),new Map([['a',new Float32Array([100,100])]]),h.hooks,h.deps);
  return h;
}

// Pinning wins over a newer unapproved candidate; current completed unapproved
// takes are reused, while only missing/prepared effects spend generation calls.
{
  const p=shot([effect('a','explosion'),effect('b'),effect('c','fire'),effect('d','dust_hit')],[
    take('a-newer','a'),take('a-approved','a',{approved:true}),take('b-ready','b'),
    take('c-prepared','c',{status:'prepared'}),take('d-old-account','d',{status:'prepared',backend_current:false})
  ]);p.element_approvals.a='a-approved';
  const plan=B.effectBatchPlan(p);assert.equal(plan.length,4);assert.equal(plan[0].take.id,'a-approved');assert.equal(plan[0].approved,true);assert.equal(plan[1].ready,true);
  const frozen=JSON.stringify(p),h=harness(p),r=await h.run();
  assert.equal(r.generated,2);assert.equal(r.reused,2);assert.equal(r.render.id,'review-batch');assert.equal(r.render.status,'done');assert.equal(r.render.review_only,true);
  assert.deepEqual(h.compositeIds,['a-approved','b-ready','c-prepared','d-new']);
  assert.deepEqual(h.calls.filter(x=>/^(prepare|start|wait):/.test(x)),['start:c-prepared','wait:c-prepared','prepare:d','start:d-new','wait:d-new','wait:composite']);
  assert.deepEqual(h.state.element_approvals,{a:'a-approved'});assert.equal(JSON.stringify(p),frozen,'Orchestration must not mutate its input project.');
  assert(h.updates.length>0&&h.progress.length>0);assert(h.progress.every(p=>p.index>=1&&p.index<=p.total));
}

// A finished batch can be reviewed again for free. This does not silently pin
// the completed version or require an unnecessary new Cloud generation.
{
  const h=harness(shot([effect('a')],[take('ready','a')])),r=await h.run();
  assert.equal(r.generated,0);assert.equal(r.reused,1);assert.deepEqual(h.compositeIds,['ready']);
  assert(!h.calls.some(c=>c.startsWith('prepare:')||c.startsWith('start:')));assert.deepEqual(h.state.element_approvals,{});
}

// A current unapproved legacy explosion requires a true opacity capture for
// core assistance. A manually approved legacy explosion stays pinned.
for(const status of ['done','prepared']){
  const p=shot([effect('a','explosion'),effect('b','explosion')],[
    take('legacy-unapproved','a',{status,needs_alpha_core:true}),
    take('legacy-approved','b',{approved:true})
  ]);p.element_approvals.b='legacy-approved';
  const plan=B.effectBatchPlan(p);assert.equal(plan[0].take,undefined);assert.equal(plan[0].ready,false);assert.equal(plan[1].take.id,'legacy-approved');
  const h=harness(p),r=await h.run();assert.equal(r.generated,1);assert.equal(r.reused,1);
  assert.deepEqual(h.compositeIds,['a-new','legacy-approved']);assert.deepEqual(h.state.element_approvals,{b:'legacy-approved'});
  assert(h.calls.includes('prepare:a'));assert(!h.calls.some(c=>c==='prepare:b'||c==='start:legacy-approved'));
}

// An outdated old pin is preserved for history. Its explicit new current
// candidate may be used in the review without inheriting visual approval.
{
  const p=shot([effect('a')],[take('old-pin','a',{current:false,approved:false,approval_outdated:true}),take('new-current','a')]);p.element_approvals.a='old-pin';
  const h=harness(p);await h.run();assert.deepEqual(h.compositeIds,['new-current']);assert.deepEqual(h.state.element_approvals,{a:'old-pin'});
}

// Errors stop the sequence: no later effect, automatic retry or partial final
// composite can run. Successful work remains in the project for a later retry.
for(const options of [{startError:true},{failedTake:'a-new'},{compositeError:true}]){
  const effects=options.compositeError?[effect('a')]:[effect('a'),effect('b')],h=harness(shot(effects),options);
  await assert.rejects(h.run());assert.equal(h.calls.filter(c=>c==='start:a-new').length,1);
  assert(!h.calls.some(c=>c==='prepare:b'||c==='start:b-new'));
  if(!options.compositeError)assert(!h.calls.some(c=>c.startsWith('composite:')));
}

// Leaving the shot stops before a paid start, or after an already-running job
// completes. It must never queue the next effect into a different open shot.
for(const timing of ['prepared','running']){
  const h=harness(shot([effect('a'),effect('b')]),{
    afterPrepare:(h)=>{if(timing==='prepared')h.valid=false;},
    afterWait:(h)=>{if(timing==='running')h.valid=false;}
  });
  await assert.rejects(h.run());assert(!h.calls.includes('prepare:b'));assert(!h.calls.some(c=>c.startsWith('composite:')));
  assert.equal(h.calls.filter(c=>c.startsWith('start:')).length,timing==='prepared'?0:1);
}

// New input/collider changes during a job cannot be mixed with the old batch.
// Renders/takes/approvals are deliberately absent from this configuration guard.
for(const change of ['effects','geometry','settings','source','native_profile']){
  const h=harness(shot([effect('a'),effect('b')]),{afterWait:(h)=>{
    if(change==='effects')h.state.effects[1].size=.9;
    else if(change==='geometry')h.state.geometry.reference_frame=2;
    else if(change==='settings')h.state.settings.seed=99;
    else if(change==='source')h.state.source.file='other.mp4';
    else h.state.native_profile.width=1920;
  }});
  await assert.rejects(h.run());assert(!h.calls.includes('prepare:b'));assert(!h.calls.some(c=>c.startsWith('composite:')));
}

// Capture is local and can take time. Re-read after it, before the paid start,
// including changes that the raw-generation signature intentionally ignores.
for(const change of ['integration','stale','account']){
  const h=harness(shot([effect('a')]),{afterPrepare:h=>{
    if(change==='integration')h.state.effects[0].element.opacity=.4;
    else if(change==='stale')h.state.element_takes[0].current=false;
    else h.state.element_takes[0].backend_current=false;
  }});
  await assert.rejects(h.run());assert(!h.calls.some(c=>c.startsWith('start:')),'Changed capture must stop before the first paid submission.');
}

// A pin made after generation supersedes the earlier batch candidate, and is
// read again before the review request without rewriting the user's approvals.
{
  const p=shot([effect('a'),effect('b')],[take('a-first','a'),take('a-pin','a')]);
  const h=harness(p,{afterWait:(h)=>{
    h.state.element_takes.find(t=>t.id==='a-pin').approved=true;h.state.element_approvals.a='a-pin';
  }});await h.run();assert.deepEqual(h.compositeIds,['a-pin','b-new']);assert.equal(h.state.element_approvals.a,'a-pin');
}

// An approval can arrive while the local guide is being captured. Recheck it
// before starting the prepared version and preserve the new manual pin.
{
  const h=harness(shot([effect('a')]),{afterPrepare:h=>{
    h.state.element_takes.unshift(take('capture-pin','a',{approved:true}));h.state.element_approvals.a='capture-pin';
  }}),r=await h.run();
  assert.equal(r.generated,0);assert.equal(r.reused,1);assert.deepEqual(h.compositeIds,['capture-pin']);
  assert(!h.calls.some(c=>c.startsWith('start:')));assert.deepEqual(h.state.element_approvals,{a:'capture-pin'});
}

// A manual pin may win atomically inside the backend after the frontend's
// final read. The skip response must not wait for or submit the prepared take.
{
  const h=harness(shot([effect('a')]));
  h.deps.start=async(pid,id)=>{
    assert.equal(pid,'batch-shot');assert.equal(id,'a-new');h.calls.push('start:'+id);
    h.state.element_takes.unshift(take('atomic-pin','a',{approved:true}));h.state.element_approvals.a='atomic-pin';
    return {skipped_approved:true,take_id:'atomic-pin'};
  };
  const r=await h.run();assert.equal(r.generated,0);assert.equal(r.reused,1);assert.deepEqual(h.compositeIds,['atomic-pin']);
  assert(!h.calls.includes('wait:a-new'));assert.deepEqual(h.calls.filter(c=>c.startsWith('wait:')),['wait:composite']);
  assert.deepEqual(h.state.element_approvals,{a:'atomic-pin'});assert.equal(h.state.element_takes.find(t=>t.id==='a-new').status,'prepared');
}
{
  const h=harness(shot([effect('a')]));h.deps.start=async()=>({skipped_approved:true,take_id:'gone-pin'});
  await assert.rejects(h.run());assert(!h.calls.some(c=>c.startsWith('wait:')||c.startsWith('composite:')),'A disappearing atomic pin must fail safely instead of waiting for an unsubmitted take.');
}

// Review presentation must never label a fresh batch as approved/final-ready.
for(const approved of [false,true]){
  const presentation=W.finishTakePresentation({kind:'effect',status:'done',source_current:true,review_only:true},true,approved);
  assert.match(presentation.heading,/review/i);assert(presentation.notice.length>0);assert(!/CURRENT TAKE|FINAL TAKE/.test(presentation.heading));
}

// A lost/outdated terminal candidate cannot quietly fall back to a different
// unapproved take between generation and the final composite request.
{
  const h=harness(shot([effect('a')]),{afterWait:h=>{
    h.state.element_takes.find(t=>t.id==='a-new').current=false;
    h.state.element_takes.unshift(take('other-candidate','a'));
  }});
  await assert.rejects(h.run());assert(!h.calls.some(c=>c.startsWith('composite:')));
}
{
  const h=harness(shot([]));await assert.rejects(h.run());assert.equal(h.calls.length,0,'An empty batch must never call a provider or create a composite.');
}
console.log('Batch LTX orchestration passes: pinned/ready reuse, sequential missing effects, manual approval, error/navigation/configuration stops, final pin recheck and review-only presentation.');
