import { apiErrorMessage, isRecord, parseProjectSnapshot } from './workflowTypes';
import type { ProjectSnapshot } from './workflowTypes';

export const ASSET_KINDS = { character: '角色', setting: '场景', prop: '道具', image: '图片', video: '视频' } as const;

export function displayDefinition(version: AssetVersion): string {
  if (version.source.type !== 'script') return version.definition;
  try {
    const item: unknown = JSON.parse(version.definition);
    if (!isRecord(item)) return version.definition;
    const labels: Record<string, string> = { description: '描述', appearance: '外观', personality: '性格', age: '年龄', gender: '性别', role: '角色定位', background: '背景', environment: '环境', lighting: '光线', atmosphere: '氛围', style: '风格', clothing: '服装', voice: '声音', notes: '补充设定' };
    const content = (value: unknown): string => Array.isArray(value) ? value.map(content).filter(Boolean).join('、') : isRecord(value) ? Object.entries(value).filter(([key]) => key !== 'id').map(([key, value]) => `${labels[key] ?? '其他设定'}：${content(value)}`).join('；') : value == null ? '' : String(value);
    const lines = Object.entries(item).filter(([key]) => !['id', 'name'].includes(key)).map(([key, value]) => `${labels[key] ?? '其他设定'}：${content(value)}`);
    return lines.length ? lines.join('\n') : version.name;
  } catch { return version.definition; }
}
export type AssetKind = keyof typeof ASSET_KINDS;
export type TextKind = 'character' | 'setting' | 'prop';
export interface AssetSource { type: 'manual' | 'script' | 'artifact' | 'generation_task' | 'upload'; project_id?: string; item_id?: string; stage?: string; item_type?: string; upload_id?: string }
export interface AssetMedia { file_id: string; mime_type: string; sha256: string; byte_size: number; width: number; height: number; duration: number | null }
export interface AssetVersion { version_id: string; number: number; name: string; definition: string; created_at: string; source: AssetSource; media: AssetMedia | null }
export interface Asset { asset_id: string; kind: AssetKind; versions: AssetVersion[] }
export interface AssetTarget { segment_id: string | null; shot_number: number | null }
export interface AssetReference extends AssetTarget { reference_id: string; asset_id: string; version_id: string; project_id: string; revision: number; history: { version_id: string; selected_at: string }[]; has_newer_version: boolean; target_exists: boolean }
export interface TargetOption extends AssetTarget { key: string; label: string }
export interface ImportOption { key: string; label: string; mode: 'script' | 'media'; kind?: 'character' | 'setting'; stage?: string; item_type?: string; item_id: string }
const idPattern = /^[a-f0-9]{32}$/;
const projectPattern = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
const id = (v: unknown): v is string => typeof v === 'string' && idPattern.test(v);
const projectId = (v: unknown): v is string => typeof v === 'string' && projectPattern.test(v);
const positive = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v) && v > 0;
const text = (v: unknown, limit: number): v is string => typeof v === 'string' && v.trim().length > 0 && [...v].length <= limit;
function invalid(): never { throw new Error('素材数据格式异常，请重新读取'); }

