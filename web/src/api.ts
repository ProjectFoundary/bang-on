export type ImageModel = 'qwen' | 'flux3' | 'nano';
export type ImageModels = { location: ImageModel; dressing: ImageModel; polish: ImageModel; flash: ImageModel };
export type ModelCheck = { id: string; label: string; status: 'ready' | 'missing' | 'unknown' | 'off'; details: string[]; required: boolean };
export type ConnectionReport = { connected: boolean; mode: 'cloud' | 'local'; message: string; checks: ModelCheck[]; checked_at: string; test_id?: string; ready?: boolean; catalogue_note?: string };
export type ConnectionSummary = {
  configured: boolean; mode: 'cloud' | 'local'; local_url: string; comfyui_url: string;
  has_cloud_key: boolean; has_local_token: boolean; allow_cloud_partner: boolean; nano_available: boolean;
  source: 'saved' | 'environment'; report: ConnectionReport | null; error: string | null;
};
export type ConnectionDraft = {
  mode: 'cloud' | 'local'; local_url: string; comfyui_url: string; cloud_api_key: string; local_token: string;
  allow_cloud_partner: boolean; clear_cloud_key?: boolean; clear_local_token?: boolean; test_id?: string;
};
export type Phase = {
  phase: string; label: string; offset: number; stamp?: string; anchor?: [number, number]; scale?: number;
  glow?: number; glow_radius?: number; opacity?: number; hard?: boolean; persist?: boolean; with_hole?: number;
};
export type Preset = {
  id: string; name: string; category: string; status: 'proven' | 'experimental'; description: string;
  default_where: string; default_start_frame?: number; phases: Phase[]; looks: { file: string; text: string }[];
  guide_phases?: string[];
  direction?: { kind: 'wind' | 'spray' | 'barrel' | 'none'; angle: number; strength: number; label: string; noun: string };
};
export type Key = { phase: string; frame: number; x?: number; y?: number; scale?: number; rot?: number; enabled?: boolean };
export type Anchor = { frame: number; x: number; y: number };
export type ElementSettings = {duration_s:number;cadence:number;frame_blend?:number;guide_displacement?:number;guide_displacement_blur?:number;guide_style?:'painted'|'rendered';light_intensity?:number;fill_intensity?:number;rim_width?:number;rim_strength?:number;light_rotation?:number;light_elevation?:number;blur:number;shading:number;seed:number;alpha_seed?:number;opacity:number;gain:number;light:number;shadow:number;depth_offset:number;colliders:string[];wind_dispersion?:number;smoke_dispersion?:number;density?:number;brightness?:number;alpha_support?:string;matte_enabled?:boolean;matte_multiply?:number;matte_erode?:number;matte_blur?:number;body_matte?:boolean;edge_blend?:number;holdout_blur?:number;ltx_glow?:number};
export type Effect = { id: string; preset: string; where: string; flip: boolean; keys: Key[]; anchors?: Anchor[]; angle?: number; strength?: number; size?: number; tracking?: 'camera' | 'features' | 'screen'; element?:ElementSettings };
export type PlacementBox = { x0: number; y0: number; x1: number; y1: number };
export type PropObject = { id: string; text: string; action: 'add' | 'replace'; box: PlacementBox };
export type ProtectedArea = { id: string; box: PlacementBox };
export type DressingDraft = { source: string | null; objects: PropObject[]; protected: ProtectedArea[]; people: boolean; seed: number; margin: number };
export type RoomReference = { id: string; file: string; name: string; width: number; height: number; sha256: string };
export type LocationVersion = { id: string; file: string; seed: number; frame: number | null; role?: string; empty_set?: boolean;
  prompt: string; created: string; kind?: string; model?: string; parent_source?: string; dressing_approved_at?: string;
  opening_method?: 'matte' | 'empty' | 'plate'; people_current?: boolean };
export type Render = {
  id: string; kind: 'effect' | 'element' | 'lighting' | 'geometry' | 'people_alpha' | 'environment' | 'keys' | 'final' | 'location' | 'dressing' | 'pipeline' | 'matte' | 'analysis'; status: string; created: string; seed: number; strength: string; polish?: boolean;
  draft?: string; progress?: { value?: number; step?: number; steps?: number; current_node?: string; cloud_status?: string; queue_position?: number; nodes_done?: number; nodes_total?: number };
  finished_at?: number; final_id?: string;
  quality_warnings?: string[];
  scene_phase?: 'draft' | 'upscale'; geometry_kind?:'depth'|'matte'; lms_strength?: number; render_profile?: string;
  jobs?: {draft?: string; upscale?: string}; draft_file?: string;
  error?: string; job?: string; mockup?: boolean; files: { result?: string; master?: string; compare?: string; poster?: string; frames?: number; sheet?: string; keys?: string[]; location?: string; source?: string; verification?: string }; finished?: string;
  base?: string; source_current?: boolean; approved_at?: string; opening_reference?: string; reference_mode?: string;
  review_only?:boolean;review_take_ids?:string[];
};
export type Flash = { id: string; frame: number; origin: { x: number; y: number }; tip: { x: number; y: number }; seed: number;
  status: 'new' | 'queued' | 'generating' | 'done' | 'error'; result?: string; result_base?: string; result_current?: boolean; error?: string | null };
