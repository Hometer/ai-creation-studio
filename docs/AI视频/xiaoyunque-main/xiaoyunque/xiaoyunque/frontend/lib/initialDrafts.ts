import {isRecord} from './workflowTypes';
import {RevisionError,parseChanges,parseImpacts,type Change,type Impact,type Stage} from './contentRevisions';
export interface Person{id:string;name:string;description:string}
export interface Script{title:string;logline:string;beat_sheet:string;characters:Person[];settings:Person[];episodes:{episode_number:number;act_title:string;content:string}[]}
export interface Board{episodes:{episode_number:number;episode_title:string;segments:{segment_id:string;visual_prompt:string;character_ids:string[];setting_ids:string[];shots:{shot_number:number;duration:number;content:string;action:string;dialogue:string;sound:string}[]}[]}[]}
export interface InitialResult{kind:Stage;content:Script|Board;explanation:string;preservation:string}
export const INITIAL_STATES={awaiting_confirmation:'等待费用与素材确认',queued:'已确认，排队中',submitting:'正在生成候选',succeeded:'候选已保存',failed:'未发送或已失败',manual_review:'回复未确认，需核对'} as const;
export interface InitialPlan{project_id:string;expected_workspace:string;kind:Stage;prompt:string;preserve:string;model:'qwen-plus-2025-12-01';input_digest:string;input_byte_size:number;max_output_tokens:4096;estimate_fen:number;price_version:string;expires_at:number;episodes:number;target_duration:number|null;scope:string}
export interface InitialJob{job_id:string;number:number;plan:InitialPlan;plan_hash:string;created_at:string;status:keyof typeof INITIAL_STATES;reserved_fen:number;attempts:number;result:InitialResult|null;usage_estimate_fen:number|null;actual_billed_fen:null;error:string|null;simulation_only:boolean;current:boolean;selected:boolean;selected_version_id:string|null;result_digest:string|null;changes:Change[];impacts:Impact[]}
export interface InitialWorkspace{project_id:string;expected_workspace:string;story:string;style:string;video_ratio:string;target_duration:number|null;episodes:number;script:Record<string,unknown>;storyboard:Record<string,unknown>;credential_present:boolean;endpoint_allowed:boolean;price_current:boolean;single_limit_fen:500;round_limit_fen:2000;occupied_fen:number;available_fen:number;jobs:InitialJob[]}
const id=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{32}$/.test(v);
const key=(v:unknown):v is string=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(v);
const hash=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const int=(v:unknown,a:number,b:number):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=a&&v<=b;
const txt=(v:unknown,n:number):v is string=>typeof v==='string'&&v.length<=n;
const nonblank=(v:unknown,n:number):v is string=>txt(v,n)&&!!v.trim();
const goal=(v:unknown)=>v===null||int(v,1,600);
const exact=(v:Record<string,unknown>,keys:string[])=>Object.keys(v).length===keys.length&&keys.every(k=>k in v);
const invalid=():never=>{throw new RevisionError('剧本／分镜候选或额度记录不完整，请重新读取',0);};
export function parseInitialResult(v:unknown,kind:Stage,episodes:number,target:number|null):InitialResult{
 if(!isRecord(v)||!exact(v,['kind','content','explanation','preservation'])||v.kind!==kind||!nonblank(v.explanation,2000)||!nonblank(v.preservation,2000)||!isRecord(v.content))return invalid();const c=v.content;
 if(!Array.isArray(c.episodes)||c.episodes.length!==episodes||!c.episodes.every((e,i)=>isRecord(e)&&e.episode_number===i+1))return invalid();
 if(kind==='script_generation'){
  if(!exact(c,['title','logline','beat_sheet','characters','settings','episodes'])||!nonblank(c.title,100)||!nonblank(c.logline,2000)||!nonblank(c.beat_sheet,2000))return invalid();
  const ids:string[]=[];for(const k of ['characters','settings']){const list=c[k];if(!Array.isArray(list)||!list.length||list.length>10)return invalid();for(const p of list){if(!isRecord(p)||!exact(p,['id','name','description'])||!key(p.id)||!nonblank(p.name,100)||!nonblank(p.description,2000))return invalid();ids.push(p.id);}}
  if(new Set(ids).size!==ids.length||!c.episodes.every(e=>isRecord(e)&&exact(e,['episode_number','act_title','content'])&&nonblank(e.act_title,100)&&nonblank(e.content,2000)))return invalid();
 }else{
  if(!exact(c,['episodes']))return invalid();const segments:string[]=[];let total=0;
  for(const e of c.episodes){if(!isRecord(e)||!exact(e,['episode_number','episode_title','segments'])||!nonblank(e.episode_title,100)||!Array.isArray(e.segments)||!e.segments.length)return invalid();
   for(const s of e.segments){if(!isRecord(s)||!exact(s,['segment_id','visual_prompt','character_ids','setting_ids','shots'])||!key(s.segment_id)||!nonblank(s.visual_prompt,2000)||!Array.isArray(s.shots)||!s.shots.length||s.shots.length>20)return invalid();segments.push(s.segment_id);
    for(const k of ['character_ids','setting_ids']){const list=s[k];if(!Array.isArray(list)||list.length>10||k==='setting_ids'&&!list.length||list.some(x=>!key(x))||new Set(list).size!==list.length)return invalid();}
    let duration=0;for(const [i,h] of s.shots.entries()){if(!isRecord(h)||!exact(h,['shot_number','duration','content','action','dialogue','sound'])||h.shot_number!==i+1||!int(h.duration,1,15)||!nonblank(h.content,2000)||!txt(h.action,500)||!txt(h.dialogue,500)||!txt(h.sound,500))return invalid();duration+=h.duration;}
    if(duration>15)return invalid();total+=duration;
   }
  }if(segments.length>20||new Set(segments).size!==segments.length||target!==null&&total!==target)return invalid();
 }return v as unknown as InitialResult;
}
export function parseInitialJob(v:unknown,project:string):InitialJob{
 if(!isRecord(v)||!exact(v,['job_id','number','plan','plan_hash','created_at','status','reserved_fen','attempts','result','usage_estimate_fen','actual_billed_fen','error','simulation_only','current','selected','selected_version_id','result_digest','changes','impacts'])||!id(v.job_id)||!int(v.number,1,100)||!hash(v.plan_hash)||!txt(v.created_at,100)||!Number.isFinite(Date.parse(v.created_at))||!Object.hasOwn(INITIAL_STATES,String(v.status))||!int(v.reserved_fen,0,500)||!int(v.attempts,0,1)||v.actual_billed_fen!==null||!(v.error===null||nonblank(v.error,300))||['simulation_only','current','selected'].some(k=>typeof v[k]!=='boolean')||!(v.selected_version_id===null||id(v.selected_version_id))||!(v.result_digest===null||hash(v.result_digest))||!(v.usage_estimate_fen===null||int(v.usage_estimate_fen,1,v.reserved_fen)))return invalid();
 const p=v.plan;if(!isRecord(p)||!exact(p,['project_id','expected_workspace','kind','prompt','preserve','model','input_digest','input_byte_size','max_output_tokens','estimate_fen','price_version','expires_at','episodes','target_duration','scope'])||p.project_id!==project||!hash(p.expected_workspace)||!['script_generation','storyboard'].includes(String(p.kind))||!nonblank(p.prompt,2000)||!txt(p.preserve,2000)||p.model!=='qwen-plus-2025-12-01'||!hash(p.input_digest)||!int(p.input_byte_size,1,100000)||p.max_output_tokens!==4096||!int(p.estimate_fen,1,500)||!nonblank(p.price_version,200)||!int(p.expires_at,1,Number.MAX_SAFE_INTEGER)||!int(p.episodes,1,10)||!goal(p.target_duration)||!nonblank(p.scope,1000))return invalid();
 if(v.status==='awaiting_confirmation'?(v.reserved_fen!==0||v.attempts!==0):v.reserved_fen!==p.estimate_fen)return invalid();if(['submitting','succeeded','manual_review'].includes(String(v.status))&&v.attempts!==1)return invalid();
 if(v.status==='succeeded'){parseInitialResult(v.result,p.kind as Stage,p.episodes,p.target_duration as number|null);if(v.result_digest===null||v.usage_estimate_fen===null)return invalid();}else if(v.result!==null||v.result_digest!==null||v.usage_estimate_fen!==null||v.selected||v.selected_version_id!==null)return invalid();
 if(v.selected&&v.selected_version_id===null)return invalid();parseChanges(v.changes);parseImpacts(v.impacts);return v as unknown as InitialJob;
}
export function parseInitialWorkspace(v:unknown,project:string):InitialWorkspace{
 if(!isRecord(v)||!exact(v,['project_id','expected_workspace','story','style','video_ratio','target_duration','episodes','script','storyboard','credential_present','endpoint_allowed','price_current','single_limit_fen','round_limit_fen','occupied_fen','available_fen','jobs'])||v.project_id!==project||!hash(v.expected_workspace)||!nonblank(v.story,80000)||!txt(v.style,1000)||!txt(v.video_ratio,100)||!goal(v.target_duration)||!int(v.episodes,1,10)||!isRecord(v.script)||!isRecord(v.storyboard)||['credential_present','endpoint_allowed','price_current'].some(k=>typeof v[k]!=='boolean')||v.single_limit_fen!==500||v.round_limit_fen!==2000||!int(v.occupied_fen,0,2000)||!int(v.available_fen,0,2000)||v.occupied_fen+v.available_fen!==2000||!Array.isArray(v.jobs)||v.jobs.length>100)return invalid();
 const jobs=v.jobs.map(j=>parseInitialJob(j,project));if(new Set(jobs.map(j=>j.job_id)).size!==jobs.length||new Set(jobs.map(j=>j.number)).size!==jobs.length||jobs.reduce((n,j)=>n+j.reserved_fen,0)>v.occupied_fen)return invalid();return {...v,jobs} as InitialWorkspace;
}
async function call(project:string,suffix:string,input?:unknown):Promise<unknown>{
 if(!key(project))return invalid();const c=new AbortController(),timer=setTimeout(()=>c.abort(),20000);
 try{const r=await fetch(`/api/project/${encodeURIComponent(project)}/initial-drafts${suffix}`,{method:input?'POST':'GET',cache:'no-store',headers:input?{'Content-Type':'application/json'}:undefined,body:input?JSON.stringify(input):undefined,signal:c.signal});const raw:unknown=await r.json();if(!r.ok)throw new RevisionError(isRecord(raw)&&isRecord(raw.error)&&txt(raw.error.message,1000)?raw.error.message:'候选操作未完成',r.status,!!input&&r.status>=500);if(c.signal.aborted)throw Error();return raw;}catch(e){if(e instanceof RevisionError)throw e;throw new RevisionError(input?'结果未确认，请重新读取原候选；不会自动重发':'无法读取候选记录，请检查本地服务',0,!!input);}finally{clearTimeout(timer);}
}
async function write(project:string,suffix:string,input:unknown){const raw=await call(project,suffix,input);try{return parseInitialJob(raw,project);}catch{throw new RevisionError('保存结果未确认，请重新读取原候选',0,true);}}
export async function readInitialDrafts(project:string){return parseInitialWorkspace(await call(project,''),project);}
export async function prepareInitial(project:string,w:InitialWorkspace,kind:Stage,prompt:string,preserve:string,request:string){parseInitialWorkspace(w,project);if(!id(request)||!['script_generation','storyboard'].includes(kind)||!nonblank(prompt,2000)||!txt(preserve,2000))return invalid();return write(project,'',{request_id:request,expected_workspace:w.expected_workspace,kind,prompt,preserve});}
export async function confirmInitial(project:string,j:InitialJob,request:string,fee:boolean,material:boolean){parseInitialJob(j,project);if(!id(request)||!j.current||j.status!=='awaiting_confirmation'||fee!==true||material!==true)throw new RevisionError('请核对本次候选费用与素材声明',409);return write(project,`/${j.job_id}/confirm`,{request_id:request,plan_hash:j.plan_hash,acknowledged:true,material_reviewed:true});}
export async function selectInitial(project:string,w:InitialWorkspace,j:InitialJob,request:string,ack:boolean){parseInitialWorkspace(w,project);parseInitialJob(j,project);if(!id(request)||!j.current||j.status!=='succeeded'||!j.result_digest||ack!==true)throw new RevisionError('请重新核对候选全文与项目依据',409);return write(project,`/${j.job_id}/select`,{request_id:request,expected_workspace:w.expected_workspace,result_digest:j.result_digest,acknowledged:true});}
