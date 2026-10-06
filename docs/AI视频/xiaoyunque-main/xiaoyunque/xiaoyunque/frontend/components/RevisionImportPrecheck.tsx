'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import {FileCheck} from 'lucide-react';
import {STAGES,type Workspace,type Stage} from '@/lib/contentRevisions';
import {checkBackup,type PrecheckReport} from '@/lib/revisionImportPrecheck';
const sources={baseline:'首次归档',external:'原入口更新归档',edit:'手动修改',restore:'恢复旧版'};
export default function RevisionImportPrecheck({workspace,disabled}:{workspace:Workspace;disabled:boolean}){
  const [file,setFile]=useState<File|null>(null),[report,setReport]=useState<PrecheckReport|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const generation=useRef(0),lock=useRef(false),controller=useRef<AbortController|null>(null);
  const cancel=useCallback(()=>{++generation.current;lock.current=false;controller.current?.abort();},[]);
  useEffect(()=>{cancel();const timer=window.setTimeout(()=>{setBusy(false);setReport(null);setError('');},0);return()=>{cancel();window.clearTimeout(timer);};},[workspace,cancel]);
  const inspect=async()=>{
    if(disabled||lock.current||!file)return;
    lock.current=true;const request=++generation.current;controller.current=new AbortController();setBusy(true);setReport(null);setError('');
    try{const result=await checkBackup(workspace,file,undefined,controller.current.signal);if(request===generation.current)setReport(result);}
    catch(e){if(request===generation.current)setError(e instanceof Error?e.message:'检查未完成，请保留文件并手动重试');}
    finally{if(request===generation.current){lock.current=false;setBusy(false);}}
  };
  const visible=!disabled&&report?.project_id===workspace.project_id&&report.token===workspace.token?report:null;
  return <section id="revision-import-precheck" className="revision-card revision-export" aria-label="文字备份只读预检">
    <h2><FileCheck size={20}/>检查文字备份</h2>
    <p>选择下载过的 JSON 文字备份，先核对格式、版本关系和包含的范围。</p>
    <p className="revision-muted">仅送到本地后端检查，不保存文件、不改正文或版本。支持 JSON，最大 64 MiB；TXT、媒体和完整会话文件不支持。检查不会改变本页编辑草稿与预览。</p>
    <label className="revision-field">选择 JSON 文字备份<input className="revision-backup-file" type="file" accept=".json,application/json" disabled={disabled||busy} onChange={e=>{setFile(e.target.files?.[0]??null);setReport(null);setError('');}}/></label>
    <div className="revision-actions"><button className="revision-primary" disabled={disabled||busy||!file} onClick={()=>void inspect()}>检查这份备份</button></div>
    {!file&&<p className="revision-muted">尚未选择文件。选择后仍需点检查，不会自动上传。</p>}
    {busy&&<p role="status">正在检查所选备份…</p>}
    {error&&<p role="alert" className="revision-alert">{error}</p>}
    {visible&&<div className="revision-precheck-result" role="status"><h3>检查通过 · 尚未恢复</h3>
      <p>来源项目：{visible.source_project_id} · JSON 格式第 {visible.schema_version} 版 · {visible.file_bytes.toLocaleString('zh-CN')} 字节 · {visible.versions.length} 条历史版本</p>
      {!visible.same_project&&<p className="revision-alert">备份来自另一个项目。这里只检查内容，不会把来源项目编号或版本关联写入本项目。</p>}
      {(Object.keys(STAGES) as Stage[]).map(stage=>{const s=visible.stages[stage];return <section key={stage} className="revision-precheck-stage"><h4>{STAGES[stage]}</h4><p>{s.field_count} 个可编辑文字字段 · {s.version_count} 条历史版本</p><p>{s.matches_current?'备份当前内容与本项目当前正文相同':'备份当前内容与本项目当前正文不同'}；{s.untracked?'备份当前内容尚未归档':s.version_id?'备份当前内容已有对应归档版本':'备份没有对应归档版本'}。</p></section>;})}
      <details><summary>查看备份版本范围 · 共 {visible.versions.length} 条</summary>{visible.versions.length?<ol className="revision-precheck-versions">{visible.versions.map(v=><li key={v.version_id}>v{v.number} · {STAGES[v.stage]} · {sources[v.source]} · {new Date(v.created_at).toLocaleString('zh-CN')} · {v.field_count} 个文字字段{v.restored_from?` · 恢复自 v${visible.versions.find(old=>old.version_id===v.restored_from)?.number}`:''}</li>)}</ol>:<p>没有历史版本，仍保留两阶段当前结构。</p>}</details>
      <p className="revision-muted">仅表示文件格式、摘要与内部关系通过检查，不能证明来源可信。还未写入任何内容；恢复预览与确认将在后续提供。文件和报告仅在本页，刷新需重新选择检查。</p>
    </div>}
  </section>;
}
