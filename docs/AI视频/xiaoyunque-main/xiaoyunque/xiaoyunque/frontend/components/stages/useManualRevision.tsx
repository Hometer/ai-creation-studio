'use client';
import {useEffect,useRef,useState} from 'react';
import {readEditor,manualInput,previewManualRevision,saveRevision,RevisionError,type Editor,type Input,type Preview,type Stage} from '@/lib/contentRevisions';
import type {StageArtifact} from '@/lib/workflowTypes';

export function useManualRevision(projectId: string,stage: Stage,onApplied?: (artifact: StageArtifact)=>void) {
  const [editor,setEditor]=useState<Editor|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[frozen,setFrozen]=useState(false);
  const [review,setReview]=useState<{input:Input;preview:Preview}|null>(null),[ack,setAck]=useState(false),[notice,setNotice]=useState('');
  const lock=useRef(false),mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  const editing=editor!==null;
  useEffect(()=>{
    if (!editing) return;
    const sourceUrl=window.location.href,sourceState=window.history.state;
    const guard=crypto.randomUUID();let leaving=false,returning=false;
    // A same-document entry makes browser Back ask before a cross-document unload.
    window.history.pushState({...sourceState,manualRevisionGuard:guard},'',sourceUrl);
    const warn=(event:BeforeUnloadEvent)=>{if(!leaving){event.preventDefault();event.returnValue='';}};
    const leave=(event:MouseEvent)=>{
      const target=event.target as Element|null;
      if (!target?.closest('a,button') || target.closest('[data-manual-editor]') && !target.closest('a')) return;
      if (lock.current || !window.confirm('当前编辑尚未保存。离开会丢弃草稿，确定离开吗？')) {event.preventDefault();event.stopImmediatePropagation();}
      else {leaving=true;window.setTimeout(()=>{if(mounted.current)leaving=false;},0);}
    };
    const back=(event:PopStateEvent)=>{
      if(returning)return;
      event.stopImmediatePropagation();
      if(lock.current||!window.confirm('当前编辑尚未保存。返回会丢弃草稿，确定返回吗？')) window.history.pushState({...sourceState,manualRevisionGuard:guard},'',sourceUrl);
      else {leaving=true;returning=true;window.history.back();}
    };
    window.addEventListener('popstate',back,true);window.addEventListener('beforeunload',warn);document.addEventListener('click',leave,true);
    return()=>{
      window.removeEventListener('popstate',back,true);window.removeEventListener('beforeunload',warn);document.removeEventListener('click',leave,true);
      if(window.location.href===sourceUrl&&window.history.state?.manualRevisionGuard===guard){
        if(leaving)window.history.replaceState(sourceState,'',sourceUrl);
        else window.history.back();
      }
    };
  },[editing]);
  function fail(e:unknown) {
    setError(e instanceof RevisionError?e.message:'修改暂未完成，请保留草稿并检查服务');
    if (!(e instanceof RevisionError) || e.status===0 || e.status===409 || e.status>=500) setFrozen(true);
  }
  async function start(load:(content:Record<string,unknown>)=>void,discard=false) {
    if(lock.current || (discard && !window.confirm('重新载入会放弃当前草稿。请先复制需要保留的内容，确定载入当前已保存正文吗？'))) return;
    lock.current=true;setBusy(true);setError('');
    try {
      const next=await readEditor(projectId,stage);
      if(!mounted.current) return;
      if(next.workspace.running) throw new RevisionError('项目正在执行，请结束任务后再编辑',409);
      setEditor(next);setFrozen(false);setReview(null);setAck(false);setNotice('');onApplied?.(next.artifact as StageArtifact);load(next.artifact);
    } catch(e){if(mounted.current)fail(e);} finally{lock.current=false;if(mounted.current)setBusy(false);}
  }
  async function preview(content:unknown) {
    if(lock.current || frozen || !editor) return;
    lock.current=true;setBusy(true);setError('');setAck(false);
    try {
      const input=manualInput(editor,stage,content),result=await previewManualRevision(projectId,editor,input);
      if(mounted.current)setReview({input,preview:result});
    } catch(e){if(mounted.current)fail(e);} finally{lock.current=false;if(mounted.current)setBusy(false);}
  }
  async function confirm(close:()=>void) {
    if(lock.current || frozen || !review || !ack) return;
    lock.current=true;setBusy(true);setError('');
    try {
      const result=await saveRevision(projectId,{...review.input,preview_token:review.preview.preview_token,request_id:crypto.randomUUID().replaceAll('-',''),acknowledged:true});
      if(!mounted.current)return;
      onApplied?.(result.artifact as StageArtifact);
      const version=result.workspace.versions.find(v=>v.version_id===result.version_id)!;
      setNotice(`已保存为 v${version.number}，原内容已归档。`);setEditor(null);setReview(null);setAck(false);close();
    } catch(e){if(mounted.current){fail(e);setReview(null);}} finally{lock.current=false;if(mounted.current)setBusy(false);}
  }
  function cancel(close:()=>void) {
    if(lock.current)return;
    if(!window.confirm('确定放弃当前未保存修改吗？'))return;
    setEditor(null);setReview(null);setError('');setFrozen(false);close();
  }
  return {editor,busy,error,frozen,review,ack,notice,start,preview,confirm,cancel,setError,setAck,back:()=>{if(!lock.current){setReview(null);setAck(false);}}};
}
function changeLabel(path:(string|number)[]) {
  const labels:Record<string,string>={episodes:'章节',segments:'片段',shots:'镜头',content:'正文',plot:'剧情',dialogue:'台词',duration:'时长',total_duration:'片段总时长',shot_number:'镜头编号',shot_type:'景别',title:'标题',mood:'情绪基调',logline:'故事梗概',characters:'角色',settings:'场景',scenes:'故事线',name:'名称',description:'描述',motivation:'动机',role:'角色定位',location:'地点',genre:'类型',visual_prompt:'画面提示词',episode_title:'章节标题',episode_number:'章节编号',segment_id:'片段标识',segment_number:'片段编号',payload:'原正文',overall_style:'整体风格',beat_sheet:'节拍大纲'};
  return path.map(part=>typeof part==='number'?`第 ${part+1} 项`:labels[part]??part).join(' / ');
}
export function ManualRevisionPanel({manual,projectId,onReload,onClose}:{manual:ReturnType<typeof useManualRevision>;projectId:string;onReload:()=>void;onClose:()=>void}) {
  const dialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{if(manual.review){dialog.current?.showModal();dialog.current?.querySelector('h3')?.focus({preventScroll:true});const content=dialog.current?.querySelector('.manual-review-content');if(content)content.scrollTop=0;}else dialog.current?.close();},[manual.review]);
  return <>
    <div className="manual-revision-panel">
      <a href={`/projects/${encodeURIComponent(projectId)}/revisions`}>文字版本与恢复</a>
      {manual.busy&&<p role="status">正在核对，请稍候…</p>}
      {manual.notice&&<p role="status">{manual.notice}</p>}
      {manual.error&&<p role="alert">{manual.error}。草稿会保留在当前页面。</p>}
      {manual.editor&&<p>手动保存仅更新本阶段正文。现有图片、视频及绑定保留，请核对预览中的影响。</p>}
      {manual.frozen&&<button type="button" disabled={manual.busy} onClick={onReload}>重新载入已保存正文</button>}
    </div>
    <dialog ref={dialog} className="manual-revision-dialog" aria-labelledby="manual-review-title" onCancel={event=>{event.preventDefault();manual.back();}}>
      {manual.review&&<><div className="manual-review-content"><h3 id="manual-review-title" tabIndex={-1}>核对修改与影响</h3><p>确认后保存本阶段完整正文并追加版本，可在文字版本页恢复。不会自动生成媒体。</p>
        <div className="manual-revision-diffs">{manual.review.preview.changes.map(change=><section key={JSON.stringify(change.path)}><h4>{changeLabel(change.path)}</h4><span>修改前</span><pre>{change.before_present===false?'（无此字段）':change.before}</pre><span>修改后</span><pre>{change.after_present===false?'（已移除字段）':change.after}</pre></section>)}</div>
        <p>待核对 {manual.review.preview.impacts.length} 个镜头／片段</p><ul>{manual.review.preview.impacts.map(item=><li key={`${item.segment_id}:${item.shot_number}`}>{item.segment_id}{item.shot_number!==null?` · 镜头 ${item.shot_number}`:''}：{item.reason}</li>)}</ul>
        </div><div className="manual-review-confirm"><label><input type="checkbox" checked={manual.ack} onChange={event=>manual.setAck(event.target.checked)} disabled={manual.busy}/>我已核对修改范围，确认保存为新版本</label>
        <div className="manual-revision-buttons"><button type="button" disabled={manual.busy} onClick={manual.back}>返回编辑</button><button type="button" disabled={manual.busy||!manual.ack||manual.frozen} onClick={()=>void manual.confirm(onClose)}>{manual.busy?'正在保存…':'确认保存新版本'}</button></div></div>
      </>}
    </dialog>
  </>;
}
