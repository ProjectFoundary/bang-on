<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { api, type Effect, type Flash, type Preset, type Project, type Render, type Stage } from './api';
  import { COLORS, PREROLL_MAX, image, keyFrames, phaseOf, prerolled, respace, secs } from './effects';
  import { H, W, onsetOf, renderLayers } from './mockup';
  import NewProject from './NewProject.svelte';
  import ComparePlate from './ComparePlate.svelte';
  import ModelPicker from './ModelPicker.svelte';
  import RoomReferences from './RoomReferences.svelte';
  import TakePreview from './TakePreview.svelte';
  import FlashView from './FlashView.svelte';
  import LocationView from './LocationView.svelte';
  import DressingView from './DressingView.svelte';
  import ResultView from './ResultView.svelte';
  import Timeline from './Timeline.svelte';
  import { Tracker } from './track';
  import Viewer from './Viewer.svelte';
  import PeopleView from './PeopleView.svelte';
  import ProcessingBanner from './ProcessingBanner.svelte';
  import ActionDraft from './ActionDraft.svelte';
  import Brand from './Brand.svelte';
  import ConnectionSetup from './ConnectionSetup.svelte';
  import { connection } from './connection';
  import brandMark from './assets/bang-on-mark.png';
  import { pruneSquib } from './squibfx';
  import { colliderKey, loadCollider } from './collider';
  import ElementPanel from './ElementPanel.svelte';
  import IntegrationPanel from './IntegrationPanel.svelte';
  import FinishApprovalDialog from './FinishApprovalDialog.svelte';
  import FinishingLighting from './FinishingLighting.svelte';
  import LayerExports from './LayerExports.svelte';
  import { prepareElement, startElement, startBatchElement, compositeElements, reviewCompositeElements, waitForProject,elementRequest,refreshMatteSupport } from './elementFlow';
  import {runEffectBatch,effectBatchPlan,batchConfiguration,type BatchProgress} from './elementBatch';
  import { downloadSimulationPreview, previewRecipe } from './simulationPreview';
  import { isElementProject, nativeAspect, nativeFPS } from './volumeScene';
  import {ViewerPlaybackClock,type ViewerCacheStatus} from './viewerCache';
  import {elementOnset} from './elementOptions';
  import {effectPlaceable,placementNeedsElements} from './effectAvailability';
  import { effectTrackingPath } from './effectTracking';
  import {approvedEffectCount,finishTakePresentation,preferredEffectView,elementTakeStatus} from './elementWorkflow';

  let presets = $state<Record<string, Preset>>({});
  let projectList = $state<{ id: string; name: string; mode: string; frames: number }[]>([]);
  let project = $state<Project | null>(null);
  let tracker = $state.raw<Tracker | null>(null);
  let frame = $state(0);
  let selectedId = $state<string | null>(null);
  let soloPreview = $state(false);
  let armed = $state<string | null>(null);
  let effectLibraryOpen = $state(false);
  let previewSection=$state<HTMLElement|null>(null);
  let inspectorSection=$state<HTMLElement|null>(null);
  let playing = $state(false);
  let viewerCacheStatus=$state.raw<ViewerCacheStatus|null>(null);
  function viewerCacheUpdated(status:ViewerCacheStatus){viewerCacheStatus=status;}
  let showNew = $state(false);
  let showConnection = $state(false);
  let connectionLoading = $state(true);
  let showResult = $state<Render | null>(null);
  let showPrompt = $state(false);
  let promptText = $state('');
  let message = $state('');
  let busy = $state('');
  let roomReferenceBusy = $state(false);
  let localProgress = $state<{done:number;total:number}|null>(null);
  let batchProgress=$state<BatchProgress|null>(null);
  let batchActive=$state(false);
  let reviewResultId=$state<string|null>(null);
  let approvalPreview=$state<string|null>(null),approvalBusy=$state(false),approvalError=$state('');
  let previewQuality = $state<'draft'|'high'>('draft');
  let previewAbort = $state.raw<AbortController|null>(null);
  let simulationDownload = $state<{url:string;recipe:string;quality:string}|null>(null);
  let framesLoaded = $state(0);
  let saveTimer: ReturnType<typeof setTimeout> | undefined;
  let pendingShotSave: (() => Promise<void>) | undefined;
  let shotWrite: Promise<unknown> | undefined;
  let openingShot = $state(false);
  let raf = 0;
  // undo: one step per gesture (snapshots are taken when the debounced save fires)
  let undoStack: string[] = [], redoStack: string[] = [], lastSnap = '';
  // Scene -> isolated LTX elements and muzzle frames -> native composite and export.
  let step = $state<1 | 2 | 3>(1);
  let effectTool = $state<'effects' | 'muzzle'>('effects');
  let compareMode = $state<'result' | 'source' | 'wipe' | 'preview'>('result');
  let compareSplit = $state(50);
  let stageList = $state<Stage[]>([]);
  let flashSel = $state<string | null>(null);
  let flashCost = $state<{ per_flash: number; allowance_left: number | null; unlimited: boolean } | null>(null);
  const BIG = new Set(['Fire & explosions', 'Smoke', 'Impacts']);
  let dressSel = $state<string | null>(null);
  let locMix = $state(1);
  let locPreview = $state<string | null>(null);
  let locationTab = $state<'room' | 'objects' | 'people'>('room');
  let dressingTool = $state<'object' | 'protect'>('object');
  let dressingResult = $state<string | null>(null);
  let dressingMix = $state(0), dressingBoxes = $state(true);
  let dressingTimer: ReturnType<typeof setTimeout> | undefined;
  let dressingWrite: Promise<unknown> = Promise.resolve();
  let peopleWrite: Promise<unknown> = Promise.resolve();
  let flashWrite: Promise<unknown> = Promise.resolve();
  let omitUnready = $state(false);
  const LIGHTS: [string, string][] = [['match', 'Match the plate'], ['overcast', 'Overcast daylight'], ['sun', 'Hard sun through windows'], ['dusk', 'Dusk, practical lamps'], ['night', 'Night, work lights']];
  const SIZE_PX = { small: 32, medium: 96, large: 240 } as const;

  const groups = $derived.by(() => {
    const g: Record<string, Preset[]> = {};
    for (const p of Object.values(presets)) if (!['smoke_plume', 'sparks'].includes(p.id) && BIG.has(p.category)) (g[p.category] ??= []).push(p);
    return g;
  });
  const selected = $derived(project?.effects.find((e) => e.id === selectedId) ?? null);
  const soloEffectId = $derived(soloPreview && effectTool === 'effects' ? selected?.id ?? null : null);
  $effect(() => { if (!selected) soloPreview = false; });
  const elementMode=$derived(!!project&&isElementProject(project));
  const sourceFPS=$derived(project?nativeFPS(project):24);
  const sourceTimes=$derived(project?.native_profile?.timestamps);
  const sourceAspect=$derived(project?nativeAspect(project):16/9);
  const running = $derived(project?.renders.some((r) => r.kind !== 'analysis' && !['done', 'error'].includes(r.status)) ?? false);
  const analysing = $derived(project?.renders.some((r) => r.kind === 'analysis' && !['done', 'error'].includes(r.status)) ?? false);
  const flashing = $derived(project?.flashes?.some((f) => f.status === 'queued' || f.status === 'generating') ?? false);
  const sceneBase = $derived(project?.mode === 'greenscreen' && project.environment ? `environment:${project.environment}` : 'plate');
  const flashBase = $derived(project?.effects.length ? stageList.filter(s => s.id.startsWith('render:')).at(-1)?.id ?? sceneBase : sceneBase);
  const flashList = $derived([...(project?.flashes ?? [])].sort((a, b) => a.frame - b.frame));
  const flashTodo = $derived(flashList.filter((f) => !f.result_current && f.status !== 'queued' && f.status !== 'generating'));
  const flashSelected = $derived(flashList.find((f) => f.id === flashSel) ?? null);
  const finals = $derived(project?.renders.filter((r) => r.kind === 'final') ?? []);
  const plateOnly = $derived(project ? { ...project, effects: [], flashes: [] } : null);
  const envReady = $derived(project ? project.mode !== 'greenscreen' || !!project.environment_ready : false);
  const openingRef = $derived(project?.locations?.find((l) => l.file === project?.opening_reference));
  const dressingDraft = $derived(project?.set_dressing);
  const dressingSource = $derived(project?.locations?.find(l => l.file === dressingDraft?.source));
  const canPlaceObjects = $derived(!!dressingSource && !!dressingDraft?.objects.length &&
    dressingDraft.objects.every(o => o.text.trim()) && (!dressingDraft.people || !!dressingDraft.protected.length));
  const manualPeople = $derived(project?.people.selection==='manual');
  const hasPeople = $derived(manualPeople && !!project?.people?.points.length);
  const cutoutMode = $derived(hasPeople && project?.people.mode === 'cutout');
  const canBuildLocation = $derived(!manualPeople || !!project?.people_matte || project?.location_role==='scene' && !!project.empty_set);
  const roomReferencesOverLimit = $derived((project?.room_references?.length??0) > (project?.room_reference_limits?.[project.image_models.location]??(project?.image_models.location==='qwen'?2:6)));
  const canBuildEnvironment = $derived(!!openingRef && openingRef.people_current!==false && (cutoutMode ? !!openingRef.empty_set : !openingRef.empty_set) && (!hasPeople || !!project?.people_matte) && (project?.reference_mode !== 'scene_and_opening' || !!project.background));
  const readyFlashes = $derived(flashList.filter((f) => f.result_current).length);
  const approvedElements=$derived(project?approvedEffectCount(project):0);
  const batchPlan=$derived(project?effectBatchPlan(project):[]);
  const batchNeedsGeneration=$derived(batchPlan.some(item=>!item.ready));
  const allElementsApproved=$derived(!!project&&approvedElements===project.effects.length);
  const currentFinal = $derived(finals.find((r) => r.status==='done'&&r.source_current));
  const pipeline = $derived(project?.renders.find(r => r.kind === 'pipeline' && !['done','error'].includes(r.status)));
  const latestTake = $derived(project?.renders.find(r => r.kind === 'effect' && r.status === 'done' && r.source_current));
  const activeReview=$derived(step===2?project?.renders.find(r=>r.id===reviewResultId&&r.status==='done'&&r.source_current!==false):undefined);
  const displayedTake = $derived(activeReview ?? currentFinal ?? latestTake ?? project?.renders.find(r=>r.kind==='effect'&&r.status==='done'));
  const layerExportTake = $derived(displayedTake?.source_current===false?undefined:displayedTake);
  const finishPresentation=$derived(finishTakePresentation(displayedTake,elementMode,allElementsApproved));
  // A result can disappear after an edit. Keep the placement view usable even
  // when a navigation path or stale saved state still requests Result/Wipe.
  const viewMode = $derived(step===2 && !displayedTake && ['result','wipe'].includes(compareMode) ? 'preview' : compareMode);
  const comparisonMode = $derived(viewMode==='preview'?'result':viewMode);
  const showingVideo = $derived(!!displayedTake && ((step===2 && ['result','wipe'].includes(viewMode)) || step===3) && viewMode!=='source');
  const pendingFlashCount = $derived(project?.effects.length && !project.renders.some(r=>r.kind==='effect'&&r.status==='done'&&r.source_current&&!r.review_only) ? flashList.length : flashTodo.length);
  const allowanceBlocked = $derived(pendingFlashCount > 0 && (!flashCost || (!flashCost.unlimited && (flashCost.allowance_left == null || pendingFlashCount * flashCost.per_flash > flashCost.allowance_left))));
  const comparisonFrame = $derived(step===1&&locationTab==='people'?frame:step === 1 && locationTab === 'objects' ? dressingSource?.frame ?? 0 : step === 1 && project?.mode === 'greenscreen' && (locPreview ?? project?.opening_reference ?? project?.background) ? project?.locations?.find(l => l.file === (locPreview ?? project?.opening_reference ?? project?.background))?.frame ?? 0 : frame);
  const envRender = $derived(project?.renders.find((r) => r.kind === 'environment') ?? null);
  const kf = $derived(project && Object.keys(presets).length ? keyFrames(project.effects, presets, project.frames, project.settings.guide_mode ?? 'sparse') : []);

  /** Anchor list for an effect (older projects: derive one from the first stamped key). */
  function anchorsOf(e: Effect) {
    if (e.anchors?.length) return e.anchors;
    const k = [...e.keys].sort((a, b) => a.frame - b.frame).find((q) => q.x != null);
    return k ? [{ frame: k.frame, x: k.x!, y: k.y! }] : [{ frame: 0, x: 0.5, y: 0.5 }];
  }
  /** Tracked position per frame for every effect (recomputed when anchors move; the tracker caches each anchor's track). */
  const paths = $derived.by(() => {
    const m = new Map<string, Float32Array>();
    if (!project) return m;
    for (const e of project.effects) {
      const as = anchorsOf(e);
      const cameraDefault=isElementProject(project)||!!project.settings.collider&&['smoke_white','smoke_plume'].includes(e.preset);
      m.set(e.id,effectTrackingPath(as,project.frames,e.tracking,tracker,cameraDefault,W,H));
    }
    return m;
  });

  onMount(() => {
    api.connection().then(value => { connection.set(value); })
      .catch(() => { message='Could not read Comfy settings. Reload to try again.'; })
      .finally(() => connectionLoading=false);
    (async () => {
      const list = await api.presets();
      presets = Object.fromEntries(list.map((p) => [p.id, p]));
      for (const p of list) for (const ph of p.phases) if (ph.stamp && ph.stamp !== 'hole') image(api.stampUrl(p.id, ph.stamp));
      projectList = await api.projects();
      const want = location.hash.slice(1) || projectList[0]?.id;
      if (want) await open(want);
    })();
    const poll = setInterval(async () => {
      if (!project || !(running || analysing || flashing)) return;
      try {
      const polledId = project.id, fresh = await api.project(polledId);
      if (project?.id !== polledId) return;
      if (flashing) { project.flashes = fresh.flashes; if (!fresh.flashes?.some((f) => f.status === 'queued' || f.status === 'generating')) message = 'Flashes ready — scrub to a flash frame to see it'; }
      const completed = fresh.renders.filter((r) => r.status === 'done' && project!.renders.find((o) => o.id === r.id && o.status !== 'done'));
      const finished = completed.find(r => r.kind !== 'analysis') ?? completed[0];
      const envChanged = fresh.environment !== project.environment;
      project.renders = fresh.renders; project.environment = fresh.environment; loadStages();
      if (envChanged) loadTrack();
      project.flashes = fresh.flashes; project.flash_base = fresh.flash_base; project.image_models = fresh.image_models;
      project.environment_ready = fresh.environment_ready; project.approved_environment = fresh.approved_environment;
      project.people_matte = fresh.people_matte; project.render_timing = fresh.render_timing;
      project.geometry=fresh.geometry;project.element_takes=fresh.element_takes;project.element_approvals=fresh.element_approvals;project.people_alpha_takes=fresh.people_alpha_takes;
      if (fresh.locations?.length !== project.locations?.length) { project.locations = fresh.locations; locMix = 1; }
      if (finished) {
        message = finished.kind === 'environment' ? 'Environment candidate ready — review and approve before adding effects' : finished.kind === 'keys' ? 'AI keys ready' : 'Shot generated';
        if (finished.kind === 'pipeline') { step = 3; compareMode='result'; message = 'Take ready. Effects, muzzle frames and the final splice are complete.'; }
        else if (finished.kind === 'dressing') {
          const candidate = fresh.locations?.find(l => l.id === finished.id);
          if (candidate && candidate.parent_source === project.set_dressing?.source) {
            dressingResult = candidate.file; dressingMix = 1; dressingBoxes = false; locationTab = 'objects';
          }
          message = 'Objects ready — compare the source and result, then select the still for your environment';
        } else if (finished.kind === 'environment') showResult = finished;
        else if (finished.kind === 'final') { step = 3; compareMode='result'; }
        else if (finished.kind === 'keys') showResult = finished;
        else if (finished.kind === 'analysis') message = 'Action draft ready. Review it in Scene before using it.';
        else if (finished.kind === 'matte') message = 'People matte ready. Scrub the clip to check the selection.';
        else if (finished.kind === 'people_alpha') {stop();message='AlphaGen foreground matte ready. Review every frame and approve its optical holdout before use.';}
        else if (finished.kind === 'geometry') message = finished.geometry_kind==='depth'?'Depth pass ready. Check the Interaction preview before generating an element.':'Collider matte ready. Check its assignment in Interaction.';
        else if (finished.kind === 'element') {stop();message='Element RGB and alpha ready. Review and approve the version before finishing.';}
        else if (finished.kind === 'location') {
          const candidate = fresh.locations?.find(l => l.id === finished.id);
          if(candidate) previewLocation(candidate.file);
          message = 'Location ready. Compare with your plate, then choose Use still.';
        } else { compareMode='result'; stop(); message = 'Effects video ready in Result. Preview keeps your live placement.'; }
      }
      } catch(e) { message = 'Could not refresh processing status. Your Cloud job may still be running; reconnect before starting another render. '+String(e); }
    }, 3000);
    // dev/review hook: drive the view from scripts (set a frame, read state)
    (window as any).__keyfx = { setFrame: (f: number) => { stop(); frame = f; }, get project() { return project; }, get tracker() { return tracker; },
      select: (id: string | null) => (selectedId = id), save, generate,
      // render the live view at these frames and save them under data/projects/<id>/snaps/<prefix>_<f>.png (review aid)
      snaps: async (prefix: string, frames: number[]) => {
        if (!project) return; const c = (window as any).__keyfxCanvas as HTMLCanvasElement;
        for (const f of frames) { stop(); frame = f; await tick(); await tick();
          const b = await new Promise<Blob>((r) => c.toBlob((x) => r(x!), 'image/png'));
          await fetch(`/api/projects/${project.id}/snap/${prefix}_${f}`, { method: 'POST', body: b }); }
      },
      open,
      bench: (n = 40) => { // ms per frame to render every effect's mock-up layers
        if (!project) return 0; const a = document.createElement('canvas'), b = document.createElement('canvas'); a.width = b.width = W; a.height = b.height = H;
        const t = performance.now(); for (let f = 0; f < n; f++) renderLayers(a, b, project.effects, presets, f % project.frames, project.frames, paths, () => {});
        return (performance.now() - t) / n;
      } };
    const onHash = () => { const id = location.hash.slice(1); if (id && id !== project?.id) open(id); };
    window.addEventListener('hashchange', onHash);
    return () => { clearInterval(poll); clearTimeout(dressingTimer); window.removeEventListener('hashchange', onHash); cancelAnimationFrame(raf); };
  });

  async function loadStages() {
    if (!project) return;
    const id = project.id, r = await api.stages(id);
    if (project?.id === id) stageList = r.stages;
  }
  async function refreshCost() { if (project) flashCost = await api.flashCost(project.id, 1); }
  async function saveFlashes(strict = false) {
    if (!project || pipeline) return;
    const id = project.id;
    const edits = JSON.parse(JSON.stringify(project.flashes ?? [])) as Flash[];
    const base = flashBase;
    const write = flashWrite.catch(() => {}).then(() => api.patch(id, { flashes: edits, flash_base: base } as Partial<Project>));
    flashWrite = write;
    try {
      const fresh = await write;
      if (project?.id === id) {
        // Keep live objects intact: another drag may already hold one of them.
        const placement = (f: Flash) => JSON.stringify([f.frame, f.origin, f.tip]);
        for (const f of project.flashes ?? []) {
          const sent = edits.find(e => e.id === f.id), saved = fresh.flashes?.find(e => e.id === f.id);
          if (sent && saved && placement(f) === placement(sent)) Object.assign(f, saved);
        }
        project.flash_base = fresh.flash_base;
      }
    } catch (e) { message = String(e); if(strict) throw e; }
  }
  function deleteFlash(id: string) {
    if (!project) return;
    project.flashes = (project.flashes ?? []).filter((f) => f.id !== id); if (flashSel === id) flashSel = null; saveFlashes();
  }
  function selectFlash(id:string){
    const flash=project?.flashes?.find(f=>f.id===id);
    if(!flash)return;
    stop();compareMode='preview';effectTool='muzzle';armed=null;flashSel=id;frame=flash.frame;
  }
  function flashSize(f: Flash, size: keyof typeof SIZE_PX) {
    // keep the direction, set the flame length in muzzle_hub's 1280x720 working pixels
    const dx = (f.tip.x - f.origin.x) * 1280, dy = (f.tip.y - f.origin.y) * 720, L = Math.hypot(dx, dy) || 1, k = SIZE_PX[size] / L;
    f.tip = { x: Math.min(1, Math.max(0, f.origin.x + (dx * k) / 1280)), y: Math.min(1, Math.max(0, f.origin.y + (dy * k) / 720)) };
    f.status = 'new'; f.result_current = false; saveFlashes();
  }
  const flashLen = (f: Flash) => Math.round(Math.hypot((f.tip.x - f.origin.x) * 1280, (f.tip.y - f.origin.y) * 720));
  async function generateFlashes(ids?: string[]) {
    if (!project) return;
    await api.patch(project.id, { flashes: project.flashes, flash_base: flashBase } as Partial<Project>);
    try {
      const r = await api.generateFlashes(project.id, ids);
      project = await api.project(project.id);
      message = `Generating ${r.queued} flash${r.queued === 1 ? '' : 'es'} (about ${r.credits} credits)…`;
    } catch (e) { message = String(e); }
    refreshCost();
  }
  async function exportFinal() {
    if (!project) return;
    busy = 'Building the final shot…';
    try { const r = await api.exportFinal(project.id, omitUnready); project = await api.project(project.id); showResult = project.renders.find((x) => x.id === r.id) ?? r; }
    catch (e) { message = String(e); }
    busy = '';
  }

  async function loadTrack() {
    if (!project) return;
    tracker = null;
    const id = project.id;
    try { const d = await api.track(id); if (project?.id === id) tracker = new Tracker(d); } catch (e) { message = 'Auto-track failed: ' + e; }
  }

  async function setCameraMode(mode:'auto'|'locked'){
    if(!project||busy||running)return;
    const pid=project.id;
    stop();busy='Updating camera motion…';
    try {
      await flushShotSave();
      const fresh=await writeShot(pid,{camera_mode:mode});
      if(project?.id!==pid)return;
      project.camera_mode=fresh.camera_mode;
      adoptElementUpdate(fresh);
      await loadTrack();
      message=mode==='locked'?'Camera locked for this shot. Moving mattes and object tracks still work.':'Automatic camera motion restored.';
    }catch(e){message=e instanceof Error?e.message:String(e);}
    finally{busy='';}
  }

  async function open(id: string) {
    if(openingShot||id===project?.id)return;
    openingShot=true;
    let next: Project;
    try {
      await flushShotSave();
      next=await api.project(id);
    } catch(e) {
      message=`Could not switch shots: ${e instanceof Error?e.message:String(e)}`;
      return;
    } finally { openingShot=false; }
    clearTimeout(dressingTimer);
    stop();
    batchActive=false;batchProgress=null;busy='';localProgress=null;
    message='';simulationDownload=null;
    project = next; location.hash = id;
    previewQuality='draft';
    roomReferenceBusy=false;
    project.location_role ??= 'opening'; project.location_seed ??= 11; project.environment_seed ??= project.settings.seed;
    project.reference_mode ??= 'opening_only'; locPreview = null; locMix = 1; omitUnready = false;
    locationTab = 'room'; dressingResult = null; dressingMix = 0; dressingBoxes = true; dressSel = null;
    frame = 0; selectedId = null; soloPreview = false; armed = null; effectLibraryOpen=false; framesLoaded = 0;reviewResultId=null;
    undoStack = []; redoStack = []; lastSnap = JSON.stringify(project.effects);
    step = project.effects.length || project.flashes?.length ? 2 : 1; effectTool = project.flashes?.length && !project.effects.length ? 'muzzle' : 'effects'; flashSel = null;
    project.people ??= {selection:'auto',mode:'guide',points:[],feather:1,trim:0,extra_reference:false};
    project.people.selection ??= 'auto';
    project.people.extra_reference ??= false;
    compareMode = step===2 ? preferredEffectView(project) : 'result'; refreshCost();
    loadStages();
    loadTrack();
    if(step===2)await preferElementWorkflow();
  }

  /** Write tracked positions and sizes into the keys (what the server and CLI read), then save. */
  function syncKeys() {
    if (!project) return;
    for (const e of project.effects) {
      const pth = paths.get(e.id), p = presets[e.preset]; if (!pth || !p) continue;
      e.anchors = anchorsOf(e);
      for (const k of e.keys) {
        const ph = phaseOf(p, k.phase); if (!ph?.stamp) continue;
        const f = Math.min(project.frames - 1, Math.max(0, k.frame));
        k.x = +(pth[f * 2] / W).toFixed(4); k.y = +(pth[f * 2 + 1] / H).toFixed(4); k.scale = +((ph.scale ?? 0.3) * (e.size ?? 1)).toFixed(4);
      }
    }
  }
  function cancelShotSave() {
    clearTimeout(saveTimer);
    pendingShotSave=undefined;
  }
  // Finish writes for the current shot before swapping the live project object.
  async function flushShotSave() {
    const write=pendingShotSave;
    cancelShotSave();
    if(write)await write();
    else await shotWrite;
  }
  function scheduleShotSave(write:()=>Promise<void>,delay:number) {
    cancelShotSave();
    pendingShotSave=write;
    saveTimer=setTimeout(()=>void flushShotSave().catch(e=>message=String(e)),delay);
  }
  function writeShot(id:string,body:Partial<Project>) {
    const edits=JSON.parse(JSON.stringify(body)) as Partial<Project>;
    const write=(shotWrite??Promise.resolve()).catch(()=>{}).then(()=>api.patch(id,edits));
    shotWrite=write;
    const settled=()=>{if(shotWrite===write)shotWrite=undefined;};
    void write.then(settled,settled);
    return write;
  }
  function save() {
    if (!project || pipeline) return;
    scheduleShotSave(async () => {
      if (!project) return;
      syncKeys(); snapshot();
      const p = project;
      const fresh=await writeShot(p.id, { effects: p.effects, shot: p.shot, location: p.location, settings: p.settings, name: p.name });
      if(project?.id===p.id){project.renders=fresh.renders;project.flashes=fresh.flashes;project.element_takes=fresh.element_takes;project.element_approvals=fresh.element_approvals;project.people_alpha_takes=fresh.people_alpha_takes;loadStages();}
    }, 350);
  }
  function snapshot() {
    if (!project) return;
    const now = JSON.stringify(project.effects);
    if (now !== lastSnap) { undoStack.push(lastSnap); if (undoStack.length > 100) undoStack.shift(); redoStack = []; lastSnap = now; }
  }
  function restore(from: string[], to: string[]) {
    if (!project || !from.length) return;
    cancelShotSave(); syncKeys(); snapshot();
    to.push(JSON.stringify(project.effects));
    const prev = from.pop()!; project.effects = JSON.parse(prev); lastSnap = prev;
    if (selectedId && !project.effects.some((e) => e.id === selectedId)) selectedId = null;
    void writeShot(project.id, { effects: project.effects }).catch(e=>message=String(e));
  }
  const undo = () => restore(undoStack, redoStack);
  const redo = () => restore(redoStack, undoStack);
  function duplicate() {
    if (!project || !selected) return;
    const c: Effect = JSON.parse(JSON.stringify(selected)), n = project.frames;
    const d = Math.min(6, n - 1 - (elementMode?elementOnset(c):Math.max(...c.keys.map((k) => k.frame))));
    c.id = Math.random().toString(16).slice(2, 8);
    c.keys.forEach((k) => (k.frame += d)); (c.anchors ?? []).forEach((a) => (a.frame = Math.min(n - 1, a.frame + d)));
    project.effects = [...project.effects, c]; selectedId = c.id; frame = onsetOf(c, presets[c.preset]); save();
  }
  async function saveNow() {
    if (!project) return;
    cancelShotSave(); syncKeys(); snapshot();
    await writeShot(project.id, { effects: project.effects, shot: project.shot, location: project.location, settings: project.settings,
      dressing: project.dressing, lighting: project.lighting, empty_set: project.empty_set,
      location_role: project.location_role, location_seed: project.location_seed, environment_seed: project.environment_seed,
      reference_mode: project.reference_mode, image_models: project.image_models });
  }

  async function place(x: number, y: number) {
    if (!project || !armed || busy || openingShot) return;
    const pid=project.id,preset=armed,at=frame;
    busy='Placing effect…';
    try {
      await saveNow();
      const fresh=await api.addEffect(pid,preset,at,x,y);
      if(project?.id!==pid)return;
      project=fresh;
      selectedId=fresh.effects.at(-1)!.id;armed=null;
      save();
      await tick();
      const panel=inspectorSection?.querySelector('fieldset');
      const controls=panel?.querySelector('#selected-effect-placement');
      if(panel&&controls&&window.innerWidth>760)panel.scrollTop+=controls.getBoundingClientRect().top-panel.getBoundingClientRect().top;
    } catch(e) { message=e instanceof Error?e.message:String(e); }
    finally { busy=''; }
  }

  function removeSelected() {
    if (!project || !selectedId) return;
    project.effects = project.effects.filter((e) => e.id !== selectedId); selectedId = null; save();
  }
  const canPreroll = (p: Preset) => !!p.phases[p.phases.length - 1]?.persist;
  function setStart(e: Effect, f: number) {
    const p = presets[e.preset], n = project!.frames;
    if(elementMode){const target=Math.min(n-1,Math.max(canPreroll(p)?-PREROLL_MAX:0,f)),delta=target-elementOnset(e);e.keys.forEach(k=>k.frame+=delta);(e.anchors??[]).forEach(a=>a.frame=Math.min(n-1,Math.max(0,a.frame+delta)));frame=Math.max(0,target);save();return;}
    const ks = e.keys.filter((k) => k.phase !== p.phases[p.phases.length - 1].phase || k === e.keys.find((q) => q.phase === k.phase));
    const lo = (canPreroll(p) ? -PREROLL_MAX : 0) - Math.min(...ks.map((k) => k.frame)), hi = n - 1 - Math.max(...ks.map((k) => k.frame));
    const dd = Math.min(hi, Math.max(lo, f - onsetOf(e, p)));
    e.keys.forEach((k) => (k.frame += dd)); (e.anchors ?? []).forEach((a) => (a.frame = Math.min(n - 1, Math.max(0, a.frame + dd))));
    respace(e, p, n);
    frame = Math.max(0, onsetOf(e, p)); save();
  }
  function retimed(e: Effect) { stop();if(elementMode)compareMode='preview';else respace(e, presets[e.preset], project!.frames);save(); }

  /** Render the mock-up at every key frame and send it: the AI gets exactly what was blocked, on the tested key frames. */
  async function generate(kind: 'effect' | 'keys' | 'finish') {
    if (!project) return;
    if(elementMode){if(kind==='finish')await generateElementTake();else if(kind==='effect')await buildElementComposite();else if(selected)await prepareOneElement(selected.id,false);return;}
    stop(); busy = kind === 'keys' ? 'Preparing keys…' : 'Preparing the shot…';
    localProgress={done:0,total:kf.length};
    try {
      await saveNow();
      if(kind === 'finish') await saveFlashes(true);
      const targetId=project.id, effects=JSON.parse(JSON.stringify(project.effects)), frameCount=project.frames;
      const keyframes=keyFrames(effects,presets,frameCount,project.settings.guide_mode ?? 'sparse'), keyPaths=new Map([...paths].map(([id,p])=>[id,new Float32Array(p)]));
      const colliderVersion = colliderKey(project), collider = await loadCollider(project);
      await Promise.all(Object.values(presets).flatMap((p) => p.phases.filter((ph) => ph.stamp && ph.stamp !== 'hole').map((ph) => new Promise<void>((res) => {
        const im = image(api.stampUrl(p.id, ph.stamp!)); if (im.complete) res(); else { im.onload = () => res(); im.onerror = () => res(); }
      }))));
      const fxC = document.createElement('canvas'), glowC = document.createElement('canvas');
      fxC.width = glowC.width = project.render_format?.width ?? 1920;
      fxC.height = glowC.height = project.render_format?.height ?? 1080;
      const layers: { frame: number; fx: Blob; glow: Blob }[] = [];
      for (const k of keyframes) {
        await tick(); await new Promise(requestAnimationFrame);
        if(project?.id!==targetId || colliderKey(project)!==colliderVersion)throw new Error('Shot or collider matte changed. Key preparation stopped before submission.');
        renderLayers(fxC, glowC, effects, presets, k.frame, frameCount, keyPaths, () => {}, 'high', 'keys', collider);
        const [fx, glow] = await Promise.all([fxC, glowC].map((c) => new Promise<Blob>((res) => c.toBlob((b) => res(b!), 'image/png'))));
        layers.push({ frame: k.frame, fx, glow });
        localProgress={done:layers.length,total:keyframes.length};
      }
      if(project?.id!==targetId || colliderKey(project)!==colliderVersion)throw new Error('Shot or collider matte changed before submission.');
      const r = await api.renderMockup(targetId, kind, layers, colliderVersion);
      project.renders = [r, ...project.renders];
      message = kind === 'keys' ? 'Preparing the AI keys…' : kind === 'finish' ? 'Generating your take: effects video, then muzzle frames and final splice.' : 'Generating the effects video…';
    } catch (e) { message = String(e); }
    pruneSquib(new Set(),'keys'); busy = ''; localProgress=null;
  }
  function saveLocation() {
    if (!project) return;
    scheduleShotSave(async () => { if (project) await writeShot(project.id, { dressing: project.dressing, lighting: project.lighting, location: project.location,
      empty_set: project.empty_set, shot: project.shot, location_role: project.location_role, location_seed: project.location_seed,
      environment_seed: project.environment_seed, reference_mode: project.reference_mode } as Partial<Project>); }, 300);
  }
  async function buildLocation() {
    if (!project) return;
    cancelShotSave();
    project.location_frame = project.location_role === 'scene' ? frame : 0;
    if (project.location_role !== 'scene') { stop(); frame = 0; project.empty_set = false; }
    try {
      await savePeople(true);
      await writeShot(project.id, { dressing: project.dressing, lighting: project.lighting, location: project.location, empty_set: project.empty_set,
        location_frame: project.location_frame, location_role: project.location_role, shot: project.shot, location_seed: project.location_seed, image_models: project.image_models } as Partial<Project>);
      const r = await api.render(project.id, 'location'); project.renders = [r, ...project.renders]; message = cutoutMode || project.empty_set ? 'Building the empty location…' : manualPeople ? 'Building the location, then integrating the selected people…' : 'Building the location directly from your original plate…'; }
    catch (e) { message = String(e); }
  }
  async function pickLocation(file: string, role: 'opening' | 'scene') {
    if (!project) return;
    try { await saveDressingNow(); project = await api.patch(project.id, role === 'opening' ? { opening_reference: file } : { background: file }); previewLocation(file); }
    catch (e) { message = String(e); }
  }
  function previewLocation(file: string) {
    if (!project) return;
    const version = project.locations?.find(l => l.file === file);
    if (version?.kind === 'dressing' && version.parent_source === dressingDraft?.source) {
      locationTab = 'objects'; dressingResult = file; dressingMix = 1; dressingBoxes = false; return;
    }
    locationTab = 'room';
    stop(); frame = project.locations?.find((l) => l.file === file)?.frame ?? 0; locPreview = file; locMix = 1;
  }
  async function saveDressingNow() {
    clearTimeout(dressingTimer);
    if (!project?.set_dressing) return;
    const id = project.id, draft = JSON.parse(JSON.stringify(project.set_dressing));
    const write = dressingWrite.catch(() => {}).then(() => api.patch(id, {set_dressing: draft}));
    dressingWrite = write;
    await write;
  }
  function saveDressing() {
    if (pipeline) return;
    dressingMix = 0;
    clearTimeout(dressingTimer);
    dressingTimer = setTimeout(() => saveDressingNow().catch(e => message = String(e)), 350);
  }
  async function approveDressingBase(file: string) {
    if (!project) return;
    try {
      await saveDressingNow(); await saveNow();
      project = await api.dressingSource(project.id, file);
      stop(); locationTab = 'objects'; dressingResult = null; dressingMix = 0; dressingBoxes = true; dressSel = null;
      dressingTool = project.set_dressing?.people && !project.set_dressing.protected.length ? 'protect' : 'object';
      message = 'Location approved for object placement. Protect people and details, then draw and label your objects.';
    } catch(e) { message = String(e); }
  }
  async function buildDressing() {
    if (!project) return;
    busy = 'Preparing object edit…';
    try {
      await saveDressingNow(); await saveModels();
      const r = await api.render(project.id, 'dressing'); project.renders = [r, ...project.renders];
      message = 'Placing objects… your source and protected areas are saved with this version';
    } catch(e) { message = String(e); }
    busy = '';
  }
  function removeDressing(id: string) {
    if (!project?.set_dressing) return;
    project.set_dressing.objects = project.set_dressing.objects.filter(o => o.id !== id);
    project.set_dressing.protected = project.set_dressing.protected.filter(o => o.id !== id);
    if (dressSel === id) dressSel = null;
    saveDressing();
  }
  async function approveEnvironment(r: Render) {
    if (!project) return;
    try {
      project = await api.approveEnvironment(project.id, r.id); showResult = null;
      await loadStages(); await loadTrack(); omitUnready = false;
      message = 'Environment approved. Earlier results are retained and marked outdated where their inputs differ.';
    } catch (e) { message = String(e); }
  }

  async function buildEnvironment() {
    if (!project) return;
    try { await savePeople(true); await saveNow(); const r = await api.render(project.id, 'environment'); project.renders = [r, ...project.renders]; message = 'Building the environment on Comfy…'; }
    catch (e) { message = String(e); }
  }
  async function uploadBackground(f: File | undefined) {
    if (project && f) {
      try { project = await api.background(project.id, f, project.location_role ?? 'opening'); previewLocation(project.locations![0].file); }
      catch (e) { message = String(e); }
    }
  }
  async function togglePrompt() {
    showPrompt = !showPrompt;
    if (showPrompt && project) { await saveNow(); promptText = await api.prompt(project.id); }
  }

  // ---- transport (requestAnimationFrame at 24 fps)
  function play() {
    if (playing) return stop();
    playing = true;
    if(showingVideo)return;
    const clock=new ViewerPlaybackClock(performance.now(),frame,sourceFPS,sourceTimes);
    const loop = (t: number) => {
      if (!playing || !project) return;
      const duration=project.native_profile?.duration??project.frames/sourceFPS;
      const cold=step===2&&viewMode==='preview'&&elementMode&&!!viewerCacheStatus?.fits&&!viewerCacheStatus.warm&&!viewerCacheStatus.error;
      frame=clock.next(t,frame,project.frames,sourceFPS,sourceTimes,duration,cold,viewerCacheStatus?.frame===frame&&viewerCacheStatus.ready);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
  }
  function stop() { playing = false; cancelAnimationFrame(raf); }

  function keydown(ev: KeyboardEvent) {
    if (showNew || showConnection || approvalPreview) return;
    if ((ev.target as HTMLElement | null)?.closest?.('input, textarea, select')) return;
    if (!project) return;
    if (pipeline && ![' ','ArrowRight','ArrowLeft','Home','Escape'].includes(ev.key)) return;
    if (step === 1 && locationTab === 'objects' && (ev.key === 'Delete' || ev.key === 'Backspace') && dressSel) {
      ev.preventDefault(); removeDressing(dressSel); return;
    }
    if (pipeline && (ev.key === 'Delete' || ev.key === 'Backspace' || ev.metaKey || ev.ctrlKey)) return;
    const mod = ev.metaKey || ev.ctrlKey;
    if (mod && ev.key.toLowerCase() === 'z') { ev.preventDefault(); ev.shiftKey ? redo() : undo(); return; }
    if (mod && ev.key.toLowerCase() === 'd') { ev.preventDefault(); duplicate(); return; }
    if (step === 2 && effectTool === 'muzzle' && (ev.key === 'Delete' || ev.key === 'Backspace') && flashSel) { deleteFlash(flashSel); return; }
    if (ev.key === 'Escape' && showNew) { showNew = false; return; }
    if (ev.key === ' ') { ev.preventDefault(); play(); }
    else if (ev.key === 'ArrowRight') { stop(); frame = Math.min(project.frames - 1, frame + (ev.shiftKey ? 10 : 1)); }
    else if (ev.key === 'ArrowLeft') { stop(); frame = Math.max(0, frame - (ev.shiftKey ? 10 : 1)); }
    else if (ev.key === 'Home') frame = 0;
    else if (ev.key === 'Escape') { effectLibraryOpen=false; armed = null; selectedId = null; }
    else if (ev.key === 'Delete' || ev.key === 'Backspace') removeSelected();
  }


  async function saveModels() {
    if (!project) return;
    try {
      const fresh = await api.patch(project.id, { image_models: project.image_models });
      project.flashes = fresh.flashes; project.renders = fresh.renders; project.people_alpha_takes=fresh.people_alpha_takes;
      await loadStages(); await refreshCost();
    } catch (e) { message = String(e); }
  }
  function goScene() { stop(); step = 1; locationTab = 'room'; compareMode='result'; effectLibraryOpen=false; }
  function goStep(next:1|2|3) {
    if(next===2){void goEffects();return;}
    if(next===1){goScene();return;}
    if(next===3&&elementMode&&!allElementsApproved&&!busy&&!running){
      const preview=project?.renders.find(r=>r.kind==='effect'&&r.status==='done'&&r.source_current!==false&&r.render_profile==='native-ltx-elements-v1');
      if(preview){stop();approvalPreview=preview.id;approvalError='';return;}
    }
    stop();step=next;compareMode='result';effectLibraryOpen=false;loadStages();refreshCost();
  }
  async function approvePreviewAndFinish(){
    if(!project||!approvalPreview||approvalBusy)return;
    const pid=project.id,rid=approvalPreview;approvalBusy=true;approvalError='';
    try{
      const fresh=await elementRequest(`/api/projects/${pid}/finishing/approve-preview`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({render_id:rid})});
      if(project?.id!==pid)return;adoptElementUpdate(fresh);approvalPreview=null;step=3;compareMode='result';
      await compositeCurrentElements();message='Current preview effects approved. Finish can now add lighting and build the final take.';
    }catch(e){approvalError=e instanceof Error?e.message:String(e);message=approvalError;}
    finally{approvalBusy=false;busy='';}
  }
  async function runLighting(newVersion=false){
    if(!project||busy||running)return;stop();const pid=project.id;busy='Preparing MiniMax lighting references…';
    try{
      await saveNow();const job=await elementRequest(`/api/projects/${pid}/lighting/run`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({new_version:newVersion})});
      const fresh=await waitForProject(pid,p=>p.renders.some(r=>r.id===job.id&&['done','error'].includes(r.status)),p=>{adoptElementUpdate(p);const status=p.renders.find(r=>r.id===job.id)?.status;busy=status==='rendering'?'MiniMax generating interaction lighting…':status==='extracting'?'Extracting signed light/shadow difference…':'Preparing MiniMax lighting references…';});
      if(project?.id!==pid)return;const result=fresh.renders.find(r=>r.id===job.id);if(result?.status==='error')throw new Error(result.error);
      await compositeCurrentElements();message='MiniMax light/shadow difference added over the original plate; original LTX elements restored above it.';
    }catch(e){message=e instanceof Error?e.message:String(e);}finally{busy='';localProgress=null;}
  }
  async function lightingSetting(body:Record<string,unknown>){
    if(!project||busy||running)return;busy='Updating lighting layer…';
    try{const fresh=await elementRequest(`/api/projects/${project.id}/lighting`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});adoptElementUpdate(fresh);await compositeCurrentElements();}
    catch(e){message=e instanceof Error?e.message:String(e);}finally{busy='';}
  }
  async function chooseEffect(id: string) {
    if(!project||!effectPlaceable(id,project.mode,elementMode)||busy||running)return;
    stop();
    if(placementNeedsElements(id,project.mode,elementMode)&&!project.settings.legacy_h3){
      if(!await adoptWorkingPlate())return;
    }
    selectedId=null;effectTool='effects';armed=armed===id?null:id;compareMode='preview';effectLibraryOpen=false;
    if(window.innerWidth<=760){await tick();previewSection?.scrollIntoView({block:'start',behavior:'smooth'});}
  }
  function reviewElementSimulation(id:string){
    const effect=project?.effects.find(e=>e.id===id);if(!effect)return;
    stop();selectedId=id;effectTool='effects';compareMode='preview';frame=Math.max(0,elementOnset(effect));play();
    if(window.innerWidth<=760)previewSection?.scrollIntoView({block:'start',behavior:'smooth'});
  }
  async function useStill(file: string) {
    const l = project?.locations?.find(l => l.file === file);
    if (l?.frame === 0 && (!l.empty_set || cutoutMode)) await pickLocation(file, 'opening');
    else { message = 'This still uses a later pose. Build an opening reference from frame 0 for the scene video.'; }
  }
  async function generateTake() {
    if (!project) return;
    if(elementMode){await generateElementTake();return;}
    if (project.effects.length) await generate('finish');
    else {
      busy = 'Preparing take…';
      try { await saveNow(); await saveFlashes(true); const r = await api.render(project.id, 'finish'); project.renders = [r, ...project.renders]; message = 'Generating muzzle frames, then splicing the final take.'; }
      catch(e) { message = String(e); }
      busy = '';
    }
  }
  function adoptElementUpdate(fresh:Project){if(project?.id===fresh.id){project.renders=fresh.renders;project.element_takes=fresh.element_takes;project.element_approvals=fresh.element_approvals;project.geometry=fresh.geometry;project.flashes=fresh.flashes;project.people_alpha_takes=fresh.people_alpha_takes;project.lighting_takes=fresh.lighting_takes;project.light_integration=fresh.light_integration;loadStages();}}
  async function elementWork(id:string,render:boolean){
    if(!project)return;
    await saveNow();const pid=project.id;
    let fresh=await api.project(pid);adoptElementUpdate(fresh);
    const candidates=fresh.element_takes?.filter(t=>t.effect_id===id&&t.current&&!t.needs_alpha_core)??[];
    let take=candidates.find(t=>t.status==='done'&&t.approved)??candidates.find(t=>t.status==='done')??candidates.find(t=>t.status==='prepared'&&t.backend_current!==false);
    if(!take){
      busy='Capturing isolated simulation guide…';
      const snapshot=JSON.parse(JSON.stringify(project)) as Project,recipe=JSON.stringify(snapshot.effects);
      take=await prepareElement(snapshot,id,new Map(paths),(done,total)=>localProgress={done,total},()=>project?.id===pid&&JSON.stringify(project.effects)===recipe);
      adoptElementUpdate(await api.project(pid));
    }
    if(!render)return;
    if(take!.status==='done')return;
    busy='LTX element, then AlphaGen…';localProgress=null;await startElement(pid,take!.id);
    fresh=await waitForProject(pid,p=>{const t=p.element_takes?.find(t=>t.id===take!.id);return t?.status==='done'||t?.status==='error';},adoptElementUpdate);
    const finished=fresh.element_takes?.find(t=>t.id===take!.id);if(finished?.status==='error')throw new Error(finished.error??'Element generation failed. Review its status before preparing another version.');
  }
  async function prepareOneElement(id:string,render:boolean){stop();try{await elementWork(id,render);message=render?'Element RGB and alpha ready.':'Guide prepared. Choose Review prepared guide video to check it before generating.';}catch(e){message=String(e);}finally{busy='';localProgress=null;}}
  async function refreshExplosionMatte(effectId:string,takeId:string){
    if(!project||busy||running)return;stop();const pid=project.id;busy='Capturing local explosion opacity…';
    try{
      await saveNow();const snapshot=JSON.parse(JSON.stringify(project)) as Project,key=batchConfiguration(project);
      const fresh=await refreshMatteSupport(snapshot,effectId,takeId,new Map(paths),(done,total)=>localProgress={done,total},()=>project?.id===pid&&batchConfiguration(project)===key);
      if(project?.id!==pid)return;project.effects=fresh.effects;adoptElementUpdate(fresh);compareMode='preview';
      const ready=approvedEffectCount(fresh)===fresh.effects.length;
      if(ready)await compositeCurrentElements();
      message='Explosion matte refreshed from simulated opacity. Raw LTX/AlphaGen and approval pins are retained. No model generation used.';
    }catch(e){message=e instanceof Error?e.message:String(e);}finally{busy='';localProgress=null;}
  }
  async function generateEffectsBatch(){
    if(!project||busy||running||flashing||!project.effects.length)return;
    stop();armed=null;soloPreview=false;batchActive=true;busy='Preparing LTX effects…';message='';
    const pid=project.id;
    try{
      await saveNow();const fresh=await api.project(pid);
      if(project?.id!==pid)throw new Error('The shot changed before the batch started.');
      project.effects=fresh.effects;project.settings=fresh.settings;adoptElementUpdate(fresh);
      const key=batchConfiguration(fresh);
      const result=await runEffectBatch(fresh,new Map([...paths].map(([id,path])=>[id,new Float32Array(path)])),{
        valid:()=>batchActive&&project?.id===pid&&batchConfiguration(project)===key,
        onUpdate:adoptElementUpdate,
        onProgress:value=>{if(project?.id!==pid)return;batchProgress=value;const phase=elementTakeStatus(value.stage);busy=value.effectId?`${value.index} / ${value.total} · ${value.label} · ${phase}`:phase;localProgress=value.frames?{done:value.done??0,total:value.frames}:null;},
      },{project:api.project,prepare:prepareElement,start:startBatchElement,wait:waitForProject,composite:reviewCompositeElements});
      if(project?.id!==pid)return;
      reviewResultId=result.render.id;compareMode='result';step=2;selectedId=null;
      message=`Review composite ready · ${result.generated} generated, ${result.reused} reused. Approve individual elements when you are happy with them.`;
    }catch(e){if(project?.id===pid){message=e instanceof Error?e.message:String(e);try{adoptElementUpdate(await api.project(pid));}catch{/* Keep the stopped queue visible if the local service is unreachable. */}}}
    finally{if(project?.id===pid){batchActive=false;batchProgress=null;busy='';localProgress=null;}}
  }
  async function downloadPreview(){
    if(!project||busy||running)return;
    stop();busy='Preparing simulation preview…';const controller=new AbortController();previewAbort=controller;
    try{
      await saveNow();await saveFlashes(true);
      const snapshot=JSON.parse(JSON.stringify(project)) as Project,recipe=previewRecipe(project),quality=previewQuality;
      const output=await downloadSimulationPreview(snapshot,new Map([...paths].map(([id,path])=>[id,new Float32Array(path)])),quality,
        (done,total)=>{if(done===total){busy='Encoding simulation preview…';localProgress=null;}else{busy='Capturing simulation preview…';localProgress={done,total};}},
        ()=>!!project&&previewRecipe(project)===recipe,controller.signal);
      simulationDownload={url:output.url,recipe,quality};
      const link=document.createElement('a');link.href=output.url;link.download=output.filename;document.body.append(link);link.click();link.remove();
      message='Simulation preview ready: plate and all FX, without handles. No model generation was used.';
    }catch(e){message=controller.signal.aborted?'Simulation preview cancelled.':e instanceof Error?e.message:String(e);}
    finally{busy='';localProgress=null;previewAbort=null;}
  }
  async function rerunOneElement(id:string){
    if(!project||busy||running)return;stop();busy='Preparing a new element version…';
    const pid=project.id;
    try{
      await saveNow();await api.newElementVersion(pid,id);
      const fresh=await api.project(pid);if(project?.id!==pid)return;
      project.effects=fresh.effects;project.settings=fresh.settings;adoptElementUpdate(fresh);
      await elementWork(id,true);message='New element version ready. Review it before approving.';
    }catch(e){message=String(e);}finally{busy='';localProgress=null;}
  }
  async function compositeCurrentElements(){
    if(!project)return;busy='Compositing onto native photography…';
    const pid=project.id,r=await compositeElements(pid);
    const fresh=await waitForProject(pid,p=>p.renders.some(t=>t.id===r.id&&['done','error'].includes(t.status)),adoptElementUpdate);
    const result=fresh.renders.find(t=>t.id===r.id)!;if(result.status==='error')throw new Error(result.error);reviewResultId=null;compareMode='result';
  }
  async function buildElementComposite(){try{await saveNow();await compositeCurrentElements();message='Native plate composite ready.';}catch(e){message=String(e);}finally{busy='';}}
  async function generateElementTake(){
    if(!project)return;stop();
    try{
      busy='Checking approved elements…';await saveNow();await saveFlashes(true);
      const fresh=await api.project(project.id);adoptElementUpdate(fresh);
      if(approvedEffectCount(fresh)!==fresh.effects.length)throw new Error('Approve a current version of every effect in Effects before building the final take.');
      if(fresh.effects.length)await compositeCurrentElements();
      busy='Preparing muzzle frames and native final…';const r=await api.render(project.id,'finish');project.renders=[r,...project.renders];message='Finishing approved effects, muzzle frames and the native master.';
    }
    catch(e){message=String(e);}finally{busy='';localProgress=null;}
  }
  async function adoptWorkingPlate(){
    if(!project||project.mode!=='plate'||busy||running)return false;
    const pid=project.id;busy='Preparing LTX placement…';
    try{
      await saveNow();const fresh=await api.patch(pid,{settings:{...project.settings,pipeline:'ltx_elements',legacy_h3:false,polish:false}});
      if(project?.id!==pid)return false;
      project=fresh;previewQuality='draft';await loadStages();
      message=project.native_profile?'LTX elements enabled. Effects are generated separately over your original photography.':'LTX elements enabled on this working plate. Reimport the original source to retain its native format.';
      return true;
    }catch(e){message=String(e);return false;}finally{busy='';}
  }
  async function preferElementWorkflow(){
    if(project?.mode==='plate'&&!elementMode&&!project.settings.legacy_h3)await adoptWorkingPlate();
  }
  async function useLegacyWorkflow(){
    if(!project||project.native_profile||busy||running)return;
    const pid=project.id;busy='Selecting legacy H3…';
    try{
      await saveNow();const fresh=await api.patch(pid,{settings:{...project.settings,pipeline:'h3',legacy_h3:true}});
      if(project?.id!==pid)return;project=fresh;armed=null;selectedId=null;effectLibraryOpen=false;compareMode=preferredEffectView(fresh);
      message='Legacy H3 selected. This workflow regenerates the shot; LTX elements keeps your original photography.';
    }catch(e){message=String(e);}finally{busy='';}
  }
  async function goEffects() { step = 2; stop(); compareMode=project?preferredEffectView(project):'preview';effectLibraryOpen=false;await preferElementWorkflow();loadStages();refreshCost(); }

  async function savePeople(strict=false) {
    if(!project)return;
    const id=project.id;
    const cfg=JSON.parse(JSON.stringify(project.people));
    const write=peopleWrite.catch(()=>{}).then(()=>api.patch(id,{people:cfg}));peopleWrite=write;
    try { const fresh=await write; if(project?.id===id){project.people_matte=fresh.people_matte;project.environment_ready=fresh.environment_ready;project.locations=fresh.locations;} }
    catch(e){message=String(e);if(strict)throw e;}
  }
  function setPeopleSelection(value:'auto'|'manual') {
    if(!project)return;
    project.people.selection=value;
    if(value==='auto'){project.people.mode='guide';project.people.extra_reference=false;}
    savePeople();
  }
  async function buildPeople() {
    if(!project)return;
    stop(); busy='Preparing the people selection…';
    try { await savePeople(true); const r=await api.render(project.id,'matte');project.renders=[r,...project.renders]; }
    catch(e){message=String(e);} finally{busy='';}
  }
  async function changeScene() {
    if(!project)return;
    try {await savePeople(true);project=await api.patch(project.id,{mode:'greenscreen'});locationTab='room';compareMode='result';}
    catch(e){message=String(e);}
  }
  function viewGenerated(r:Render) {
    if(r.kind==='pipeline') {const final=project?.renders.find(v=>v.id===r.final_id);if(final)viewGenerated(final);return;}
    stop(); if(r.kind==='effect'&&r.id===displayedTake?.id){step=2;compareMode='result';}else if(r.kind==='final'&&r.id===currentFinal?.id){step=3;compareMode='result';}else showResult=r;
  }

  const statusChip = (s: string) => (s === 'done' ? 'done' : s === 'error' ? 'error' : 'run');
  const arrow = (a: number) => ['↑', '↗', '→', '↘', '↓', '↙', '←', '↖'][Math.round(((a % 360) + 360) % 360 / 45) % 8];
  const strengthWord = (kind: string, s: number) => kind === 'wind' ? (s < 0.15 ? 'still air' : s < 0.45 ? 'light breeze' : s < 0.75 ? 'windy' : 'strong wind') : s < 0.35 ? 'wide' : s < 0.7 ? 'aimed' : 'tight';
