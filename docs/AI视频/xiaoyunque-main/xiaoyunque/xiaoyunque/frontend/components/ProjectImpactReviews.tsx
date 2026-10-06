'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import Link from 'next/link';
import BrandHeader from './BrandHeader';
import {RevisionError,STAGES} from '@/lib/contentRevisions';
import {DECISIONS,REVIEW_STATUSES,readReviews,reviewInput,saveReview,type ReviewWorkspace} from '@/lib/impactReviews';

import {addressId,reviewScope} from '@/lib/restoreHistory';

export default function ProjectImpactReviews({projectId}:{projectId:string}) {
 const [data,setData]=useState<ReviewWorkspace|null>(null),[reading,setReading]=useState(true),[busy,setBusy]=useState(false),[blocked,setBlocked]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [search,setSearch]=useState('');
 const [selected,setSelected]=useState(''),[filter,setFilter]=useState('open'),[decision,setDecision]=useState<keyof typeof DECISIONS>('follow_up'),[note,setNote]=useState(''),[confirming,setConfirming]=useState(false),[ack,setAck]=useState(false);
 const lock=useRef(false),mounted=useRef(true),draft=useRef(false),dialog=useRef<HTMLDialogElement>(null),openButton=useRef<HTMLButtonElement>(null);
 const errorPanel=useRef<HTMLDivElement>(null);
 useEffect(()=>{if(error){errorPanel.current?.focus({preventScroll:true});errorPanel.current?.scrollIntoView({block:'start'});}},[error]);
 const dirty=!!note||confirming;
 let scopeError='',version='',restore='',scoped=data?.items??[];
 try{if(data){const scope=reviewScope(data,search);version=scope.version;scoped=scope.items;}restore=addressId(search,'restore');}catch(e){scopeError=e instanceof Error?e.message:'核对范围无法读取';scoped=[];}
 const item=scoped.find(i=>i.item_id===selected);
 const source=data?.sources.find(s=>s.version_id===version);
 useEffect(()=>{draft.current=dirty;},[dirty]);
 const reload=useCallback(async()=>{
  if(lock.current||draft.current&&!window.confirm('重新读取会放弃当前核对草稿，请先复制需要保留的说明。确定读取吗？'))return;
  lock.current=true;setReading(true);setError('');
  try{const next=await readReviews(projectId);if(!mounted.current)return;setData(next);setBlocked(false);setNote('');setConfirming(false);setAck(false);setSelected(new URL(location.href).searchParams.get('item')??'');setSearch(location.search);if(new URLSearchParams(location.search).has('version'))setFilter('all');setNotice('已读取持久化核对记录，请按当前依据核对。');}
  catch(e){if(mounted.current){setError(e instanceof RevisionError?e.message:'暂时无法读取核对记录');setBlocked(true);}}
  finally{lock.current=false;if(mounted.current)setReading(false);}
 },[projectId]);
 useEffect(()=>{mounted.current=true;const timer=setTimeout(()=>void reload(),0);const pop=()=>{setSelected(new URL(location.href).searchParams.get('item')??'');setSearch(location.search);if(new URLSearchParams(location.search).has('version'))setFilter('all');};window.addEventListener('popstate',pop);return()=>{mounted.current=false;clearTimeout(timer);window.removeEventListener('popstate',pop);};},[reload]);
 useEffect(()=>{
  if(!dirty)return;
  const url=location.href,state=history.state,guard=crypto.randomUUID();let leaving=false,returning=false;
  history.pushState({...state,impactReviewGuard:guard},'',url);
  const unload=(e:BeforeUnloadEvent)=>{if(!leaving){e.preventDefault();e.returnValue='';}};
  const leave=(e:MouseEvent)=>{if(!(e.target instanceof Element)||!e.target.closest('a[href]'))return;if(lock.current||!window.confirm('核对说明尚未保存，离开会丢弃草稿。确定离开吗？')){e.preventDefault();e.stopImmediatePropagation();}else{leaving=true;setTimeout(()=>{if(mounted.current)leaving=false;},0);}};
  const back=(e:PopStateEvent)=>{if(returning)return;e.stopImmediatePropagation();if(lock.current||!window.confirm('核对说明尚未保存，返回会丢弃草稿。确定返回吗？'))history.pushState({...state,impactReviewGuard:guard},'',url);else{leaving=true;returning=true;history.back();}};
  window.addEventListener('beforeunload',unload);window.addEventListener('popstate',back,true);document.addEventListener('click',leave,true);
  return()=>{window.removeEventListener('beforeunload',unload);window.removeEventListener('popstate',back,true);document.removeEventListener('click',leave,true);if(location.href===url&&history.state?.impactReviewGuard===guard){if(leaving)history.replaceState(state,'',url);else history.back();}};
 },[dirty]);
 useEffect(()=>{if(confirming){dialog.current?.showModal();dialog.current?.querySelector('h2')?.focus();}else{dialog.current?.close();openButton.current?.focus({preventScroll:true});}},[confirming]);
 const disabled=reading||busy||blocked||!!scopeError||!!data?.running;
 function choose(id:string){if(scopeError||!scoped.some(i=>i.item_id===id)||lock.current||dirty&&!window.confirm('切换事项会丢弃核对草稿，确定切换吗？'))return;setSelected(id);setNote('');setDecision('follow_up');setAck(false);setConfirming(false);const url=new URL(location.href);url.searchParams.set('item',id);history.replaceState(history.state,'',url);}
 async function save(){
  if(lock.current||disabled||!ack||!item||!data)return;
  lock.current=true;setBusy(true);setError('');
  try{const input=reviewInput(data,item.item_id,decision,note,crypto.randomUUID().replaceAll('-',''));const result=await saveReview(projectId,input);if(!mounted.current)return;setData(result.workspace);setNote('');setAck(false);setConfirming(false);setNotice('核对决定已保存。实际修改或重新制作请从对应入口单独操作。');}
  catch(e){if(mounted.current){setError(e instanceof RevisionError?e.message:'核对保存结果未确认，请保留说明并重新读取');if(!(e instanceof RevisionError)||e.uncertain||e.status===409||e.status>=500)setBlocked(true);setConfirming(false);}}
  finally{lock.current=false;if(mounted.current)setBusy(false);}
 }
 return <><BrandHeader/><main className="revision-shell impact-shell">
  <nav className="revision-nav"><Link href={`/projects/${encodeURIComponent(projectId)}/revisions${restore?`?restore=${restore}#revision-restore-records`:version?`?version=${version}#revision-history`:""}`}>文字版本与恢复</Link><Link href={`/?session=${encodeURIComponent(projectId)}`}>返回项目</Link><button disabled={reading||busy} onClick={reload}>重新读取核对记录</button></nav>
  <div className="revision-heading"><div><span className="revision-eyebrow">创作工作室 · 修改影响</span><h1>修改之后，逐项核对</h1><p>确认哪些镜头继续使用，哪些内容还需要处理。</p></div><span className="revision-local">人工决定 · 本地保存</span></div>
  <p className="revision-boundary">结论只适用于核对时的文字、项目产物、素材引用与计划。依据变化后需要重新核对。记录不会重生成、替换素材或批准任务执行。</p>
  {error&&!item&&<div ref={errorPanel} tabIndex={-1} role="alert" className="revision-alert">{error}<p>当前说明与已读取清单保留；保存暂停时，请重新读取核对。</p></div>}{notice&&<p className="revision-notice" role="status">{notice}</p>}
  {reading&&<p role="status">正在读取修改影响与人工记录…</p>}
  {!reading&&!data&&<section className="revision-card"><h2>暂未读取到核对事项</h2><p>检查项目与本地服务，再重新读取。</p></section>}
  {data&&<>{(version||scopeError)&&<section className="revision-card" aria-label="核对版本范围"><h2>{scopeError?'核对查看位置不可用':`只查看 v${source?.version_number} · ${source?STAGES[source.stage]:''} 的修改影响`}</h2><p>{scopeError||'这里按当时的修改来源列出全部状态；当前目标与核对依据仍以本次后端读取为准。恢复记录不表示已审核。'}</p><button disabled={reading||busy} onClick={()=>{if(lock.current||dirty&&!window.confirm('切换范围会丢弃当前核对草稿，确认查看全部？'))return;const url=new URL(location.href);url.searchParams.delete('version');url.searchParams.delete('restore');url.searchParams.delete('item');history.pushState(history.state,'',url);setSearch(url.search);setSelected('');setNote('');setConfirming(false);setAck(false);setFilter('all');}}>查看项目全部核对事项</button></section>}{data.running&&<p className="revision-alert">项目正在执行，结束后才能保存核对决定。</p>}{!!data.untracked_stages.length&&<p className="revision-alert">{data.untracked_stages.map(s=>STAGES[s]).join('、')}有未归档更新，先从文字版本页归档，再决定继续使用。</p>}
   <div className="impact-summary" aria-label="核对统计"><span>待处理 <strong>{scoped.filter(i=>i.status!=='retained').length}</strong></span><span>继续使用 <strong>{scoped.filter(i=>i.status==='retained').length}</strong></span><span>历史记录 <strong>{data.records.filter(r=>scoped.some(i=>i.item_id===r.item_id)).length}</strong></span></div>
   {!scoped.length?<section className="revision-card revision-empty"><h2>{scopeError?'请先核对查看位置':version?'这个版本没有可关联的镜头事项':'没有已归档的修改影响'}</h2><p>没有事项不表示文字或媒体已审核。请回到文字版本检查差异；未记录的旧修改无法补回。</p></section>:<div className="impact-grid"><aside className="revision-card"><h2>核对清单</h2><label className="revision-field">显示范围<select value={filter} disabled={reading||busy} onChange={e=>setFilter(e.target.value)}><option value="open">待处理</option><option value="all">全部事项</option><option value="retained">继续使用</option></select></label>
   <div className="impact-list">{[...scoped].reverse().filter(i=>filter==='all'||(filter==='retained'?i.status==='retained':i.status!=='retained')).map(i=><button key={i.item_id} aria-pressed={selected===i.item_id} disabled={reading||busy} onClick={()=>choose(i.item_id)}><strong>v{i.version_number} · {STAGES[i.stage]}修改</strong><span>{i.segment_id}{i.shot_number===null?'':` · 镜头 ${i.shot_number}`}</span><small>{REVIEW_STATUSES[i.status]}</small></button>)}</div>
   {!scoped.some(i=>filter==='all'||(filter==='retained'?i.status==='retained':i.status!=='retained'))&&<p className="revision-muted">此范围没有事项，可切换查看全部。</p>}</aside>
   <section className="revision-card" aria-label="事项详情">{!item?<><h2>{selected?'所选事项不存在':'选择一项开始核对'}</h2><p>按修改来源逐项检查，记录决定和理由。</p></>:<><h2>v{item.version_number} · {item.segment_id}{item.shot_number===null?'':` · 镜头 ${item.shot_number}`}</h2><p className="impact-badge">{REVIEW_STATUSES[item.status]}</p><p className="revision-muted">{item.reason}</p>{!item.target_exists&&<p className="revision-alert">原目标已移除或无法唯一定位。只能记录仍需处理，请核对结构变化。</p>}
   <details className="impact-details"><summary>查看这次{STAGES[item.stage]}修改的全部差异</summary>{data.sources.find(s=>s.version_id===item.version_id)?.changes.map((c,n)=><div className="revision-change" key={n}><p>变化 {n+1}</p><div><section><h4>修改前</h4><pre>{c.before_present===false?'（无此字段）':c.before||'（空白）'}</pre></section><section><h4>修改后</h4><pre>{c.after_present===false?'（已移除字段）':c.after||'（空白）'}</pre></section></div></div>)}</details>
   <h3>当前目标文字</h3>{item.current_fields.map((f,n)=><div key={n}><p className="revision-muted">{f.label}</p><pre className="revision-text">{f.value||'（空白）'}</pre></div>)}{!item.current_fields.length&&<p className="revision-muted">没有可展示的目标文字，请从原入口核对。</p>}
   <nav className="impact-links" aria-label="核对相关内容">{[['storyboard','分镜'],['reference_generation','参考图'],['video_generation','视频'],['post_production','后期']].map(([stage,label])=><Link key={stage} href={`/?session=${encodeURIComponent(projectId)}&stage=${stage}`}>{label}</Link>)}<Link href={`/projects/${encodeURIComponent(projectId)}/assets`}>素材与引用</Link><Link href={`/projects/${encodeURIComponent(projectId)}/plans`}>生成计划</Link></nav>
   {error&&<div ref={errorPanel} tabIndex={-1} role="alert" className="revision-alert">{error}<p>说明保留；请先复制需要保留的内容，再重新读取核对记录。</p></div>}
   <h3>人工核对决定</h3><label className="revision-field">核对决定<select value={decision} disabled={disabled} onChange={e=>{setDecision(e.target.value as keyof typeof DECISIONS);setAck(false);}}><option value="follow_up">仍需处理</option><option value="retain" disabled={!item.target_exists||!!data.untracked_stages.length}>已核对，继续使用</option></select></label>
   <label className="revision-field">核对说明<textarea rows={4} maxLength={1000} value={note} disabled={busy||reading} readOnly={blocked||data.running} onChange={e=>{setNote(e.target.value);setAck(false);}} placeholder="写下检查结果，以及仍需处理的内容"/></label><p className="revision-muted">{note.length} / 1000 字 · 请勿填写密钥或密码</p>
   <div className="revision-actions"><button ref={openButton} className="revision-primary" disabled={disabled||!note.trim()} onClick={()=>{setAck(false);setConfirming(true);}}>核对决定并保存</button><button disabled={busy||!note} onClick={()=>{if(window.confirm('确定放弃未保存核对说明吗？')){setNote('');setAck(false);}}}>放弃草稿</button></div>
   <h3>此事项的人工记录</h3>{data.records.filter(r=>r.item_id===item.item_id).reverse().map(r=><article className="impact-record" key={r.review_id}><strong>{DECISIONS[r.decision]}</strong><p>{r.note}</p><small>{new Date(r.created_at).toLocaleString('zh-CN')} · {r.applicable?'当时依据与当前一致':'历史依据已变化'}</small></article>)}{!data.records.some(r=>r.item_id===item.item_id)&&<p className="revision-muted">尚无人工记录。</p>}</>}
   </section></div>}
  </>}
  <dialog ref={dialog} className="impact-dialog" onCancel={e=>{e.preventDefault();if(!busy)setConfirming(false);}}><h2 tabIndex={-1}>确认保存人工核对决定</h2><p>{item&&`v${item.version_number} · ${item.segment_id}${item.shot_number===null?'':` · 镜头 ${item.shot_number}`}`}</p><strong>{DECISIONS[decision]}</strong><pre className="revision-text">{note}</pre><p>仅追加人工记录。仍需处理的内容请之后单独操作；不会重生成、改变素材选择或计划权限。</p><label className="revision-check"><input type="checkbox" checked={ack} disabled={busy} onChange={e=>setAck(e.target.checked)}/>我已检查相关内容，并确认上述决定与说明</label><div className="revision-actions"><button disabled={busy} onClick={()=>setConfirming(false)}>返回核对</button><button className="revision-primary" disabled={busy||!ack||disabled} onClick={save}>{busy?'正在保存…':'确认保存核对记录'}</button></div></dialog>
 </main></>;
}
