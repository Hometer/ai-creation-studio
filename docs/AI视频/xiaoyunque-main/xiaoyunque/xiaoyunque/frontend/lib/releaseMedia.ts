import {isRecord} from './workflowTypes';
import {parseAsset,parseReference,type Asset,type AssetReference,type AssetVersion} from './assets';
import {RevisionError} from './contentRevisions';
export const MEDIA_STATES={awaiting_confirmation:'等待本次确认',queued:'已确认，等待执行',submitting:'正在提交',pending:'平台正在生成',querying:'正在查询原任务',ready_download:'等待归档',downloading:'正在归档',succeeded:'候选已保存',failed:'未执行成功',manual_review:'需要核对原任务',cancelled:'已停止本地后续步骤'} as const;
export interface MediaInput {kind:'image'|'video';source_id:string;segment_id:string|null;expected_workspace:string;prompt:string;preserve:string;request_id:string}
interface MediaRef {reference_id:string;asset_id:string;version_id:string;role:string;width:number;height:number;byte_size:number;sha256:string}
export interface MediaPlan {project_id:string;kind:'image'|'video';source_id:string;segment_id:string|null;expected_token:string;reference_revision:number|null;request_digest:string;model:string;execution_prompt:string;preserve:string;duration:number|null;resolution:'720P'|'1080P'|null;input_refs:MediaRef[];estimate_fen:number;quantity:1;price_version:string;expires_at:number}
export interface MediaJob {job_id:string;number:number;plan:MediaPlan;plan_hash:string;created_at:string;status:keyof typeof MEDIA_STATES;reserved_fen:number;submit_attempts:number;provider_task_id:string|null;query_attempts:number;usage_estimate_fen:number|null;actual_billed_fen:null;asset_id:string|null;version_id:string|null;error:string|null;simulation_only:boolean;current:boolean;selected:boolean;selection_token:string}
export interface ImageOption {reference:AssetReference;asset:Asset;version:AssetVersion}
interface Selection {request_id:string;job_id:string;project_id:string;kind:'image'|'video';target:string;asset_id:string;version_id:string;selected_at:string;previous_token:string}
export interface MediaWorkspace {project_id:string;expected_workspace:string;credential_present:boolean;endpoint_allowed:boolean;price_current:boolean;single_limit_fen:500;round_limit_fen:2000;occupied_fen:number;available_fen:number;images:ImageOption[];jobs:MediaJob[];selections:Selection[]}
const id=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{32}$/.test(v);
const hash=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const key=(v:unknown):v is string=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(v);
const integer=(v:unknown,lo:number,hi:number):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=lo&&v<=hi;
const date=(v:unknown)=>typeof v==='string'&&Number.isFinite(Date.parse(v));
const text=(v:unknown,n:number):v is string=>typeof v==='string'&&[...v].length<=n;
const exact=(v:Record<string,unknown>,keys:string[])=>Object.keys(v).length===keys.length&&keys.every(k=>k in v);
const invalid=():never=>{throw new RevisionError('媒体任务记录不完整，请重新读取核对',0);};
export function parseMediaJob(x:unknown,project:string):MediaJob {
  if(!isRecord(x)||!exact(x,['job_id','number','plan','plan_hash','created_at','status','reserved_fen','submit_attempts','provider_task_id','query_attempts','usage_estimate_fen','actual_billed_fen','asset_id','version_id','error','simulation_only','current','selected','selection_token'])||!id(x.job_id)||!integer(x.number,1,100)||!hash(x.plan_hash)||!date(x.created_at)||!Object.hasOwn(MEDIA_STATES,String(x.status))||!integer(x.reserved_fen,0,500)||!integer(x.submit_attempts,0,1)||!integer(x.query_attempts,0,30)||!(x.provider_task_id===null||typeof x.provider_task_id==='string'&&/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(x.provider_task_id))||x.actual_billed_fen!==null||!(x.error===null||text(x.error,300))||typeof x.simulation_only!=='boolean'||typeof x.current!=='boolean'||typeof x.selected!=='boolean'||!hash(x.selection_token))return invalid();
  const p=x.plan;
  if(!isRecord(p)||!exact(p,['project_id','kind','source_id','segment_id','expected_token','reference_revision','request_digest','model','execution_prompt','preserve','duration','resolution','input_refs','estimate_fen','quantity','price_version','expires_at'])||p.project_id!==project||!['image','video'].includes(String(p.kind))||!id(p.source_id)||!(p.segment_id===null||key(p.segment_id))||!hash(p.expected_token)||!hash(p.request_digest)||!text(p.execution_prompt,5000)||!p.execution_prompt.trim()||!text(p.preserve,2000)||!integer(p.estimate_fen,1,500)||p.quantity!==1||!text(p.price_version,200)||!integer(p.expires_at,1,Number.MAX_SAFE_INTEGER)||!Array.isArray(p.input_refs)||p.input_refs.length<1||p.input_refs.length>2)return invalid();
  if(p.kind==='image'?(p.model!=='wanx2.1-imageedit'||p.duration!==null||p.resolution!==null||p.estimate_fen!==14||[...p.execution_prompt].length>800||p.input_refs.length!==1||!integer(p.reference_revision,1,Number.MAX_SAFE_INTEGER)):(!['wan2.7-i2v','wan2.7-i2v-2026-04-25'].includes(String(p.model))||p.segment_id===null||p.reference_revision!==null||!integer(p.duration,2,15)||!['720P','1080P'].includes(String(p.resolution))||p.estimate_fen!==p.duration*(p.resolution==='720P'?60:100)))return invalid();
  const seen=new Set<string>();
  for(const r of p.input_refs){if(!isRecord(r)||!exact(r,['reference_id','asset_id','version_id','role','width','height','byte_size','sha256'])||!id(r.reference_id)||seen.has(r.reference_id)||!id(r.asset_id)||!id(r.version_id)||!hash(r.sha256)||!integer(r.width,1,4096)||!integer(r.height,1,4096)||!integer(r.byte_size,1,10*1024*1024)||!['first_frame','last_frame','reference_image'].includes(String(r.role)))return invalid();seen.add(r.reference_id);}
  if(p.kind==='image'&&p.input_refs[0].role!=='reference_image'||p.kind==='video'&&(p.input_refs[0].role!=='first_frame'||p.input_refs.length===2&&p.input_refs[1].role!=='last_frame'))return invalid();
  if(!(x.reserved_fen===0||x.reserved_fen===p.estimate_fen)||x.status==='awaiting_confirmation'&&(x.reserved_fen!==0||x.submit_attempts!==0)||!['awaiting_confirmation','cancelled'].includes(String(x.status))&&x.reserved_fen!==p.estimate_fen||!(x.usage_estimate_fen===null||integer(x.usage_estimate_fen,0,x.reserved_fen)))return invalid();
  if(['pending','querying','ready_download','downloading','succeeded'].includes(String(x.status))&&(!x.provider_task_id||x.submit_attempts!==1)||x.status==='submitting'&&x.submit_attempts!==1)return invalid();
  if(x.status==='succeeded'?(!id(x.asset_id)||!id(x.version_id)||x.usage_estimate_fen===null):(x.asset_id!==null||x.version_id!==null||x.selected))return invalid();
  return x as unknown as MediaJob;
}
export function parseMediaWorkspace(x:unknown,project:string):MediaWorkspace {
  if(!isRecord(x)||!exact(x,['project_id','expected_workspace','credential_present','endpoint_allowed','price_current','single_limit_fen','round_limit_fen','occupied_fen','available_fen','images','jobs','selections'])||x.project_id!==project||!hash(x.expected_workspace)||typeof x.credential_present!=='boolean'||typeof x.endpoint_allowed!=='boolean'||typeof x.price_current!=='boolean'||x.single_limit_fen!==500||x.round_limit_fen!==2000||!integer(x.occupied_fen,0,2000)||!integer(x.available_fen,0,2000)||x.occupied_fen+x.available_fen!==2000||!Array.isArray(x.images)||!Array.isArray(x.jobs)||x.jobs.length>100||!Array.isArray(x.selections)||x.selections.length>500)return invalid();
  const images=x.images.map((v):ImageOption=>{
    if(!isRecord(v)||!exact(v,['reference','asset','version'])||!isRecord(v.reference))return invalid();
    const asset=parseAsset(v.asset);if(asset.kind!=='image')return invalid();
    const ref=parseReference({...v.reference,has_newer_version:false,target_exists:true});
    const version=asset.versions.find(t=>t.version_id===ref.version_id);
    if(ref.project_id!==project||ref.asset_id!==asset.asset_id||!version||JSON.stringify(v.version)!==JSON.stringify((v.asset as {versions:unknown[]}).versions[version.number-1]))return invalid();
    return {reference:ref,asset,version};
  });
  const jobs=x.jobs.map(j=>parseMediaJob(j,project));
  if(new Set(jobs.map(j=>j.job_id)).size!==jobs.length||new Set(images.map(i=>i.reference.reference_id)).size!==images.length||jobs.reduce((n,j)=>n+j.reserved_fen,0)>x.occupied_fen)return invalid();
  const selections=x.selections.map(s=>{if(!isRecord(s)||!exact(s,['request_id','job_id','project_id','kind','target','asset_id','version_id','selected_at','previous_token'])||!id(s.request_id)||!id(s.job_id)||s.project_id!==project||!key(s.target)||!id(s.asset_id)||!id(s.version_id)||!date(s.selected_at)||!hash(s.previous_token))return invalid();const job=jobs.find(j=>j.job_id===s.job_id);if(!job||job.status!=='succeeded'||job.plan.kind!==s.kind||job.asset_id!==s.asset_id||job.version_id!==s.version_id||s.target!==(s.kind==='image'?job.plan.source_id:job.plan.segment_id))return invalid();return s as unknown as Selection;});
  if(new Set(selections.map(s=>s.request_id)).size!==selections.length)return invalid();
  return {...x,images,jobs,selections} as MediaWorkspace;
}
async function call(project:string,suffix:string,input?:unknown):Promise<unknown>{
  if(!key(project))return invalid();const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),60000);
  try{const r=await fetch(`/api/project/${encodeURIComponent(project)}/media-revisions${suffix}`,{method:input?'POST':'GET',cache:'no-store',headers:input?{'Content-Type':'application/json'}:undefined,body:input?JSON.stringify(input):undefined,signal:controller.signal});const raw:unknown=await r.json();if(!r.ok)throw new RevisionError(isRecord(raw)&&isRecord(raw.error)&&text(raw.error.message,1000)?raw.error.message:'媒体操作未完成，请重新读取',r.status,!!input&&r.status>=500);if(controller.signal.aborted)throw new Error();return raw;
  }catch(e){if(e instanceof RevisionError)throw e;throw new RevisionError(input?'结果未确认，请重新读取原任务，不会自动重发':'无法读取媒体任务，请检查本地服务',0,!!input);}finally{clearTimeout(timer);}
}
async function write(project:string,suffix:string,input:unknown){const raw=await call(project,suffix,input);try{return parseMediaJob(raw,project);}catch{throw new RevisionError('提交结果未确认，请重新读取原任务核对',0,true);}}
export async function readMedia(project:string){return parseMediaWorkspace(await call(project,''),project);}
export async function prepareMedia(project:string,input:MediaInput){if(!id(input.source_id)||!id(input.request_id)||!hash(input.expected_workspace)||!['image','video'].includes(input.kind)||!text(input.prompt,2000)||!input.prompt.trim()||!text(input.preserve,2000)||!(input.segment_id===null||key(input.segment_id))||input.kind==='video'&&input.segment_id===null)return invalid();return write(project,'',input);}
export async function mediaOperation(project:string,job:MediaJob,operation:'confirm'|'advance'|'stop'|'resume'|'select',requestId:string){parseMediaJob(job,project);if(!id(requestId))return invalid();let body:unknown={};
  if(operation==='confirm'){if(job.status!=='awaiting_confirmation'||!job.current)throw new RevisionError('请重新核对有效计划',409);body={plan_hash:job.plan_hash,request_id:requestId,acknowledged:true,material_reviewed:true};}
  if(operation==='select'){if(job.status!=='succeeded'||!job.current&&!(job.plan.kind==='image'&&job.selected))throw new RevisionError('候选依据已变化，请重新核对',409);body={expected_selection:job.selection_token,request_id:requestId,acknowledged:true};}
  if(operation==='resume'){if(!job.provider_task_id||!['manual_review','cancelled'].includes(job.status))throw new RevisionError('仅可恢复有平台编号的原任务查询',409);body={acknowledged:true};}
  if(operation==='stop')body={acknowledged:true};
  if(operation==='advance'&&!['queued','pending','ready_download'].includes(job.status))throw new RevisionError('请读取原任务状态后再继续',409);
  return write(project,`/${job.job_id}/${operation}`,body);
}
