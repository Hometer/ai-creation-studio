import {isRecord} from './workflowTypes';
import {RevisionError} from './contentRevisions';
import {parseApplications,type ApplicationWorkspace} from './coCreationApplications';
export interface Draft {draft_id:string;number:number;intent_id:string;value:string;text_token:string;created_at:string;request_id:string}
export interface DraftWorkspace {data:ApplicationWorkspace;drafts:Draft[]}
export interface DraftInput {expected_token:string;intent_id:string;value:string;request_id:string}
const id=(x:unknown):x is string=>typeof x==='string'&&/^[a-f0-9]{32}$/.test(x);
const hash=(x:unknown):x is string=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x);
const exact=(x:Record<string,unknown>,keys:string[])=>Object.keys(x).length===keys.length&&keys.every(k=>k in x);
const invalid=():never=>{throw new RevisionError('草稿记录不完整，请保留输入并重新读取',0);};
export function latestDraft(drafts:Draft[],intentId:string){return [...drafts].reverse().find(d=>d.intent_id===intentId);}
export function parseDrafts(x:unknown,projectId:string):DraftWorkspace {
  if(!isRecord(x)||!exact(x,['data','drafts'])||!Array.isArray(x.drafts)||x.drafts.length>200)return invalid();
  const data=parseApplications(x.data,projectId),ids=new Set<string>();
  const requests=new Set([...data.workspace.records,...data.workspace.decisions,...data.applications].map(r=>r.request_id));
  for(const [n,d] of x.drafts.entries()){
    if(!isRecord(d)||!exact(d,['draft_id','number','intent_id','value','text_token','created_at','request_id'])||!id(d.draft_id)||ids.has(d.draft_id)||d.number!==n+1||!id(d.intent_id)||!id(d.request_id)||requests.has(d.request_id)||typeof d.value!=='string'||d.value.length>10000||!hash(d.text_token)||typeof d.created_at!=='string'||!Number.isFinite(Date.parse(d.created_at)))return invalid();
    const intent=data.workspace.records.find(r=>r.intent_id===d.intent_id),stamp=Date.parse(d.created_at);
    if(!intent||intent.text_token!==d.text_token||!data.workspace.decisions.some(a=>a.intent_id===d.intent_id&&a.decision==='confirm'&&Date.parse(a.created_at)<=stamp)||data.workspace.decisions.some(a=>a.intent_id===d.intent_id&&a.decision==='withdraw'&&Date.parse(a.created_at)<stamp)||data.applications.some(a=>a.intent_id===d.intent_id&&Date.parse(a.created_at)<stamp))return invalid();
    ids.add(d.draft_id);requests.add(d.request_id);
  }
  return {data,drafts:x.drafts as unknown as Draft[]};
}
export function draftInput(data:ApplicationWorkspace,intentId:string,value:string,requestId:string):DraftInput {
  const intent=data.workspace.records.find(r=>r.intent_id===intentId);
  if(!intent||intent.status!=='confirmed'||data.workspace.running||data.applications.some(a=>a.intent_id===intentId)||typeof value!=='string'||value.length>10000||!id(requestId))throw new RevisionError('只有依据有效且尚未应用的已确认请求可以保存草稿，请核对字数和项目状态',422);
  return {expected_token:data.workspace.token,intent_id:intentId,value,request_id:requestId};
}
async function call(projectId:string,input?:DraftInput,fetcher:typeof fetch=fetch):Promise<unknown>{
  if(!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(projectId))return invalid();
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
  try{
    const response=await fetcher(`/api/project/${encodeURIComponent(projectId)}/co-creation/drafts`,{method:input?'POST':'GET',cache:'no-store',headers:input?{'Content-Type':'application/json'}:undefined,body:input?JSON.stringify(input):undefined,signal:controller.signal});
    if(!response.ok){const messages:Record<number,string>={404:'项目或请求不存在，请返回项目核对',409:'正文、记录或草稿已变化，请重新读取核对',422:'草稿暂不可保存，请核对确认状态、字数和上限',503:'草稿保存或读取尚未确认，请保留输入并重新读取'};throw new RevisionError(messages[response.status]??'服务暂未完成草稿操作，请重新读取核对',response.status,!!input&&response.status>=500);}
    const raw=await response.json();if(controller.signal.aborted)throw new Error('Deadline');return raw;
  }catch(e){if(e instanceof RevisionError)throw e;throw new RevisionError(input?'草稿保存结果尚未确认，请重新读取；不会自动重发':'暂时无法读取草稿，请保留输入并检查本地服务',0,!!input);}finally{clearTimeout(timer);}
}
export async function readDrafts(projectId:string,fetcher?:typeof fetch){return parseDrafts(await call(projectId,undefined,fetcher),projectId);}
export async function saveDraft(projectId:string,input:DraftInput,fetcher?:typeof fetch){
  try{
    const raw=await call(projectId,input,fetcher);
    if(!isRecord(raw)||!exact(raw,['draft_id','result'])||!id(raw.draft_id))return invalid();
    const result=parseDrafts(raw.result,projectId),draft=result.drafts.find(d=>d.draft_id===raw.draft_id);
    if(!draft||draft.intent_id!==input.intent_id||draft.request_id!==input.request_id||draft.value!==input.value)return invalid();
    return {draft_id:raw.draft_id,result};
  }catch(e){if(e instanceof RevisionError){if(e.status===0)e.uncertain=true;throw e;}throw new RevisionError('草稿保存结果尚未确认，请重新读取核对',0,true);}
}
