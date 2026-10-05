'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Download } from 'lucide-react';
import { type Workspace, STAGES } from '@/lib/contentRevisions';
import { readExport, saveExport, type ExportFormat } from '@/lib/revisionExport';

export default function RevisionExport({workspace,disabled}:{workspace:Workspace;disabled:boolean}) {
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const generation=useRef(0),lock=useRef(false);
  const cancel=useCallback(()=>{++generation.current;lock.current=false;},[]);
  useEffect(()=>{
    cancel();
    const timer=window.setTimeout(()=>{setBusy(false);setError('');setNotice('');},0);
    return()=>{cancel();window.clearTimeout(timer);};
  },[workspace,cancel]);
  const download=async(format:ExportFormat)=>{
    if(disabled||lock.current)return;
    lock.current=true;const request=++generation.current;setBusy(true);setError('');setNotice('');
    try {
      const file=await readExport(workspace,format);
      if(request!==generation.current)return;
      saveExport(file);
      setNotice(`已将${format==='json'?' JSON 备份':'可读 TXT'}交给浏览器下载，含当前两阶段文字与 ${file.versions} 条归档版本。请查看浏览器下载目录。`);
    } catch(e) {if(request===generation.current)setError(e instanceof Error?e.message:'导出未完成，请重试');}
    finally {if(request===generation.current){lock.current=false;setBusy(false);}}
  };
  const empty=!workspace.versions.length&&Object.values(workspace.fields).every(f=>!f.length);
  return <section id="revision-export" className="revision-card revision-export" aria-label="文字版本导出与备份">
    <h2><Download size={20}/>文字版本导出与备份</h2>
    <p>当前剧本、当前分镜与全部 {workspace.versions.length} 条已归档文字版本，一次下载留存。</p>
    <p className="revision-muted">JSON 保留完整结构和版本信息；TXT 方便离线阅读。未保存的编辑草稿不进入文件，下载后仍留在本页。</p>
    <p className="revision-muted">文件只备份文字，不含图片、视频、计划、任务、费用或人工核对记录。本轮没有导入恢复功能；完整项目备份还需保留会话目录和整个资产库。</p>
    {!!workspace.untracked_stages.length&&<p className="revision-alert">{workspace.untracked_stages.map(s=>STAGES[s]).join('、')}当前正文尚未归档，文件会将当前内容与历史分别保留。</p>}
    {empty&&<p className="revision-empty">没有归档版本或可编辑文字；文件仍保留两阶段的当前结构，下载不会创建版本。</p>}
    <div className="revision-actions"><button className="revision-primary" disabled={disabled||busy} onClick={()=>void download('json')}><Download size={16}/>下载 JSON 备份</button><button disabled={disabled||busy} onClick={()=>void download('text')}><Download size={16}/>下载可读 TXT</button></div>
    {busy&&<p role="status" className="revision-muted">正在读取全部文字版本并校验下载文件…</p>}
    {error&&<p role="alert" className="revision-alert">{error}</p>}
    {notice&&<p role="status" className="revision-notice">{notice}</p>}
  </section>;
}
