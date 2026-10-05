import { isRecord } from './workflowTypes';

export const STAGES = { script_generation: '剧本', storyboard: '分镜' } as const;
export type Stage = keyof typeof STAGES;
export type Path = (string | number)[];
export interface TextField { path: Path; label: string; value: string }
export interface Change { path: Path; before: string; after: string; before_present?: boolean; after_present?: boolean }
export interface Impact { segment_id: string; shot_number: number | null; reason: string }
export interface Version { version_id: string; number: number; stage: Stage; source: 'baseline' | 'external' | 'edit' | 'restore'; created_at: string; current: boolean; fields: TextField[]; changes: Change[]; impacts: Impact[]; restored_from: string | null }
export interface Workspace { project_id: string; token: string; running: boolean; fields: Record<Stage, TextField[]>; versions: Version[]; untracked_stages: Stage[]; impacts: Impact[] }
export interface Input { expected_token: string; stage: Stage; path?: Path; value?: string; restore_version_id?: string; replacement?: Record<string, unknown> }
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
function renderedTypeMayDiffer(value: string): boolean {
  // Legacy summaries render text and JSON values alike. Full snapshot comparison
  // restores the types; identical ordinary text still cannot represent a change.
  try { const parsed = JSON.parse(value); return parsed === null || (typeof parsed !== 'string' && (typeof parsed !== 'number' || Number.isFinite(parsed))); } catch { return false; }
}
function parseChanges(x: unknown): Change[] {
  if (!Array.isArray(x) || !x.every(c => isRecord(c) && path(c.path) && text(c.before) && text(c.after) && (c.before_present === undefined || bool(c.before_present)) && (c.after_present === undefined || bool(c.after_present)) && (c.before !== c.after || (c.before_present ?? true) !== (c.after_present ?? true) || renderedTypeMayDiffer(c.before))) || new Set(x.map(c => pathKey(c.path))).size !== x.length) return invalid();
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
export function parsePreview(x: unknown, input: Input, before?: Record<string, unknown>): Preview {
  if (!isRecord(x) || x.stage !== input.stage || x.expected_token !== input.expected_token || !hash(x.preview_token) || x.restore_version_id !== (input.restore_version_id ?? null)) return invalid();
  const changes = parseChanges(x.changes);
  if (!changes.length || (!input.restore_version_id && !input.replacement && (changes.length !== 1 || pathKey(changes[0].path) !== pathKey(input.path!) || changes[0].after !== input.value))) return invalid();
  if (input.replacement) verifyManualChanges(changes, before, input.replacement);
  return { stage: input.stage, expected_token: input.expected_token, preview_token: x.preview_token, changes, impacts: parseImpacts(x.impacts), restore_version_id: input.restore_version_id ?? null };
}
export class RevisionError extends Error {
  constructor(message: string, public status: number, public uncertain = false) { super(message); }
}
export async function revisionRequest(projectId: string, suffix = '', input?: unknown, write = false, fetcher: typeof fetch = fetch): Promise<unknown> {
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
export async function readRevisions(projectId: string, fetcher?: typeof fetch) { return parseWorkspace(await revisionRequest(projectId, '', undefined, false, fetcher), projectId); }
export async function previewRevision(projectId: string, input: Input, fetcher?: typeof fetch) { return parsePreview(await revisionRequest(projectId, '/preview', input, false, fetcher), input); }
export async function saveRevision(projectId: string, input: SaveInput, fetcher?: typeof fetch) {
  const x = await revisionRequest(projectId, '', input, true, fetcher);
  if (!isRecord(x) || !id(x.version_id)) return invalid();
  const workspace = parseWorkspace(x.workspace, projectId);
  const version = workspace.versions.find(v => v.version_id === x.version_id);
  if (!version || version.stage !== input.stage || version.source !== (input.restore_version_id ? 'restore' : 'edit') || version.restored_from !== (input.restore_version_id ?? null)) return invalid();
    if (input.replacement && (!version.current || !isRecord(x.artifact) || canonical(x.artifact) !== canonical(input.replacement))) return invalid();
  return { workspace, version_id: x.version_id, artifact: isRecord(x.artifact) ? x.artifact : undefined };
}

export interface Editor { workspace: Workspace; artifact: Record<string, unknown> }
function canonical(value: unknown): string {
  return JSON.stringify(value, (_, item) => isRecord(item) ? Object.fromEntries(Object.keys(item).sort().map(key => [key,item[key]])) : item);
}
export function manualContent(value: unknown): Record<string, unknown> {
  let count = 0;
  function check(v: unknown, depth: number) {
    if (++count > 20000 || depth > 20) throw new RevisionError('编辑内容过大或层级过深，请保留草稿',422);
    if (Array.isArray(v)) v.forEach(item => check(item,depth+1));
    else if (isRecord(v)) for (const [key,item] of Object.entries(v)) {
      if (['__proto__','prototype','constructor'].includes(key)) throw new RevisionError('JSON 包含不支持的字段',422);
      check(item,depth+1);
    } else if (!(v === null || typeof v === 'string' || typeof v === 'boolean' || (typeof v === 'number' && Number.isFinite(v)))) throw new RevisionError('JSON 内容类型不支持',422);
  }
  if (!isRecord(value)) throw new RevisionError('JSON 必须是完整对象，请保留草稿并核对',422);
  check(value,0);
  if (new TextEncoder().encode(JSON.stringify(value)).length > 2*1024*1024) throw new RevisionError('编辑内容超过 2 MiB，请保留草稿',422);
  return value;
}
export function parseEditor(x: unknown, projectId: string, selected: Stage): Editor {
  if (!isRecord(x) || !isRecord(x.artifact)) return invalid();
  const artifact = editorStructure(selected,x.artifact), workspace = parseWorkspace(x.workspace,projectId);
  for (const field of workspace.fields[selected]) {
    let value: unknown = artifact;
    for (const part of field.path) value = (isRecord(value) || Array.isArray(value)) ? (value as Record<string|number,unknown>)[part] : undefined;
    if (value !== field.value) return invalid();
  }
  return {artifact,workspace};
}
export async function readEditor(projectId: string, selected: Stage, fetcher?: typeof fetch) {
  return parseEditor(await revisionRequest(projectId,`/editor/${selected}`,undefined,false,fetcher),projectId,selected);
}
export function manualInput(editor: Editor, selected: Stage, content: unknown): Input {
  const replacement = editorStructure(selected,content);
  if (editor.workspace.running || canonical(replacement) === canonical(editor.artifact)) throw new RevisionError('内容没有变化或项目正在执行，请核对后再预览',422);
  return {expected_token:editor.workspace.token,stage:selected,replacement:JSON.parse(JSON.stringify(replacement))};
}
function verifyManualChanges(changes: Change[], before: Record<string,unknown> | undefined, after: Record<string,unknown>) {
  if (!before) return invalid();
  const expected: {path:Path;before:unknown;after:unknown;before_present?:boolean;after_present?:boolean}[]=[];
  function diff(a: unknown,b: unknown,p: Path) {
    if (canonical(a)===canonical(b)) return;
    if (isRecord(a) && isRecord(b)) for(const key of [...new Set([...Object.keys(a),...Object.keys(b)])].sort()) {if(!(key in a)||!(key in b))expected.push({path:[...p,key],before:a[key]??null,after:b[key]??null,before_present:key in a,after_present:key in b});else diff(a[key],b[key],[...p,key]);}
    else if (Array.isArray(a) && Array.isArray(b) && a.length===b.length) a.forEach((value,index)=>diff(value,b[index],[...p,index]));
    else expected.push({path:p,before:a,after:b});
  }
  diff(before,after,[]);
  function matches(actual: string,value: unknown) {
    if (typeof value==='string') return actual===value;
    try {return canonical(JSON.parse(actual))===canonical(value);} catch {return false;}
  }
  if (expected.length!==changes.length || expected.some((item,index)=>pathKey(item.path)!==pathKey(changes[index].path)||!matches(changes[index].before,item.before)||!matches(changes[index].after,item.after)||(changes[index].before_present??true)!==(item.before_present??true)||(changes[index].after_present??true)!==(item.after_present??true))) return invalid();
}
export async function previewManualRevision(projectId: string, editor: Editor, input: Input, fetcher?: typeof fetch) {
  return parsePreview(await revisionRequest(projectId,'/preview',input,false,fetcher),input,editor.artifact);
}

export function editorStructure(selected: Stage, value: unknown): Record<string,unknown> {
  const content=manualContent(value);
  const error=()=>{throw new RevisionError('编辑内容结构不支持，请核对数组、文字和数值类型',422);};
  const objects=(obj:Record<string,unknown>,key:string):Record<string,unknown>[]=>{
    const array=obj[key]??[];
    if(!Array.isArray(array)||!array.every(isRecord))return error();
    return array;
  };
  const texts=(obj:Record<string,unknown>,keys:string[])=>{if(keys.some(key=>key in obj&&typeof obj[key]!=='string'))error();};
  const strings=(value:unknown)=>{if(!Array.isArray(value)||!value.every(item=>typeof item==='string'))error();};
  const number=(obj:Record<string,unknown>,key:string,integer=false)=>{if(key in obj&&(typeof obj[key]!=='number'||!Number.isFinite(obj[key])||obj[key]<0||(integer&&(!Number.isSafeInteger(obj[key])||obj[key]<1))))error();};
  if(selected==='script_generation') {
    texts(content,['title','logline','overall_style','mood','beat_sheet']);
    if('genre' in content)strings(content.genre);
    for(const character of objects(content,'characters')){texts(character,['name','character_id','description','motivation','role','age','species','occupation','arc_description']);if('personality' in character)strings(character.personality);}
    for(const setting of objects(content,'settings'))texts(setting,['name','description']);
    for(const scene of objects(content,'scenes')){texts(scene,['location','plot']);number(scene,'scene_number',true);if('characters' in scene)strings(scene.characters);}
    for(const episode of objects(content,'episodes')){texts(episode,['act_title','episode_title','content']);number(episode,'episode_number',true);}
  } else {
    const body=!('episodes' in content)&&isRecord(content.payload)?content.payload:content;
    const ids=new Set<string>();
    for(const episode of objects(body,'episodes')){
      texts(episode,['episode_title','act_title']);number(episode,'episode_number',true);
      for(const segment of objects(episode,'segments')){
        texts(segment,['segment_id','location','visual_prompt']);
        if(typeof segment.segment_id!=='string'||!segment.segment_id||ids.has(segment.segment_id))error();
        ids.add(segment.segment_id as string);
        if('characters' in segment)strings(segment.characters);
        number(segment,'total_duration');number(segment,'segment_number',true);number(segment,'episode_number',true);
        const numbers=new Set<number>();
        for(const shot of objects(segment,'shots')){texts(shot,['shot_type','content','plot','dialogue']);number(shot,'duration');number(shot,'shot_number',true);if(typeof shot.shot_number!=='number'||numbers.has(shot.shot_number))error();numbers.add(shot.shot_number as number);}
      }
    }
  }
  return content;
}
