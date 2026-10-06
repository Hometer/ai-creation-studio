import {isRecord} from './workflowTypes';
import {RevisionError} from './contentRevisions';
import type {AssetMedia} from './assets';
export const FILM_STATES={draft:'等待核对与确认',queued:'已确认，等待本机合成',rendering:'正在本机合成',succeeded:'成片已保存',failed:'合成未完成',manual_review:'需要核对原合成任务',cancelled:'已取消'} as const;
export interface ClipSelection {segment_id:string;asset_id:string;version_id:string}
export interface ClipOption {asset_id:string;version_id:string;name:string;number:number;media:AssetMedia;source_type:string;source_project:string|null;simulation_only:boolean;stale:boolean}
export interface FilmClip extends ClipSelection {name:string;version_number:number;media:AssetMedia;simulation_only:boolean;stale:boolean}
export interface FilmPlan {project_id:string;expected_workspace:string;clips:FilmClip[];keep_stale:string[];width:number;height:number;video_ratio:string;resolution:'720P';fps:30;target_duration:number|null;total_duration:number;model_fee_fen:0}
export interface FilmJob {job_id:string;number:number;plan:FilmPlan;plan_hash:string;created_at:string;status:keyof typeof FILM_STATES;confirmed_at:string|null;attempts:number;output:AssetMedia|null;error:string|null;current:boolean}
export interface FilmWorkspace {project_id:string;expected_workspace:string;segments:{segment_id:string;label:string;selected:ClipSelection|null}[];options:ClipOption[];jobs:FilmJob[];video_ratio:string;target_duration:number|null;tools_available:boolean}
export interface FilmInput {request_id:string;expected_workspace:string;clips:ClipSelection[];keep_stale:string[]}
const id=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{32}$/.test(v);
const key=(v:unknown):v is string=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(v);
const hash=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const integer=(v:unknown,min:number,max:number):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=min&&v<=max;
const text=(v:unknown,max:number):v is string=>typeof v==='string'&&[...v].length<=max;
const date=(v:unknown)=>typeof v==='string'&&Number.isFinite(Date.parse(v));
const exact=(v:Record<string,unknown>,keys:string[])=>Object.keys(v).length===keys.length&&keys.every(k=>k in v);
const duration=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v)&&v>0&&v<=600;
const goal=(v:unknown)=>v===null||integer(v,1,600);
const dimensions:Record<string,[number,number]>={'16:9':[1280,720],'9:16':[720,1280],'1:1':[720,720],'4:3':[960,720],'3:4':[720,960]};
const invalid=():never=>{throw new RevisionError('成片记录不完整，请重新读取核对',0);};
export function parseFilmMedia(v:unknown):AssetMedia {
 if(!isRecord(v)||!exact(v,['file_id','mime_type','sha256','byte_size','width','height','duration'])||!id(v.file_id)||v.mime_type!=='video/mp4'||!hash(v.sha256)||!integer(v.byte_size,1,100*1024*1024)||!integer(v.width,1,Number.MAX_SAFE_INTEGER)||!integer(v.height,1,Number.MAX_SAFE_INTEGER)||typeof v.duration!=='number'||!Number.isFinite(v.duration)||v.duration<=0)return invalid();
 return v as unknown as AssetMedia;
}
function selection(v:unknown):ClipSelection {
 if(!isRecord(v)||!exact(v,['segment_id','asset_id','version_id'])||!key(v.segment_id)||!id(v.asset_id)||!id(v.version_id))return invalid();return v as unknown as ClipSelection;
}
export function parseFilmJob(v:unknown,project:string):FilmJob {
 if(!isRecord(v)||!exact(v,['job_id','number','plan','plan_hash','created_at','status','confirmed_at','attempts','output','error','current'])||!id(v.job_id)||!integer(v.number,1,100)||!hash(v.plan_hash)||!date(v.created_at)||!Object.hasOwn(FILM_STATES,String(v.status))||!(v.confirmed_at===null||date(v.confirmed_at))||!integer(v.attempts,0,1)||!(v.error===null||text(v.error,300))||typeof v.current!=='boolean')return invalid();
 const p=v.plan;
 if(!isRecord(p)||!exact(p,['project_id','expected_workspace','clips','keep_stale','width','height','video_ratio','resolution','fps','target_duration','total_duration','model_fee_fen'])||p.project_id!==project||!hash(p.expected_workspace)||!Array.isArray(p.clips)||p.clips.length<1||p.clips.length>20||!Array.isArray(p.keep_stale)||p.keep_stale.some(s=>!key(s))||!text(p.video_ratio,10)||!dimensions[p.video_ratio]||p.width!==dimensions[p.video_ratio][0]||p.height!==dimensions[p.video_ratio][1]||p.resolution!=='720P'||p.fps!==30||!goal(p.target_duration)||!duration(p.total_duration)||p.model_fee_fen!==0)return invalid();
 const clips=p.clips.map((c):FilmClip=>{if(!isRecord(c)||!exact(c,['segment_id','asset_id','version_id','name','version_number','media','simulation_only','stale'])||!text(c.name,100)||!c.name.trim()||!integer(c.version_number,1,100000)||typeof c.simulation_only!=='boolean'||typeof c.stale!=='boolean')return invalid();selection({segment_id:c.segment_id,asset_id:c.asset_id,version_id:c.version_id});parseFilmMedia(c.media);return c as unknown as FilmClip;});
 if(new Set(clips.map(c=>c.segment_id)).size!==clips.length||new Set(p.keep_stale).size!==p.keep_stale.length||Math.abs(clips.reduce((n,c)=>n+c.media.duration!,0)-p.total_duration)>.000001||JSON.stringify([...p.keep_stale].sort())!==JSON.stringify(clips.filter(c=>c.stale).map(c=>c.segment_id).sort()))return invalid();
 if(v.output!==null){const output=parseFilmMedia(v.output);if(!['rendering','succeeded'].includes(String(v.status))||output.file_id!==v.job_id||output.width!==p.width||output.height!==p.height||Math.abs(output.duration!-p.total_duration)>.2)return invalid();}
 if(v.status==='succeeded'&&v.output===null||['queued','rendering','succeeded','failed','manual_review'].includes(String(v.status))&&v.confirmed_at===null||['rendering','succeeded','failed','manual_review'].includes(String(v.status))&&v.attempts!==1||v.status==='draft'&&(v.attempts!==0||v.confirmed_at!==null))return invalid();
 return v as unknown as FilmJob;
}
export function parseFilmWorkspace(v:unknown,project:string):FilmWorkspace {
 if(!isRecord(v)||!exact(v,['project_id','expected_workspace','segments','options','jobs','video_ratio','target_duration','tools_available'])||v.project_id!==project||!hash(v.expected_workspace)||!Array.isArray(v.segments)||v.segments.length>20||!Array.isArray(v.options)||!Array.isArray(v.jobs)||v.jobs.length>100||!text(v.video_ratio,32)||!goal(v.target_duration)||typeof v.tools_available!=='boolean')return invalid();
 const options=v.options.map((o):ClipOption=>{if(!isRecord(o)||!exact(o,['asset_id','version_id','name','number','media','source_type','source_project','simulation_only','stale'])||!id(o.asset_id)||!id(o.version_id)||!text(o.name,100)||!o.name.trim()||!integer(o.number,1,100000)||!['upload','artifact','media_task','generation_task'].includes(String(o.source_type))||!(o.source_project===null||key(o.source_project))||typeof o.simulation_only!=='boolean'||typeof o.stale!=='boolean')return invalid();parseFilmMedia(o.media);return o as unknown as ClipOption;});
 const segments=v.segments.map(s=>{if(!isRecord(s)||!exact(s,['segment_id','label','selected'])||!key(s.segment_id)||!text(s.label,200))return invalid();if(s.selected!==null){const selected=selection(s.selected);if(selected.segment_id!==s.segment_id||!options.some(o=>o.asset_id===selected.asset_id&&o.version_id===selected.version_id))return invalid();}return s as unknown as FilmWorkspace['segments'][number];});
 const jobs=v.jobs.map(j=>parseFilmJob(j,project));
 if(new Set(segments.map(s=>s.segment_id)).size!==segments.length||new Set(options.map(o=>o.version_id)).size!==options.length||new Set(jobs.map(j=>j.job_id)).size!==jobs.length)return invalid();
 return {...v,options,segments,jobs} as FilmWorkspace;
}
export function filmInput(workspace:FilmWorkspace,clips:ClipSelection[],keep:string[],requestId:string):FilmInput {
 parseFilmWorkspace(workspace,workspace.project_id);
 if(!id(requestId)||!clips.length||clips.length!==workspace.segments.length||new Set(clips.map(c=>c.segment_id)).size!==clips.length)return invalid();
 const stale:string[]=[];let total=0;
 for(const c of clips){selection(c);const option=workspace.options.find(o=>o.asset_id===c.asset_id&&o.version_id===c.version_id);if(!option||!workspace.segments.some(s=>s.segment_id===c.segment_id))return invalid();total+=option.media.duration!;if(option.stale)stale.push(c.segment_id);}
 if(total>600||new Set(keep).size!==keep.length||JSON.stringify([...keep].sort())!==JSON.stringify(stale.sort()))return invalid();
 return {request_id:requestId,expected_workspace:workspace.expected_workspace,clips,keep_stale:keep};
}
async function call(project:string,suffix:string,input?:unknown):Promise<unknown>{
 if(!key(project))return invalid();const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
 try{const r=await fetch(`/api/project/${encodeURIComponent(project)}/compositions${suffix}`,{method:input?'POST':'GET',cache:'no-store',headers:input?{'Content-Type':'application/json'}:undefined,body:input?JSON.stringify(input):undefined,signal:controller.signal});const raw:unknown=await r.json();if(!r.ok)throw new RevisionError(isRecord(raw)&&isRecord(raw.error)&&text(raw.error.message,1000)?raw.error.message:'成片操作未完成',r.status,!!input&&r.status>=500);if(controller.signal.aborted)throw new Error();return raw;}catch(e){if(e instanceof RevisionError)throw e;throw new RevisionError(input?'结果未确认，请读取原合成任务，输入已保留':'无法读取成片记录，请检查本地服务',0,!!input);}finally{clearTimeout(timer);}
}
async function write(project:string,suffix:string,input:unknown){const raw=await call(project,suffix,input);try{return parseFilmJob(raw,project);}catch{throw new RevisionError('保存结果未确认，请读取原成片任务',0,true);}}
export async function readFilms(project:string){return parseFilmWorkspace(await call(project,''),project);}
export async function prepareFilm(project:string,w:FilmWorkspace,clips:ClipSelection[],keep:string[],requestId:string){parseFilmWorkspace(w,project);return write(project,'',filmInput(w,clips,keep,requestId));}
export async function filmOperation(project:string,job:FilmJob,operation:'confirm'|'stop',requestId:string){parseFilmJob(job,project);if(!id(requestId))return invalid();if(operation==='confirm'&&(job.status!=='draft'||!job.current))throw new RevisionError('请重新核对有效合成计划',409);if(operation==='stop'&&!['draft','queued','cancelled'].includes(job.status))throw new RevisionError('任务已开始或结束，请核对原结果',409);return write(project,`/${job.job_id}/${operation}`,operation==='confirm'?{request_id:requestId,plan_hash:job.plan_hash,acknowledged:true}:{acknowledged:true});}
export const filmUrl=(project:string,job:string,download=false)=>`/api/project/${encodeURIComponent(project)}/compositions/${encodeURIComponent(job)}/media${download?'?download=true':''}`;
