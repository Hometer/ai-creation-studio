'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import {FileCheck} from 'lucide-react';
import {STAGES,type Workspace,type Stage} from '@/lib/contentRevisions';
import {checkBackup,type PrecheckReport} from '@/lib/revisionImportPrecheck';
import {prepareBackup,restoreBackup,readBackupRestores,type PreparedBackup,type RestoreReceipt,type RestoreWorkspace} from '@/lib/revisionImportRestore';
import RestoreHistory from './RestoreHistory';
import RevisionBackupPreview from './RevisionBackupPreview';
const sources={baseline:'首次归档',external:'原入口更新归档',edit:'手动修改',restore:'恢复旧版'};
export default function RevisionImportPrecheck({workspace,disabled,onSaving,onRestored,onBeforeRestore,onBeforeRead,onBlocked,onRecords}:{workspace:Workspace;disabled:boolean;onSaving:(saving:boolean)=>void;onRestored:(workspace:Workspace)=>void;onBeforeRestore:()=>boolean;onBeforeRead:()=>boolean;onBlocked:()=>void;onRecords:(data:RestoreWorkspace|null)=>void}){
  const [file,setFile]=useState<File|null>(null),[report,setReport]=useState<PrecheckReport|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[preview,setPreview]=useState<PreparedBackup|null>(null),[operation,setOperation]=useState<'check'|'preview'|'save'|'read'>('check');
  const [ack,setAck]=useState(false),[halted,setHalted]=useState(false),[receipts,setReceipts]=useState<RestoreReceipt[]>([]),[recordError,setRecordError]=useState(''),[notice,setNotice]=useState(''),[recordsToken,setRecordsToken]=useState('');
  const recordsReady=recordsToken===workspace.token;
  const pending=useRef<string|null>(null);
  const generation=useRef(0),lock=useRef(false),controller=useRef<AbortController|null>(null);
  const cancel=useCallback(()=>{++generation.current;lock.current=false;controller.current?.abort();},[]);
  useEffect(()=>{cancel();const timer=window.setTimeout(()=>{setBusy(false);setReport(null);setPreview(null);setAck(false);setError('');},0);return()=>{cancel();window.clearTimeout(timer);};},[workspace,cancel]);
  const inspect=async()=>{
    if(disabled||halted||lock.current||!file)return;
    lock.current=true;const request=++generation.current;controller.current=new AbortController();setBusy(true);setOperation('check');setReport(null);setPreview(null);setAck(false);setError('');setNotice('');
    try{const result=await checkBackup(workspace,file,undefined,controller.current.signal);if(request===generation.current)setReport(result);}
    catch(e){if(request===generation.current)setError(e instanceof Error?e.message:'检查未完成，请保留文件并手动重试');}
    finally{if(request===generation.current){lock.current=false;setBusy(false);}}
  };
  const restorePreview=async()=>{
    if(disabled||halted||workspace.running||lock.current||!file||!report?.same_project)return;
    lock.current=true;const request=++generation.current;controller.current=new AbortController();setBusy(true);setOperation('preview');setPreview(null);setAck(false);setError('');setNotice('');
    try{const result=await prepareBackup(workspace,file,report,undefined,controller.current.signal);if(request===generation.current)setPreview(result);}
    catch(e){if(request===generation.current)setError(e instanceof Error?e.message:'恢复预览未完成，请保留文件并手动重试');}
    finally{if(request===generation.current){lock.current=false;setBusy(false);}}
  };
  useEffect(()=>{let active=true;void readBackupRestores(workspace.project_id).then(x=>{if(active){setReceipts(x.restores);setRecordError('');setRecordsToken(x.workspace.token);onRecords(x);}}).catch(()=>{if(active){setRecordError('恢复记录暂未读取，请手动读取核对后再恢复');onRecords(null);}});return()=>{active=false;};},[workspace,onRecords]);
  const save=async()=>{
    if(disabled||halted||!recordsReady||recordError||lock.current||!file||!preview||!ack||!preview.preview.total_changes||!onBeforeRestore())return;
    lock.current=true;const request=++generation.current;controller.current=new AbortController();setBusy(true);setOperation('save');setError('');setNotice('');onSaving(true);const requestId=crypto.randomUUID().replaceAll('-','');pending.current=requestId;
    try{const result=await restoreBackup(workspace,file,preview,requestId,true,undefined,controller.current.signal);if(request===generation.current){setReceipts(result.data.restores);setRecordsToken(result.data.workspace.token);onRecords(result.data);setNotice(`已恢复并保存第 ${result.receipt.number} 次记录，原正文与历史已保留。`);setPreview(null);setAck(false);pending.current=null;onRestored(result.data.workspace);}}
    catch(e){if(request===generation.current){setError(e instanceof Error?e.message:'恢复结果尚未确认，请读取恢复记录与版本');setPreview(null);setAck(false);setHalted(true);onRecords(null);onBlocked();}}
    finally{if(request===generation.current){lock.current=false;setBusy(false);}onSaving(false);}
  };
  const read=async()=>{
    if(lock.current||!onBeforeRead())return;lock.current=true;const request=++generation.current;setBusy(true);setOperation('read');onSaving(true);setRecordError('');
    try{const result=await readBackupRestores(workspace.project_id);if(request===generation.current){const found=result.restores.find(r=>r.request_id===pending.current);setReceipts(result.restores);setRecordsToken(result.workspace.token);onRecords(result);setNotice(pending.current?(found?`已找到第 ${found.number} 次持久恢复记录，请核对新增版本。`:'未找到本次持久恢复记录；请重新检查、预览并确认后继续。'):'已读取持久恢复记录与版本。');setError('');setReport(null);setPreview(null);setAck(false);setHalted(false);pending.current=null;onRestored(result.workspace);}}
    catch(e){if(request===generation.current){setRecordError(e instanceof Error?e.message:'读取未完成，请保留数据重试');setHalted(true);onRecords(null);onBlocked();}}
    finally{if(request===generation.current){lock.current=false;setBusy(false);}onSaving(false);}
  };
  const visible=!disabled&&report?.project_id===workspace.project_id&&report.token===workspace.token?report:null;
  return <section id="revision-import-precheck" className="revision-card revision-export" aria-label="文字备份检查与恢复">
    <h2><FileCheck size={20}/>检查文字备份</h2>
    <p>选择下载过的 JSON 文字备份，先核对格式、版本关系和包含的范围。</p>
    <p className="revision-muted">仅送到本地后端检查，不保存文件、不改正文或版本。支持 JSON，最大 64 MiB；TXT、媒体和完整会话文件不支持。检查不会改变本页编辑草稿与预览。</p>
    <label className="revision-field">选择 JSON 文字备份<input className="revision-backup-file" type="file" accept=".json,application/json" disabled={disabled||halted||busy} onChange={e=>{setFile(e.target.files?.[0]??null);setReport(null);setPreview(null);setAck(false);setError('');setNotice('');}}/></label>
    <div className="revision-actions"><button className="revision-primary" disabled={disabled||halted||busy||!file} onClick={()=>void inspect()}>检查这份备份</button></div>
    {!file&&<p className="revision-muted">尚未选择文件。选择后仍需点检查，不会自动上传。</p>}
    {busy&&<p role="status">{operation==='check'?'正在检查所选备份…':operation==='preview'?'正在计算备份恢复差异…':operation==='save'?'正在保存两阶段文字与恢复记录…':'正在读取恢复记录与版本…'}</p>}
    {error&&<p role="alert" className="revision-alert">{error}</p>}
    {visible&&<div className="revision-precheck-result" role="status"><h3>检查通过 · 尚未恢复</h3>
      <p>来源项目：{visible.source_project_id} · JSON 格式第 {visible.schema_version} 版 · {visible.file_bytes.toLocaleString('zh-CN')} 字节 · {visible.versions.length} 条历史版本</p>
      {!visible.same_project&&<p className="revision-alert">备份来自另一个项目。这里只检查内容，不会把来源项目编号或版本关联写入本项目。</p>}
      {(Object.keys(STAGES) as Stage[]).map(stage=>{const s=visible.stages[stage];return <section key={stage} className="revision-precheck-stage"><h4>{STAGES[stage]}</h4><p>{s.field_count} 个可编辑文字字段 · {s.version_count} 条历史版本</p><p>{s.matches_current?'备份当前内容与本项目当前正文相同':'备份当前内容与本项目当前正文不同'}；{s.untracked?'备份当前内容尚未归档':s.version_id?'备份当前内容已有对应归档版本':'备份没有对应归档版本'}。</p></section>;})}
      <details><summary>查看备份版本范围 · 共 {visible.versions.length} 条</summary>{visible.versions.length?<ol className="revision-precheck-versions">{visible.versions.map(v=><li key={v.version_id}>v{v.number} · {STAGES[v.stage]} · {sources[v.source]} · {new Date(v.created_at).toLocaleString('zh-CN')} · {v.field_count} 个文字字段{v.restored_from?` · 恢复自 v${visible.versions.find(old=>old.version_id===v.restored_from)?.number}`:''}</li>)}</ol>:<p>没有历史版本，仍保留两阶段当前结构。</p>}</details>
      <p className="revision-muted">仅表示文件格式、摘要与内部关系通过检查，不能证明来源可信。还未写入任何内容；可继续预览同项目恢复差异；恢复前需逐次勾选确认，原正文与历史保留。文件和报告仅在本页，刷新需重新选择检查。</p>
      <p className="revision-muted">恢复预览只比较备份中的当前剧本和分镜，不导入历史。</p>
      {!visible.same_project?<p className="revision-muted">不同来源项目不能预览恢复，请回到备份所属项目。</p>:workspace.running?<p className="revision-alert">项目正在执行，请结束任务并重新读取后预览文字恢复。</p>:null}
      <div className="revision-actions"><button disabled={disabled||halted||busy||!visible.same_project||workspace.running} onClick={()=>void restorePreview()}>预览恢复当前文字</button></div>
      {!disabled&&!workspace.running&&preview?.preview.token===workspace.token&&preview.preview.file_sha256===visible.file_sha256&&<><RevisionBackupPreview preview={preview.preview}/>{preview.preview.total_changes>0&&<section className="revision-preview" aria-label="备份恢复确认"><h3>确认恢复当前剧本与分镜</h3><p>只将有变化阶段保存为新本地版本，两阶段同次提交。恢复前正文与原历史保留；备份旧历史不导入，媒体与共创保留但需重新核对。</p><p>本页未保存编辑与原预览在恢复成功后失效，请先复制需要保留的文字。核对备份来源与全部差异后再确认。</p><label className="revision-check"><input type="checkbox" checked={ack} disabled={busy||halted} onChange={e=>setAck(e.target.checked)}/>我已核对备份、两阶段差异及影响，确认恢复并保留旧版本</label><div className="revision-actions"><button disabled={busy} onClick={()=>{setPreview(null);setAck(false);}}>取消恢复预览</button><button className="revision-primary" disabled={busy||halted||!recordsReady||!!recordError||!ack} onClick={()=>void save()}>确认恢复并保存新版本</button></div></section>}</>}
    </div>}
    {notice&&<p className="revision-notice" role="status">{notice}</p>}
    {(halted||recordError)&&<p className="revision-alert" role="alert">{recordError||'恢复回复需要核对，已暂停写入。先读取恢复记录与版本，不会自动重发。'}</p>}
    <RestoreHistory workspace={workspace} receipts={receipts} ready={recordsReady} failed={!!recordError} disabled={busy} actionsDisabled={disabled||halted} onRead={()=>void read()} />
  </section>;
}