function parseSource(value: unknown): AssetSource {
  if (!isRecord(value)) return invalid();
  if (value.type === 'manual') {
    if (['project_id', 'item_id', 'stage'].some(k => value[k] != null)) return invalid();
    return { type: 'manual' };
  }
  if (!projectId(value.project_id)) return invalid();
  if (value.type === 'upload' && id(value.upload_id)) return { type: 'upload', project_id: value.project_id, upload_id: value.upload_id };
  if (!projectId(value.item_id)) return invalid();
  if (value.type === 'script' && value.stage === 'script_generation') return { type: 'script', project_id: value.project_id, item_id: value.item_id, stage: value.stage };
  const pairs: Record<string, string[]> = { character_design: ['characters', 'settings'], reference_generation: ['scenes'], video_generation: ['clips'] };
  if (typeof value.stage !== 'string' || typeof value.item_type !== 'string' || !pairs[value.stage]?.includes(value.item_type)) return invalid();
  if (value.type === 'generation_task') {
    if (value.stage !== 'video_generation' || !id(value.task_id) || !id(value.plan_id) || typeof value.content_hash !== 'string' || !/^[a-f0-9]{64}$/.test(value.content_hash) || value.simulation_only !== true) return invalid();
  } else if (value.type !== 'artifact') return invalid();
  return { type: value.type, project_id: value.project_id, stage: value.stage, item_type: value.item_type, item_id: value.item_id };
}
export function parseAsset(value: unknown): Asset {
  if (!isRecord(value) || !id(value.asset_id) || typeof value.kind !== 'string' || !Object.hasOwn(ASSET_KINDS, value.kind) || !Array.isArray(value.versions) || !value.versions.length) return invalid();
  const kind = value.kind as AssetKind, seen = new Set<string>();
  const versions = value.versions.map((v, index): AssetVersion => {
    if (!isRecord(v) || !id(v.version_id) || seen.has(v.version_id) || v.number !== index + 1 || !text(v.name, 100) || !text(v.definition, 10000) || typeof v.created_at !== 'string' || !Number.isFinite(Date.parse(v.created_at))) return invalid();
    seen.add(v.version_id);
    const source = parseSource(v.source);
    let media: AssetMedia | null = null;
    if (kind === 'image' || kind === 'video') {
      const m = v.media;
      if (!isRecord(m) || !id(m.file_id) || typeof m.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(m.sha256) || !positive(m.byte_size) || !positive(m.width) || !positive(m.height) || !['artifact', 'generation_task', 'upload'].includes(source.type)) return invalid();
      if (kind === 'video' ? m.mime_type !== 'video/mp4' || m.byte_size > 100 * 1024 * 1024 || typeof m.duration !== 'number' || !Number.isFinite(m.duration) || m.duration <= 0 : !['image/jpeg', 'image/png', 'image/webp', 'image/bmp'].includes(String(m.mime_type)) || m.byte_size > 10 * 1024 * 1024 || m.duration != null || source.type === 'generation_task') return invalid();
      media = { file_id: m.file_id, mime_type: String(m.mime_type), sha256: m.sha256, byte_size: m.byte_size, width: m.width, height: m.height, duration: typeof m.duration === 'number' ? m.duration : null };
    } else if (v.media != null || !['manual', 'script'].includes(source.type)) return invalid();
    return { version_id: v.version_id, number: index + 1, name: v.name, definition: v.definition, created_at: v.created_at, source, media };
  });
  return { asset_id: value.asset_id, kind, versions };
}
export function parseAssets(value: unknown): Asset[] {
  if (!Array.isArray(value)) return invalid();
  const assets = value.map(parseAsset);
  if (new Set(assets.map(a => a.asset_id)).size !== assets.length) return invalid();
  return assets;
}
export function parseReference(value: unknown): AssetReference {
  if (!isRecord(value) || !id(value.reference_id) || !id(value.asset_id) || !id(value.version_id) || !projectId(value.project_id) || !positive(value.revision) || typeof value.has_newer_version !== 'boolean' || typeof value.target_exists !== 'boolean' || !Array.isArray(value.history) || value.history.length !== value.revision) return invalid();
  const segment_id = value.segment_id ?? null, shot_number = value.shot_number ?? null;
  if (segment_id !== null && !projectId(segment_id) || shot_number !== null && (!positive(shot_number) || !segment_id)) return invalid();
  const history = value.history.map(h => {
    if (!isRecord(h) || !id(h.version_id) || typeof h.selected_at !== 'string' || !Number.isFinite(Date.parse(h.selected_at))) return invalid();
    return { version_id: h.version_id, selected_at: h.selected_at };
  });
  if (history.at(-1)?.version_id !== value.version_id) return invalid();
  return { reference_id: value.reference_id, asset_id: value.asset_id, version_id: value.version_id, project_id: value.project_id, revision: value.revision, history, segment_id, shot_number, has_newer_version: value.has_newer_version, target_exists: value.target_exists };
}
export function parseReferences(value: unknown): AssetReference[] {
  if (!Array.isArray(value)) return invalid();
  const refs = value.map(parseReference);
  if (new Set(refs.map(r => r.reference_id)).size !== refs.length) return invalid();
  return refs;
}
export function filterAssets(assets: Asset[], refs: AssetReference[], project: string, scope: 'project' | 'all', kind: AssetKind | 'all', query: string): Asset[] {
  const bound = new Set(refs.filter(r => r.project_id === project).map(r => r.asset_id));
  const q = query.trim().toLocaleLowerCase();
  return assets.filter(a => (scope === 'all' || bound.has(a.asset_id) || a.versions.some(v => v.source.project_id === project)) && (kind === 'all' || a.kind === kind) && (!q || a.versions.some(v => v.name.toLocaleLowerCase().includes(q)))).sort((a, b) => b.versions.at(-1)!.created_at.localeCompare(a.versions.at(-1)!.created_at));
}
export function targetOptions(snapshot: ProjectSnapshot): TargetOption[] {
  const result: TargetOption[] = [{ key: 'project', label: '整个项目', segment_id: null, shot_number: null }];
  const board: unknown = snapshot.artifacts?.storyboard;
  const episodes = isRecord(board) ? board.episodes : board;
  if (!Array.isArray(episodes)) return result;
  const segments: { id: string; label: string; shots: unknown }[] = [];
  for (const episode of episodes) {
    if (!isRecord(episode) || !Array.isArray(episode.segments)) continue;
    episode.segments.forEach((segment, i) => {
      if (!isRecord(segment)) return;
      const ep = episode.episode_number;
      const fallback = typeof ep === 'number' && Number.isInteger(ep) ? `seg_${String(ep).padStart(2, '0')}_${String(i + 1).padStart(2, '0')}` : undefined;
      const key = segment.segment_id ?? fallback;
      if (projectId(key)) segments.push({ id: key, label: `${positive(ep) ? `第 ${ep} 集 · ` : ''}片段 ${i + 1}`, shots: segment.shots });
    });
  }
  for (const segment of segments) {
    if (segments.filter(s => s.id === segment.id).length !== 1) continue;
    result.push({ key: segment.id, label: segment.label, segment_id: segment.id, shot_number: null });
    if (Array.isArray(segment.shots)) for (const shot of segment.shots) {
      if (!isRecord(shot) || !positive(shot.shot_number) || segment.shots.filter(s => isRecord(s) && s.shot_number === shot.shot_number).length !== 1) continue;
      result.push({ key: `${segment.id}:${shot.shot_number}`, label: `${segment.label} · 镜头 ${shot.shot_number}`, segment_id: segment.id, shot_number: shot.shot_number });
    }
  }
  return result;
}
export function importOptions(snapshot: ProjectSnapshot): ImportOption[] {
  const result: ImportOption[] = [];
  const add = (value: unknown, mode: 'script' | 'media', group: string, stage: string, kind?: 'character' | 'setting') => {
    const entries = Array.isArray(value) ? value : isRecord(value) ? Object.entries(value).map(([key, item]) => isRecord(item) ? { ...item, id: key } : { id: key, name: key, selected: item }) : [];
    for (const item of entries) {
      if (!isRecord(item) || !projectId(item.id) || entries.filter(x => isRecord(x) && x.id === item.id).length !== 1 || mode === 'media' && (typeof item.selected !== 'string' || !item.selected)) continue;
      result.push({ key: `${stage}:${group}:${item.id}`, label: `${mode === 'script' ? '文字' : stage === 'video_generation' ? '视频' : '图片'} · ${typeof item.name === 'string' ? item.name : item.id}`, mode, kind, stage, item_type: group, item_id: item.id });
    }
  };
  const artifacts = snapshot.artifacts ?? {}, script = artifacts.script_generation;
  add(script?.characters, 'script', 'characters', 'script_generation', 'character');
  add(script?.settings, 'script', 'settings', 'script_generation', 'setting');
  const design = artifacts.character_design;
  add(design?.characters, 'media', 'characters', 'character_design'); add(design?.settings, 'media', 'settings', 'character_design');
  const reference = artifacts.reference_generation ?? artifacts.reference;
  add(reference?.scenes ?? reference?.scene_images ?? reference?.scene_map, 'media', 'scenes', 'reference_generation');
  const video = artifacts.video_generation ?? artifacts.video;
  add(video?.clips ?? video?.video_clips ?? video?.video_map, 'media', 'clips', 'video_generation');
  return result;
}
export const mediaUrl = (asset: string, version: string) => `/api/assets/${encodeURIComponent(asset)}/versions/${encodeURIComponent(version)}/media`;
export function sourceLabel(source: AssetSource): string { return { manual: '手动创建', script: '剧本导入', artifact: '项目产物', generation_task: '人工任务样例', upload: '本地上传' }[source.type]; }

