'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, MessageSquare, RefreshCw, ShieldCheck } from 'lucide-react';
import BrandHeader from './BrandHeader';
import CoCreationApplication from './CoCreationApplication';
import {type ApplicationWorkspace} from '@/lib/coCreationApplications';
import {readDrafts,type Draft} from '@/lib/coCreationDrafts';
import { STAGES, pathKey, RevisionError, type Stage } from '@/lib/contentRevisions';
import { requestInput, saveCreation, recoverRequest, type CreationWorkspace, type RequestInput, type DecisionInput } from '@/lib/coCreation';

const labels={pending:'待确认',confirmed:'意图已确认',withdrawn:'已撤回',stale:'依据已变化'};
const date=(s:string)=>new Date(s).toLocaleString('zh-CN');
export default function ProjectCoCreation({projectId}:{projectId:string}){
  const [data,setData]=useState<CreationWorkspace|null>(null);
  const [bundle,setBundle]=useState<ApplicationWorkspace|null>(null),[applicationBusy,setApplicationBusy]=useState(false),[readEpoch,setReadEpoch]=useState(0);
  const [drafts,setDrafts]=useState<Draft[]>([]);
  const selectedRef=useRef('');
  const applicationDirty=useRef(false),applicationLock=useRef(false);
  const activity=useCallback((busy:boolean,dirty:boolean)=>{applicationLock.current=busy;applicationDirty.current=dirty;setApplicationBusy(busy);},[]);
  const [reading,setReading]=useState(true),[busy,setBusy]=useState(false),[blocked,setBlocked]=useState(false);
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[status,setStatus]=useState(0);
  const [stage,setStage]=useState<Stage>('script_generation'),[key,setKey]=useState('');
  const [message,setMessage]=useState(''),[preserve,setPreserve]=useState(''),[selected,setSelected]=useState(''),[ack,setAck]=useState(false);
  const applied=useCallback((next:ApplicationWorkspace,nextDrafts?:Draft[])=>{setBundle(next);setData(next.workspace);if(nextDrafts)setDrafts(nextDrafts);setAck(false);},[]);
  const lock=useRef(false),mounted=useRef(true),generation=useRef(0),dirty=useRef(false),pending=useRef<RequestInput|DecisionInput|null>(null);
  const invalidate=useCallback(()=>{mounted.current=false;++generation.current;lock.current=false;},[]);
  useEffect(()=>{dirty.current=!!message||!!preserve;},[message,preserve]);
  const choose=useCallback((id:string,approved=false)=>{
    if(applicationLock.current)return;
    if(!approved&&applicationDirty.current&&id!==new URL(location.href).searchParams.get('intent')&&!window.confirm('尚有未保存的修改文字或预览，切换请求会丢失输入。确认切换？'))return;
    selectedRef.current=id;setSelected(id);setAck(false);
    const url=new URL(location.href);if(id)url.searchParams.set('intent',id);else url.searchParams.delete('intent');
    history.pushState(null,'',url);
  },[]);
  const reload=useCallback(async()=>{
    if(lock.current||applicationLock.current)return;
    if((dirty.current||applicationDirty.current)&&!pending.current&&!window.confirm('重新读取会刷新正文依据，未提交输入仍保留。读取后请重新核对修改对象。继续读取？'))return;
    lock.current=true;setReading(true);setError('');setNotice('');setAck(false);
    const serial=++generation.current;
    try{
      const read=await readDrafts(projectId),nextBundle=read.data,next=nextBundle.workspace;
      if(!mounted.current||serial!==generation.current)return;
      setDrafts(read.drafts);setReadEpoch(n=>n+1);setBundle(nextBundle);setData(next);setBlocked(false);setStatus(0);
      const waiting=pending.current;
      if(waiting){
        const found=recoverRequest(next,waiting.request_id);
        if(found){
          choose(found);
          if(!('decision'in waiting)){setMessage('');setPreserve('');}
          setNotice('已从持久化记录找回上次操作，请核对状态；没有重复提交。');
        }else setNotice('尚未发现上次操作记录，输入仍保留。请核对当前依据后手动提交；不会自动重发。');
        pending.current=null;
      }else{
        selectedRef.current=new URL(location.href).searchParams.get('intent')??'';setSelected(selectedRef.current);
        setNotice('已读取项目内记录。确认意图不会修改正文。');
      }
    }catch(e){if(mounted.current&&serial===generation.current){setError(e instanceof Error?e.message:'读取失败');setStatus(e instanceof RevisionError?e.status:0);setBlocked(true);}}
    finally{if(mounted.current&&serial===generation.current){lock.current=false;setReading(false);}}
  },[projectId,choose]);
  useEffect(()=>{
    mounted.current=true;
    const timer=window.setTimeout(()=>{void reload();},0);
    const nav=(e:MouseEvent)=>{if(e.target instanceof Element&&e.target.closest('a[href]:not([href^="#"])')&&(lock.current||applicationLock.current||(dirty.current||applicationDirty.current)&&!window.confirm('尚有未保存的修改意图或文字，离开会丢失本页输入。确认离开？'))){e.preventDefault();e.stopImmediatePropagation();}};
    const pop=()=>{const next=new URL(location.href).searchParams.get('intent')??'';if(applicationLock.current||lock.current||applicationDirty.current&&next!==selectedRef.current&&!window.confirm('尚有未保存的修改文字，切换请求会丢失输入。确认切换？')){const url=new URL(location.href);if(selectedRef.current)url.searchParams.set('intent',selectedRef.current);else url.searchParams.delete('intent');history.replaceState(null,'',url);return;}selectedRef.current=next;setSelected(next);setAck(false);};
    const leave=(e:BeforeUnloadEvent)=>{if(dirty.current||applicationDirty.current||lock.current||applicationLock.current)e.preventDefault();};
    document.addEventListener('click',nav,true);window.addEventListener('popstate',pop);window.addEventListener('beforeunload',leave);
    return()=>{invalidate();window.clearTimeout(timer);document.removeEventListener('click',nav,true);window.removeEventListener('popstate',pop);window.removeEventListener('beforeunload',leave);};
  },[reload,invalidate]);
  const submit=async(input:RequestInput|DecisionInput)=>{
    if(lock.current||applicationLock.current||blocked)return;
    lock.current=true;setBusy(true);setError('');setNotice('');pending.current=input;
    const serial=++generation.current;
    try{
      const result=await saveCreation(projectId,input);
      if(!mounted.current||serial!==generation.current)return;
      setData(result.workspace);choose(result.intent_id,true);pending.current=null;setBlocked(false);
      if(!('decision'in input)){setMessage('');setPreserve('');setNotice('修改请求已保存，请核对范围卡再确认。');}
      else setNotice(input.decision==='confirm'?'修改意图已确认并留存，正文尚未改写。':'请求已撤回，原话和确认历史继续保留。');
    }catch(e){
      if(mounted.current&&serial===generation.current){
        setError(e instanceof Error?e.message:'保存未确认');
        if(!(e instanceof RevisionError)||e.uncertain||e.status===409)setBlocked(true);
        else pending.current=null;
      }
    }finally{if(mounted.current&&serial===generation.current){lock.current=false;setBusy(false);}}
  };
  const create=()=>{
    if(!data)return;
    if(applicationDirty.current&&!window.confirm('当前请求有未保存的修改文字，新请求会切换对象并丢失这些输入。继续记录？'))return;
    try{void submit(requestInput(data,stage,key,message,preserve,crypto.randomUUID().replaceAll('-','')));}
    catch(e){setError(e instanceof Error?e.message:'请核对输入');}
  };
  const decide=(decision:'confirm'|'withdraw')=>{
    if(!data||!selected)return;
    if(applicationDirty.current&&decision==='withdraw'&&!window.confirm('当前有未保存文字，撤回后不能应用；输入不保存。继续撤回？'))return;
    if(decision==='withdraw'&&!window.confirm('撤回这条修改请求？原请求和确认记录继续保留，已保存的正文和版本不会回退，也不取消生成任务。'))return;
    void submit({expected_token:data.token,request_id:crypto.randomUUID().replaceAll('-',''),intent_id:selected,decision,acknowledged:true});
  };
  const changeStage=(s:Stage)=>{
    if(s===stage)return;
    if(dirty.current&&!window.confirm('切换阶段会清空未提交输入。确认切换？'))return;
    setStage(s);setKey('');setMessage('');setPreserve('');
  };
  const changeField=(next:string)=>{
    if(dirty.current&&!window.confirm('切换修改对象会清空未提交输入。确认切换？'))return;
    setKey(next);setMessage('');setPreserve('');
  };
  const intent=data?.records.find(r=>r.intent_id===selected);
  const field=data?.fields[stage].find(f=>pathKey(f.path)===key);
  const disabled=reading||busy||blocked||applicationBusy;
  const createDisabled=disabled||!!data?.running;
  return <div><BrandHeader/><main className="revision-shell">
    <nav className="revision-nav"><Link href={`/projects/${encodeURIComponent(projectId)}/assets`}><ArrowLeft size={16}/>素材与版本</Link><Link href={`/projects/${encodeURIComponent(projectId)}/revisions`}>文字修改与版本</Link><Link href={`/?session=${encodeURIComponent(projectId)}`}>返回项目</Link><button onClick={reload} disabled={reading||busy||applicationBusy}><RefreshCw size={16}/>重新读取共创记录</button></nav>
    <div className="revision-heading"><div><span className="revision-eyebrow">创作工作室 · 对话共创</span><h1>先说清楚，再一起修改</h1><p>留下修改原话，核对对象与必须保留的内容。</p></div><span className="revision-local"><ShieldCheck size={18}/>本地记录 · 模型尚未接入</span></div>
    <p className="revision-boundary">记录并确认修改意图后，可人工填写新文字，预览差异再保存关联版本。尚未调用模型；确认意图本身不会改写正文或授权费用。</p>
    {error&&<div role="alert" className="revision-alert">{error}{blocked&&<p>输入与已显示记录保留，暂停提交。请重新读取，核对上次操作是否已保存。</p>}</div>}
    {notice&&<p role="status" className="revision-notice">{notice}</p>}
    {reading&&<p role="status">正在读取项目内共创记录与文字…</p>}
    {!reading&&!data&&<section className="revision-card"><h2>{status===404?'找不到这个项目':'暂未读取到共创记录'}</h2><p>返回项目核对，或启动本地服务后重新读取。</p></section>}
    {data&&<>
      {data.running&&<div className="revision-alert">项目正在执行，暂时不能记录或确认新意图。查看、撤回本地请求不会停止任务。</div>}
      <div className="revision-grid creation-grid"><section className="revision-card">
        <h2><MessageSquare size={20}/>提出修改</h2>
        <div className="revision-tabs">{(Object.keys(STAGES) as Stage[]).map(s=><button key={s} aria-pressed={stage===s} disabled={disabled} onClick={()=>changeStage(s)}>{STAGES[s]}</button>)}</div>
        {!data.fields[stage].length?<div className="revision-empty"><h3>还没有可选择的{STAGES[stage]}文字</h3><p>先在项目中准备正文，再围绕具体对象提出修改。</p></div>:<>
          <label className="revision-field">修改对象<select value={key} disabled={createDisabled} onChange={e=>changeField(e.target.value)}><option value="">请选择一处文字</option>{data.fields[stage].map(f=><option key={pathKey(f.path)} value={pathKey(f.path)}>{f.label}</option>)}</select></label>
          {key&&!field&&<p className="revision-alert">原对象已变化或移除，请重新选择。</p>}
          {field&&<><h3>已保存的当前文字</h3><pre className="revision-text">{field.value||'（空白）'}</pre></>}
          <label className="revision-field">希望怎样修改<textarea rows={5} maxLength={10000} value={message} disabled={createDisabled} onChange={e=>setMessage(e.target.value)} placeholder="例如，让这句台词更坚定，保留等船的情节。"/></label>
          <p className="revision-muted">{message.length} / 10000 字</p>
          <label className="revision-field">必须保留的内容（可选）<textarea rows={3} maxLength={2000} value={preserve} disabled={createDisabled} onChange={e=>setPreserve(e.target.value)} placeholder="例如，人物关系、镜头时长和其他台词保持。"/></label>
          <p className="revision-muted">{preserve.length} / 2000 字 · 未提交输入只在当前页</p>
          <button className="revision-primary" disabled={createDisabled||!field||!message.trim()} onClick={create}>{busy?'正在保存…':'记录修改请求'}</button>
        </>}
      </section><section className="revision-card">
        <h2>项目内请求记录</h2><p className="revision-muted">原话与每次决定都会留存。没有自动回复或自动改写。</p>
        {!data.records.length?<div className="revision-empty"><h3>还没有修改请求</h3><p>先选对象并记录意图，再核对范围。</p></div>:<div className="revision-history">{[...data.records].reverse().map(r=><button key={r.intent_id} disabled={disabled} aria-pressed={selected===r.intent_id} onClick={()=>choose(r.intent_id)}><strong>第 {r.number} 轮 · {STAGES[r.stage]}<span>{bundle?.applications.some(a=>a.intent_id===r.intent_id)?(r.status==='withdrawn'?'已撤回 · 已保存版本':'已保存版本'):labels[r.status]}</span></strong><small>{r.target.label} · {date(r.created_at)}</small></button>)}</div>}
        {selected&&!intent&&<p className="revision-alert">所选请求不存在。<button onClick={()=>choose('')} disabled={disabled}>返回记录列表</button></p>}
        {intent&&<article className="revision-version creation-record">
          <h3>第 {intent.number} 轮 · {bundle?.applications.some(a=>a.intent_id===intent.intent_id)?(intent.status==='withdrawn'?'已撤回 · 已保存版本':'已保存版本'):labels[intent.status]}</h3><p className="revision-muted">你的修改请求 · {date(intent.created_at)}</p><pre className="revision-text">{intent.message}</pre>
          <h3>范围核对</h3><p><strong>{STAGES[intent.stage]} · {intent.target.label}</strong></p>
          <details><summary>提出请求时的原文字</summary><pre className="revision-text">{intent.target.value||'（空白）'}</pre></details>
          <h3>必须保留</h3><pre className="revision-text">{intent.preserve||'未补充保留要求；选定对象以外的文字仍需保持。'}</pre>
          <p className="revision-boundary">{bundle?.applications.some(a=>a.intent_id===intent.intent_id)?'该请求已通过单字段预览保存为版本，其他字段保持；后续正文变化不会删除原关联。':'本次只围绕所选字段提出修改，其他正文保持。请求尚未变成新正文；影响范围以实际修改后的预览为准。'}</p>
          {intent.status==='stale'&&!bundle?.applications.some(a=>a.intent_id===intent.intent_id)&&<p className="revision-alert">正文或历史已经变化。旧请求保留，请基于当前文字重新记录和核对，不能继续确认旧依据。</p>}
          {intent.status==='pending'&&<label className="revision-check"><input type="checkbox" checked={ack} disabled={createDisabled} onChange={e=>setAck(e.target.checked)}/>我已核对修改对象和保留要求，确认这条修改意图；正文尚未改写</label>}
          <div className="revision-actions">{intent.status==='pending'&&<button className="revision-primary" disabled={createDisabled||!ack} onClick={()=>decide('confirm')}>确认修改意图</button>}{intent.status!=='withdrawn'&&<button disabled={disabled} onClick={()=>decide('withdraw')}>撤回这条请求</button>}</div>
          {bundle&&<CoCreationApplication key={intent.intent_id} projectId={projectId} intentId={intent.intent_id} drafts={drafts} readEpoch={readEpoch} data={{...bundle,workspace:data}} onChange={applied} onActivity={activity} disabled={reading||busy||blocked}/>}
          {data.decisions.filter(d=>d.intent_id===intent.intent_id).map(d=><p key={d.decision_id} className="revision-muted">{d.decision==='confirm'?'确认意图':'撤回请求'} · {date(d.created_at)}</p>)}
        </article>}
      </section></div>
    </>}
  </main></div>;
}
