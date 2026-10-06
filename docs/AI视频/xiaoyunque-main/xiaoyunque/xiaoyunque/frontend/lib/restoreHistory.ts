import {RevisionError,type Stage,type Version,type Workspace} from './contentRevisions';
import type {RestoreReceipt} from './revisionImportRestore';
import type {ReviewWorkspace} from './impactReviews';
export const LOCATION_EVENT='revisionlocationchange';
const validId=(s:string)=>/^[a-f0-9]{32}$/.test(s);
export function addressId(search:string,key:string):string{
  const params=new URLSearchParams(search),values=params.getAll(key);
  if(!values.length)return '';
  if(values.length!==1||!validId(values[0]))throw new RevisionError('地址中的查看位置无效，请重新选择',422);
  return values[0];
}
export function selectedRestore(receipts:RestoreReceipt[],search:string){
  const id=addressId(search,'restore');
  if(!id)return undefined;
  const record=receipts.find(r=>r.restore_id===id);
  if(!record)throw new RevisionError('所选恢复记录不存在于本项目，请重新选择',404);
  return record;
}
export function restorePairs(workspace:Workspace,record:RestoreReceipt):{stage:Stage;before:Version;after:Version}[]{
  return (Object.entries(record.version_ids) as [Stage,string][]).map(([stage,id])=>{
    const after=workspace.versions.find(v=>v.version_id===id&&v.stage===stage);
    const before=after&&workspace.versions.filter(v=>v.stage===stage&&v.number<after.number).at(-1);
    if(!after||!before)throw new RevisionError('恢复前后版本暂不可定位，请重新读取记录与版本',422);
    return {stage,before,after};
  });
}
export function historyAddress(href:string,record:RestoreReceipt,pair?:{before:Version;after:Version},compare=false){
  const url=new URL(href);url.searchParams.set('restore',record.restore_id);
  if(pair)url.searchParams.set('version',pair.after.version_id);
  if(pair&&compare){url.searchParams.set('compare_from',pair.before.version_id);url.searchParams.set('compare_to',pair.after.version_id);}
  url.hash=compare?'revision-comparison':pair?'revision-history':'revision-restore-records';
  return url;
}
export function reviewScope(workspace:ReviewWorkspace,search:string){
  const version=addressId(search,'version');
  if(version&&!workspace.sources.some(s=>s.version_id===version))throw new RevisionError('所选修改版本没有可读取的核对来源，请返回文字版本核对',404);
  return {version,items:workspace.items.filter(i=>!version||i.version_id===version)};
}
export function reviewHistoryLink(projectId:string,record:RestoreReceipt,versionId:string){
  if(!Object.values(record.version_ids).includes(versionId))throw new RevisionError('核对版本与恢复记录不一致',422);
  return `/projects/${encodeURIComponent(projectId)}/reviews?version=${versionId}&restore=${record.restore_id}`;
}
