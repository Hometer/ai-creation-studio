import { isRecord } from './workflowTypes';
import { RevisionError, type Stage, type TextField, type Workspace } from './contentRevisions';

export type ExportFormat = 'json' | 'text';
export const MAX_EXPORT_BYTES = 64 * 1024 * 1024;
const stages: Stage[] = ['script_generation', 'storyboard'];
const hash = (x: unknown): x is string => typeof x === 'string' && /^[a-f0-9]{64}$/.test(x);
const invalid = (): never => { throw new RevisionError('导出文件不完整或与所选项目不符，请重新读取版本后重试', 0); };
const exact = (x: Record<string, unknown>, keys: string[]) => Object.keys(x).length === keys.length && keys.every(k => k in x);
const canonical = (value: unknown) => JSON.stringify(value,(_,item)=>isRecord(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
export function exportFilename(workspace: Workspace, format: ExportFormat) {
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(workspace.project_id) || !hash(workspace.token) || !['json', 'text'].includes(format)) return invalid();
  return `text-versions-${workspace.project_id}-${workspace.token.slice(0,12)}.${format === 'json' ? 'json' : 'txt'}`;
}
function matchesFields(content: Record<string, unknown>, fields: TextField[]) {
  return fields.every(field => {
    let value: unknown = content;
    for (const part of field.path) {
      if (typeof part === 'number' ? !Array.isArray(value) : !isRecord(value)) return false;
      value = (value as Record<string | number, unknown>)[part];
    }
    return value === field.value;
  });
}
export function parseExportDocument(x: unknown, workspace: Workspace) {
  if (!isRecord(x) || !exact(x, ['format','schema_version','project_id','token','current','versions','untracked_stages']) ||
      x.format !== 'text-revisions' || x.schema_version !== 1 || x.project_id !== workspace.project_id || x.token !== workspace.token ||
      !isRecord(x.current) || !exact(x.current, stages) || !Array.isArray(x.versions) || x.versions.length !== workspace.versions.length ||
      JSON.stringify(x.untracked_stages) !== JSON.stringify(workspace.untracked_stages)) return invalid();
  for (const stage of stages) {
    const current = x.current[stage];
    const expected = workspace.versions.find(v => v.stage === stage && v.current)?.version_id ?? null;
    if (!isRecord(current) || !exact(current, ['content','digest','version_id']) || !isRecord(current.content) ||
        !hash(current.digest) || current.version_id !== expected || !matchesFields(current.content,workspace.fields[stage])) return invalid();
  }
  for (const [index, value] of x.versions.entries()) {
    const expected = workspace.versions[index];
    if (!isRecord(value) || !exact(value, ['version_id','number','stage','source','created_at','content','digest','changes','impacts','restored_from']) ||
        !isRecord(value.content) || !hash(value.digest) || !matchesFields(value.content, expected.fields) ||
        ['version_id','number','stage','source','created_at','restored_from'].some(k => value[k] !== expected[k as keyof typeof expected]) ||
        canonical(value.changes) !== canonical(expected.changes) || canonical(value.impacts) !== canonical(expected.impacts)) return invalid();
    if (expected.current) {
      const current = x.current[expected.stage];
      if (!isRecord(current) || current.digest !== value.digest || canonical(current.content) !== canonical(value.content)) return invalid();
    }
  }
  return x;
}
export async function readExport(workspace: Workspace, format: ExportFormat, fetcher: typeof fetch = fetch) {
  const filename = exportFilename(workspace,format);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(),20000);
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    const response = await fetcher(`/api/project/${encodeURIComponent(workspace.project_id)}/content-revisions/export?format=${format}&expected_token=${workspace.token}`,
      {method:'GET',cache:'no-store',signal:controller.signal});
    if (!response.ok) {
      const messages: Record<number,string> = {404:'项目不存在，请返回项目核对',409:'文字或历史已变化，请先重新读取版本，再导出',422:'文字结构不支持导出或文件超过 64 MiB，请保留原数据',503:'版本数据暂不可用，请保留数据并检查服务'};
      throw new RevisionError(messages[response.status] ?? '导出暂未完成，请检查本地服务后重试',response.status);
    }
    const type = format === 'json' ? 'application/json' : 'text/plain';
    // Browser fetch yields decompressed bytes; proxies may remove Content-Length.
    const checksum = response.headers.get('X-Export-SHA256'), length = response.headers.get('X-Export-Bytes');
    const transportLength=response.headers.get('Content-Length');
    if (response.headers.get('Content-Type')?.split(';')[0] !== type || response.headers.get('X-Revision-Token') !== workspace.token ||
        response.headers.get('X-Export-Format') !== format || response.headers.get('X-Export-Versions') !== String(workspace.versions.length) ||
        response.headers.get('Content-Disposition') !== `attachment; filename="${filename}"` || !hash(checksum) ||
        !length || !/^[1-9]\d*$/.test(length) || !Number.isSafeInteger(Number(length)) || Number(length)>MAX_EXPORT_BYTES || !response.body ||
        (!response.headers.get('Content-Encoding') && transportLength!==null && transportLength!==length)) return invalid();
    reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const part = await reader.read();
      if (controller.signal.aborted) throw new Error('Read deadline');
      if (part.done) break;
      size += part.value.byteLength;
      if (size>MAX_EXPORT_BYTES || size>Number(length)) return invalid();
      chunks.push(part.value);
    }
    if (size !== Number(length)) return invalid();
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {bytes.set(chunk,offset);offset+=chunk.length;}
    const actual = [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(b=>b.toString(16).padStart(2,'0')).join('');
    if (actual !== checksum) return invalid();
    const text = new TextDecoder('utf-8',{fatal:true}).decode(bytes);
    if (format === 'json') parseExportDocument(JSON.parse(text),workspace);
    else if (!text.startsWith(`创作工作室 · 文字版本导出\n项目：${workspace.project_id}\n导出依据：${workspace.token}\n范围：当前剧本、当前分镜与全部 ${workspace.versions.length} 条已归档文字版本\n`)) return invalid();
    if (controller.signal.aborted) throw new Error('Read deadline');
    return {filename,blob:new Blob([bytes],{type:type+';charset=utf-8'}),bytes:size,versions:workspace.versions.length};
  } catch(e) {
    if(e instanceof RevisionError) throw e;
    throw new RevisionError('导出未完成或文件无法校验，请检查本地服务后重试',0);
  } finally {
    clearTimeout(timer);
    try {await reader?.cancel();} catch { /* A failed or cancelled read is already reported safely. */ }
    reader?.releaseLock();
  }
}
export function saveExport(file: {filename:string;blob:Blob}) {
  const url = URL.createObjectURL(file.blob);
  try {
    const anchor = document.createElement('a');
    anchor.href=url;anchor.download=file.filename;anchor.hidden=true;
    document.body.appendChild(anchor);
    try {anchor.click();} finally {anchor.remove();}
  } finally {setTimeout(()=>URL.revokeObjectURL(url),1000);}
}
