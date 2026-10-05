import { apiErrorMessage, isRecord, parseProjectSnapshot, type ProjectSnapshot } from './workflowTypes';
import { parseAsset, parseAssets, parseReferences, targetOptions, type Asset, type AssetReference, type AssetVersion } from './assets';

export const MODES = { first_frame: '首帧生视频', start_end_frame: '首尾帧生视频', reference: '参考图生视频' } as const;
export const ROLES = { context: '文字设定', first_frame: '首帧', last_frame: '尾帧', reference_image: '参考图' } as const;
export const IMPACTS = { target_missing: '片段已不存在', source_changed: '分镜内容已修改', reference_changed: '素材引用已切换', version_changed: '固定素材版本已变化' } as const;
export const BLOCKERS = { pricing_unconfirmed: '真实费用待确认', model_and_privacy_unconfirmed: '模型与本次素材授权待确认', execution_not_implemented: '真实执行尚未开放' } as const;
export type Mode = keyof typeof MODES;
export type Role = keyof typeof ROLES;
export interface Options { model: string; mode: Mode; video_ratio: string; resolution: string; sound: 'on' | 'off'; shot_type: 'single' | 'multi'; style: string }
export interface Binding { reference_id: string; role: Role; revision: number; asset_id: string; kind: 'character' | 'setting' | 'prop' | 'image'; version: AssetVersion; target: { project_id: string; segment_id: string | null; shot_number: number | null } }
export interface Target { segment_id: string; duration: number; source_hash: string; prompt: string; shots: { shot_number: number; duration: number; content: string }[]; bindings: Binding[] }
export interface Plan { plan_id: string; created_at: string; content_hash: string; content: { project_id: string; template_version: 1; options: Options; targets: Target[] }; confirmation: { confirmed_at: string; content_hash: string; scope: 'local_input_only' } | null; status: 'draft' | 'confirmed_local'; current: boolean; impacts: { segment_id: string; reasons: (keyof typeof IMPACTS)[]; newer_version_references: string[] }[]; cost: { status: 'pending_confirmation'; amount: null; currency: null }; execution_allowed: false; execution_blockers: (keyof typeof BLOCKERS)[] }
export interface Workspace { project: ProjectSnapshot | null; assets: Asset[]; refs: AssetReference[]; plans: Plan[] }
export interface Input { project_id: string; options: Options; targets: { segment_id: string; duration: number; bindings: { reference_id: string; role: Role }[] }[] }
export interface Draft { segment: string; duration: string; options: Options; roles: Record<string, Role | ''> }
const id = (x: unknown): x is string => typeof x === 'string' && /^[a-f0-9]{32}$/.test(x);
const digest = (x: unknown): x is string => typeof x === 'string' && /^[a-f0-9]{64}$/.test(x);
const key = (x: unknown): x is string => typeof x === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(x);
const integer = (x: unknown, max = Number.MAX_SAFE_INTEGER): x is number => typeof x === 'number' && Number.isSafeInteger(x) && x >= 1 && x <= max;
const text = (x: unknown, max: number): x is string => typeof x === 'string' && x.trim().length > 0 && [...x].length <= max;
const date = (x: unknown): x is string => typeof x === 'string' && Number.isFinite(Date.parse(x));
function invalid(): never { throw new Error('生成计划数据不完整，请重新读取核对'); }
function unique<T>(items: T[], get: (x: T) => unknown) { return new Set(items.map(get)).size === items.length; }
function options(x: unknown): Options {
  if (!isRecord(x) || typeof x.model !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,99}$/.test(x.model) || !Object.hasOwn(MODES, String(x.mode)) || !['16:9', '9:16', '1:1', '4:3', '3:4', '21:9'].includes(String(x.video_ratio)) || !['480P', '720P', '1080P'].includes(String(x.resolution)) || !['on', 'off'].includes(String(x.sound)) || !['single', 'multi'].includes(String(x.shot_type)) || !text(x.style, 100)) return invalid();
  return { model: x.model, mode: x.mode as Mode, video_ratio: String(x.video_ratio), resolution: String(x.resolution), sound: x.sound as Options['sound'], shot_type: x.shot_type as Options['shot_type'], style: x.style };
}
function pending(x: unknown) {
  if (!isRecord(x) || x.execution_allowed !== false || !isRecord(x.cost) || x.cost.status !== 'pending_confirmation' || x.cost.amount !== null || x.cost.currency !== null) return invalid();
}
function validRoles(bindings: { role: Role }[], mode: Mode) {
  const counts = ['first_frame', 'last_frame', 'reference_image'].map(r => bindings.filter(b => b.role === r).length);
  return mode === 'reference' ? counts[0] === 0 && counts[1] === 0 && counts[2] >= 1 && counts[2] <= 4 : counts[0] === 1 && counts[1] === (mode === 'start_end_frame' ? 1 : 0) && counts[2] === 0;
}
export function parsePlan(x: unknown): Plan {
  pending(x);
  if (!isRecord(x) || !id(x.plan_id) || !date(x.created_at) || !digest(x.content_hash) || !isRecord(x.content) || !key(x.content.project_id) || x.content.template_version !== 1 || !Array.isArray(x.content.targets) || x.content.targets.length < 1 || x.content.targets.length > 20 || typeof x.current !== 'boolean') return invalid();
  const project = x.content.project_id, opts = options(x.content.options);
  const targets = x.content.targets.map((t): Target => {
    if (!isRecord(t) || !key(t.segment_id) || !integer(t.duration, 60) || !digest(t.source_hash) || !text(t.prompt, 250000) || !Array.isArray(t.shots) || t.shots.length < 1 || t.shots.length > 100 || !Array.isArray(t.bindings) || t.bindings.length < 1 || t.bindings.length > 20) return invalid();
    const shots = t.shots.map(s => {
      if (!isRecord(s) || !integer(s.shot_number) || typeof s.duration !== 'number' || !Number.isFinite(s.duration) || s.duration <= 0 || s.duration > 60 || !text(s.content, 10000)) return invalid();
      return { shot_number: s.shot_number, duration: s.duration, content: s.content };
    });
    const bindings = t.bindings.map((b): Binding => {
      if (!isRecord(b) || !id(b.reference_id) || !id(b.asset_id) || !integer(b.revision) || !Object.hasOwn(ROLES, String(b.role)) || !['character', 'setting', 'prop', 'image'].includes(String(b.kind)) || !isRecord(b.version) || !integer(b.version.number) || !isRecord(b.target)) return invalid();
      // A pinned version can be v2+ without carrying the entire asset history.
      const version = { ...parseAsset({ asset_id: b.asset_id, kind: b.kind, versions: [{ ...b.version, number: 1 }] }).versions[0], number: b.version.number };
      const target = b.target;
      if (target.project_id !== project || target.segment_id !== null && target.segment_id !== t.segment_id || target.shot_number !== null && (!integer(target.shot_number) || target.segment_id === null || !shots.some(s => s.shot_number === target.shot_number)) || (b.role === 'context' ? b.kind === 'image' || version.media !== null : b.kind !== 'image' || !version.media)) return invalid();
      return { reference_id: b.reference_id, asset_id: b.asset_id, revision: b.revision, role: b.role as Role, kind: b.kind as Binding['kind'], version, target: { project_id: project, segment_id: target.segment_id as string | null, shot_number: target.shot_number as number | null } };
    });
    if (!unique(shots, s => s.shot_number) || !unique(bindings, b => b.reference_id) || !validRoles(bindings, opts.mode)) return invalid();
    return { segment_id: t.segment_id, duration: t.duration, source_hash: t.source_hash, prompt: t.prompt, shots, bindings };
  });
  if (!unique(targets, t => t.segment_id)) return invalid();
  let confirmation: Plan['confirmation'] = null;
  if (x.confirmation !== null) {
    const c = x.confirmation;
    if (!isRecord(c) || c.content_hash !== x.content_hash || c.scope !== 'local_input_only' || !date(c.confirmed_at)) return invalid();
    confirmation = { confirmed_at: c.confirmed_at, content_hash: x.content_hash, scope: 'local_input_only' };
  }
  if (x.status !== (confirmation ? 'confirmed_local' : 'draft') || !Array.isArray(x.impacts) || x.impacts.length !== targets.length || !Array.isArray(x.execution_blockers) || x.execution_blockers.length !== 3 || !unique(x.execution_blockers, b => b) || !x.execution_blockers.every(b => Object.hasOwn(BLOCKERS, String(b)))) return invalid();
  const impacts = x.impacts.map(i => {
    if (!isRecord(i) || !key(i.segment_id) || !targets.some(t => t.segment_id === i.segment_id) || !Array.isArray(i.reasons) || !unique(i.reasons, r => r) || !i.reasons.every(r => Object.hasOwn(IMPACTS, String(r))) || !Array.isArray(i.newer_version_references) || !unique(i.newer_version_references, r => r) || !i.newer_version_references.every(r => id(r) && targets.find(t => t.segment_id === i.segment_id)?.bindings.some(b => b.reference_id === r))) return invalid();
    return { segment_id: i.segment_id, reasons: i.reasons as (keyof typeof IMPACTS)[], newer_version_references: i.newer_version_references as string[] };
  });
  if (!unique(impacts, i => i.segment_id) || x.current !== impacts.every(i => i.reasons.length === 0)) return invalid();
  return { plan_id: x.plan_id, created_at: x.created_at, content_hash: x.content_hash, content: { project_id: project, template_version: 1, options: opts, targets }, status: x.status as Plan['status'], confirmation, current: x.current, impacts, cost: { status: 'pending_confirmation', amount: null, currency: null }, execution_allowed: false, execution_blockers: x.execution_blockers as Plan['execution_blockers'] };
}
export function parsePlans(x: unknown, project: string) {
  if (!Array.isArray(x)) return invalid();
  const plans = x.map(parsePlan);
  if (!unique(plans, p => p.plan_id) || plans.some(p => p.content.project_id !== project)) return invalid();
  return plans.sort((a, b) => b.created_at.localeCompare(a.created_at) || b.plan_id.localeCompare(a.plan_id));
}
export function eligibleReferences(data: Workspace, segment: string) {
  const targets = data.project ? targetOptions(data.project) : [];
  return data.refs.filter(r => r.target_exists && r.project_id === data.project?.session_id && (r.segment_id === null || r.segment_id === segment) && (r.shot_number === null || targets.some(t => t.segment_id === segment && t.shot_number === r.shot_number))).flatMap(ref => {
    const asset = data.assets.find(a => a.asset_id === ref.asset_id), version = asset?.versions.find(v => v.version_id === ref.version_id);
    return asset && version && asset.kind !== 'video' ? [{ ref, asset, version }] : [];
  });
}
export function newDraft(data: Workspace): Draft {
  const meta = data.project?.meta ?? data.project, mode = meta?.video_generation_mode && Object.hasOwn(MODES, meta.video_generation_mode) ? meta.video_generation_mode : 'first_frame';
  const model = (mode === 'first_frame' ? meta?.video_first_frame_model : mode === 'start_end_frame' ? meta?.video_start_end_model : meta?.video_reference_model) || meta?.video_model || '';
  return { segment: data.project ? targetOptions(data.project).find(t => t.segment_id && t.shot_number === null)?.segment_id ?? '' : '', duration: '5', roles: {}, options: { model, mode, video_ratio: '16:9', resolution: '720P', sound: 'off', shot_type: 'multi', style: 'realistic' } };
}
export function buildInput(data: Workspace, draft: Draft): Input {
  if (!data.project || !targetOptions(data.project).some(t => t.segment_id === draft.segment && t.shot_number === null)) throw new Error('请先在项目里准备有效分镜片段');
  const opts = options({ ...draft.options, model: draft.options.model.trim(), style: draft.options.style.trim() });
  if (!/^\d+$/.test(draft.duration) || !integer(Number(draft.duration), 60)) throw new Error('时长需为 1—60 的整数秒');
  const eligible = eligibleReferences(data, draft.segment);
  const bindings = Object.entries(draft.roles).filter(([, role]) => role !== '').map(([reference_id, role]) => {
    const selected = eligible.find(r => r.ref.reference_id === reference_id);
    if (!selected || !Object.hasOwn(ROLES, role) || (role === 'context' ? selected.asset.kind === 'image' : selected.asset.kind !== 'image')) throw new Error('素材引用已变化，请重新选择适用的引用与用途');
    return { reference_id, role: role as Role };
  });
  if (bindings.length > 20 || !validRoles(bindings, opts.mode)) throw new Error(opts.mode === 'reference' ? '请选择 1—4 张参考图；文字设定可选' : opts.mode === 'start_end_frame' ? '请分别指定一张首帧和一张尾帧；同一引用只使用一次' : '请指定一张首帧；文字设定可选');
  return { project_id: data.project.session_id, options: opts, targets: [{ segment_id: draft.segment, duration: Number(draft.duration), bindings }] };
}
export class PlanApiError extends Error {
  constructor(message: string, public status = 0, public uncertain = false) { super(message); }
}
async function request(path: string, method = 'GET', body?: unknown): Promise<unknown> {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), method === 'GET' ? 20000 : 60000);
  try {
    const response = await fetch(path, { method, signal: controller.signal, cache: 'no-store', ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) });
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) throw new PlanApiError(apiErrorMessage(payload, '生成计划操作暂不可用'), response.status, method !== 'GET' && response.status >= 500);
    return payload;
  } catch (e) {
    if (e instanceof PlanApiError) throw e;
    throw new PlanApiError(method === 'GET' ? '无法读取计划，请检查本地服务后重新读取' : '提交结果未确认，请重新读取并核对；不要重复提交', 0, method !== 'GET');
  } finally { clearTimeout(timer); }
}
export async function readWorkspace(project: string): Promise<Workspace> {
  if (!key(project)) return invalid();
  const [plans, rawProject, assets, refs] = await Promise.all([
    request(`/api/project/${encodeURIComponent(project)}/generation-plans`).then(x => parsePlans(x, project)),
    request(`/api/project/${encodeURIComponent(project)}/status`).catch(e => { if (e instanceof PlanApiError && e.status === 404) return null; throw e; }),
    request('/api/assets').then(parseAssets), request(`/api/project/${encodeURIComponent(project)}/asset-references`).then(parseReferences),
  ]);
  const snapshot = rawProject === null ? null : parseProjectSnapshot(rawProject);
  if (snapshot && snapshot.session_id !== project || refs.some(r => r.project_id !== project || !assets.some(a => a.asset_id === r.asset_id && a.versions.some(v => v.version_id === r.version_id)))) return invalid();
  return { project: snapshot, assets, refs, plans };
}
async function writePlan(path: string, body: unknown, verify: (p: Plan) => boolean) {
  const payload = await request(path, 'POST', body);
  try { const plan = parsePlan(payload); if (!verify(plan)) return invalid(); return plan; }
  catch { throw new PlanApiError('提交结果未确认，请重新读取计划并核对；不要重复提交', 0, true); }
}
export function savePlan(input: Input) {
  return writePlan('/api/generation-plans', input, p => p.status === 'draft' && p.content.project_id === input.project_id && JSON.stringify(p.content.options) === JSON.stringify(input.options) && p.content.targets.length === input.targets.length && input.targets.every(t => p.content.targets.some(r => r.segment_id === t.segment_id && r.duration === t.duration && r.bindings.length === t.bindings.length && t.bindings.every(b => r.bindings.some(v => v.reference_id === b.reference_id && v.role === b.role)))));
}
export function confirmPlan(plan: Plan) {
  if (!plan.current) throw new Error('计划已受修改影响，请创建新计划');
  return writePlan(`/api/generation-plans/${plan.plan_id}/confirm`, { content_hash: plan.content_hash, local_only: true }, p => p.plan_id === plan.plan_id && p.content_hash === plan.content_hash && p.status === 'confirmed_local' && p.current);
}
export async function checkPlan(plan: Plan): Promise<{ count: number; images: number }> {
  if (!plan.current || !plan.confirmation) throw new Error('请先核对并确认当前计划');
  const x = await request(`/api/generation-plans/${plan.plan_id}/dry-run`, 'POST');
  try {
    pending(x);
    if (!isRecord(x) || x.plan_id !== plan.plan_id || x.content_hash !== plan.content_hash || JSON.stringify(options(x.options)) !== JSON.stringify(plan.content.options) || !Array.isArray(x.targets) || x.targets.length !== plan.content.targets.length || !unique(x.targets, t => isRecord(t) ? t.segment_id : null)) return invalid();
    let images = 0;
    for (const t of x.targets) {
      if (!isRecord(t)) return invalid();
      const original = plan.content.targets.find(v => v.segment_id === t.segment_id);
      if (!original || t.duration !== original.duration || t.prompt !== original.prompt || !Array.isArray(t.reference_image_urls) || !t.reference_image_urls.every(u => typeof u === 'string' && u.length > 0)) return invalid();
      const counts = original.bindings.map(b => b.role);
      if (counts.includes('first_frame') ? typeof t.first_frame_url !== 'string' || !t.first_frame_url : t.first_frame_url !== null) return invalid();
      if (counts.includes('last_frame') ? typeof t.last_frame_url !== 'string' || !t.last_frame_url : t.last_frame_url !== null) return invalid();
      if (t.reference_image_urls.length !== counts.filter(r => r === 'reference_image').length) return invalid();
      images += counts.filter(r => r !== 'context').length;
    }
    // Backend private file paths are deliberately discarded, never rendered or fetched.
    return { count: x.targets.length, images };
  } catch { throw new PlanApiError('输入检查响应不完整，请重新读取并核对', 0, true); }
}
