import {isRecord} from './workflowTypes';
import {RevisionError,pathKey,type Workspace,type Stage,type Impact} from './contentRevisions';
import {requestBackup,type PrecheckReport} from './revisionImportPrecheck';
import {type ComparisonChange} from './revisionComparison';
export type RestoreStagePreview={before_digest:string;after_digest:string;before_field_count:number;after_field_count:number;changes:ComparisonChange[];impacts:Impact[]};
export type RestoreBackupPreview={project_id:string;token:string;mode:'read_only';source_project_id:string;file_bytes:number;file_sha256:string;scope:'backup_current';total_changes:number;stages:Record<Stage,RestoreStagePreview>};
const stages:Stage[]=['script_generation','storyboard'];
const exact=(x:Record<string,unknown>,keys:string[])=>Object.keys(x).length===keys.length&&keys.every(k=>Object.hasOwn(x,k));
const hash=(x:unknown):x is string=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x);
const count=(x:unknown,max:number):x is number=>typeof x==='number'&&Number.isSafeInteger(x)&&x>=0&&x<=max;
const invalid=():never=>{throw new RevisionError('恢复预览无法与当前项目和所选备份核对，请重新检查备份后预览',0);};
function typed(value:unknown,kind:unknown,present:unknown){
  if(typeof value!=='string'||typeof present!=='boolean')return false;
  if(kind==='missing')return !present&&value==='null';
  if(!present)return false;
  if(kind==='text')return true;
  try{const x:unknown=JSON.parse(value);return kind==='null'?x===null:kind==='number'?typeof x==='number'&&Number.isFinite(x):kind==='boolean'?typeof x==='boolean':kind==='array'?Array.isArray(x):kind==='object'?isRecord(x):false;}catch{return false;}
}
export function parseRestorePreview(x:unknown,workspace:Workspace,checked:PrecheckReport,size:number,checksum:string):RestoreBackupPreview{
  if(workspace.running||!checked.same_project||checked.source_project_id!==workspace.project_id||checked.project_id!==workspace.project_id||checked.token!==workspace.token||checked.file_bytes!==size||checked.file_sha256!==checksum||!hash(checksum)||!isRecord(x)||!exact(x,['project_id','token','mode','source_project_id','file_bytes','file_sha256','scope','total_changes','stages'])||x.project_id!==workspace.project_id||x.token!==workspace.token||x.mode!=='read_only'||x.source_project_id!==workspace.project_id||x.file_bytes!==size||x.file_sha256!==checksum||x.scope!=='backup_current'||!count(x.total_changes,2000)||!isRecord(x.stages)||!exact(x.stages,stages))return invalid();
  let total=0;
  for(const stage of stages){
    const s=x.stages[stage],target=checked.stages[stage];
    if(!isRecord(s)||!exact(s,['before_digest','after_digest','before_field_count','after_field_count','changes','impacts'])||!hash(s.before_digest)||s.after_digest!==target.digest||s.before_field_count!==workspace.fields[stage].length||s.after_field_count!==target.field_count||!count(s.after_field_count,500)||!Array.isArray(s.changes)||!Array.isArray(s.impacts)||s.changes.length>2000||(s.before_digest===s.after_digest)!==target.matches_current||(s.before_digest===s.after_digest)!==(s.changes.length===0)||(!s.changes.length&&s.impacts.length))return invalid();
    for(const c of s.changes){
      if(!isRecord(c)||!exact(c,['path','label','before','after','before_present','after_present','before_type','after_type'])||!Array.isArray(c.path)||!c.path.every(p=>typeof p==='string'||(Number.isSafeInteger(p)&&p>=0))||typeof c.label!=='string'||!c.label||!typed(c.before,c.before_type,c.before_present)||!typed(c.after,c.after_type,c.after_present)||(c.before===c.after&&c.before_type===c.after_type&&c.before_present===c.after_present))return invalid();
    }
    if(new Set(s.changes.map(c=>pathKey(c.path))).size!==s.changes.length)return invalid();
    for(const i of s.impacts){if(!isRecord(i)||!exact(i,['segment_id','shot_number','reason'])||typeof i.segment_id!=='string'||!i.segment_id||!(i.shot_number===null||(Number.isSafeInteger(i.shot_number)&&Number(i.shot_number)>0))||typeof i.reason!=='string'||!i.reason)return invalid();}
    total+=s.changes.length;
  }
  if(total!==x.total_changes)return invalid();
  return x as unknown as RestoreBackupPreview;
}
export async function previewBackup(workspace:Workspace,file:File,checked:PrecheckReport,fetcher:typeof fetch=fetch,signal?:AbortSignal){
  if(workspace.running)throw new RevisionError('项目正在执行，请结束任务并重新读取后预览文字恢复',409);
  if(!checked.same_project||checked.project_id!==workspace.project_id||checked.source_project_id!==workspace.project_id||checked.token!==workspace.token||checked.file_bytes!==file.size)throw new RevisionError('请先检查同项目文字备份，再预览恢复当前文字',422);
  try{return await requestBackup(workspace,file,'import-preview',(x,size,checksum)=>parseRestorePreview(x,workspace,checked,size,checksum),fetcher,signal);}
  catch(e){if(e instanceof RevisionError&&e.status===409)throw new RevisionError('当前文字／历史已变化或项目正在执行，请重新读取并检查备份后预览',409);if(e instanceof RevisionError&&e.status===422)throw new RevisionError('备份无法预览恢复：请核对同项目来源、文件格式和差异容量，保留原文件',422);throw e;}
}
