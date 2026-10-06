'use client';
import {useSelectedJob} from '@/lib/useSelectedJob';
import Link from 'next/link';
import MediaImage from './MediaImage';
import {useCallback,useEffect,useRef,useState} from 'react';
import {readMedia,prepareMedia,mediaOperation,MEDIA_STATES,type MediaWorkspace,type MediaJob} from '@/lib/releaseMedia';
import {readWorkspace,type Plan} from '@/lib/generationPlans';
import {mediaUrl} from '@/lib/assets';
const uid=()=>crypto.randomUUID().replaceAll('-','');
const money=(fen:number)=>(fen/100).toFixed(2)+' 元';
const active=['queued','submitting','pending','querying','ready_download','downloading'];
export default function ProjectMediaRevisions({projectId}:{projectId:string}){
  const [selected,setSelected]=useSelectedJob();
 const [data,setData]=useState<MediaWorkspace|null>(null),[plans,setPlans]=useState<Plan[]>([]),[kind,setKind]=useState<'image'|'video'>('image'),[source,setSource]=useState(''),[segment,setSegment]=useState(''),[prompt,setPrompt]=useState(''),[preserve,setPreserve]=useState('');
  const [busy,setBusy]=useState(false),[blocked,setBlocked]=useState(true),[error,setError]=useState(''),[ack,setAck]=useState(false),[material,setMaterial]=useState(false),[review,setReview]=useState(false),[now,setNow]=useState(0);
  useEffect(()=>{const clear=()=>{setAck(false);setMaterial(false);setReview(false);};window.addEventListener('popstate',clear);window.addEventListener('native-job-selection',clear);return()=>{window.removeEventListener('popstate',clear);window.removeEventListener('native-job-selection',clear);};},[]);
 const alive=useRef(true),lock=useRef(false),serial=useRef(0),requestIds=useRef(new Map<string,string>());
  const job=data?.jobs.find(j=>j.job_id===selected),image=data?.images.find(i=>i.reference.reference_id===source),plan=plans.find(p=>p.plan_id===source);
  const dirty=Boolean(prompt||preserve),validPlans=plans.filter(p=>p.current&&p.confirmation);
  const reset=()=>{setAck(false);setMaterial(false);setReview(false);};
  const reload=useCallback(async()=>{
    if(lock.current)return;lock.current=true;const sequence=++serial.current;
    try{const [next,workspace]=await Promise.all([readMedia(projectId),readWorkspace(projectId)]);if(!alive.current||sequence!==serial.current)return;setData(next);setPlans(workspace.plans);setBlocked(false);setError('');}
    catch(e){if(alive.current&&sequence===serial.current){setBlocked(true);setError(e instanceof Error?e.message:'读取失败');}}
    finally{if(alive.current&&sequence===serial.current)lock.current=false;}
  },[projectId]);
  const invalidate=useCallback(()=>{alive.current=false;++serial.current;},[]);
  useEffect(()=>{alive.current=true;const timer=window.setTimeout(()=>void reload(),0),clock=window.setInterval(()=>setNow(Date.now()/1000),1000);return()=>{invalidate();window.clearTimeout(timer);window.clearInterval(clock);};},[reload,invalidate]);
  useEffect(()=>{if(!data?.jobs.some(j=>active.includes(j.status)))return;const timer=window.setInterval(()=>void reload(),2500);return()=>window.clearInterval(timer);},[data,reload]);
  useEffect(()=>{if(!dirty&&!busy)return;const before=(e:BeforeUnloadEvent)=>{e.preventDefault();};window.addEventListener('beforeunload',before);return()=>window.removeEventListener('beforeunload',before);},[dirty,busy]);
  const operationId=(key:string)=>{let value=requestIds.current.get(key);if(!value){value=uid();requestIds.current.set(key,value);}return value;};
  const run=async(operation:'prepare'|'confirm'|'advance'|'stop'|'resume'|'select')=>{
    if(lock.current||blocked||!data)return;if(operation==='confirm'&&(!ack||!material)||operation==='select'&&!review)return;
    lock.current=true;setBusy(true);setError('');const sequence=++serial.current;
    try{
      let result:MediaJob;
      if(operation==='prepare'){
        const input={kind,source_id:source,segment_id:kind==='image'?image?.reference.segment_id??null:segment,expected_workspace:data.expected_workspace,prompt,preserve};
        result=await prepareMedia(projectId,{...input,request_id:operationId(JSON.stringify(input))});
      }else{
        if(!job)return;
        if(operation==='stop'&&!window.confirm('停止本地后续步骤？平台已接收的任务可能仍在执行和计费，原任务编号及额度占用会保留。'))return;
        if(operation==='resume'&&!window.confirm('恢复同一平台任务的查询与归档？不会重新提交生成请求。'))return;
        result=await mediaOperation(projectId,job,operation,operationId(operation+job.job_id+job.selection_token));
      }
      if(!alive.current||sequence!==serial.current)return;
      const [next,workspace]=await Promise.all([readMedia(projectId),readWorkspace(projectId)]);if(!alive.current||sequence!==serial.current)return;
      setData(next);setPlans(workspace.plans);setSelected(result.job_id);reset();
    }catch(e){if(alive.current&&sequence===serial.current){setBlocked(true);setError(e instanceof Error?e.message:'提交结果未确认，请读取原任务');reset();}}
    finally{if(alive.current&&sequence===serial.current){lock.current=false;setBusy(false);}}
  };
  const leave=(e:React.MouseEvent<HTMLAnchorElement>)=>{if((dirty||busy)&&!window.confirm(busy?'正在保存或读取任务，确认离开？请返回后核对原任务。':'修改说明尚在输入框，确认离开？'))e.preventDefault();};
  const canSelect=job&&(job.current||job.plan.kind==='image'&&job.selected);
  const canConfirm=job&&job.current&&job.status==='awaiting_confirmation'&&data?.credential_present&&data.endpoint_allowed&&data.price_current&&job.plan.expires_at>now&&job.plan.estimate_fen<=data.available_fen;
  return <main className="asset-workspace media-workspace">
    <nav className="asset-breadcrumb"><Link onClick={leave} href={`/?session=${encodeURIComponent(projectId)}`}>返回项目</Link><Link onClick={leave} href={`/projects/${encodeURIComponent(projectId)}/assets`}>素材与版本</Link><Link onClick={leave} href={`/projects/${encodeURIComponent(projectId)}/plans`}>生成计划</Link><Link onClick={leave} href={`/projects/${encodeURIComponent(projectId)}/compose`}>合成与导出</Link><button className="asset-button asset-refresh" disabled={busy} onClick={()=>{reset();void reload();}}>重新读取任务</button></nav>
    <header className="asset-heading"><div><span className="studio-eyebrow">PROJECT · {projectId}</span><h1>媒体修改与<span>候选选用</span></h1><p>明确修改与保留要求，核对结果后选用新版本。</p></div></header>
    {data&&<section className="media-budget"><strong>单次最多 {money(data.single_limit_fen)} · 本轮最多 {money(data.round_limit_fen)}</strong><p>已占用 {money(data.occupied_fen)} · 剩余 {money(data.available_fen)}，与文字共创共用额度。占用不是实际账单，失败或未知提交也保守保留占用。</p></section>}
    {data&&(!data.credential_present||!data.endpoint_allowed)&&<p className="media-alert">后端模型配置尚不可用，可以准备计划；真实生成等待配置。</p>}
    {data&&!data.price_current&&<p className="media-alert">价格依据已过期，暂不能准备或提交新的收费任务。</p>}
    {data&&selected&&!job&&<p className="media-alert" role="alert">地址中的任务未在这个项目中找到，请重新读取并核对原任务。</p>}{error&&<p className="media-alert" role="alert">{error} 输入保留，请先重新读取任务核对。</p>}{busy&&<p role="status">正在保存或读取原任务…</p>}
    <div className="media-columns"><section className="asset-detail asset-editor"><h2>准备本次修改</h2><p className="media-muted">准备计划不调用模型。生成候选后仍须人工核对，确认选用才更新本项目的对应位置。</p>
      <label>修改对象<select value={kind} disabled={busy} onChange={e=>{setKind(e.target.value as 'image'|'video');setSource('');setSegment('');reset();}}><option value="image">图片引用</option><option value="video">视频片段</option></select></label>
      {kind==='image'?<><label>当前图片引用<select value={source} disabled={busy} onChange={e=>{setSource(e.target.value);reset();}}><option value="">请选择</option>{data?.images.map(i=><option key={i.reference.reference_id} value={i.reference.reference_id}>{i.version.name} · v{i.version.number} · {i.reference.segment_id??'整个项目'}</option>)}</select></label>{image&&<div className="asset-media-preview"><MediaImage width={image.version.media!.width} height={image.version.media!.height} src={mediaUrl(image.asset.asset_id,image.version.version_id)} alt="本次原图片版本"/></div>}<p className="media-muted">图片编辑输入宽高为 512—4096 像素；修改与保留要求合计最多 800 字。</p></>:<><label>已确认的生成计划<select value={source} disabled={busy} onChange={e=>{setSource(e.target.value);setSegment('');reset();}}><option value="">请选择</option>{validPlans.map(p=><option key={p.plan_id} value={p.plan_id}>{p.content.options.model} · {p.content.options.resolution} · {p.plan_id.slice(0,8)}</option>)}</select></label><label>计划中的片段<select value={segment} disabled={busy} onChange={e=>{setSegment(e.target.value);reset();}}><option value="">请选择</option>{plan?.content.targets.map(t=><option key={t.segment_id} value={t.segment_id}>{t.segment_id} · {t.duration} 秒</option>)}</select></label><p className="media-muted">需先在生成计划选定输入素材与规格。单次超过 5 元会阻止提交，请明确调整计划后再核对。</p></>}
      <label>希望怎么修改<textarea value={prompt} maxLength={2000} rows={4} disabled={busy} onChange={e=>{setPrompt(e.target.value);reset();}} placeholder="例如：把背景改为蓝色"/></label><label>必须保留什么<textarea value={preserve} maxLength={2000} rows={3} disabled={busy} onChange={e=>{setPreserve(e.target.value);reset();}} placeholder="例如：保留角色外观与构图"/></label>
      <button className="asset-button primary" disabled={busy||blocked||!data?.price_current||!source||!prompt.trim()||kind==='video'&&!segment} onClick={()=>void run('prepare')}>准备计划（不收费）</button>
    </section><section className="asset-detail"><h2>原任务与候选</h2><label>任务历史<select value={selected} disabled={busy} onChange={e=>{setSelected(e.target.value);reset();}}><option value="">请选择任务</option>{data?.jobs.slice().reverse().map(j=><option key={j.job_id} value={j.job_id}>#{j.number} · {j.plan.kind==='image'?'图片':'视频'} · {MEDIA_STATES[j.status]}{j.simulation_only?' · 人工样例':''}</option>)}</select></label>
      {!job&&<p className="media-muted">准备后在这里核对计划；刷新或重启服务后，可从任务历史继续查看。</p>}
      {job&&<><p className="media-state">{MEDIA_STATES[job.status]}{job.selected?' · 已选用':''}</p>{job.simulation_only&&<p className="media-alert">人工接口样例，无模型调用、收费或素材外发，不代表模型效果。</p>}<p>模型：{job.plan.model} · 本次估算 {money(job.plan.estimate_fen)} · 数量 1{job.plan.duration?` · ${job.plan.duration} 秒 · ${job.plan.resolution}`:''}</p><p className="media-muted">价格依据：{job.plan.price_version} · 计划有效至 {new Date(job.plan.expires_at*1000).toLocaleString()}</p>
      <h3>固定输入素材</h3><div className="media-inputs">{job.plan.input_refs.map(r=><a key={r.reference_id} href={mediaUrl(r.asset_id,r.version_id)} target="_blank" rel="noreferrer"><MediaImage width={r.width} height={r.height} src={mediaUrl(r.asset_id,r.version_id)} alt={`已固定输入：${r.role}`}/><span>{r.role==='first_frame'?'首帧':r.role==='last_frame'?'尾帧':'原图片'} · {r.width}×{r.height}</span></a>)}</div><h3>完整生成说明</h3><pre className="media-prompt">{job.plan.execution_prompt}</pre>
      {!job.current&&(job.plan.kind==='video'||!job.selected)&&<p className="media-alert">输入依据已变化。旧结果继续保留；再次生成或选用前请核对当前素材与受影响的片段。</p>}
      {job.provider_task_id&&<p className="media-id">原平台任务：{job.provider_task_id} · 已提交 {job.submit_attempts} 次 · 已查询 {job.query_attempts} 次</p>}
      {job.error&&<p className="media-alert">{job.error}</p>}
      {job.status==='awaiting_confirmation'&&<><label className="media-check"><input type="checkbox" checked={material} disabled={busy||blocked||!canConfirm} onChange={e=>setMaterial(e.target.checked)}/>我确认本次素材有权使用、不含敏感信息，可以发送到境内百炼处理</label><label className="media-check"><input type="checkbox" checked={ack} disabled={busy||blocked||!canConfirm} onChange={e=>setAck(e.target.checked)}/>我已核对输入素材、完整说明与本次估算，确认生成费用</label><button className="asset-button primary" disabled={busy||blocked||!canConfirm||!ack||!material} onClick={()=>void run('confirm')}>确认费用并生成候选</button></>}
      {['queued','pending','ready_download'].includes(job.status)&&<button className="asset-button" disabled={busy||blocked} onClick={()=>void run('advance')}>继续原任务</button>}
      {['awaiting_confirmation','queued','pending','ready_download','manual_review'].includes(job.status)&&<button className="asset-button" disabled={busy||blocked} onClick={()=>void run('stop')}>停止本地后续步骤</button>}
      {['manual_review','cancelled'].includes(job.status)&&job.provider_task_id&&<button className="asset-button" disabled={busy||blocked} onClick={()=>void run('resume')}>恢复原任务查询</button>}
      {job.asset_id&&job.version_id&&<><h3>候选结果</h3><div className="asset-media-preview">{job.plan.kind==='image'?<MediaImage width={640} height={640} src={mediaUrl(job.asset_id,job.version_id)} alt="待人工核对的图片候选"/>:<video src={mediaUrl(job.asset_id,job.version_id)} controls preload="metadata"/>}</div><a className="asset-button" href={mediaUrl(job.asset_id,job.version_id)} download>下载此候选</a><p className="media-muted">实际账单待账户核对{job.usage_estimate_fen!==null?`；返回用量对应估算 ${money(job.usage_estimate_fen)}`:''}。</p><label className="media-check"><input type="checkbox" checked={review} disabled={busy||blocked||!canSelect} onChange={e=>setReview(e.target.checked)}/>我已核对修改结果和必须保留的要素，确认选用此版本</label><button className="asset-button primary" disabled={busy||blocked||!review||!job.current&&!job.selected} onClick={()=>void run('select')}>确认选用此候选</button><p className="media-muted">{job.plan.kind==='image'?'选用会更新这一处图片引用，相关视频计划可能失效。':'选用会记录此片段的当前视频候选，供后续成片使用。'}旧版本与其他位置继续保留。</p></>}
      </>}
    </section></div>
  </main>;
}