export class AssetApiError extends Error {
  constructor(message: string, public code: string, public status: number, public uncertain = false) { super(message); }
}
async function request(path: string, options: RequestInit = {}): Promise<unknown> {
  const write = options.method && options.method !== 'GET', controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), write ? 120000 : 20000);
  try {
    const response = await fetch(path, { ...options, signal: controller.signal });
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const code = isRecord(payload) && isRecord(payload.error) && typeof payload.error.code === 'string' ? payload.error.code : 'REQUEST_FAILED';
      throw new AssetApiError(apiErrorMessage(payload, '素材操作暂不可用'), code, response.status, Boolean(write && response.status >= 500));
    }
    return payload;
  } catch (error) {
    if (error instanceof AssetApiError) throw error;
    throw new AssetApiError(write ? '提交结果未确认，请先重新读取素材与版本再核对' : '无法读取素材，请检查本地服务后重新读取', 'CONNECTION_FAILED', 0, Boolean(write));
  } finally { clearTimeout(timer); }
}
const jsonPost = (payload: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
async function writeAsset(path: string, options: RequestInit): Promise<Asset> {
  const payload = await request(path, options);
  try { return parseAsset(payload); } catch { throw new AssetApiError('提交结果未确认，请先重新读取素材与版本再核对', 'INVALID_RESPONSE', 0, true); }
}
export async function fetchAssets() { return parseAssets(await request('/api/assets')); }
export async function fetchAssetProject(project: string) { return parseProjectSnapshot(await request(`/api/project/${encodeURIComponent(project)}/status`)); }
export async function fetchReferences(project: string) { return parseReferences(await request(`/api/project/${encodeURIComponent(project)}/asset-references`)); }
export async function fetchUsage(asset: string) { return parseReferences(await request(`/api/assets/${encodeURIComponent(asset)}/usage`)); }
export async function createTextAsset(kind: TextKind, name: string, definition: string) { return writeAsset('/api/assets', jsonPost({ kind, name, definition })); }
export async function addTextVersion(asset: string, name: string, definition: string) {
  const result = await writeAsset(`/api/assets/${encodeURIComponent(asset)}/versions`, jsonPost({ name, definition }));
  if (result.asset_id !== asset) throw new AssetApiError('提交结果未确认，请先重新读取素材与版本再核对', 'INVALID_RESPONSE', 0, true);
  return result;
}
export async function importAsset(project: string, option: ImportOption) {
  return writeAsset(`/api/assets/import-${option.mode}`, jsonPost(option.mode === 'script' ? { project_id: project, kind: option.kind, item_id: option.item_id } : { project_id: project, stage: option.stage, item_type: option.item_type, item_id: option.item_id }));
}
export async function uploadAsset(project: string, uploadId: string, file: File, name: string, definition: string, asset?: string) {
  const body = new FormData();
  body.append('upload_id', uploadId); body.append('name', name); body.append('definition', definition); body.append('file', file);
  if (asset) body.append('asset_id', asset);
  const result = await writeAsset(`/api/project/${encodeURIComponent(project)}/assets/upload`, { method: 'POST', body });
  if (asset && result.asset_id !== asset || !result.versions.some(v => v.source.upload_id === uploadId && v.source.project_id === project)) throw new AssetApiError('上传结果未确认，请先重新读取核对，重试时沿用此上传编号', 'INVALID_RESPONSE', 0, true);
  return result;
}
async function writeReference(path: string, options: RequestInit) {
  const payload = await request(path, options);
  try { return parseReference(payload); } catch { throw new AssetApiError('引用提交结果未确认，请先重新读取再核对', 'INVALID_RESPONSE', 0, true); }
}
export async function bindAsset(project: string, asset: string, version: string, target: AssetTarget) {
  const result = await writeReference('/api/asset-references', jsonPost({ project_id: project, asset_id: asset, version_id: version, segment_id: target.segment_id, shot_number: target.shot_number }));
  if (result.project_id !== project || result.asset_id !== asset || result.version_id !== version || result.segment_id !== target.segment_id || result.shot_number !== target.shot_number) throw new AssetApiError('引用提交结果未确认，请先重新读取再核对', 'INVALID_RESPONSE', 0, true);
  return result;
}
export async function updateReference(ref: AssetReference, version: string) {
  const result = await writeReference(`/api/asset-references/${encodeURIComponent(ref.reference_id)}`, { ...jsonPost({ version_id: version, expected_revision: ref.revision }), method: 'PATCH' });
  if (result.reference_id !== ref.reference_id || result.asset_id !== ref.asset_id || result.project_id !== ref.project_id || result.version_id !== version || result.revision !== ref.revision + (version === ref.version_id ? 0 : 1)) throw new AssetApiError('引用提交结果未确认，请先重新读取再核对', 'INVALID_RESPONSE', 0, true);
  return result;
}
