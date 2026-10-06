import {isRecord} from './workflowTypes';
import {RevisionError,type Workspace,type Stage} from './contentRevisions';
import {MAX_EXPORT_BYTES} from './revisionExport';

type StageSummary={digest:string;field_count:number;version_count:number;matches_current:boolean;untracked:boolean;version_id:string|null};
type VersionSummary={version_id:string;number:number;stage:Stage;source:'baseline'|'external'|'edit'|'restore';created_at:string;restored_from:string|null;field_count:number};
export type PrecheckReport={project_id:string;token:string;mode:'read_only';source_project_id:string;same_project:boolean;file_bytes:number;file_sha256:string;schema_version:1;stages:Record<Stage,StageSummary>;versions:VersionSummary[]};
const stages:Stage[]=['script_generation','storyboard'];
const hash=(x:unknown):x is string=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x);
const id=(x:unknown):x is string=>typeof x==='string'&&/^[a-f0-9]{32}$/.test(x);
const count=(x:unknown,max=Number.MAX_SAFE_INTEGER):x is number=>typeof x==='number'&&Number.isSafeInteger(x)&&x>=0&&x<=max;
const exact=(x:Record<string,unknown>,keys:string[])=>Object.keys(x).length===keys.length&&keys.every(k=>Object.hasOwn(x,k));
const invalid=():never=>{throw new RevisionError('检查报告无法与所选文件核对，请保留文件并重新检查',0);};
export function validateBackupFile(file:Pick<File,'name'|'size'|'type'>){
  if(!file.name||file.name.length>128||file.name!==file.name.trim()||!file.name.toLowerCase().endsWith('.json')||/[\\/\x00-\x1f\x7f]/.test(file.name)||!['','application/json'].includes(file.type))throw new RevisionError('请选择文件名安全的 JSON 文字备份，不支持 TXT、路径或其他格式',415);
  if(!Number.isSafeInteger(file.size)||file.size<=0||file.size>MAX_EXPORT_BYTES)throw new RevisionError('文字备份必须非空且不超过 64 MiB',413);
}
export function parsePrecheckReport(x:unknown,workspace:Workspace,size:number,checksum:string):PrecheckReport{
  if(!isRecord(x)||!exact(x,['project_id','token','mode','source_project_id','same_project','file_bytes','file_sha256','schema_version','stages','versions'])||x.project_id!==workspace.project_id||x.token!==workspace.token||x.mode!=='read_only'||x.schema_version!==1||x.file_bytes!==size||x.file_sha256!==checksum||!hash(checksum)||typeof x.source_project_id!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(x.source_project_id)||x.same_project!==(x.source_project_id===workspace.project_id)||!isRecord(x.stages)||!exact(x.stages,stages)||!Array.isArray(x.versions))return invalid();
  const versions=x.versions as unknown[];
  const previous=new Map<string,Stage>();
  for(const [index,v] of versions.entries()){
    if(!isRecord(v)||!exact(v,['version_id','number','stage','source','created_at','restored_from','field_count'])||!id(v.version_id)||previous.has(v.version_id)||v.number!==index+1||!stages.includes(v.stage as Stage)||!['baseline','external','edit','restore'].includes(String(v.source))||typeof v.created_at!=='string'||!Number.isFinite(Date.parse(v.created_at))||!count(v.field_count,500)||(v.source==='restore'?(!id(v.restored_from)||previous.get(v.restored_from)!==v.stage):v.restored_from!==null))return invalid();
    previous.set(v.version_id,v.stage as Stage);
  }
  for(const stage of stages){
    const s=x.stages[stage],rows=(versions as VersionSummary[]).filter(v=>v.stage===stage),latest=rows.at(-1);
    if(!isRecord(s)||!exact(s,['digest','field_count','version_count','matches_current','untracked','version_id'])||!hash(s.digest)||!count(s.field_count,500)||s.version_count!==rows.length||typeof s.matches_current!=='boolean'||typeof s.untracked!=='boolean'||!(s.version_id===null||id(s.version_id))||(s.version_id!==null&&(s.untracked||s.version_id!==latest?.version_id))||(s.untracked&&!latest)||(latest&&!s.untracked&&s.version_id!==latest.version_id))return invalid();
  }
  return x as unknown as PrecheckReport;
}
export async function requestBackup<T>(workspace:Workspace,file:File,suffix:string,parse:(value:unknown,size:number,checksum:string)=>T,fetcher:typeof fetch=fetch,signal?:AbortSignal):Promise<T>{
  validateBackupFile(file);
  const controller=new AbortController(),abort=()=>controller.abort();
  if(signal?.aborted)abort();signal?.addEventListener('abort',abort,{once:true});
  const timer=setTimeout(abort,20000);
  let reader:ReadableStreamDefaultReader<Uint8Array>|undefined;
  try{
    const bytes=await file.arrayBuffer();
    if(bytes.byteLength!==file.size) return invalid();
    const checksum=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(b=>b.toString(16).padStart(2,'0')).join('');
    if(controller.signal.aborted)throw new Error('Read deadline');
    const query=new URLSearchParams({expected_token:workspace.token,filename:file.name});
    const response=await fetcher(`/api/project/${encodeURIComponent(workspace.project_id)}/content-revisions/${suffix}?${query}`,{method:'POST',headers:{'Content-Type':'application/json'},body:bytes,cache:'no-store',signal:controller.signal});
    if(!response.ok){
      const messages:Record<number,string>={404:'项目不存在，请返回项目核对',409:'当前文字或历史已变化，请重新读取版本后再检查备份',413:'备份超过 64 MiB，请保留文件',415:'只支持文件名安全的 JSON 文字备份',422:'备份格式、摘要或版本关系无法校验，请保留文件并重新导出 JSON',503:'当前项目版本暂不可读，请检查本地服务后重试'};
      throw new RevisionError(messages[response.status]??'检查暂未完成，请保留文件并手动重试',response.status);
    }
    if(response.headers.get('Content-Type')?.split(';')[0]!=='application/json'||!response.body)return invalid();
    reader=response.body.getReader();const chunks:Uint8Array[]=[];let size=0;
    while(true){const part=await reader.read();if(controller.signal.aborted)throw new Error('Read deadline');if(part.done)break;size+=part.value.byteLength;if(size>8*1024*1024)return invalid();chunks.push(part.value);}
    const reportBytes=new Uint8Array(size);let offset=0;for(const part of chunks){reportBytes.set(part,offset);offset+=part.byteLength;}
    const result=parse(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(reportBytes)),file.size,checksum);
    if(controller.signal.aborted)throw new Error('Read deadline');
    return result;
  }catch(e){if(e instanceof RevisionError)throw e;throw new RevisionError('检查未完成，请保留文件并检查本地服务后手动重试；不会自动提交',0);}
  finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);try{await reader?.cancel();}catch{/* Report only; never a business write. */}reader?.releaseLock();}
}

export async function checkBackup(workspace:Workspace,file:File,fetcher:typeof fetch=fetch,signal?:AbortSignal){
  return requestBackup(workspace,file,'import-precheck',(value,size,checksum)=>parsePrecheckReport(value,workspace,size,checksum),fetcher,signal);
}
