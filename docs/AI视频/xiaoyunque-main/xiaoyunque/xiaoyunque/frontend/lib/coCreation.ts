import { isRecord } from './workflowTypes';
import { RevisionError, type Stage, type TextField, type Path, pathKey } from './contentRevisions';

export type IntentStatus = 'pending' | 'confirmed' | 'withdrawn' | 'stale';
export interface Intent {
  intent_id:string;number:number;stage:Stage;target:TextField;message:string;preserve:string;
  text_token:string;created_at:string;request_id:string;status:IntentStatus;
}
export interface Decision {decision_id:string;number:number;intent_id:string;decision:'confirm'|'withdraw';created_at:string;request_id:string}
export interface CreationWorkspace {
  project_id:string;token:string;text_token:string;running:boolean;model_enabled:false;
  fields:Record<Stage,TextField[]>;records:Intent[];decisions:Decision[];
}
export interface RequestInput {expected_token:string;request_id:string;stage:Stage;path:Path;message:string;preserve:string}
export interface DecisionInput {expected_token:string;request_id:string;intent_id:string;decision:'confirm'|'withdraw';acknowledged:true}
const id=(x:unknown):x is string=>typeof x==='string'&&/^[a-f0-9]{32}$/.test(x);
const hash=(x:unknown):x is string=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x);
const stage=(x:unknown):x is Stage=>x==='script_generation'||x==='storyboard';
const text=(x:unknown):x is string=>typeof x==='string';
const date=(x:unknown)=>text(x)&&Number.isFinite(Date.parse(x));
const exact=(x:Record<string,unknown>,keys:string[])=>Object.keys(x).length===keys.length&&keys.every(k=>k in x);
const invalid=():never=>{throw new RevisionError('共创记录不完整，请重新读取核对',0);};
function field(x:unknown):x is TextField {
  return isRecord(x)&&exact(x,['path','label','value'])&&Array.isArray(x.path)&&x.path.length>0&&x.path.length<=10&&
    x.path.every(p=>typeof p==='string'||Number.isSafeInteger(p)&&p>=0)&&text(x.label)&&text(x.value);
}
export function parseCreation(x:unknown,projectId:string):CreationWorkspace {
  if(!isRecord(x)||!exact(x,['project_id','token','text_token','running','model_enabled','fields','records','decisions'])||
    x.project_id!==projectId||!hash(x.token)||!hash(x.text_token)||typeof x.running!=='boolean'||x.model_enabled!==false||
    !isRecord(x.fields)||!exact(x.fields,['script_generation','storyboard'])||!Array.isArray(x.records)||x.records.length>100||
    !Array.isArray(x.decisions)||x.decisions.length>200)return invalid();
  for(const value of Object.values(x.fields))if(!Array.isArray(value)||value.length>500||!value.every(field)||
    new Set(value.map(f=>pathKey(f.path))).size!==value.length)return invalid();
  const identities=new Set<string>(),requests=new Set<string>(),states=new Map<string,'pending'|'confirmed'|'withdrawn'>();
  for(const [n,r] of x.records.entries()){
    if(!isRecord(r)||!exact(r,['intent_id','number','stage','target','message','preserve','text_token','created_at','request_id','status'])||
      !id(r.intent_id)||identities.has(r.intent_id)||r.number!==n+1||!stage(r.stage)||!field(r.target)||
      !text(r.message)||!r.message.trim()||r.message.length>10000||!text(r.preserve)||r.preserve.length>2000||
      !hash(r.text_token)||!date(r.created_at)||!id(r.request_id)||requests.has(r.request_id)||
      !['pending','confirmed','withdrawn','stale'].includes(String(r.status)))return invalid();
    identities.add(r.intent_id);requests.add(r.request_id);states.set(r.intent_id,'pending');
  }
  const decisionIds=new Set<string>();
  for(const [n,d] of x.decisions.entries()){
    if(!isRecord(d)||!exact(d,['decision_id','number','intent_id','decision','created_at','request_id'])||
      !id(d.decision_id)||decisionIds.has(d.decision_id)||d.number!==n+1||!id(d.intent_id)||!states.has(d.intent_id)||
      !['confirm','withdraw'].includes(String(d.decision))||!date(d.created_at)||!id(d.request_id)||requests.has(d.request_id)||
      states.get(d.intent_id)==='withdrawn'||d.decision==='confirm'&&states.get(d.intent_id)!=='pending')return invalid();
    states.set(d.intent_id,d.decision==='confirm'?'confirmed':'withdrawn');decisionIds.add(d.decision_id);requests.add(d.request_id);
  }
  for(const r of x.records){
    const expected=states.get(r.intent_id)==='withdrawn'?'withdrawn':r.text_token!==x.text_token?'stale':states.get(r.intent_id);
    if(r.status!==expected)return invalid();
    if(r.text_token===x.text_token&&!((x.fields[r.stage] as TextField[]).some(f=>pathKey(f.path)===pathKey(r.target.path)&&f.label===r.target.label&&f.value===r.target.value)))return invalid();
  }
  return x as unknown as CreationWorkspace;
}
export function requestInput(w:CreationWorkspace,selected:Stage,key:string,message:string,preserve:string,requestId:string):RequestInput {
  const target=w.fields[selected].find(f=>pathKey(f.path)===key);
  if(w.running||!target||!message.trim()||message.length>10000||preserve.length>2000||!id(requestId))
    throw new RevisionError('请选择修改对象并填写意图，核对字数及项目状态',422);
  return {expected_token:w.token,request_id:requestId,stage:selected,path:[...target.path],message,preserve};
}
async function call(projectId:string,input?:RequestInput|DecisionInput,fetcher:typeof fetch=fetch):Promise<unknown>{
  if(!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(projectId))return invalid();
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
  try{
    const response=await fetcher('/api/project/'+encodeURIComponent(projectId)+'/co-creation'+(input&&'decision'in input?'/decision':''),
      {method:input?'POST':'GET',cache:'no-store',headers:input?{'Content-Type':'application/json'}:undefined,body:input?JSON.stringify(input):undefined,signal:controller.signal});
    if(!response.ok){
      const messages:Record<number,string>={404:'项目或请求不存在，请返回项目核对',409:'正文、记录或项目状态已变化，请重新读取核对',422:'请求无法保存，请核对对象、内容、状态与记录上限',503:'保存或读取暂未确认，请保留输入并重新读取'};
      throw new RevisionError(messages[response.status]??'本地服务暂未完成请求，请重新读取核对',response.status,!!input&&response.status>=500);
    }
    const result=await response.json();
    if(controller.signal.aborted)throw new Error('Deadline');
    return result;
  }catch(e){
    if(e instanceof RevisionError)throw e;
    throw new RevisionError(input?'保存结果尚未确认，请重新读取共创记录；不会自动重发':'暂时无法读取，请检查本地服务后重新读取',0,!!input);
  }finally{clearTimeout(timer);}
}
export async function readCreation(projectId:string,fetcher?:typeof fetch){
  return parseCreation(await call(projectId,undefined,fetcher),projectId);
}
export async function saveCreation(projectId:string,input:RequestInput|DecisionInput,fetcher?:typeof fetch){
  try{
    const raw=await call(projectId,input,fetcher);
    if(!isRecord(raw)||!exact(raw,['intent_id','workspace'])||!id(raw.intent_id))return invalid();
    const workspace=parseCreation(raw.workspace,projectId),intent=workspace.records.find(r=>r.intent_id===raw.intent_id);
    if(!intent)return invalid();
    if('decision'in input){
      if(raw.intent_id!==input.intent_id||!workspace.decisions.some(d=>d.intent_id===raw.intent_id&&d.request_id===input.request_id&&d.decision===input.decision))return invalid();
    }else if(intent.request_id!==input.request_id||intent.stage!==input.stage||pathKey(intent.target.path)!==pathKey(input.path)||intent.message!==input.message||intent.preserve!==input.preserve)return invalid();
    return {workspace,intent_id:raw.intent_id};
  }catch(e){
    if(e instanceof RevisionError){if(e.status===0)e.uncertain=true;throw e;}
    throw new RevisionError('保存结果尚未确认，请重新读取核对',0,true);
  }
}
export function recoverRequest(w:CreationWorkspace,requestId:string){
  return w.records.find(r=>r.request_id===requestId)?.intent_id??w.decisions.find(d=>d.request_id===requestId)?.intent_id??null;
}
