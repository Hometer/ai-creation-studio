import {isRecord} from './workflowTypes';
import {RevisionError} from './contentRevisions';
export interface ProjectGoal {project_id:string;project_name:string|null;target_duration:number|null;story_excerpt:string;style:string;video_ratio:string;revision:number;running:boolean;expected_token:string}
const key=(v:unknown):v is string=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(v);
const id=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{32}$/.test(v);
const hash=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const text=(v:unknown,n:number):v is string=>typeof v==='string'&&[...v].length<=n;
const integer=(v:unknown,lo:number,hi:number):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=lo&&v<=hi;
const invalid=():never=>{throw new RevisionError('项目目标记录不完整，请重新读取核对',0);};
export function parseGoal(x:unknown,project:string):ProjectGoal {
 const keys=['project_id','project_name','target_duration','story_excerpt','style','video_ratio','revision','running','expected_token'];
 if(!isRecord(x)||Object.keys(x).length!==keys.length||!keys.every(k=>k in x)||x.project_id!==project||!(x.project_name===null||text(x.project_name,100)&&x.project_name.trim())||!(x.target_duration===null||integer(x.target_duration,1,600))||!text(x.story_excerpt,2000)||!text(x.style,100)||!text(x.video_ratio,32)||!integer(x.revision,0,200)||typeof x.running!=='boolean'||!hash(x.expected_token))return invalid();
 return x as unknown as ProjectGoal;
}
async function call(project:string,input?:unknown):Promise<unknown>{
 if(!key(project))return invalid();const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
 try{const r=await fetch(`/api/project/${encodeURIComponent(project)}/goal`,{method:input?'POST':'GET',cache:'no-store',headers:input?{'Content-Type':'application/json'}:undefined,body:input?JSON.stringify(input):undefined,signal:controller.signal});const raw:unknown=await r.json();if(!r.ok)throw new RevisionError(isRecord(raw)&&isRecord(raw.error)&&text(raw.error.message,1000)?raw.error.message:'项目目标操作未完成',r.status,!!input&&r.status>=500);if(controller.signal.aborted)throw new Error();return raw;
 }catch(e){if(e instanceof RevisionError)throw e;throw new RevisionError(input?'保存结果未确认，请重新读取目标核对，输入已保留':'无法读取项目目标，请检查本地服务',0,!!input);}finally{clearTimeout(timer);}
}
export async function readGoal(project:string){return parseGoal(await call(project),project);}
export async function saveGoal(project:string,data:ProjectGoal,name:string,duration:string,requestId:string){parseGoal(data,project);const trimmed=name.trim();if(data.running||!trimmed||!text(trimmed,100)||!/^\d+$/.test(duration)||!integer(Number(duration),1,600)||!id(requestId))throw new RevisionError('请填写1—100字项目名称和1—600秒目标时长，并等待项目执行结束',422);
 const raw=await call(project,{project_name:trimmed,target_duration:Number(duration),expected_token:data.expected_token,request_id:requestId});try{return parseGoal(raw,project);}catch{throw new RevisionError('保存结果未确认，请重新读取目标核对',0,true);}
}
