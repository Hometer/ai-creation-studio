import { isRecord } from './workflowTypes';
import { parseCreation, type CreationWorkspace } from './coCreation';
import { parseWorkspace, parsePreview, pathKey, RevisionError, type Workspace, type Preview } from './contentRevisions';
export interface Application {application_id:string;number:number;intent_id:string;version_id:string;created_at:string;request_id:string}
export interface ApplicationWorkspace {workspace:CreationWorkspace;revisions:Workspace;applications:Application[]}
export interface ApplyInput {expected_token:string;intent_id:string;value:string}
export interface ApplyPreview {intent_id:string;expected_token:string;preview_token:string;revision:Preview}
export interface ApplySave extends ApplyInput {preview_token:string;request_id:string;acknowledged:true}
const id=(x:unknown):x is string=>typeof x==='string'&&/^[a-f0-9]{32}$/.test(x);
const hash=(x:unknown):x is string=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x);
const exact=(x:Record<string,unknown>,keys:string[])=>Object.keys(x).length===keys.length&&keys.every(k=>k in x);
const invalid=():never=>{throw new RevisionError('修改关联或预览不完整，请重新读取核对',0);};
export function parseApplications(x:unknown,projectId:string):ApplicationWorkspace {
  if(!isRecord(x)||!exact(x,['workspace','revisions','applications'])||!Array.isArray(x.applications)||x.applications.length>100)return invalid();
  const workspace=parseCreation(x.workspace,projectId),revisions=parseWorkspace(x.revisions,projectId);
  if(workspace.text_token!==revisions.token||workspace.running!==revisions.running||JSON.stringify(workspace.fields)!==JSON.stringify(revisions.fields))return invalid();
  const ids=new Set<string>(),intents=new Set<string>(),versions=new Set<string>();
  const requests=new Set([...workspace.records,...workspace.decisions].map(r=>r.request_id));
  for(const [n,a] of x.applications.entries()){
    if(!isRecord(a)||!exact(a,['application_id','number','intent_id','version_id','created_at','request_id'])||!id(a.application_id)||ids.has(a.application_id)||a.number!==n+1||!id(a.intent_id)||intents.has(a.intent_id)||!id(a.version_id)||versions.has(a.version_id)||!id(a.request_id)||requests.has(a.request_id)||typeof a.created_at!=='string'||!Number.isFinite(Date.parse(a.created_at)))return invalid();
    const intent=workspace.records.find(r=>r.intent_id===a.intent_id),version=revisions.versions.find(v=>v.version_id===a.version_id);
    if(!intent||!workspace.decisions.some(d=>d.intent_id===a.intent_id&&d.decision==='confirm')||!version||version.source!=='edit'||version.stage!==intent.stage||version.created_at!==a.created_at||version.changes.length!==1)return invalid();
    const change=version.changes[0],field=version.fields.find(f=>pathKey(f.path)===pathKey(intent.target.path));
    if(!field||pathKey(change.path)!==pathKey(intent.target.path)||change.before!==intent.target.value||change.after!==field.value||change.before===change.after||(change.before_present??true)!==true||(change.after_present??true)!==true)return invalid();
    ids.add(a.application_id);intents.add(a.intent_id);versions.add(a.version_id);requests.add(a.request_id);
  }
  return {workspace,revisions,applications:x.applications as unknown as Application[]};
}
export function applyInput(data:ApplicationWorkspace,intentId:string,value:string):ApplyInput {
  const intent=data.workspace.records.find(r=>r.intent_id===intentId);
  if(data.workspace.running||!intent||intent.status!=='confirmed'||data.applications.some(a=>a.intent_id===intentId)||typeof value!=='string'||value.length>10000||value===intent.target.value)throw new RevisionError('请核对已确认请求并填写不同的新文字',422);
  return {expected_token:data.workspace.token,intent_id:intentId,value};
}
export function parseApplicationPreview(x:unknown,input:ApplyInput,data:ApplicationWorkspace):ApplyPreview {
  if(!isRecord(x)||!exact(x,['intent_id','expected_token','preview_token','revision'])||x.intent_id!==input.intent_id||x.expected_token!==input.expected_token||!hash(x.preview_token)||input.expected_token!==data.workspace.token)return invalid();
  const intent=data.workspace.records.find(r=>r.intent_id===input.intent_id);
  if(!intent||intent.status!=='confirmed')return invalid();
  const revision=parsePreview(x.revision,{expected_token:data.workspace.text_token,stage:intent.stage,path:intent.target.path,value:input.value});
  if(revision.changes[0].before!==intent.target.value||(revision.changes[0].before_present??true)!==true||(revision.changes[0].after_present??true)!==true)return invalid();
  return {intent_id:input.intent_id,expected_token:input.expected_token,preview_token:x.preview_token,revision};
}
async function call(projectId:string,suffix:string,input?:unknown,write=false,fetcher:typeof fetch=fetch):Promise<unknown>{
  if(!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(projectId))return invalid();
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
  try{
    const response=await fetcher(`/api/project/${encodeURIComponent(projectId)}/co-creation/applications${suffix}`,{method:input?'POST':'GET',cache:'no-store',headers:input?{'Content-Type':'application/json'}:undefined,body:input?JSON.stringify(input):undefined,signal:controller.signal});
    if(!response.ok){const messages:Record<number,string>={404:'项目或请求不存在，请返回项目核对',409:'正文、请求或预览已变化，请重新读取再预览',422:'请求暂不可应用，请核对确认状态、文字和上限',503:'保存或读取尚未确认，请保留输入并重新读取'};throw new RevisionError(messages[response.status]??'服务暂未完成操作，请重新读取核对',response.status,write&&response.status>=500);}
    const result=await response.json();if(controller.signal.aborted)throw new Error('Deadline');return result;
  }catch(e){if(e instanceof RevisionError)throw e;throw new RevisionError(write?'保存结果尚未确认，请重新读取关联；不会自动重发':'暂时无法读取或预览，请保留输入后重试',0,write);}finally{clearTimeout(timer);}
}
export async function readApplications(projectId:string,fetcher?:typeof fetch){return parseApplications(await call(projectId,'',undefined,false,fetcher),projectId);}
export async function previewApplication(projectId:string,input:ApplyInput,data:ApplicationWorkspace,fetcher?:typeof fetch){return parseApplicationPreview(await call(projectId,'/preview',input,false,fetcher),input,data);}
export async function saveApplication(projectId:string,input:ApplySave,fetcher?:typeof fetch){
  try{
    const raw=await call(projectId,'',input,true,fetcher);
    if(!isRecord(raw)||!exact(raw,['application_id','version_id','data'])||!id(raw.application_id)||!id(raw.version_id))return invalid();
    const data=parseApplications(raw.data,projectId),application=data.applications.find(a=>a.application_id===raw.application_id);
    const version=data.revisions.versions.find(v=>v.version_id===raw.version_id);
    if(!application||application.version_id!==raw.version_id||application.intent_id!==input.intent_id||application.request_id!==input.request_id||!version||version.changes[0].after!==input.value)return invalid();
    return {application_id:raw.application_id,version_id:raw.version_id,data};
  }catch(e){if(e instanceof RevisionError){if(e.status===0)e.uncertain=true;throw e;}throw new RevisionError('保存结果尚未确认，请重新读取关联核对',0,true);}
}