export type Stage = { id: string; label: string };
export type MattePoint = { frame: number; person: number; label: 0 | 1; x: number; y: number };
export type People = { selection: 'auto' | 'manual'; mode: 'guide' | 'cutout'; points: MattePoint[]; feather: number; trim: number; extra_reference: boolean };
export type Project = {
  id: string; name: string; mode: 'plate' | 'greenscreen'; frames: number; created: string; start_s?:number;
  bundled_example?: {id:string;description:string;generation_required_to_open:boolean;references:{id:string;label:string;file:string}[]};
  source: { file: string; fps: number; width: number; height: number; duration?: number };
  shot: string; location: string; background: string | null; environment: string | null;
  approved_environment?: string; environment_ready?: boolean; opening_reference?: string; reference_mode?: 'opening_only' | 'scene_and_opening';
  location_role?: 'opening' | 'scene'; location_seed?: number; environment_seed?: number;
  set_dressing?: DressingDraft; image_models: ImageModels;
  room_references?: RoomReference[]; room_reference_limits?: Record<ImageModel,number>; room_reference_max?: number;
  people: People; people_matte?: {id: string; current: boolean} | null;
  action_review?: string;
  render_timing?: {source_frames: number; generated_frames: number; padding_frames: number; fps: number};
  render_format?: {width: number; height: number; canvas_width: number; canvas_height: number; fps: number; profile: string};
  effects: Effect[]; flashes?: Flash[]; flash_base?: string;
  dressing?: { id: string; text: string; frame?: number; box: { x0: number; y0: number; x1: number; y1: number } | null }[]; lighting?: string; empty_set?: boolean;
  location_frame?: number; locations?: LocationVersion[];
  native_profile?:{width:number;height:number;fps:number;fps_rational:string;timestamps:number[];storage_bit_depth:number;colour:Record<string,string>;duration:number;working_copy_warning?:string|null;rgb_range_policy?:'preserve'|'legalize'};
  element_model_defaults?:Record<string,string>;
  element_alpha_bf16_models?:Record<string,string>;
  geometry?:{reference_frame:number;focal_pixels:number;depth_scale:number;depth_bias:number;collision_mode?:'matte2d'|'depth3d';depth:{id:string;kind:'inverse'|'distance';reference_frame?:number}|null;mattes:any[]};
  element_takes?:{id:string;effect_id:string;status:string;current:boolean;approved?:boolean;approval_outdated?:boolean;needs_alpha_core?:boolean;backend_current?:boolean;created:string;error?:string;assets?:any;plan?:any;recoverable_stages?:{stage:'rgb'|'alpha';raw_sha256:string;job:string;output_filename:string;error_detail?:string}[];local_recovery?:{stage:string;raw_sha256:string;new_inference_submissions:number;recovered_at:number}}[];
  element_approvals?:Record<string,string>;
  light_integration?:{enabled?:boolean;take_id?:string;gain?:number};
  lighting_takes?:{id:string;status:string;current:boolean;created:string;error?:string;active_frames?:number}[];
  people_alpha_takes?:{id:string;seed:number;signature:string;status:string;current:boolean;backend_current?:boolean;created:string;error?:string;plan?:any;asset?:any;approved_mattes?:string[]}[];
  settings: { pipeline?:'h3'|'ltx_elements';legacy_h3?:boolean;element_models?:Record<string,string>;element_model_revisions?:Record<string,string>; guide_mode?: 'sparse' | 'full'; strength: 'guide' | 'firm' | 'exact'; seed: number; polish?: boolean; collider?: boolean }; renders: Render[];
};

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const text=await r.text(); let message=text||r.statusText;
    try { const body=JSON.parse(text); if(typeof body.detail==='string')message=body.detail; } catch { /* non-JSON response */ }
    throw new Error(message);
  }
  return r.json();
}

