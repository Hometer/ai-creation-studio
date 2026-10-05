import { isRecord } from './workflowTypes';
import { revisionRequest, RevisionError, pathKey, type Change, type Impact, type Stage, type Workspace } from './contentRevisions';

export type ValueType = 'text' | 'number' | 'boolean' | 'object' | 'array' | 'null' | 'missing';
export interface ComparisonInput { expected_token: string; before_version_id: string; after_version_id: string }
export interface ComparisonChange extends Change { label: string; before_type: ValueType; after_type: ValueType }
export interface Comparison { project_id: string; token: string; stage: Stage; before_version_id: string; after_version_id: string; changes: ComparisonChange[]; impacts: Impact[] }
export interface Pair { from: string; to: string; stage: Stage }
const invalid = (): never => { throw new RevisionError('两版对比结果不完整，请重新读取版本后重试',0); };
export function comparisonInput(workspace: Workspace, from: string, to: string): ComparisonInput {
  const before=workspace.versions.find(v=>v.version_id===from), after=workspace.versions.find(v=>v.version_id===to);
  if (!before || !after || before.stage!==after.stage) throw new RevisionError('请选择同一阶段已归档的两个版本',422);
  return {expected_token:workspace.token,before_version_id:from,after_version_id:to};
}
export function comparisonSelection(workspace: Workspace, search: string): Pair | undefined {
  const params=new URLSearchParams(search);
  if (!params.has('compare_from') && !params.has('compare_to')) return undefined;
  if (params.getAll('compare_from').length!==1 || params.getAll('compare_to').length!==1) throw new RevisionError('地址中的对比版本不完整，请重新选择',422);
  const from=params.get('compare_from')!,to=params.get('compare_to')!;
  comparisonInput(workspace,from,to);
  return {from,to,stage:workspace.versions.find(v=>v.version_id===from)!.stage};
}
function typed(value: unknown, kind: unknown, present: unknown): boolean {
  if (typeof value!=='string' || typeof present!=='boolean') return false;
  if (kind==='missing') return !present && value==='null';
  if (!present) return false;
  if (kind==='text') return true;
  try {
    const parsed=JSON.parse(value);
    return kind==='null' ? parsed===null : kind==='number' ? typeof parsed==='number' && Number.isFinite(parsed)
      : kind==='boolean' ? typeof parsed==='boolean' : kind==='array' ? Array.isArray(parsed)
      : kind==='object' ? isRecord(parsed) : false;
  } catch { return false; }
}
export function parseComparison(x: unknown, workspace: Workspace, input: ComparisonInput): Comparison {
  const before=workspace.versions.find(v=>v.version_id===input.before_version_id),after=workspace.versions.find(v=>v.version_id===input.after_version_id);
  if (!before || !after || before.stage!==after.stage || input.expected_token!==workspace.token || !isRecord(x) ||
      x.project_id!==workspace.project_id || x.token!==input.expected_token || x.stage!==before.stage ||
      x.before_version_id!==before.version_id || x.after_version_id!==after.version_id || !Array.isArray(x.changes) ||
      x.changes.length>2000 || !Array.isArray(x.impacts)) return invalid();
  for(const c of x.changes) {
    if (!isRecord(c) || !Array.isArray(c.path) || !c.path.every(p=>typeof p==='string'||(Number.isSafeInteger(p)&&p>=0)) ||
        typeof c.label!=='string' || !c.label || !typed(c.before,c.before_type,c.before_present) || !typed(c.after,c.after_type,c.after_present) ||
        (c.before===c.after && c.before_type===c.after_type && c.before_present===c.after_present)) return invalid();
  }
  if (new Set(x.changes.map(c=>pathKey(c.path))).size!==x.changes.length ||
      (before.version_id===after.version_id && x.changes.length) || (!x.changes.length && x.impacts.length)) return invalid();
  for(const i of x.impacts) {
    if(!isRecord(i)||typeof i.segment_id!=='string'||!i.segment_id||!(i.shot_number===null||(Number.isSafeInteger(i.shot_number)&&Number(i.shot_number)>0))||typeof i.reason!=='string')return invalid();
  }
  return x as unknown as Comparison;
}
export async function readComparison(workspace: Workspace, from: string, to: string, fetcher?: typeof fetch) {
  const input=comparisonInput(workspace,from,to);
  const suffix='/compare?expected_token='+input.expected_token+'&before_version_id='+input.before_version_id+'&after_version_id='+input.after_version_id;
  try { return parseComparison(await revisionRequest(workspace.project_id,suffix,undefined,false,fetcher),workspace,input); }
  catch(e) {
    if(e instanceof RevisionError && e.status===409)throw new RevisionError('文字或历史已变化，请先重新读取版本，再查看差异',409);
    if(e instanceof RevisionError && e.status===422)throw new RevisionError('所选版本无法对比，或超过内容／差异上限；请检查版本和项目大小',422);
    throw e;
  }
}
