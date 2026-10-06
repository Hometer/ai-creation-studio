import {isRecord} from './workflowTypes';
import type {TaskRecord,TaskStatus} from './taskCenter';
const id=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{32}$/.test(v);
const key=(v:unknown):v is string=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(v);
const int=(v:unknown,a:number,b:number):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=a&&v<=b;
const text=(v:unknown,n:number):v is string=>typeof v==='string'&&!!v.trim()&&v.length<=n;
const labels:Record<string,string>={creation:'文字修改候选',script:'剧本候选',storyboard:'分镜候选',image:'参考图修订',video:'视频候选',composition:'成片合成'};
const states:Record<string,TaskStatus>={draft:'waiting',awaiting_confirmation:'waiting',queued:'queued',submitting:'running',pending:'running',querying:'running',ready_download:'running',downloading:'running',rendering:'running',succeeded:'completed',failed:'failed',manual_review:'review',cancelled:'cancelled'};
const phases:Record<string,string>={draft:'等待本次合成确认',awaiting_confirmation:'等待费用与素材确认',queued:'已确认，排队中',submitting:'提交回复核对中',pending:'平台处理中',querying:'正在核对原任务',ready_download:'准备归档',downloading:'正在归档',rendering:'本机合成中',succeeded:'结果已保存',failed:'执行未完成',manual_review:'核对原任务，不自动重发',cancelled:'已取消后续动作'};
const invalid=():never=>{throw Error('首版任务摘要不完整');};
export function parseReleaseTasks(v:unknown):TaskRecord[]{
 if(!isRecord(v)||Object.keys(v).length!==1||!Array.isArray(v.tasks)||v.tasks.length>500)return invalid();
 const rows=v.tasks.map((j):TaskRecord=>{
  const keys=['job_id','kind','project_id','project_exists','title','number','created_at','status','simulation_only','model_call','estimate_fen','reserved_fen','actual_billed_fen','provider_task_id','href','result_url','error_summary'];
  if(!isRecord(j)||Object.keys(j).length!==keys.length||keys.some(k=>!(k in j))||!id(j.job_id)||!key(j.project_id)||typeof j.project_exists!=='boolean'||!text(j.kind,20)||!labels[j.kind]||!text(j.title,160)||!int(j.number,1,200)||!text(j.created_at,100)||!Number.isFinite(Date.parse(j.created_at))||!text(j.status,40)||!states[j.status]||typeof j.simulation_only!=='boolean'||typeof j.model_call!=='boolean'||!int(j.estimate_fen,0,500)||!int(j.reserved_fen,0,500)||j.actual_billed_fen!==null||!(j.provider_task_id===null||typeof j.provider_task_id==='string'&&/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(j.provider_task_id))||!text(j.href,600)||!(j.error_summary===null||text(j.error_summary,160)))return invalid();
  const composition=j.kind==='composition';if(j.model_call===composition||composition&&(j.estimate_fen!==0||j.reserved_fen!==0||j.provider_task_id!==null)||!composition&&(j.estimate_fen===0||j.reserved_fen>0&&j.reserved_fen!==j.estimate_fen)||!['image','video'].includes(j.kind)&&j.provider_task_id!==null)return invalid();
  const project=encodeURIComponent(j.project_id);let expected='';
  if(j.kind==='creation'){const url=new URL(j.href,'http://local.invalid');const intent=url.searchParams.get('intent');if(!id(intent)||[...url.searchParams.keys()].length!==2||url.searchParams.get('candidate')!==j.job_id)return invalid();expected=`/projects/${project}/co-creation?intent=${intent}&candidate=${j.job_id}`;}
  else expected=`/projects/${project}/${composition?'compose':['image','video'].includes(j.kind)?'media':'drafts'}?job=${j.job_id}`;
  if(j.href!==expected)return invalid();
  if(j.result_url!==null){
   if(j.status!=='succeeded'||!text(j.result_url,600))return invalid();
   const valid=composition?j.result_url===`/api/project/${project}/compositions/${j.job_id}/media`:j.kind==='video'&&/^\/api\/assets\/[a-f0-9]{32}\/versions\/[a-f0-9]{32}\/media$/.test(j.result_url);
   if(!valid)return invalid();
  }
  const allowed=composition?['draft','queued','rendering','succeeded','failed','manual_review','cancelled']:['image','video'].includes(j.kind)?['awaiting_confirmation','queued','submitting','pending','querying','ready_download','downloading','succeeded','failed','manual_review','cancelled']:['awaiting_confirmation','queued','submitting','succeeded','failed','manual_review'];
  if(!allowed.includes(j.status)||!composition&&(j.status==='awaiting_confirmation'?j.reserved_fen!==0:j.status!=='cancelled'&&j.reserved_fen!==j.estimate_fen))return invalid();

  const reasons:string[]=[];if(j.error_summary)reasons.push(j.error_summary);if(!j.project_exists)reasons.push('所属项目暂不可读取，任务记录保留');if(j.provider_task_id)reasons.push('平台任务标识：'+j.provider_task_id);
  return {key:`first_release:${j.kind}:${j.job_id}`,id:j.job_id,title:j.title,source:'first_release',status:states[j.status],date:j.created_at,href:j.href,projectId:j.project_id,projectAvailable:j.project_exists,phase:labels[j.kind]+' · '+phases[j.status],progress:null,note:composition?'成片按明确选用片段在本机合成。查看不会重做；旧成片保留。':'候选完成只表示结果已保存；正文、引用和片段选用须回原入口核对。查看不提交模型或自动收费。',simulation:j.simulation_only,resultUrl:j.result_url as string|null,billing:composition?'本机合成 · 模型费用0元':j.simulation_only?'人工接口样例 · 未产生真实模型费用':`计划估算 ${(j.estimate_fen/100).toFixed(2)} 元 · 预留 ${(j.reserved_fen/100).toFixed(2)} 元 · 实际账单待核对`,reasons};
 });
 if(new Set(rows.map(r=>r.key)).size!==rows.length)return invalid();return rows;
}
export async function readReleaseTasks(signal?:AbortSignal){const c=new AbortController();const stop=()=>c.abort();signal?.addEventListener('abort',stop,{once:true});if(signal?.aborted)c.abort();const timer=setTimeout(stop,10000);try{const r=await fetch('/api/first-release-tasks',{cache:'no-store',signal:c.signal});if(!r.ok)throw Error('首版任务暂不可读取');const rows=parseReleaseTasks(await r.json());if(c.signal.aborted)throw Error('已取消');return rows;}finally{clearTimeout(timer);signal?.removeEventListener('abort',stop);}}
