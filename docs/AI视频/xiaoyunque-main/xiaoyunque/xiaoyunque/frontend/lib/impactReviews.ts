import { isRecord } from './workflowTypes';
import { RevisionError, revisionRequest, type Change, type Stage, type TextField } from './contentRevisions';
export const DECISIONS = {retain:'已核对，继续使用',follow_up:'仍需处理'} as const;
export const REVIEW_STATUSES = {pending:'待核对',follow_up:'仍需处理',retained:'已核对，继续使用',stale:'依据已变化，重新核对'} as const;
export interface ReviewRecord {review_id:string;number:number;item_id:string;decision:keyof typeof DECISIONS;note:string;context_hash:string;request_id:string;request_digest:string;created_at:string;applicable:boolean}
export interface ReviewItem {item_id:string;version_id:string;version_number:number;stage:Stage;segment_id:string;shot_number:number|null;reason:string;current_fields:TextField[];target_exists:boolean;status:keyof typeof REVIEW_STATUSES;latest_review_id:string|null}
export interface ReviewSource {version_id:string;version_number:number;stage:Stage;changes:Change[]}
export interface ReviewWorkspace {project_id:string;token:string;running:boolean;untracked_stages:Stage[];items:ReviewItem[];records:ReviewRecord[];sources:ReviewSource[]}
export interface ReviewInput {expected_token:string;item_id:string;decision:keyof typeof DECISIONS;note:string;request_id:string;acknowledged:true}
const hash=(x:unknown):x is string=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x);
const id=(x:unknown):x is string=>typeof x==='string'&&/^[a-f0-9]{32}$/.test(x);
const text=(x:unknown):x is string=>typeof x==='string';
const stage=(x:unknown):x is Stage=>x==='script_generation'||x==='storyboard';
const path=(x:unknown)=>Array.isArray(x)&&x.length<=10&&x.every(p=>typeof p==='string'||Number.isSafeInteger(p)&&p>=0);
function invalid():never {throw new RevisionError('核对记录不完整，请重新读取后核对',0,true);}
export function parseReviews(x:unknown,projectId:string):ReviewWorkspace {
 if(!isRecord(x)||x.project_id!==projectId||!hash(x.token)||typeof x.running!=='boolean'||!Array.isArray(x.untracked_stages)||!x.untracked_stages.every(stage)||new Set(x.untracked_stages).size!==x.untracked_stages.length||!Array.isArray(x.items)||x.items.length>1000||!Array.isArray(x.records)||x.records.length>2000||!Array.isArray(x.sources)) return invalid();
 const sources=x.sources;
 for(const i of x.items) {
  if(!isRecord(i)||!hash(i.item_id)||!id(i.version_id)||typeof i.version_number!=='number'||!Number.isSafeInteger(i.version_number)||i.version_number<1||!stage(i.stage)||!text(i.segment_id)||!i.segment_id||!(i.shot_number===null||typeof i.shot_number==='number'&&Number.isSafeInteger(i.shot_number)&&i.shot_number>0)||!text(i.reason)||typeof i.target_exists!=='boolean'||!Object.hasOwn(REVIEW_STATUSES,String(i.status))||!(i.latest_review_id===null||id(i.latest_review_id))||!Array.isArray(i.current_fields)) return invalid();
  if(!i.current_fields.every(f=>isRecord(f)&&path(f.path)&&text(f.label)&&text(f.value)))return invalid();
 }
 if(new Set(x.items.map(i=>i.item_id)).size!==x.items.length)return invalid();
 for(const s of sources){if(!isRecord(s)||!id(s.version_id)||typeof s.version_number!=='number'||!Number.isSafeInteger(s.version_number)||s.version_number<1||!stage(s.stage)||!Array.isArray(s.changes)||!s.changes.every(c=>isRecord(c)&&path(c.path)&&text(c.before)&&text(c.after)&&(c.before_present===undefined||typeof c.before_present==='boolean')&&(c.after_present===undefined||typeof c.after_present==='boolean')))return invalid();}
 if(new Set(sources.map(s=>s.version_id)).size!==sources.length||x.items.some(i=>!sources.some(s=>s.version_id===i.version_id&&s.version_number===i.version_number&&s.stage===i.stage)))return invalid();
 for(const [index,r] of x.records.entries()) {
  if(!isRecord(r)||!id(r.review_id)||r.number!==index+1||!hash(r.item_id)||!x.items.some(i=>i.item_id===r.item_id)||!Object.hasOwn(DECISIONS,String(r.decision))||!text(r.note)||!r.note.trim()||r.note.length>1000||!hash(r.context_hash)||!id(r.request_id)||!hash(r.request_digest)||!text(r.created_at)||!Number.isFinite(Date.parse(r.created_at))||typeof r.applicable!=='boolean')return invalid();
 }
 if(new Set(x.records.map(r=>r.review_id)).size!==x.records.length||new Set(x.records.map(r=>r.request_id)).size!==x.records.length)return invalid();
 for(const i of x.items) {
  const latest=x.records.findLast(r=>r.item_id===i.item_id);
  if(i.latest_review_id!==(latest?.review_id??null))return invalid();
  const status=!latest?'pending':latest.decision==='follow_up'?'follow_up':latest.applicable&&i.target_exists&&!x.untracked_stages.length?'retained':'stale';
  if(i.status!==status)return invalid();
 }
 return x as unknown as ReviewWorkspace;
}
export function reviewInput(workspace:ReviewWorkspace,itemId:string,decision:keyof typeof DECISIONS,note:string,requestId:string):ReviewInput {
 const item=workspace.items.find(i=>i.item_id===itemId);
 if(!item||workspace.running||!Object.hasOwn(DECISIONS,decision)||!note.trim()||note.length>1000||!id(requestId)||decision==='retain'&&(!item.target_exists||workspace.untracked_stages.length))throw new RevisionError('请核对事项、填写说明并检查目标是否仍存在',422);
 return {expected_token:workspace.token,item_id:itemId,decision,note,request_id:requestId,acknowledged:true};
}
async function requestReviews(projectId:string,input?:ReviewInput,fetcher?:typeof fetch):Promise<unknown> {
 try{return await revisionRequest(projectId,'/reviews',input,!!input,fetcher);}
 catch(e){if(!(e instanceof RevisionError))throw e;
 const messages:Record<number,string>={404:'项目或核对事项不存在，请返回项目核对',409:'核对依据或记录已变化，或项目正在执行，请重新读取',422:'核对内容不符合要求，请检查目标与说明',503:'核对数据暂不可用，请保留说明并检查服务'};
 throw new RevisionError(messages[e.status]??(input?'核对保存结果尚未确认，请重新读取记录；不会自动重发':'暂时无法读取核对记录，请检查本地服务'),e.status,e.uncertain);}
}
export async function readReviews(projectId:string,fetcher?:typeof fetch){return parseReviews(await requestReviews(projectId,undefined,fetcher),projectId);}
export async function saveReview(projectId:string,input:ReviewInput,fetcher?:typeof fetch){
 const x=await requestReviews(projectId,input,fetcher);
 if(!isRecord(x)||!id(x.review_id))return invalid();
 const workspace=parseReviews(x.workspace,projectId),record=workspace.records.find(r=>r.review_id===x.review_id);
 if(!record||record.item_id!==input.item_id||record.decision!==input.decision||record.note!==input.note||record.request_id!==input.request_id)return invalid();
 return {review_id:x.review_id,workspace};
}