</script>

<svelte:window onkeydown={keydown} />

<header class="app-header">
  <Brand />
  {#if project}
    <nav class="steps" aria-label="Workflow stages">
      {#each [[1, 'Scene'], [2, 'Effects'], [3, 'Finish']] as [n, label]}
        <button class:on={step === n} aria-current={step === n ? 'step' : undefined} onclick={() => goStep(n as 1|2|3)}><span class="step-number">{n}</span>{label}</button>
      {/each}
    </nav>
  {/if}
  <div class="project-actions">
    <button class="ghost connection-button" class:verified={!!$connection?.configured && !!$connection?.report?.connected}
      disabled={!!busy || connectionLoading} onclick={() => {stop();showConnection=true;}} title={$connection?.configured ? `${$connection.mode==='cloud'?'Comfy Cloud':'Local ComfyUI'} · ${$connection.report?.connected?'last connection test passed':'test connection'}` : 'Connect your Comfy account or local installation'}>
      <span class="connection-dot" aria-hidden="true"></span>{$connection?.configured?$connection.mode==='cloud'?'Comfy Cloud':'Local ComfyUI':'Connect Comfy'}</button>
    {#if projectList.length}<select aria-label="Current shot" disabled={!!busy||openingShot} value={project?.id ?? ''} onchange={async(e) => {const select=e.currentTarget;await open(select.value);select.value=project?.id??'';}}>
      {#each projectList as p}<option value={p.id}>{p.name}</option>{/each}
    </select>{/if}
    <button class="ghost" disabled={!!busy||openingShot} onclick={() => (showNew = true)}>New shot</button>
  </div>
</header>

{#if project}<ProcessingBanner {project} {busy} {localProgress} onresult={viewGenerated}/>{/if}

{#if project?.bundled_example}
  <details class="bundled-example" style="margin:12px 24px;padding:12px 16px;border:1px solid var(--line);border-radius:8px">
    <summary>Included example · editable setup and saved reference results</summary>
    <p class="small muted">{project.bundled_example.description} Opening and previewing is local. New Comfy generation uses your connection and credits.</p>
    <div style="display:flex;gap:12px;flex-wrap:wrap">
      {#each project.bundled_example.references as reference}
        <figure style="margin:0;flex:1;min-width:260px;max-width:640px">
          <figcaption class="small">{reference.label} · archived reference</figcaption>
          <video style="width:100%" controls preload="none" src={`/api/projects/${project.id}/example-media/${reference.id}`}><track kind="captions" /></video>
        </figure>
      {/each}
    </div>
  </details>
{/if}

{#if !project}
  <div class="empty">
    <img class="welcome-mark" src={brandMark} width="88" height="88" alt="" />
    <span class="eyebrow">YOUR SHOT. CALL THE CUES.</span>
    <h1>Big effects.<br /><span>Right on cue.</span></h1>
    <p class="muted">Set the scene. Cue the chaos. Place explosions and muzzle flashes on your footage, play them together, then generate your take.</p>
    <button class="primary welcome-cta" onclick={() => (showNew = true)}>Bring your footage</button>
    <span class="welcome-note">Place it. Time it. Make it Bang On.</span>
  </div>
{:else}
  <main>
    <section bind:this={previewSection} class="center" aria-label="Shot preview">
      <div class="preview-head">
        <div><span class="eyebrow">{step === 1 ? 'SCENE' : step === 2 ? 'EFFECTS' : finishPresentation.heading}</span><span class="source-context">{project.mode === 'greenscreen' ? envReady ? 'Approved scene' : 'Scene needs review' : 'Original footage'}</span>{#if step===2}<button class="effect-controls-shortcut ghost" onclick={()=>inspectorSection?.scrollIntoView({block:'start',behavior:'smooth'})}>Effect controls ↓</button>{/if}</div>
        <div class="compare-tools" role="group" aria-label="Compare with source plate">
          {#each (step===2 ? [['source','Source'],['preview','Preview'],['result','Result'],['wipe','Wipe']] : [['result', 'Result'], ['source', 'Source plate'], ['wipe', 'Wipe']]) as [value, label]}
            <button class:active={viewMode === value} aria-pressed={viewMode === value} disabled={step===2&&['result','wipe'].includes(value)&&!displayedTake} onclick={() => {stop();compareMode = value as typeof compareMode;}}>{label}</button>
          {/each}
        </div>
      </div>
      {#if step===2&&elementMode&&viewMode==='preview'&&effectTool==='effects'&&project.effects.length>1}
        <div class="preview-visibility" role="group" aria-label="Simulation preview visibility">
          <div class="compare-tools">
            <button class:active={!soloEffectId} aria-pressed={!soloEffectId} onclick={()=>soloPreview=false}>All FX</button>
            <button class:active={!!soloEffectId} aria-label="Solo selected effect" aria-pressed={!!soloEffectId} disabled={!selected} onclick={()=>soloPreview=!soloPreview}>Solo selected</button>
          </div>
          <span class="small muted" role="status">{soloEffectId ? `${presets[selected!.preset]?.name??selected!.preset} only` : 'All FX'} · preview only; downloads include all FX</span>
        </div>
      {/if}
      {#if step===2&&['result','wipe'].includes(viewMode)&&displayedTake?.source_current===false}<p class="small muted">This result uses earlier settings. Preview shows your current placement.</p>{/if}
      {#if step===3&&viewMode!=='source'&&finishPresentation.notice}<p class="small muted" role="status">{finishPresentation.notice}</p>{/if}
      {#if viewMode === 'wipe'}<label class="wipe-control">Source / result <input type="range" min="0" max="100" bind:value={compareSplit} aria-label="Source plate comparison wipe" /></label>{/if}
      <div class="preview-body" style:--source-aspect={sourceAspect} class:locked={!!pipeline||!!busy||openingShot}>
        <ComparePlate aspectRatio={sourceAspect} projectId={project.id} frame={comparisonFrame} mode={step === 1 && (locationTab==='people' || locationTab === 'objects' && !!dressingDraft?.source) ? 'result' : comparisonMode} split={compareSplit}>
          {#if step===1 && locationTab==='people'}
            <PeopleView {project} {frame} compareMode={comparisonMode} {compareSplit} disabled={running||!!busy} onchange={()=>savePeople()}/>
          {:else if step===2 && ['result','wipe'].includes(viewMode) && displayedTake}
            <TakePreview pictureWidth={project.native_profile?.width} pictureHeight={project.native_profile?.height} fps={sourceFPS} timestamps={sourceTimes} aspectRatio={sourceAspect} src={api.renderUrl(project.id,displayedTake.id,'result.mp4')} bind:frame bind:playing frames={project.frames} controls={false}/>
          {:else if step === 1 && project.mode === 'greenscreen'}
            {#if locationTab === 'objects' && dressingDraft?.source}
              <DressingView projectId={project.id} draft={dressingDraft} bind:selected={dressSel} bind:tool={dressingTool} result={dressingResult} bind:mix={dressingMix} bind:showBoxes={dressingBoxes} compareMode={comparisonMode} {compareSplit} sourceFrame={dressingSource?.frame ?? 0} onchange={saveDressing}/>
            {:else}<LocationView {project} {frame} overlay={locPreview ?? project.opening_reference ?? project.background} mix={locMix}/>{/if}
          {:else if step === 2 && effectTool === 'muzzle'}
            <FlashView fps={sourceFPS} timestamps={sourceTimes} aspectRatio={sourceAspect} {project} base={sceneBase} {presets} {paths} quality={previewQuality} showHandles={!playing&&!busy&&!running} bind:frame bind:selectedId={flashSel} onchange={saveFlashes} onerror={(text) => message=text}/>
          {:else if step === 3 && displayedTake}
            <TakePreview pictureWidth={project.native_profile?.width} pictureHeight={project.native_profile?.height} fps={sourceFPS} timestamps={sourceTimes} aspectRatio={sourceAspect} src={api.renderUrl(project.id, displayedTake.id, 'result.mp4')} bind:frame bind:playing frames={project.frames} controls={false}/>
          {:else}
            <Viewer project={step === 2 ? project : plateOnly!} {presets} {frame} {selectedId} soloEffectId={step===2&&viewMode==='preview'?soloEffectId:null} armed={step === 2&&!busy&&!running ? armed : null} {tracker} {paths} quality={previewQuality} showHandles={step === 2 && !playing&&!busy&&!running} caption={step === 3 ? "Review the scene and generate your take in Effects" : step === 1 ? "Your original source footage" : null} onplace={place} onchange={save} onselect={(id) => selectedId = id} onframes={(n) => framesLoaded = n} oncache={viewerCacheUpdated}/>
          {/if}
        </ComparePlate>
      </div>
      {#if step===2&&elementMode}
        <div class="simulation-download">
          <label>Preview quality <select bind:value={previewQuality} onchange={stop}><option value="draft">Fast · interactive</option><option value="high">High · full simulation</option></select></label>
          <button disabled={!!busy||running} onclick={downloadPreview}>Download simulation preview · MP4</button>
          {#if previewAbort}<button onclick={()=>previewAbort?.abort()}>Cancel preview export</button>{/if}
          {#if simulationDownload&&simulationDownload.recipe===previewRecipe(project)&&simulationDownload.quality===previewQuality}<a href={simulationDownload.url} download>Download again</a>{/if}
          <span class="small muted">All FX over the plate · {previewQuality==='high'?'High':'Fast'} · SDR · 24fps · no handles or model spend</span>
        </div>
      {/if}
      {#if !(step === 1 && locationTab === 'objects' && dressingDraft?.source)}
        <Timeline disabled={running||!!busy} clipDuration={project.native_profile?.duration} {elementMode} fps={sourceFPS} timestamps={sourceTimes} frames={project.frames} {frame} effects={step === 2 ? project.effects : []} {presets} selectedId={step === 2 && effectTool==='effects' ? selectedId : null} {playing}
          guideMode={project.settings.guide_mode ?? 'sparse'}
          keyFrames={step === 2 ? kf : []} flashes={step === 2 ? flashList : []} selectedFlashId={step===2&&effectTool==='muzzle'?flashSel:null} onselectflash={selectFlash}
          onseek={(f) => { stop(); frame = Math.min(project!.frames-1, Math.max(0,f)); }} onselect={(id) => {selectedId=id;if(id)effectTool='effects';}} onchange={retimed} onplay={play}/>
      {/if}
      {#if step === 1 && project.mode === 'greenscreen'}
        <div class="takes-head"><h2>Scene stills</h2><span class="small muted">Review a still, then use it for the video.</span></div>
        <div class="filmstrip">
          {#each project.locations ?? [] as l (l.id)}
            <div class="take-card" class:chosen={project.opening_reference === l.file}>
              <button class="take-image" onclick={() => previewLocation(l.file)} aria-label={`Preview ${l.model ?? 'scene'} still ${l.id}`}>
                <img src={`/api/projects/${project.id}/background?file=${encodeURIComponent(l.file)}`} alt="Scene still"/>
                <span>{l.kind === 'dressing' ? 'Object edit' : l.id.startsWith('upload') ? 'Uploaded' : 'Location'} · {l.frame == null ? 'scene only' : `frame ${l.frame}`}{project.opening_reference === l.file ? ' · selected' : ''}</span>
              </button>
              <div class="take-actions"><button disabled={running || l.people_current===false || l.frame !== 0 || (!!l.empty_set&&!cutoutMode)} title={l.people_current===false ? 'Rebuild this opening for the current people selection' : l.frame !== 0 ? "Scene video needs a first-frame reference" : "Use this still as the opening reference"} onclick={() => useStill(l.file)}>Use still</button><button disabled={running} onclick={() => approveDressingBase(l.file)}>Place objects</button></div>
            </div>
          {:else}<p class="small muted">Your location stills will appear here.</p>{/each}
        </div>
      {:else if step === 2}
        <div class="takes-head"><h2>On this shot</h2><span class="small muted">{project.effects.length} effects · {flashList.length} muzzle frames</span></div>
        <div class="shot-items">
          {#each project.effects as e}<button class:active={selectedId === e.id && effectTool === 'effects'} onclick={() => {stop();compareMode='preview';effectTool='effects';selectedId=e.id;frame=Math.max(0,onsetOf(e,presets[e.preset]));}}>{presets[e.preset]?.name} <span>{onsetOf(e,presets[e.preset])}f</span></button>{/each}
          {#each flashList as f}<button class:active={flashSel === f.id && effectTool === 'muzzle'} onclick={() => selectFlash(f.id)}>Muzzle flash <span>{f.frame}f</span></button>{/each}
          {#if !project.effects.length && !flashList.length}<p class="small muted">Choose an effect, then place it on the shot.</p>{/if}
        </div>
      {/if}
      {#if message}<div class="msg" role="status"><span>{message}</span><button class="ghost" aria-label="Dismiss message" onclick={() => message = ''}>Dismiss</button></div>{/if}
    </section>

    <aside bind:this={inspectorSection} class="inspector" aria-label="Shot controls">
      <div class="panel-title"><strong>{step === 1 ? 'Set the scene' : step === 2 ? 'Direct the effects' : 'Review your take'}</strong><span class="small muted">{(project.native_profile?.duration??project.frames/24).toFixed(2)}s</span></div>
      <fieldset disabled={!!pipeline||!!busy||roomReferenceBusy||openingShot}>
      {#if step === 1}
        <div class="subtabs" role="group" aria-label="Scene tools"><button class:active={locationTab === 'room'} onclick={() => locationTab = 'room'}>{project.mode==='greenscreen'?'Location':'Footage'}</button>{#if project.mode==='greenscreen'}<button class:active={locationTab === 'objects'} onclick={() => locationTab = 'objects'}>Objects</button>{/if}<button class:active={locationTab==='people'} onclick={()=>{stop();locationTab='people';compareMode='result';frame=0;}}>People</button></div>
        {#if locationTab==='people'}
          <div class="block"><h2>People</h2>
            <label class="check"><input type="checkbox" checked={manualPeople} disabled={running} onchange={e=>setPeopleSelection(e.currentTarget.checked?'manual':'auto')}/> Use a people matte · optional</label>
            {#if manualPeople}
            <p class="small muted">Select only the people you want to keep. Use this when crew or other people should be excluded.</p>
            <label>Use people as <select bind:value={project.people.mode} onchange={()=>savePeople()}><option value="guide">Relight · keep source detail in opening</option><option value="cutout">Original cutout · preserve source pixels</option></select></label>
            <p class="small muted">{project.people.mode==='guide'?'The opening retains your original people and transfers broad scene lighting onto them. Your full source clip supplies movement and camera. H3 can still drift in the video.':'The original people are composited over an empty scene. Their source lighting is retained.'}</p>
            <label>Edge softness <input type="range" min="0" max="8" step=".5" bind:value={project.people.feather} onchange={()=>savePeople()}/></label>
            <label>Trim edge <input type="range" min="-5" max="5" step="1" bind:value={project.people.trim} onchange={()=>savePeople()}/></label>
            <div class="row"><button disabled={running||!project.people.points.length} onclick={()=>{project!.people.points.pop();savePeople();}}>Undo click</button><button disabled={running||!project.people.points.length} onclick={()=>{project!.people.points=[];savePeople();}}>Clear selection</button></div>
            <button class="primary wide" disabled={running||!project.people.points.length} onclick={buildPeople}>{project.people_matte?'Rebuild people matte':'Build people matte'}</button>
            {#if project.people_matte}<a class="download" href={`/api/projects/${project.id}/people/cutout`}>Download transparent cutout · MOV</a>{:else if hasPeople}<p class="small muted">Selection changed. Build the matte to apply it.</p>{/if}
            {#if project.mode==='plate'}<button disabled={running||!project.people_matte} onclick={changeScene}>Use people in a new environment</button>{/if}
            {:else}
              <p class="small muted">No clicks or matte generation. The original plate goes directly to your image model, which replaces the background and integrates its lighting. The prompt protects the original people, pose, wardrobe and captured blur.</p>
              <p class="small muted">Use Manual selection only when you need to exclude people or take control of the cutout. Direct AI editing can still change a person; compare the result with your source.</p>
              {#if project.mode==='plate'}<button disabled={running} onclick={changeScene}>Create a new environment</button>{:else}<button onclick={()=>locationTab='room'}>Continue to location</button>{/if}
            {/if}
          </div>
        {:else if project.mode === 'greenscreen'}
          {#if locationTab === 'room'}
            <div class="block">
              <label>Location <textarea bind:value={project.location} oninput={saveLocation} placeholder="Describe the place you want to shoot in"></textarea></label>
              <RoomReferences {project} disabled={running||!!busy}
                onchange={(id,refs)=>{if(project?.id===id)project.room_references=refs;}}
                onbusy={(id,value)=>{if(project?.id===id)roomReferenceBusy=value;}}
                onerror={(id,text)=>{if(project?.id===id)message=text;}}/>
              <label>Lighting <select value={project.lighting ?? 'match'} onchange={(e) => {project!.lighting=e.currentTarget.value;saveLocation();}}>{#each LIGHTS as [v,l]}<option value={v}>{l}</option>{/each}</select></label>
              <ModelPicker bind:value={project.image_models.location} onchange={saveModels}/>
              <label class="check"><input type="checkbox" checked={manualPeople} disabled={running} onchange={e=>setPeopleSelection(e.currentTarget.checked?'manual':'auto')}/> Use a people matte · optional</label>
              {#if !canBuildLocation}<p class="notice small">Optional matte mode is on. Build the selection, or use the original plate directly.</p><div class="row"><button onclick={()=>setPeopleSelection('auto')}>Use original plate · no matte</button><button onclick={()=>{stop();frame=0;locationTab='people';compareMode='result';}}>Select people</button></div>{/if}
              <button class="primary wide" disabled={!canBuildLocation || roomReferencesOverLimit || running || !!busy || roomReferenceBusy} onclick={buildLocation}>Generate location still</button>
              {#if !cutoutMode && !project.empty_set}<p class="small muted">{#if manualPeople}Two image passes build the location and transfer lighting onto your selected source people, retaining their detail and silhouette.{:else}One image edit from your original plate. No matte step. People are retained through the source reference and preservation prompt.{/if}</p>{/if}
              <details><summary>Reference settings <span>{project.location_role === 'scene' ? `Frame ${frame}` : 'First frame'}</span></summary><div class="adv">
                <label>Reference frame <select bind:value={project.location_role} onchange={() => { if(project!.location_role==='opening'){stop();frame=0;project!.empty_set=false;}saveLocation();}}><option value="opening">First frame (recommended)</option><option value="scene">Current frame · scene reference only</option></select></label>
                <label>Image seed <input type="number" bind:value={project.location_seed} oninput={saveLocation}/></label>
                <label class="check"><input type="checkbox" checked={!project.empty_set} disabled={project.location_role !== 'scene'} onchange={(e) => {project!.empty_set=!e.currentTarget.checked;saveLocation();}}/> Include people</label>
                <label>Upload a finished reference still <input type="file" accept="image/*" onchange={(e) => uploadBackground(e.currentTarget.files?.[0])}/></label>
              </div></details>
            </div>
          {:else if dressingDraft?.source}
            <div class="block">
              <ModelPicker label="Object image model" bind:value={project.image_models.dressing} onchange={saveModels}/>
              {#each dressingDraft.objects as obj, i (obj.id)}
                <div class="propitem" class:chosen={dressSel === obj.id}>
                  <div class="row"><button class="ghost grow" onclick={() => {dressSel=obj.id;dressingTool='object';dressingBoxes=true;}}>Object {i+1}</button><button class="ghost danger small" aria-label={`Remove object ${i+1}`} onclick={() => removeDressing(obj.id)}>Remove</button></div>
                  <textarea aria-label={`Object ${i+1} description`} bind:value={obj.text} oninput={saveDressing} onfocus={() => {dressSel=obj.id;dressingBoxes=true;}} placeholder="Material, appearance and details"></textarea>
                  <select aria-label={`Object ${i+1} operation`} bind:value={obj.action} onchange={saveDressing}><option value="add">Add object</option><option value="replace">Replace area</option></select>
                </div>
              {:else}<p class="muted small">Draw an object on the preview, then describe it.</p>{/each}
              {#if dressingDraft.people && !dressingDraft.protected.length}<p class="notice small">Protect the person and anything they hold before generating.</p>{/if}
              <button class="primary wide" disabled={!canPlaceObjects || running || !!busy} onclick={buildDressing}>Generate object still</button>
              <details><summary>Protection & edit settings <span>{dressingDraft.protected.length} areas</span></summary><div class="adv">
                <label class="check"><input type="checkbox" bind:checked={dressingDraft.people} onchange={saveDressing}/> People in this still</label>
                {#each dressingDraft.protected as area, i}<div class="row"><button class="grow" onclick={() => {dressSel=area.id;dressingTool='protect';dressingBoxes=true;}}>Protected area {i+1}</button><button class="ghost danger small" onclick={() => removeDressing(area.id)}>Remove</button></div>{/each}
                <label>Shadow room <input type="range" min="0" max=".15" step=".005" bind:value={dressingDraft.margin} oninput={saveDressing}/></label>
                <label>Image seed <input type="number" bind:value={dressingDraft.seed} onchange={saveDressing}/></label>
                <p class="muted small">Protected pixels and areas outside the edit stay unchanged in the still.</p>
              </div></details>
            </div>
          {:else}<p class="notice">Choose “Place objects” on a scene still to start.</p>{/if}
          <div class="block">
            {#if cutoutMode}<p class="small muted">Generate an empty first-frame location still for Original cutout mode.</p>{/if}
            <ActionDraft {project} onchange={saveLocation} onerror={(s)=>message=s}/>
            <label>Action for effects <textarea bind:value={project.shot} oninput={save} placeholder="Describe the action for the effects pass"></textarea></label>
            <p class="small muted">Optional for the scene video: movement comes from your original clip. This description is used when generating effects.</p>
            <div class="selected-reference"><span class="small muted">Video reference</span><strong>{openingRef ? 'Opening still selected' : 'Choose a first-frame still'}</strong></div>
            {#if openingRef?.people_current===false}<p class="notice small">The people selection changed. Generate a new opening still before the scene video.</p>{/if}
            <p class="small muted">The opening sets the people and location. Your full original clip supplies camera movement and performance.</p>
            <button class="primary wide" disabled={!canBuildEnvironment || running || !!busy} onclick={buildEnvironment}>Generate scene video</button>
            <p class="small muted">Two automatic passes: build the scene, then finish at 1080p with 2× upscale and LMS sharpening at 0.5. Both passes use Comfy credits.</p>
            <details><summary>Video settings <span>H3</span></summary><div class="adv">
              <label>Reference strategy <select bind:value={project.reference_mode} onchange={saveLocation}><option value="opening_only">First reference only</option><option value="scene_and_opening">Opening + separate scene</option></select></label>
              {#if project.reference_mode === 'scene_and_opening'}<label>Separate scene <select value={project.background ?? ''} onchange={(e) => pickLocation(e.currentTarget.value,'scene')}><option value="" disabled>Choose a still</option>{#each project.locations ?? [] as l}<option value={l.file}>{l.id} · {l.frame == null ? 'scene' : `frame ${l.frame}`}</option>{/each}</select></label>{/if}
              {#if project.people.mode==='guide'}<label class="check"><input type="checkbox" bind:checked={project.people.extra_reference} disabled={!project.people_matte} onchange={()=>savePeople()}/> Add isolated people video · optional</label>{/if}
              <label>Video seed <input type="number" bind:value={project.environment_seed} oninput={saveLocation}/></label>
            </div></details>
            {#each project.renders.filter(r => r.kind === 'environment') as r (r.id)}
              <div class="render row"><div class="grow"><strong class="small">Scene {r.created.slice(11,16)}</strong><span class="chip {statusChip(r.status)}">{r.status === 'done' ? project.approved_environment === r.id ? 'approved' : 'review needed' : r.status}</span>{#if r.error}<p class="err small">{r.error}</p>{/if}</div>{#if r.status==='done'}<button onclick={() => showResult = r}>Review</button>{/if}</div>
            {/each}
          </div>
        {:else}<div class="block"><p>Your original footage is the scene.</p>
          <label>Camera motion<select aria-label="Camera motion" value={project.camera_mode??'auto'} disabled={running||!!busy} onchange={e=>setCameraMode(e.currentTarget.value as 'auto'|'locked')}><option value="auto">Auto-track camera</option><option value="locked">Locked camera · static plate</option></select></label>
          <p class="small muted">Use Locked camera for tripod shots or static renders, especially when moving objects dominate the image.</p><p class="small muted">Continue to Effects to place smoke, fire and explosions. The original plate supplies the action; no action analysis is required.</p>{#if project.native_profile}<p class="small muted">{project.native_profile.rgb_range_policy==='legalize'?'Legal-range working-copy master':'Native photographic master'} · {project.native_profile.width} × {project.native_profile.height} · {project.native_profile.fps_rational} fps. FX are generated separately and composited over this source.</p>{#if project.native_profile.working_copy_warning}<p class="small muted" role="status">{project.native_profile.working_copy_warning}</p>{/if}{:else}<details><summary>Change the scene <span>Optional</span></summary><div class="adv"><button disabled={running||!!busy} onclick={changeScene}>Create a new environment</button><p class="small muted">Generate a location from this plate. No matte is needed unless you choose to select people.</p></div></details>{/if}</div>{/if}

      {:else if step === 2}
        <div class="block workflow-choice"><div class="row"><h2>{elementMode?'Direct each effect':project.mode==='plate'?'Legacy H3':'Scene effects · H3'}</h2>{#if elementMode}<span class="workflow-tag">LTX · original plate</span>{/if}</div>
          <p class="small muted">{elementMode?'Place an effect, add interaction if needed, review the simulation, then generate and approve that element. Finish combines approved FX and muzzle flashes.':project.mode==='plate'?'The legacy video workflow regenerates the shot. Use LTX elements to keep the original photography.':'Generate effects over your approved environment video.'}</p>
          {#if !elementMode&&project.mode==='plate'}<button class="primary wide" disabled={running||!!busy} onclick={adoptWorkingPlate}>Use LTX elements</button>{/if}
          {#if project.mode==='plate'&&!project.native_profile}<details class="legacy-workflow"><summary>Other workflow <span>Legacy H3</span></summary><div class="adv"><p class="small muted">Saved H3 takes remain in Take history. H3 generates a replacement shot rather than a separate element.</p><button disabled={!elementMode||running||!!busy} onclick={useLegacyWorkflow}>Use legacy H3 video workflow</button></div></details>{/if}
        </div>
        {#if !envReady}<div class="notice"><p>Review the scene before generating effects.</p><button onclick={goScene}>Review scene</button></div>{/if}
        <div class="block effect-library">
          <button class="effect-picker-trigger" class:expanded={effectLibraryOpen} aria-label="Add an effect" aria-describedby="effect-picker-help" aria-expanded={effectLibraryOpen} aria-controls="effect-library-menu" onclick={()=>effectLibraryOpen=!effectLibraryOpen}>
            <span class="effect-picker-plus" aria-hidden="true">+</span><span class="effect-picker-label"><strong>Add an effect</strong><small id="effect-picker-help">{armed?`${presets[armed]?.name??'Effect'} selected · click the shot`:'Choose smoke, fire, explosion or muzzle flash'}</small></span><svg class="effect-picker-chevron" class:expanded={effectLibraryOpen} viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4"/></svg>
          </button>
          {#if effectLibraryOpen}<div id="effect-library-menu" class="effect-picker-menu" role="region" aria-label="Effect library">
          <div class="tiles">
            {#each Object.entries(groups) as [cat,list]}<h3 class="effect-category">{cat}</h3>{#each list.filter(p=>!elementMode||p.id!=='smoke_white') as p}
              {@const st = p.phases.find(ph => ph.stamp && ph.stamp !== 'hole')}
              {@const comingSoon = !effectPlaceable(p.id,project.mode,elementMode)}
              <button class="effect-tile" class:coming-soon={comingSoon} class:armed={armed===p.id && effectTool==='effects'} disabled={!envReady || comingSoon} onclick={() => chooseEffect(p.id)} title={comingSoon ? `${p.name} — coming soon` : p.description}>{#if st}<img src={api.stampUrl(p.id,st.stamp!)} alt=""/>{/if}{#if comingSoon}<span class="coming-soon-badge">Coming soon</span>{/if}<span>{elementMode&&p.id==='smoke_plume'?'Black smoke column':p.id==='smoke_plume_hq'?p.name:p.name.replace('Bullet hit (ground)','Ground hit').replace('Bullet hit (wall)','Wall hit').replace(/ \(.*\)/,'')}</span></button>
            {/each}{/each}
            <h3 class="effect-category">Finishing</h3><button class="effect-tile muzzle-tile" aria-label="Muzzle flash" title="Single-frame muzzle flash — drag from the muzzle to the flame tip" class:armed={effectTool==='muzzle'} onclick={() => {stop();effectTool='muzzle';armed=null;compareMode='preview';effectLibraryOpen=false;}}><img src={api.stampUrl('muzzle_side','look_flash.png')} alt="Muzzle flash preview"/><span class="muzzle-label">Single frame</span><span>Muzzle flash</span></button>
          </div>
          </div>{/if}
        </div>
        {#if effectTool === 'muzzle'}
          <div class="block"><h2>Muzzle flash</h2><p class="small muted">Drag from the muzzle to the flame tip. Play Preview to see temporary flashes together with your explosions.</p>
            <ModelPicker label="Muzzle image model" bind:value={project.image_models.flash} onchange={saveModels}/>
            {#if flashSelected}<div class="row"><label class="grow">Frame <input type="number" min="0" max={project.frames-1} bind:value={flashSelected.frame} onchange={() => {frame=flashSelected!.frame;saveFlashes();}}/></label><button class="ghost danger" onclick={() => deleteFlash(flashSelected!.id)}>Remove</button></div>
              <div class="seg">{#each [['small','Small'],['medium','Medium'],['large','Large']] as [k,l]}<button class:active={Math.abs(flashLen(flashSelected)-SIZE_PX[k as 'small'])<6} onclick={() => flashSize(flashSelected!,k as 'small')}>{l}</button>{/each}</div>
            {/if}
            <p class="small muted">Preview uses a temporary sprite. Generate a take to review the finished muzzle flashes in Result.</p>
            <details><summary>Frame list <span>{flashList.length} frames</span></summary><div class="adv">{#each flashList as f}<button class="frame-button" onclick={() => selectFlash(f.id)} aria-label={`Select muzzle flash at frame ${f.frame}`}>Frame {f.frame}<span class="chip {f.result_current ? 'done' : ''}">{f.result_current ? 'ready' : f.status==='error' ? 'failed' : 'pending'}</span></button>{/each}</div></details>
          </div>
        {:else if selected}
          {@const p = presets[selected.preset]}{@const d = p.direction}
          <div class="block" id="selected-effect-placement"><div class="row"><h2>{elementMode?'1. Place · ':''}{p.name}</h2><span class="grow"></span><button class="ghost small" onclick={duplicate}>Duplicate</button><button class="ghost danger small" onclick={removeSelected}>Remove</button></div>
            <label>Placement description <input bind:value={selected.where} oninput={save} placeholder={p.default_where}/></label>
            <label>Tracking<select aria-label="Tracking" value={selected.tracking??(elementMode?'camera':'features')} onchange={e=>{selected!.tracking=e.currentTarget.value as Effect['tracking'];save();}}><option value="camera">Place in scene · camera only</option><option value="features">Track object · local features</option><option value="screen">Fixed in frame</option></select></label>
            {#if elementMode}<p class="small muted">If a moving subject pulls the effect across a locked-off shot, try Fixed in frame.</p>{/if}
            <label>Size <input type="range" min=".2" max="3" step=".01" bind:value={selected.size} oninput={save}/></label>
            {#if d && d.kind !== 'none'}<label>Direction <input type="range" min="0" max="359" step="1" bind:value={selected.angle} oninput={save}/></label>{#if d.kind !== 'barrel'}<label>{d.label} <input type="range" min="0" max="1" step=".01" bind:value={selected.strength} oninput={save}/></label>{/if}{/if}
            <label>Start frame <input type="number" min={canPreroll(p) ? -PREROLL_MAX : 0} max={project.frames-1} value={elementMode?elementOnset(selected):onsetOf(selected,p)} onchange={(e) => setStart(selected!,+e.currentTarget.value)}/></label>
            {#if canPreroll(p)}<label class="check"><input type="checkbox" checked={prerolled(selected,p)} onchange={(e) => setStart(selected!,e.currentTarget.checked ? (p.default_start_frame??-48) : Math.max(0,frame))}/> Already going at the start</label>{/if}
          </div>
        {/if}
        {#if elementMode}
          {#if effectTool==='effects'}<ElementPanel {project} {frame} {selected} disabled={running||!!busy} onchange={save} onupdated={adoptElementUpdate} onprepare={id=>prepareOneElement(id,false)} onrender={id=>prepareOneElement(id,true)} onrerun={rerunOneElement} onmatte={refreshExplosionMatte} onpreview={reviewElementSimulation} onconnect={()=>{stop();showConnection=true;}}/>{/if}
        {:else}
        <div class="block"><h2>People interaction</h2>
          <label class="check"><input type="checkbox" checked={!!project.settings.collider} onchange={e=>{for(const fx of project!.effects)if(['smoke_white','smoke_plume'].includes(fx.preset))fx.tracking??='camera';project!.settings.collider=e.currentTarget.checked;save();}}/> Collider mode</label>
          <p class="small muted">Smoke and explosion fluid flows around the tracked people matte. The same collision is included in the AI guide frames.</p>
          {#if project.settings.collider}
            <p class="small muted">{project.people_matte?.current ? 'Current people matte connected.' : 'Build a people matte before preparing keys or generating this take.'}</p>
            <button onclick={()=>{stop();step=1;locationTab='people';compareMode='result';frame=0;setPeopleSelection('manual');}}>Select or rebuild people matte</button>
          {/if}
        </div>
        <div class="block"><details><summary>Legacy generation settings <span>H3 + image edits</span></summary><div class="adv">
          <label>Preview quality <select bind:value={previewQuality}><option value="draft">Draft · faster smoke</option><option value="high">High</option></select></label>
          <p class="small muted">Generation keys use full quality. Pausing holds the simulation on the selected frame.</p>
          <ModelPicker label="Effect key image model" bind:value={project.image_models.polish} onchange={saveModels}/>
          <label class="check"><input type="checkbox" checked={project.settings.polish !== false} onchange={(e) => {project!.settings.polish=e.currentTarget.checked;save();}}/> Polish the effect keys</label>
          <label>Explosion guides <select bind:value={project.settings.guide_mode} onchange={save}><option value="sparse">Natural motion (recommended)</option><option value="full">All phases</option></select></label>
          <p class="small muted">{project.settings.guide_mode === 'full' ? 'Every explosion phase supplies an image guide, including the aftermath.' : 'Guide the clean pre-blast moment, onset and peak. Later phases control the live preview and describe timing; H3 develops the motion afterward.'}</p>
          <label>Guide duration <select bind:value={project.settings.strength} onchange={save}><option value="exact">Throughout sampling (recommended)</option><option value="firm">More early steps</option><option value="guide">Early steps only</option></select></label>
          <label>Video seed <input type="number" bind:value={project.settings.seed} oninput={save}/></label>
          <button disabled={!project.effects.length || running || !envReady || !!busy} onclick={() => generate('keys')}>Preview effect keys</button>
          <button disabled={!project.effects.length || running || !envReady || !!busy} onclick={() => generate('effect')}>Generate effects video only</button>
          <button class="ghost" onclick={togglePrompt}>{showPrompt ? 'Hide' : 'Show'} prompt</button>{#if showPrompt}<pre class="prompt">{promptText}</pre>{/if}
        </div></details></div>
        {/if}
        <details class="history"><summary>Take history <span>{project.renders.filter(r => r.kind==='effect'||r.kind==='keys').length}</span></summary>{#each project.renders.filter(r => r.kind==='effect'||r.kind==='keys') as r}<div class="render row"><div class="grow"><span class="small">{r.kind==='keys' ? 'Effect keys' : 'Effects take'} · {r.created.slice(11,16)}</span><span class="chip {statusChip(r.status)}">{r.status}</span>{#if r.error}<p class="err small">{r.error}</p>{/if}</div>{#if r.status==='done'}<button onclick={() => showResult=r}>Compare</button>{/if}</div>{/each}</details>
      {:else}
        <div class="block"><h2>Ready to finish</h2>
          <div class="readiness"><span>Scene</span><strong>{envReady ? 'Ready' : 'Review needed'}</strong></div>
          <div class="readiness"><span>{elementMode?'Approved elements':'Effects video'}</span><strong>{elementMode?`${approvedElements} / ${project.effects.length}`:!project.effects.length?'Not needed':latestTake?'Ready':'Pending'}</strong></div>
          <div class="readiness"><span>Muzzle frames</span><strong>{readyFlashes} / {flashList.length} ready</strong></div>
          {#if !envReady}<button onclick={goScene}>Review scene</button>{:else if elementMode?!allElementsApproved:!currentFinal}<button onclick={goEffects}>Continue in Effects</button>{/if}
          {#if elementMode}
            {#if !allElementsApproved}<p class="notice small">Generate and approve each effect in Effects before building the final take.</p>{/if}
            <button class="primary wide" disabled={!allElementsApproved||running||flashing||!envReady||!!busy||allowanceBlocked||flashTodo.length>0&&!$connection?.configured} onclick={generateTake}>Build final take</button>
            <p class="small muted">Reuses approved cached FX. Missing muzzle frames use the selected image model; FX are not regenerated.</p>
            {#if allowanceBlocked}<p class="notice small">{flashCost?`Muzzle frames need ${pendingFlashCount*flashCost.per_flash} credits of server allowance; ${flashCost.allowance_left??0} are available.`:'Checking the muzzle-frame allowance. Reopen Finish if the allowance cannot be read.'}</p>{/if}
            {#if flashTodo.length>0&&!$connection?.configured}<p class="small muted">Connect Comfy to generate the missing muzzle frames.</p><button onclick={()=>showConnection=true}>Connect Comfy</button>{/if}
          {/if}
          {#if currentFinal}<a class="download primary" href={api.renderUrl(project.id,currentFinal.id,currentFinal.files.master?'master.mov':'result.mp4')} download>Download current master</a><button onclick={() => showResult=currentFinal!}>Open comparison</button>{:else}<p class="muted small">Your current scene stays visible while you finish the take.</p>{/if}
          {#if envReady && (!project.effects.length || latestTake)}<details><summary>Export options</summary><div class="adv">
            {#if flashList.length > readyFlashes}<label class="check"><input type="checkbox" bind:checked={omitUnready}/> Omit unfinished muzzle frames</label>{/if}
            <button disabled={!!busy || running || elementMode&&!allElementsApproved || (flashList.length>readyFlashes && !omitUnready)} onclick={exportFinal}>Build export without generation</button>
          </div></details>{/if}
        </div>
        {#if elementMode}<FinishingLighting {project} disabled={running||!!busy} configured={!!$connection?.configured} onrun={runLighting} onsetting={lightingSetting}/><IntegrationPanel {project} disabled={running||!!busy} onchange={save} onrebuild={buildElementComposite}/>{/if}
        {#if elementMode}{#key project.id}<LayerExports {project} render={layerExportTake} needsApproval={!allElementsApproved&&!layerExportTake?.review_only} disabled={running||!!busy}/>{/key}{/if}
        <details class="history"><summary>Earlier exports <span>{finals.filter(r => !r.source_current).length}</span></summary>{#each finals.filter(r => !r.source_current) as r}<div class="render row"><div class="grow"><span class="small">Earlier take · {r.created.slice(11,16)}</span><p class="muted small">Previous inputs</p></div><button onclick={() => showResult=r}>Compare</button><a href={api.renderUrl(project.id,r.id,'result.mp4')} download>Download</a></div>{/each}</details>
      {/if}
      </fieldset>
      {#if step === 1}<div class="action-footer"><button class="wide" disabled={!envReady || running} onclick={goEffects}>Continue to effects</button>{#if !envReady}<p class="small muted">Review and approve a scene video to continue.</p>{/if}</div>{:else if step === 2}        <div class="action-footer">
          {#if elementMode}
            {#if project.effects.length}
              <p class="small muted">{approvedElements} / {project.effects.length} elements approved</p>
              {#if batchNeedsGeneration&&!$connection?.configured}<button class="wide" onclick={()=>showConnection=true}>Connect Comfy to generate effects</button>
              {:else}<button class="primary wide" disabled={!!busy||running||flashing} title="Reuse ready versions, generate missing elements, then build a review. Approval stays manual." onclick={generateEffectsBatch}>Generate effects with LTX</button>{/if}
              {#if batchProgress}<p class="small" role="status">{busy}</p>{/if}
            {/if}
            <button class="wide" disabled={!!busy||running||openingShot} onclick={()=>goStep(3)}>Continue to Finish</button>{:else}
          <p class="small muted">{project.effects.length} effects · {flashList.length} muzzle frames</p>
          <button class="primary wide" disabled={running || flashing || !envReady || !!busy || allowanceBlocked} onclick={generateTake}>{pipeline ? pipeline.status==='effects' ? 'Generating effects video…' : pipeline.status==='flashes' ? 'Generating muzzle frames…' : 'Finishing take…' : busy || 'Generate take'}</button>
          {#if flashCost?.unlimited}<p class="small muted">Muzzle allowance: Unlimited</p>{:else if pendingFlashCount && flashCost}<p class="small muted">Muzzle allowance: {pendingFlashCount*flashCost.per_flash} credits needed · {flashCost.allowance_left} available.</p>{/if}{#if allowanceBlocked}<p class="small muted">Increase the server allowance to generate.</p>{/if}
          {/if}
        </div>
{/if}
      {#if pipeline}<p class="notice small" role="status">{pipeline.status === 'effects' ? '1 / 3 · Effects video' : pipeline.status === 'flashes' ? '2 / 3 · Muzzle frames' : '3 / 3 · Final splice'}. You can compare with your source while this runs.</p>{/if}
      {#each project.renders.filter(r => r.kind==='pipeline').slice(0,1).filter(r => r.status==='error') as r}<p class="err small" role="alert">{r.error}</p>{/each}
    </aside>
  </main>
{/if}

{#if showNew}<NewProject oncancel={() => showNew=false} oncreated={async(p) => {showNew=false;projectList=await api.projects();await open(p.id);}}/>{/if}
{#if approvalPreview&&project}<FinishApprovalDialog count={project.effects.length} busy={approvalBusy} error={approvalError} onapprove={approvePreviewAndFinish} oncancel={()=>{approvalPreview=null;approvalError='';}}/>{/if}
{#if showConnection}<ConnectionSetup initial={$connection} onclose={() => showConnection=false} onsaved={async value => {
  connection.set(value);showConnection=false;
  message=value.configured ? `${value.mode==='cloud'?'Comfy Cloud':'Local ComfyUI'} connection saved.` : 'Comfy disconnected. You can keep previewing your shots.';
  if(project){const id=project.id;try{const fresh=await api.project(id);if(project?.id===id)project.image_models=fresh.image_models;}catch(e){message=String(e);}}
}}/>{/if}
{#if showResult && project}<ResultView fps={sourceFPS} timestamps={sourceTimes} aspectRatio={sourceAspect} {project} render={showResult} onclose={() => showResult=null} onapprove={approveEnvironment}/>{/if}

<style>
  .preview-body{width:100%;max-width:min(100%,calc(max(220px,100dvh - 340px)*var(--source-aspect)));margin-inline:auto}
  .app-header{min-height:76px;display:flex;align-items:center;gap:28px;padding:14px 32px;border-bottom:1px solid var(--line)}
  .steps{display:flex;gap:6px;background:var(--panel);border:1px solid var(--line);padding:4px;border-radius:12px}.steps button{border-color:transparent;background:transparent;display:flex;gap:9px;align-items:center;color:var(--muted);padding:8px 18px}.steps button.on{background:var(--panel3);color:var(--text)}.step-number{font-size:10px;opacity:.7}
  .project-actions{margin-left:auto;display:flex;gap:10px}.project-actions select{max-width:230px;background:transparent}
  .connection-button{display:flex;align-items:center;gap:7px;white-space:nowrap;font-size:11px}.connection-dot{width:6px;height:6px;border-radius:50%;background:var(--muted);flex:none}.connection-button.verified .connection-dot{background:var(--accent2)}
  .effect-controls-shortcut{display:none;font-size:10px;padding:5px 7px;margin-left:8px}
  main{display:grid;grid-template-columns:340px minmax(0,1fr);grid-template-areas:'insp center';gap:24px;padding:26px 32px;max-width:1800px;margin:0 auto;align-items:start}
  .inspector{grid-area:insp;background:var(--panel);border:1px solid var(--line);border-radius:16px;height:calc(100vh - 220px);min-height:400px;overflow:hidden;display:flex;flex-direction:column;scrollbar-width:thin;scrollbar-color:var(--line) transparent}.panel-title{flex-shrink:0;padding:17px 18px;display:flex;justify-content:space-between;border-bottom:1px solid var(--line)}
  fieldset{border:0;padding:0;margin:0;min-width:0;min-height:0;flex:1;overflow:auto;scrollbar-width:thin}.block{display:grid;gap:14px;padding:18px;border-bottom:1px solid var(--line)}.block>p{margin:0}.center{grid-area:center;min-width:0;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:0 16px 16px}
  .preview-head{min-height:57px;display:flex;align-items:center;justify-content:space-between;gap:10px}.eyebrow{font-size:10px;color:var(--muted);letter-spacing:.1em;font-weight:600}.source-context{font-size:11px;color:var(--muted);margin-left:12px}.compare-tools{display:flex;gap:3px}.compare-tools button{font-size:11px;padding:6px 10px;border-color:transparent;background:transparent;border-radius:7px}.compare-tools button.active{background:var(--panel3);border-color:var(--line)}
  .wipe-control{display:flex;align-items:center;gap:16px;margin-bottom:12px;font-size:11px}.wipe-control input{flex:1}.preview-body{min-width:0}.preview-body.locked :global(canvas),.preview-body.locked :global(.tools),.preview-body.locked :global(.people-preview){pointer-events:none}.subtabs{display:flex;gap:4px;margin:14px 18px 0;padding:4px;background:var(--bg);border-radius:9px}.subtabs button{flex:1;border:0;background:transparent;color:var(--muted)}.subtabs button.active{background:var(--panel3);color:var(--text)}
  .preview-visibility{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:0 0 8px}.simulation-download{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:12px 18px;border-bottom:1px solid var(--line)}.simulation-download span{flex-basis:100%}.simulation-download a{font-size:12px}
  .selected-reference{display:grid;gap:5px;border:1px solid var(--line);border-radius:10px;padding:12px}.selected-reference strong{font-size:12px;font-weight:500}.action-footer{padding:16px 18px;background:var(--panel);position:static;flex-shrink:0;border-top:1px solid var(--line);z-index:3;display:grid;gap:8px}.action-footer p{margin:0}.wide{width:100%;padding:12px}
  .adv{display:grid;gap:12px;padding-top:14px}.propitem{display:grid;gap:8px;border:1px solid var(--line);padding:10px;border-radius:10px}.propitem.chosen{border-color:var(--accent)}.check{display:flex;align-items:center;gap:8px;font-size:12px;line-height:1.5}.check input{margin:0;flex:none}.notice{margin:14px 18px;border:1px solid var(--line);background:var(--panel2);border-radius:9px;padding:12px;color:var(--text);line-height:1.5}.block .notice{margin:0}.notice p{margin:0 0 8px}
  .takes-head{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:20px 0 10px}.takes-head h2{margin:0}.filmstrip{display:flex;gap:12px;overflow:auto;padding-bottom:4px;scrollbar-width:thin}.take-card{width:184px;flex:none;border:1px solid var(--line);border-radius:10px;padding:4px}.take-card.chosen{border-color:var(--accent)}.take-image{display:block;padding:0;background:transparent;border:0;text-align:left;width:100%;overflow:hidden}.take-image img{width:100%;height:99px;object-fit:cover;border-radius:6px;display:block}.take-image span{display:block;padding:7px 5px;font-size:11px;color:var(--muted)}.take-actions{display:flex;gap:4px}.take-actions button{font-size:10px;padding:6px;flex:1}
  .tiles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.effect-tile{position:relative;min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:space-between;gap:7px;min-height:90px;padding:8px 4px;background:var(--panel2);border-radius:10px;font-size:10px}.effect-tile img{width:100%;height:50px;object-fit:contain}.effect-tile.armed{border-color:var(--accent);background:var(--panel3)}.effect-tile.coming-soon{opacity:1;cursor:not-allowed}.coming-soon img{opacity:.25}.coming-soon>span:last-child{color:var(--muted)}.coming-soon-badge{position:absolute;top:26px;left:3px;right:3px;padding:5px 1px;background:rgba(18,16,24,.9);color:var(--text);font-size:10px;font-weight:600;line-height:1.2;border-radius:4px}.muzzle-tile img{object-fit:cover;border-radius:5px}.muzzle-label{position:absolute;top:6px;right:5px;font-size:8px;color:var(--text);padding:2px 4px;background:rgba(18,16,24,.85);border-radius:3px}.shot-items{display:flex;flex-wrap:wrap;gap:8px}.shot-items button{font-size:11px;border-radius:8px}.shot-items button span{margin-left:10px;color:var(--muted)}button.active{border-color:var(--accent)}
  .workflow-choice h2{margin:0}.workflow-tag{margin-left:auto;white-space:nowrap;font-size:9px;color:var(--accent);border:1px solid var(--line);border-radius:5px;padding:4px 6px}.legacy-workflow{font-size:11px}.effect-library{gap:10px}.effect-picker-trigger{width:100%;display:flex;align-items:center;gap:10px;padding:13px 11px;text-align:left;border:1px solid var(--accent);border-radius:10px;background:var(--panel3)}.effect-picker-trigger.expanded{border-radius:10px 10px 5px 5px}.effect-picker-plus{font-size:23px;line-height:1;color:var(--accent)}.effect-picker-label{display:grid;gap:5px;flex:1;min-width:0}.effect-picker-label strong{font-size:13px}.effect-picker-label small{font-size:10px;line-height:1.4;color:var(--muted);font-weight:400}.effect-picker-chevron{flex:none;width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.7;transition:transform .15s}.effect-picker-chevron.expanded{transform:rotate(180deg)}.effect-picker-menu{border:1px solid var(--line);border-radius:6px 6px 10px 10px;padding:10px;background:var(--bg)}.effect-category{grid-column:1/-1;margin:4px 0 2px;font-size:10px;font-weight:600;color:var(--muted)}
  .seg{display:grid;grid-template-columns:repeat(3,1fr);gap:5px}.seg button{padding:8px 5px;font-size:11px}.frame-button{display:flex;align-items:center;justify-content:space-between}.readiness{display:flex;justify-content:space-between;gap:10px;font-size:12px}.readiness strong{font-weight:500;color:var(--muted)}.download{text-align:center;text-decoration:none;padding:12px;border-radius:9px}.download.primary{background:var(--accent);color:var(--on-accent);font-weight:600}
  .render{padding:12px 0;border-bottom:1px solid var(--line)}.render .chip{display:inline-block;margin-left:6px}.render p{margin:4px 0 0}.history{padding:16px 18px}.prompt{font-size:11px;white-space:pre-wrap;max-height:240px;overflow:auto;background:var(--bg);padding:10px;border-radius:8px}.msg{display:flex;align-items:center;justify-content:space-between;gap:12px;border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-top:16px;font-size:12px}.msg button{font-size:11px}.err{color:var(--danger);padding:0 18px;overflow-wrap:anywhere}
  .empty{max-width:680px;margin:clamp(32px,9vh,100px) auto;padding:28px 24px;display:grid;justify-items:center;text-align:center;gap:22px}.welcome-mark{width:88px;height:88px;object-fit:contain;transform:rotate(-7deg)}.empty>.eyebrow{letter-spacing:.16em}.empty h1{margin:0;font-size:clamp(44px,6.6vw,78px);line-height:1.02;letter-spacing:-.06em;font-weight:850}.empty h1>span{color:var(--accent)}.empty p{line-height:1.8;max-width:425px;margin:0;font-size:14px}.welcome-cta{padding:14px 24px;font-size:14px}.welcome-note{color:var(--muted);font-size:11px;letter-spacing:.01em}
  @media(min-width:1550px){main{grid-template-columns:360px minmax(0,1fr);gap:30px;padding-top:34px}}
  @media(max-width:1000px){.app-header{padding:12px 18px;gap:16px}.project-actions select{max-width:150px}.steps button{padding:8px 12px}main{padding:20px 18px;gap:16px;grid-template-columns:300px minmax(0,1fr)}.source-context{display:none}.compare-tools button{padding:6px 7px}}
  @media(max-width:760px){.app-header{flex-wrap:wrap;gap:12px}.project-actions{margin-left:auto}.project-actions select{max-width:180px}.steps{order:3;width:100%}.steps button{flex:1;justify-content:center}main{grid-template-columns:minmax(0,1fr);grid-template-areas:'center' 'insp';padding:16px 12px;gap:16px}.inspector{height:auto;max-height:none}.inspector fieldset{overflow:visible}.center{padding:0 10px 12px}.preview-head{gap:6px}.compare-tools button{font-size:10px}.action-footer{position:static}.takes-head{align-items:flex-start}.takes-head>span{max-width:180px;text-align:right}.filmstrip .take-card{width:155px}.take-image img{height:83px}}
  @media(max-width:760px){.effect-controls-shortcut{display:inline-block}}
  @media(max-width:440px){.project-actions{gap:6px}.project-actions select{max-width:106px;font-size:11px;padding:8px 6px}.project-actions button{font-size:11px;padding:8px}.empty{margin-top:36px;gap:20px}.empty p{font-size:13px}.welcome-mark{width:72px;height:72px}}
</style>