export const api = {
  connection: () => fetch('/api/connection').then(j<ConnectionSummary>),
  testConnection: (body: ConnectionDraft) => fetch('/api/connection/test', {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}).then(j<ConnectionReport>),
  saveConnection: (body: ConnectionDraft) => fetch('/api/connection', {method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(body)}).then(j<ConnectionSummary>),
  disconnect: () => fetch('/api/connection', {method:'DELETE'}).then(j<ConnectionSummary>),
  presets: () => fetch('/api/presets').then(j<Preset[]>),
  projects: () => fetch('/api/projects').then(j<{ id: string; name: string; mode: string; frames: number; created: string }[]>),
  project: (id: string) => fetch(`/api/projects/${id}`).then(j<Project>),
  create: (file: File, name: string, mode: string, start: number, length = 0, analyse = true, colourProfile = 'auto', rgbRange: 'preserve'|'legalize' = 'legalize') => {
    const fd = new FormData(); fd.append('file', file); fd.append('name', name); fd.append('mode', mode); fd.append('start_s', String(start)); fd.append('length_s', String(length || 0));
    fd.append('analyse_action', String(analyse));
    fd.append('preserve_native',String(mode==='plate'));fd.append('colour_profile',colourProfile);
    fd.append('rgb_range',rgbRange);
    return fetch('/api/projects', { method: 'POST', body: fd }).then(j<Project>);
  },
  patch: (id: string, body: Partial<Project>) =>
    fetch(`/api/projects/${id}`, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }).then(j<Project>),
  addEffect: (id: string, preset: string, frame: number, x: number, y: number) =>
    fetch(`/api/projects/${id}/effects`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ preset, frame, x, y }) }).then(j<Project>),
  newElementVersion: (id:string,effectId:string) => fetch(`/api/projects/${id}/elements/${encodeURIComponent(effectId)}/new-version`,{method:'POST'}).then(j<{effect_id:string;seed:number;plan:unknown}>),
  background: (id: string, file: File, role: 'opening' | 'scene' = 'scene') => { const fd = new FormData(); fd.append('file', file); fd.append('role', role); return fetch(`/api/projects/${id}/background`, { method: 'POST', body: fd }).then(j<Project>); },
  roomReferences: (id: string, files: File[]) => {
    const fd=new FormData();for(const file of files)fd.append('files',file);
    return fetch(`/api/projects/${id}/room-references`,{method:'POST',body:fd}).then(j<Project>);
  },
  removeRoomReference: (id: string, ref: string) => fetch(`/api/projects/${id}/room-references/${ref}`,{method:'DELETE'}).then(j<Project>),
  roomReferenceUrl: (id: string, ref: string) => `/api/projects/${id}/room-references/${ref}`,
  approveEnvironment: (id: string, rid: string) => fetch(`/api/projects/${id}/environments/${rid}/approve`, {method:'POST'}).then(j<Project>),
  dressingSource: (id: string, file: string) => fetch(`/api/projects/${id}/dressing/source`, {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({file})}).then(j<Project>),
  render: (id: string, kind: 'effect' | 'environment' | 'keys' | 'location' | 'dressing' | 'finish' | 'matte' | 'analysis') =>
    fetch(`/api/projects/${id}/render`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ kind }) }).then(j<Render>),
  prompt: (id: string) => fetch(`/api/projects/${id}/prompt`).then((r) => r.text()),
  track: (id: string) => fetch(`/api/projects/${id}/track`).then(j<import('./track').TrackData>),
  renderMockup: (id: string, kind: 'effect' | 'keys' | 'finish', layers: { frame: number; fx: Blob; glow: Blob }[], collider = '') => {
    const fd = new FormData(); fd.append('kind', kind); fd.append('collider', collider);
    for (const l of layers) { const n = String(l.frame).padStart(4, '0'); fd.append(`fx_${n}.png`, l.fx, `fx_${n}.png`); fd.append(`glow_${n}.png`, l.glow, `glow_${n}.png`); }
    return fetch(`/api/projects/${id}/render`, { method: 'POST', body: fd }).then(j<Render>);
  },
  stages: (id: string) => fetch(`/api/projects/${id}/stages`).then(j<{ stages: Stage[]; flash_base: string }>),
  addFlash: (id: string, frame: number, origin: { x: number; y: number }, tip: { x: number; y: number }) =>
    fetch(`/api/projects/${id}/flashes`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ frame, origin, tip }) }).then(j<Project>),
  flashCost: (id: string, n: number) => fetch(`/api/projects/${id}/flashes/cost?n=${n}`).then(j<{ per_flash: number; requested: number; allowance_left: number | null; unlimited: boolean }>),
  generateFlashes: (id: string, ids?: string[]) =>
    fetch(`/api/projects/${id}/flashes/generate`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ ids: ids ?? null }) }).then(j<{ queued: number; credits: number }>),
  flashUrl: (id: string, path: string) => `/api/projects/${id}/flashfile/${path}`,
  exportFinal: (id: string, omitUnready = false) => fetch(`/api/projects/${id}/export`, { method: 'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({omit_unready:omitUnready}) }).then(j<Render>),
  frameUrl: (id: string, i: number, base = 'auto') => `/api/projects/${id}/frame/${i}?base=${base}`,
  keyUrl: (id: string, i: number, v = 0) => `/api/projects/${id}/key/${i}?v=${v}`,
  stampUrl: (preset: string, file: string) => `/api/presets/${preset}/${file}`,
  renderUrl: (id: string, rid: string, file: string) => `/api/projects/${id}/renders/${rid}/${file}`,
};
