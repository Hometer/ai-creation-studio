'use client';
import {useEffect,useState} from 'react';
import Link from 'next/link';
import {STAGES,type Workspace} from '@/lib/contentRevisions';
import type {RestoreReceipt} from '@/lib/revisionImportRestore';
import {LOCATION_EVENT,selectedRestore,restorePairs,historyAddress,reviewHistoryLink} from '@/lib/restoreHistory';
export default function RestoreHistory({workspace,receipts,ready,failed,disabled,actionsDisabled,onRead}:{workspace:Workspace;receipts:RestoreReceipt[];ready:boolean;failed:boolean;disabled:boolean;actionsDisabled:boolean;onRead:()=>void}){
  const [search,setSearch]=useState('');
  useEffect(()=>{const read=()=>setSearch(location.search);const timer=setTimeout(read,0);window.addEventListener('popstate',read);window.addEventListener(LOCATION_EVENT,read);return()=>{clearTimeout(timer);window.removeEventListener('popstate',read);window.removeEventListener(LOCATION_EVENT,read);};},[]);
  useEffect(()=>{if(!ready||failed)return;const timer=setTimeout(()=>{if(location.hash==='#revision-restore-records')document.getElementById('revision-restore-records')?.scrollIntoView({block:'start'});},0);return()=>clearTimeout(timer);},[ready,failed]);
  let selected:RestoreReceipt|undefined,error='';
  try{selected=selectedRestore(receipts,search);}catch(e){error=e instanceof Error?e.message:'查看位置无法读取';}
  const choose=(record:RestoreReceipt,pair?:ReturnType<typeof restorePairs>[number],compare=false)=>{
    if(disabled||actionsDisabled||!ready||failed)return;
    const url=historyAddress(location.href,record,pair,compare);history.pushState(history.state,'',url);window.dispatchEvent(new Event(LOCATION_EVENT));
    const target=document.getElementById(url.hash.slice(1));target?.scrollIntoView({block:'start'});target?.focus({preventScroll:true});
  };
  return <section id="revision-restore-records" className="revision-precheck-stage revision-restore-records" aria-label="持久恢复记录" tabIndex={-1}>
    <h3>备份恢复记录</h3><p className="revision-muted">选择一次恢复，查看当时新增的版本和前后差异，再核对相关镜头。历史恢复不表示当前文字或媒体已审核。</p>
    <button disabled={disabled} onClick={onRead}>读取恢复记录与版本</button>
    {(!ready||failed)&&<p role="status" className="revision-muted">{failed?'恢复记录读取失败，旧记录保留；重新读取核实后才能定位。':'恢复记录尚未与当前版本核实，正在读取；若仍未就绪请手动读取。'}</p>}
    {ready&&!failed&&!receipts.length&&<p>尚无已保存的备份恢复记录。</p>}
    {error&&ready&&!failed&&<p role="alert" className="revision-alert">{error}<button disabled={disabled} onClick={()=>{const url=new URL(location.href);url.searchParams.delete('restore');history.pushState(history.state,'',url);window.dispatchEvent(new Event(LOCATION_EVENT));}}>清除恢复查看位置</button></p>}
    <ol>{[...receipts].reverse().map(r=><li key={r.restore_id}><button aria-pressed={selected?.restore_id===r.restore_id} disabled={disabled||actionsDisabled||!ready||failed} onClick={()=>choose(r)}>查看第 {r.number} 次恢复 · {new Date(r.created_at).toLocaleString('zh-CN')}</button><p>{Object.entries(r.version_ids).map(([s,id])=>`${STAGES[s as keyof typeof STAGES]} v${workspace.versions.find(v=>v.version_id===id)?.number??'待重新读取'}`).join('、')}</p></li>)}</ol>
    {selected&&ready&&!failed&&<RestoreDetails workspace={workspace} record={selected} disabled={disabled||actionsDisabled} choose={choose}/>}
  </section>;
}
function RestoreDetails({workspace,record,disabled,choose}:{workspace:Workspace;record:RestoreReceipt;disabled:boolean;choose:(record:RestoreReceipt,pair:ReturnType<typeof restorePairs>[number],compare?:boolean)=>void}){
  let pairs:ReturnType<typeof restorePairs>;
  try{pairs=restorePairs(workspace,record);}catch(e){return <p role="alert" className="revision-alert">{e instanceof Error?e.message:'版本关系无法定位'}</p>;}
  return <section aria-label="所选恢复详情"><h4>第 {record.number} 次恢复 · 当时保存的结果</h4>
    {pairs.map(pair=><article key={pair.stage}><h4>{STAGES[pair.stage]} · v{pair.before.number} → v{pair.after.number}</h4><p>{pair.after.current?'恢复结果与当前该阶段文字一致；仍需人工核对相关内容。':'这是历史恢复结果，当前该阶段文字已有变化。'}</p><div className="revision-actions"><button disabled={disabled} onClick={()=>choose(record,pair)}>定位{STAGES[pair.stage]} v{pair.after.number}</button><button disabled={disabled} onClick={()=>choose(record,pair,true)}>对比{STAGES[pair.stage]}恢复前后</button>{!disabled&&pair.after.impacts.length>0&&<Link href={reviewHistoryLink(workspace.project_id,record,pair.after.version_id)}>核对{STAGES[pair.stage]}修改影响</Link>}</div>{!pair.after.impacts.length&&<p className="revision-muted">本阶段没有可关联的镜头核对事项；请自行检查文字和媒体，不代表已通过审核。</p>}</article>)}
    {(Object.keys(STAGES) as (keyof typeof STAGES)[]).filter(s=>!record.version_ids[s]).map(s=><p key={s}>{STAGES[s]}当时没有变化，未新增版本。</p>)}
    <details><summary>恢复依据</summary><p>来源：本项目 · {record.file_bytes.toLocaleString('zh-CN')} 字节</p><p className="revision-muted">文件摘要：{record.file_sha256}</p><p className="revision-muted">恢复编号：{record.restore_id}</p></details>
  </section>;
}
