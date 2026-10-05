import { isRecord } from './workflowTypes';

export const STAGES = { script_generation: '剧本', storyboard: '分镜' } as const;
export type Stage = keyof typeof STAGES;
export type Path = (string | number)[];
export interface TextField { path: Path; label: string; value: string }
export interface Change { path: Path; before: string; after: string }
export interface Impact { segment_id: string; shot_number: number | null; reason: string }
export interface Version { version_id: string; number: number; stage: Stage; source: 'baseline' | 'external' | 'edit' | 'restore'; created_at: string; current: boolean; fields: TextField[]; changes: Change[]; impacts: Impact[]; restored_from: string | null }
export interface Workspace { project_id: string; token: string; running: boolean; fields: Record<Stage, TextField[]>; versions: Version[]; untracked_stages: Stage[]; impacts: Impact[] }
export interface Input { expected_token: string; stage: Stage; path?: Path; value?: string; restore_version_id?: string }
export interface Preview { stage: Stage; expected_token: string; preview_token: string; changes: Change[]; impacts: Impact[]; restore_version_id: string | null }
export interface SaveInput extends Input { preview_token: string; request_id: string; acknowledged: true }
const id = (x: unknown): x is string => typeof x === 'string' && /^[a-f0-9]{32}$/.test(x);
const hash = (x: unknown): x is string => typeof x === 'string' && /^[a-f0-9]{64}$/.test(x);
const stage = (x: unknown): x is Stage => x === 'script_generation' || x === 'storyboard';
const bool = (x: unknown) => typeof x === 'boolean';
const text = (x: unknown): x is string => typeof x === 'string';
const path = (x: unknown): x is Path => Array.isArray(x) && x.length <= 10 && x.every(p => typeof p === 'string' || (Number.isSafeInteger(p) && p >= 0));
export const pathKey = (p: Path) => JSON.stringify(p);
function invalid(): never { throw new RevisionError('文字版本记录不完整，请重新读取核对', 0, true); }
function parseFields(x: unknown): TextField[] {
  if (!Array.isArray(x) || x.length > 500 || !x.every(f => isRecord(f) && path(f.path) && f.path.length > 0 && text(f.label) && text(f.value)) || new Set(x.map(f => pathKey(f.path))).size !== x.length) return invalid();
  return x as TextField[];
}
function parseChanges(x: unknown): Change[] {
  if (!Array.isArray(x) || !x.every(c => isRecord(c) && path(c.path) && text(c.before) && text(c.after) && c.before !== c.after) || new Set(x.map(c => pathKey(c.path))).size !== x.length) return invalid();
  return x as Change[];
}
function parseImpacts(x: unknown): Impact[] {
  if (!Array.isArray(x) || !x.every(i => isRecord(i) && text(i.segment_id) && i.segment_id.length > 0 && (i.shot_number === null || (typeof i.shot_number === 'number' && Number.isSafeInteger(i.shot_number) && i.shot_number > 0)) && text(i.reason))) return invalid();
  return x as Impact[];
}
export function parseWorkspace(x: unknown, projectId: string): Workspace {
  if (!isRecord(x) || x.project_id !== projectId || !hash(x.token) || !bool(x.running) || !isRecord(x.fields) || !Array.isArray(x.versions) || !Array.isArray(x.untracked_stages) || !x.untracked_stages.every(stage) || new Set(x.untracked_stages).size !== x.untracked_stages.length) return invalid();
  const fields = { script_generation: parseFields(x.fields.script_generation), storyboard: parseFields(x.fields.storyboard) };
  const versions: Version[] = x.versions.map((v, i) => {
    if (!isRecord(v) || !id(v.version_id) || v.number !== i + 1 || !stage(v.stage) || !['baseline','external','edit','restore'].includes(String(v.source)) || !text(v.created_at) || !Number.isFinite(Date.parse(v.created_at)) || !bool(v.current) || !(v.restored_from === null || id(v.restored_from))) return invalid();
    return { ...v, fields: parseFields(v.fields), changes: parseChanges(v.changes), impacts: parseImpacts(v.impacts) } as unknown as Version;
  });
  if (new Set(versions.map(v => v.version_id)).size !== versions.length) return invalid();
  for (const v of versions) {
    if (v.source === 'restore' ? !versions.some(old => old.version_id === v.restored_from && old.stage === v.stage && old.number < v.number) : v.restored_from !== null) return invalid();
    if (v.current && (x.untracked_stages.includes(v.stage) || versions.some(later => later.stage === v.stage && later.number > v.number) || pathKey(v.fields.map(f => pathKey(f.path))) !== pathKey(fields[v.stage].map(f => pathKey(f.path))) || v.fields.some((f, i) => f.value !== fields[v.stage][i]?.value))) return invalid();
  }
  return { project_id: projectId, token: x.token, running: x.running as boolean, fields, versions, untracked_stages: x.untracked_stages as Stage[], impacts: parseImpacts(x.impacts) };
}
export function editInput(workspace: Workspace, selected: Stage, key: string, value: string): Input {
  const field = workspace.fields[selected].find(f => pathKey(f.path) === key);
  if (!field || value.length > 10000 || value === field.value || workspace.running) throw new RevisionError('请选择可修改的文字，填写不同的新内容', 422);
  return { expected_token: workspace.token, stage: selected, path: field.path, value };
}
export function restoreInput(workspace: Workspace, version: Version): Input {
  if (workspace.running || version.current || !workspace.versions.some(v => v.version_id === version.version_id && v.stage === version.stage)) throw new RevisionError('所选版本暂不可恢复，请重新核对', 422);
  return { expected_token: workspace.token, stage: version.stage, restore_version_id: version.version_id };
}
export function parsePreview(x: unknown, input: Input): Preview {
  if (!isRecord(x) || x.stage !== input.stage || x.expected_token !== input.expected_token || !hash(x.preview_token) || x.restore_version_id !== (input.restore_version_id ?? null)) return invalid();
  const changes = parseChanges(x.changes);
  if (!changes.length || (!input.restore_version_id && (changes.length !== 1 || pathKey(changes[0].path) !== pathKey(input.path!) || changes[0].after !== input.value))) return invalid();
  return { stage: input.stage, expected_token: input.expected_token, preview_token: x.preview_token, changes, impacts: parseImpacts(x.impacts), restore_version_id: input.restore_version_id ?? null };
}
export class RevisionError extends Error {
  constructor(message: string, public status: number, public uncertain = false) { super(message); }
}
async function request(projectId: string, suffix = '', input?: unknown, write = false, fetcher: typeof fetch = fetch): Promise<unknown> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetcher(`/api/project/${encodeURIComponent(projectId)}/content-revisions${suffix}`, { method: input ? 'POST' : 'GET', headers: input ? { 'Content-Type': 'application/json' } : undefined, body: input ? JSON.stringify(input) : undefined, cache: 'no-store', signal: controller.signal });
    if (!response.ok) {
      const messages: Record<number,string> = { 404: '项目或版本不存在，请返回项目核对', 409: '文字已变化或项目正在执行，请重新读取并预览', 422: '修改内容不符合要求，请核对对象与新内容', 503: '版本数据暂不可用，请保留数据并检查服务' };
      throw new RevisionError(messages[response.status] ?? '服务暂未完成请求，请重新读取核对', response.status, write && response.status >= 500);
    }
    return await response.json();
  } catch (e) {
    if (e instanceof RevisionError) throw e;
    throw new RevisionError(write ? '保存结果尚未确认，请重新读取版本历史；不会自动重发' : '暂时无法读取，请检查本地服务后重新读取', 0, write);
  } finally { clearTimeout(timer); }
}
export async function readRevisions(projectId: string, fetcher?: typeof fetch) { return parseWorkspace(await request(projectId, '', undefined, false, fetcher), projectId); }
export async function previewRevision(projectId: string, input: Input, fetcher?: typeof fetch) { return parsePreview(await request(projectId, '/preview', input, false, fetcher), input); }
export async function saveRevision(projectId: string, input: SaveInput, fetcher?: typeof fetch) {
  const x = await request(projectId, '', input, true, fetcher);
  if (!isRecord(x) || !id(x.version_id)) return invalid();
  const workspace = parseWorkspace(x.workspace, projectId);
  const version = workspace.versions.find(v => v.version_id === x.version_id);
  if (!version || version.stage !== input.stage || version.source !== (input.restore_version_id ? 'restore' : 'edit') || version.restored_from !== (input.restore_version_id ?? null)) return invalid();
  return { workspace, version_id: x.version_id };
}
