import {isRecord} from './workflowTypes';
import {RevisionError,parseWorkspace,revisionRequest,type Workspace,type Stage} from './contentRevisions';
import {requestBackup,type PrecheckReport} from './revisionImportPrecheck';
import {parseRestorePreview,type RestoreBackupPreview} from './revisionImportPreview';
export type PreparedBackup={preview:RestoreBackupPreview;confirmation_token:string};
export type RestoreReceipt={restore_id:string;number:number;request_id:string;created_at:string;file_sha256:string;file_bytes:number;expected_token:string;confirmation_token:string;before_digests:Record<Stage,string>;after_digests:Record<Stage,string>;version_ids:Partial<Record<Stage,string>>};
export type RestoreWorkspace={workspace:Workspace;restores:RestoreReceipt[]};
const stages:Stage[]=['script_generation','storyboard'];
const hash=(x:unknown):x is string=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x);
const id=(x:unknown):x is string=>typeof x==='string'&&/^[a-f0-9]{32}$/.test(x);
const exact=(x:Record<string,unknown>,keys:string[])=>Object.keys(x).length===keys.length&&keys.every(k=>Object.hasOwn(x,k));
const canonical=(v:unknown):string=>JSON.stringify(v,(_,x)=>isRecord(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
const invalid=():never=>{throw new RevisionError('恢复记录无法与项目和请求核对，请重新读取核对；不会自动重发',0,true);};
export function parsePreparedBackup(x:unknown,w:Workspace,checked:PrecheckReport,size:number,checksum:string):PreparedBackup{
  if(!isRecord(x)||!exact(x,['preview','confirmation_token'])||!hash(x.confirmation_token))return invalid();
  return {preview:parseRestorePreview(x.preview,w,checked,size,checksum),confirmation_token:x.confirmation_token};
}
export function parseRestoreWorkspace(x:unknown,projectId:string):RestoreWorkspace{
  if(!isRecord(x)||!exact(x,['workspace','restores'])||!Array.isArray(x.restores)||x.restores.length>100)return invalid();
  const workspace=parseWorkspace(x.workspace,projectId),ids=new Set<string>(),requests=new Set<string>(),linked=new Set<string>();
  for(const [index,r] of x.restores.entries()){
    if(!isRecord(r)||!exact(r,['restore_id','number','request_id','created_at','file_sha256','file_bytes','expected_token','confirmation_token','before_digests','after_digests','version_ids'])||!id(r.restore_id)||ids.has(r.restore_id)||!id(r.request_id)||requests.has(r.request_id)||r.number!==index+1||typeof r.created_at!=='string'||!Number.isFinite(Date.parse(r.created_at))||!hash(r.file_sha256)||!hash(r.expected_token)||!hash(r.confirmation_token)||!Number.isSafeInteger(r.file_bytes)||Number(r.file_bytes)<1||Number(r.file_bytes)>64*1024*1024||!isRecord(r.before_digests)||!isRecord(r.after_digests)||!isRecord(r.version_ids)||!exact(r.before_digests,stages)||!exact(r.after_digests,stages))return invalid();
    const before=r.before_digests,after=r.after_digests;
    if(!stages.every(s=>hash(before[s])&&hash(after[s])))return invalid();
    const changed=stages.filter(s=>before[s]!==after[s]);
    if(!changed.length||!exact(r.version_ids,changed))return invalid();
    for(const s of changed){const versionId=r.version_ids[s],v=workspace.versions.find(v=>v.version_id===versionId);if(!id(versionId)||linked.has(versionId)||!v||v.stage!==s||v.source!=='edit'||v.created_at!==r.created_at||!v.changes.length)return invalid();linked.add(versionId);}
    ids.add(r.restore_id);requests.add(r.request_id);
  }
  return {workspace,restores:x.restores as RestoreReceipt[]};
}
export function parseRestoredBackup(x:unknown,w:Workspace,p:PreparedBackup,requestId:string,size:number,checksum:string){
  if(!isRecord(x)||!exact(x,['receipt','data'])||!isRecord(x.receipt))return invalid();
  const data=parseRestoreWorkspace(x.data,w.project_id),receipt=data.restores.find(r=>r.request_id===requestId);
  if(!receipt||canonical(receipt)!==canonical(x.receipt)||receipt.file_bytes!==size||receipt.file_sha256!==checksum||checksum!==p.preview.file_sha256||receipt.expected_token!==w.token||receipt.confirmation_token!==p.confirmation_token||!stages.every(s=>receipt.before_digests[s]===p.preview.stages[s].before_digest&&receipt.after_digests[s]===p.preview.stages[s].after_digest))return invalid();
  return {receipt,data};
}
export async function prepareBackup(w:Workspace,file:File,checked:PrecheckReport,fetcher:typeof fetch=fetch,signal?:AbortSignal){
  if(w.running||!checked.same_project||checked.project_id!==w.project_id||checked.token!==w.token)throw new RevisionError('请先检查当前同项目备份，任务结束后再预览',422);
  return requestBackup(w,file,'import-prepare',(x,size,checksum)=>parsePreparedBackup(x,w,checked,size,checksum),fetcher,signal);
}
export async function restoreBackup(w:Workspace,file:File,p:PreparedBackup,requestId:string,acknowledged:boolean,fetcher:typeof fetch=fetch,signal?:AbortSignal){
  if(acknowledged!==true||w.running||!id(requestId)||!hash(p.confirmation_token)||p.preview.project_id!==w.project_id||p.preview.token!==w.token||p.preview.file_bytes!==file.size||!p.preview.total_changes)throw new RevisionError('请重新预览并逐次确认有变化的同项目文字恢复',422);
  return requestBackup(w,file,'import-restore',(x,size,checksum)=>parseRestoredBackup(x,w,p,requestId,size,checksum),fetcher,signal,{confirmation_token:p.confirmation_token,request_id:requestId,acknowledged:'true'},true);
}
export async function readBackupRestores(projectId:string,fetcher?:typeof fetch){return parseRestoreWorkspace(await revisionRequest(projectId,'/import-restores',undefined,false,fetcher),projectId);}
