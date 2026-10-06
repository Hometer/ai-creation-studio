'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import Link from 'next/link';
import CoCreationDraftHistory from './CoCreationDraftHistory';
import CreationCandidates from './CreationCandidates';
import {draftRestoration} from '@/lib/coCreationDraftHistory';
import {RevisionError} from '@/lib/contentRevisions';
import {applyInput,previewApplication,saveApplication,type ApplicationWorkspace,type ApplyPreview,type ApplySave} from '@/lib/coCreationApplications';
import {readDrafts,saveDraft,draftInput,latestDraft,type Draft,type DraftInput} from '@/lib/coCreationDrafts';
export default function CoCreationApplication({projectId,intentId,data,onChange,onActivity,disabled,readEpoch,drafts}:{projectId:string;intentId:string;data:ApplicationWorkspace;onChange:(data:ApplicationWorkspace,drafts?:Draft[])=>void;onActivity:(busy:boolean,dirty:boolean)=>void;disabled:boolean;readEpoch:number;drafts:Draft[]}){
  const intent=data.workspace.records.find(r=>r.intent_id===intentId)!;
  const application=data.applications.find(a=>a.intent_id===intentId),version=data.revisions.versions.find(v=>v.version_id===application?.version_id);
  const latest=latestDraft(drafts,intentId);
  const [value,setValue]=useState(latest?.value??intent.target.value),[preview,setPreview]=useState<ApplyPreview|null>(null),[ack,setAck]=useState(false);
  const [busy,setBusy]=useState(false),[blocked,setBlocked]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [modelBusy,setModelBusy]=useState(false);
  const pendingDraft=useRef<DraftInput|null>(null);
  const unsaved=value!==(latest?.value??intent.target.value);
  const lock=useRef(false),alive=useRef(true),serial=useRef(0),pending=useRef<ApplySave|null>(null);
  const invalidate=useCallback(()=>{alive.current=false;++serial.current;onActivity(false,false);},[onActivity]);
  useEffect(()=>{alive.current=true;return invalidate;},[invalidate]);
  useEffect(()=>{onActivity(busy||modelBusy,!application&&(unsaved||!!pending.current||!!pendingDraft.current));},[busy,modelBusy,unsaved,application,onActivity]);
  useEffect(()=>{const timer=window.setTimeout(()=>{setPreview(null);setAck(false);},0);return()=>window.clearTimeout(timer);},[data.workspace.token,readEpoch]);
  const run=async(kind:'preview'|'save'|'read'|'draft')=>{
    if(lock.current||disabled)return;
    lock.current=true;setBusy(true);setError('');setNotice('');const generation=++serial.current;
    try{
      if(kind==='read'){
        const read=await readDrafts(projectId),next=read.data;if(!alive.current||generation!==serial.current)return;
        const found=pending.current&&next.applications.find(a=>a.request_id===pending.current!.request_id);
        const foundDraft=pendingDraft.current&&read.drafts.find(d=>d.request_id===pendingDraft.current!.request_id);
        setPreview(null);setAck(false);setBlocked(false);
        if(found){setNotice('已找回上次保存的关联版本，没有重复提交。');setValue(next.workspace.records.find(r=>r.intent_id===intentId)!.target.value);}
        else if(foundDraft)setNotice('已找回上次保存的草稿，没有重复提交；正文尚未改写。');
        else setNotice('已重新读取，当前输入保留；请核对最近草稿和当前依据，再手动操作。');
        pending.current=null;pendingDraft.current=null;onChange(next,read.drafts);
      }else if(kind==='draft'){
        const input=draftInput(data,intentId,value,crypto.randomUUID().replaceAll('-',''));pendingDraft.current=input;
        const saved=await saveDraft(projectId,input);if(!alive.current||generation!==serial.current)return;
        pendingDraft.current=null;setPreview(null);setAck(false);setBlocked(false);setNotice('修改草稿已保存，正文和文字版本没有变化；正式保存仍需预览确认。');onChange(saved.result.data,saved.result.drafts);
      }else{
        const input=applyInput(data,intentId,value);
        if(kind==='preview'){
          const result=await previewApplication(projectId,input,data);if(!alive.current||generation!==serial.current)return;
          setPreview(result);setAck(false);setNotice('请核对修改前后、保留要求与影响，再确认保存。');
        }else{
          if(!preview||!ack||preview.expected_token!==input.expected_token)return;
          const payload:ApplySave={...input,preview_token:preview.preview_token,request_id:crypto.randomUUID().replaceAll('-',''),acknowledged:true};pending.current=payload;
          const result=await saveApplication(projectId,payload);if(!alive.current||generation!==serial.current)return;
          pending.current=null;setPreview(null);setAck(false);setBlocked(false);setNotice('新文字、版本和请求关联已一起保存。');onChange(result.data);
        }
      }
    }catch(e){if(alive.current&&generation===serial.current){setError(e instanceof Error?e.message:'操作未完成');setPreview(null);setAck(false);if(!(e instanceof RevisionError)||e.uncertain||e.status===409||e.status===503)setBlocked(true);else {pending.current=null;pendingDraft.current=null;}}}
    finally{if(alive.current&&generation===serial.current){lock.current=false;setBusy(false);}}
  };
  const unavailable=disabled||busy||modelBusy||blocked||data.workspace.running||intent.status!=='confirmed'||!!application;
  const loadDraft=(draftId:string)=>{
    if(lock.current||unavailable)return;
    try{
      const restored=draftRestoration(data,drafts,intentId,draftId,value);
      if(restored.requiresConfirmation&&!window.confirm('载入已保存草稿会替换本页未保存的新文字。确认载入？'))return;
      setValue(restored.value);setPreview(null);setAck(false);setError('');
      setNotice(`已载入第 ${restored.ordinal} 次已保存草稿到本页。保存为新草稿后刷新可恢复；正式应用仍需重新预览确认。`);
    }catch(e){setError(e instanceof Error?e.message:'所选草稿暂不可用，请重新读取核对');}
  };
  return <section className="creation-apply">
    <h3>{application?'已保存为文字版本':'把已确认意图改成文字'}</h3>
    {application&&version?<><p className="revision-notice">第 {version.number} 版已留存{version.current?'，目前仍是当前正文':'，可到版本页查看历史'}。撤回请求不会回退已保存的正文。</p><Link href={`/projects/${encodeURIComponent(projectId)}/revisions?version=${version.version_id}`}>查看关联文字版本</Link><CreationCandidates projectId={projectId} intentId={intentId} expectedToken={data.workspace.token} disabled={disabled||busy} readOnly onLoad={()=>{}} onBusy={setModelBusy}/></>:(intent.status==='confirmed'||!!latest||value!==intent.target.value)?<>
      <p className="revision-muted">可人工填写新内容，或明确生成并载入 AI 候选；预览后再次确认保存。必须保留的文字含义请自行核对。</p>
      <CreationCandidates projectId={projectId} intentId={intentId} expectedToken={data.workspace.token} disabled={disabled||busy||blocked||data.workspace.running||intent.status!=='confirmed'||!!application} onBusy={setModelBusy} onLoad={candidate=>{if(unavailable)return;if(unsaved&&candidate!==value&&!window.confirm('载入 AI 候选会替换本页未保存的修改文字。确认载入？'))return;setValue(candidate);setPreview(null);setAck(false);setNotice('候选已载入本页，请预览完整差异并核对保留要求后保存；原正文仍保留。');}}/>
      <label className="revision-field">修改后的文字<textarea rows={5} maxLength={10000} value={value} disabled={unavailable} onChange={e=>{setValue(e.target.value);setPreview(null);setAck(false);}}/></label>
      <p className="revision-muted">{value.length} / 10000 字 · 可以明确清空该字段</p>
      <p className="revision-muted creation-draft-status">{unsaved?'有未保存的修改':latest?'草稿已保存 · '+new Date(latest.created_at).toLocaleString('zh-CN'):'尚未保存修改草稿'}。草稿不会改正文，预览和勾选不会随草稿恢复。</p>
      <div className="revision-actions"><button disabled={unavailable||!unsaved} onClick={()=>void run('draft')}>保存修改草稿</button><button disabled={unavailable||!latest} onClick={()=>latest&&loadDraft(latest.draft_id)}>载入已保存草稿</button></div>
      {latest&&unsaved&&<p className="revision-muted">当前输入与最近草稿不同，重新读取不会替换输入；载入前请核对。</p>}
      <button disabled={unavailable||value===intent.target.value} onClick={()=>void run('preview')}>预览这次修改</button>
      {preview&&<div className="creation-apply-preview"><h3>修改预览</h3><h4>修改前</h4><pre className="revision-text">{preview.revision.changes[0].before||'（空白）'}</pre><h4>修改后</h4><pre className="revision-text">{preview.revision.changes[0].after||'（空白）'}</pre><h4>需要人工核对</h4>{preview.revision.impacts.length?preview.revision.impacts.map((i,n)=><p key={n}>{i.segment_id}{i.shot_number===null?'':` · 镜头 ${i.shot_number}`} · {i.reason}</p>):<p>没有识别到镜头影响，仍需核对项目整体。</p>}<label className="revision-check"><input type="checkbox" checked={ack} disabled={unavailable} onChange={e=>setAck(e.target.checked)}/>我已核对前后文字、必须保留的内容及影响，确认保存新版本</label><button className="revision-primary" disabled={unavailable||!ack} onClick={()=>void run('save')}>保存文字并关联请求</button></div>}
    </>:<p className="revision-muted">只有基于当前正文确认的请求可以应用。请先记录并确认修改意图。</p>}
    <CoCreationDraftHistory drafts={drafts} intentId={intentId} disabled={busy||disabled||blocked} readOnly={data.workspace.running||intent.status!=='confirmed'||!!application} onLoad={loadDraft}/>
    {busy&&<p role="status">正在处理，请稍候…</p>}
    {error&&<div role="alert" className="revision-alert">{error}{blocked&&<p>输入保留，暂停保存；先重新读取草稿与关联核对。</p>}</div>}
    {notice&&<p role="status" className="revision-notice">{notice}</p>}
    {blocked&&<button onClick={()=>void run('read')} disabled={busy||disabled}>重新读取草稿与版本</button>}
  </section>;
}
