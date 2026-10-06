import {isRecord} from './workflowTypes';
import {RevisionError} from './contentRevisions';
export interface CandidateResult {value:string;explanation:string;preservation:string}
export interface CandidatePlan {project_id:string;intent_id:string;expected_token:string;model:'qwen-plus-2025-12-01';input_digest:string;input_byte_size:number;max_output_tokens:4096;estimate_fen:number;price_version:string;expires_at:number;scope:string}
export interface CandidateJob {job_id:string;number:number;plan:CandidatePlan;plan_hash:string;created_at:string;status:'awaiting_confirmation'|'queued'|'submitting'|'succeeded'|'failed'|'manual_review';reserved_fen:number;attempts:number;result:CandidateResult|null;usage_estimate_fen:number|null;actual_billed_fen:null;error:string|null;current:boolean;simulation_only:boolean}
export interface CandidateWorkspace {project_id:string;expected_token:string;credential_present:boolean;endpoint_allowed:boolean;single_limit_fen:500;round_limit_fen:2000;occupied_fen:number;available_fen:number;price_current:boolean;jobs:CandidateJob[]}
const id=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{32}$/.test(v);
const hash=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const integer=(v:unknown,low:number,high:number):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=low&&v<=high;
const text=(v:unknown,max:number):v is string=>typeof v==='string'&&v.trim().length>0&&v.length<=max;
const exact=(v:Record<string,unknown>,keys:string[])=>Object.keys(v).length===keys.length&&keys.every(k=>k in v);
const invalid=():never=>{throw new RevisionError('模型候选或额度记录不完整，请重新读取',0);};
export function parseCandidate(x:unknown,projectId:string):CandidateJob {
  if(!isRecord(x)||!exact(x,['job_id','number','plan','plan_hash','created_at','status','reserved_fen','attempts','result','usage_estimate_fen','actual_billed_fen','error','current','simulation_only'])||typeof x.simulation_only!=='boolean'||!id(x.job_id)||!integer(x.number,1,200)||!hash(x.plan_hash)||typeof x.created_at!=='string'||!Number.isFinite(Date.parse(x.created_at))||!['awaiting_confirmation','queued','submitting','succeeded','failed','manual_review'].includes(String(x.status))||!integer(x.reserved_fen,0,500)||!integer(x.attempts,0,1)||x.actual_billed_fen!==null||typeof x.current!=='boolean'||!(x.error===null||text(x.error,1000))||!(x.usage_estimate_fen===null||integer(x.usage_estimate_fen,0,x.reserved_fen)))return invalid();
  const p=x.plan;
  if(!isRecord(p)||!exact(p,['project_id','intent_id','expected_token','model','input_digest','input_byte_size','max_output_tokens','estimate_fen','price_version','expires_at','scope'])||p.project_id!==projectId||!id(p.intent_id)||!hash(p.expected_token)||p.model!=='qwen-plus-2025-12-01'||!hash(p.input_digest)||!integer(p.input_byte_size,1,100000)||p.max_output_tokens!==4096||!integer(p.estimate_fen,1,500)||!text(p.price_version,200)||!integer(p.expires_at,1,Number.MAX_SAFE_INTEGER)||!text(p.scope,1000))return invalid();
  if(x.status==='awaiting_confirmation'?(x.attempts!==0||x.reserved_fen!==0):(x.reserved_fen!==p.estimate_fen))return invalid();
  if(['submitting','succeeded','manual_review'].includes(String(x.status))&&x.attempts!==1)return invalid();
  if(x.status==='succeeded'){
    const r=x.result;if(!isRecord(r)||!exact(r,['value','explanation','preservation'])||!text(r.value,10000)||!text(r.explanation,2000)||!text(r.preservation,2000)||x.usage_estimate_fen===null)return invalid();
  }else if(x.result!==null||x.usage_estimate_fen!==null)return invalid();
  return x as unknown as CandidateJob;
}
export function parseCandidates(x:unknown,projectId:string):CandidateWorkspace {
  if(!isRecord(x)||!exact(x,['project_id','expected_token','credential_present','endpoint_allowed','single_limit_fen','round_limit_fen','occupied_fen','available_fen','price_current','jobs'])||x.project_id!==projectId||!hash(x.expected_token)||typeof x.credential_present!=='boolean'||typeof x.endpoint_allowed!=='boolean'||x.single_limit_fen!==500||x.round_limit_fen!==2000||!integer(x.occupied_fen,0,2000)||!integer(x.available_fen,0,2000)||x.occupied_fen+x.available_fen!==2000||typeof x.price_current!=='boolean'||!Array.isArray(x.jobs)||x.jobs.length>200)return invalid();
  const jobs=x.jobs.map(j=>parseCandidate(j,projectId));
  if(new Set(jobs.map(j=>j.job_id)).size!==jobs.length||new Set(jobs.map(j=>j.number)).size!==jobs.length||jobs.reduce((n,j)=>n+j.reserved_fen,0)>x.occupied_fen)return invalid();
  return {...x,jobs} as CandidateWorkspace;
}
async function call(projectId:string,suffix:string,input?:unknown):Promise<unknown>{
  if(!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(projectId))return invalid();
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
  try{
    const response=await fetch(`/api/project/${encodeURIComponent(projectId)}/co-creation/candidates${suffix}`,{method:input?'POST':'GET',cache:'no-store',headers:input?{'Content-Type':'application/json'}:undefined,body:input?JSON.stringify(input):undefined,signal:controller.signal});
    if(!response.ok){let message='候选操作未完成，请重新读取';try{const raw:unknown=await response.json();if(isRecord(raw)&&isRecord(raw.error)&&typeof raw.error.message==='string'&&raw.error.message.length<=1000)message=raw.error.message;}catch{}throw new RevisionError(message,response.status,!!input&&response.status>=500);}
    const raw:unknown=await response.json();if(controller.signal.aborted)throw new Error('Deadline');return raw;
  }catch(e){if(e instanceof RevisionError)throw e;throw new RevisionError(input?'结果尚未确认，请重新读取候选任务，不会自动重发':'候选记录读取失败，请手动重试',0,!!input);}finally{clearTimeout(timer);}
}
export async function readCandidates(projectId:string){return parseCandidates(await call(projectId,''),projectId);}
export async function prepareCandidate(projectId:string,input:{expected_token:string;intent_id:string;request_id:string}){if(!hash(input.expected_token)||!id(input.intent_id)||!id(input.request_id))return invalid();return parseCandidate(await call(projectId,'',input),projectId);}
export async function confirmCandidate(projectId:string,job:CandidateJob,input:{expected_token:string;request_id:string;acknowledged:true;material_reviewed:true}){parseCandidate(job,projectId);if(!job.current||job.status!=='awaiting_confirmation'||input.expected_token!==job.plan.expected_token||!id(input.request_id)||input.acknowledged!==true||input.material_reviewed!==true)throw new RevisionError('请重新核对本次计划、费用与素材声明',409);return parseCandidate(await call(projectId,`/${job.job_id}/confirm`,{...input,plan_hash:job.plan_hash}),projectId);}
export async function advanceCandidate(projectId:string,job:CandidateJob){parseCandidate(job,projectId);if(job.status!=='queued')throw new RevisionError('只可继续已确认且尚未提交的原任务',409);return parseCandidate(await call(projectId,`/${job.job_id}/advance`,{}),projectId);}
