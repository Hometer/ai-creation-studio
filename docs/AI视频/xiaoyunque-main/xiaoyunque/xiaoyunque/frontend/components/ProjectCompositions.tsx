'use client';
import {useSelectedJob} from '@/lib/useSelectedJob';
import Link from 'next/link';
import {useCallback,useEffect,useRef,useState} from 'react';
import {readFilms,prepareFilm,filmOperation,filmUrl,FILM_STATES,type FilmWorkspace,type ClipSelection} from '@/lib/compositions';
import {mediaUrl} from '@/lib/assets';
const uid=()=>crypto.randomUUID().replaceAll('-','');
const secs=(v:number)=>Number(v.toFixed(2))+' 秒';
export default function ProjectCompositions({projectId}:{projectId:string}){
 const [selected,setSelected]=useSelectedJob();
 const [data,setData]=useState<FilmWorkspace|null>(null),[clips,setClips]=useState<ClipSelection[]>([]),[keep,setKeep]=useState<string[]>([]);
 const [busy,setBusy]=useState(false),[blocked,setBlocked]=useState(true),[error,setError]=useState(''),[ack,setAck]=useState(false),[dirty,setDirty]=useState(false);
 useEffect(()=>{const clear=()=>{setAck(false);};window.addEventListener('popstate',clear);window.addEventListener('native-job-selection',clear);return()=>{window.removeEventListener('popstate',clear);window.removeEventListener('native-job-selection',clear);};},[]);
 const alive=useRef(true),lock=useRef(false),serial=useRef(0),ids=useRef(new Map<string,string>());
 const job=data?.jobs.find(j=>j.job_id===selected);
 const reload=useCallback(async()=>{
  if(lock.current)return;lock.current=true;const sequence=++serial.current;
  try{const next=await readFilms(projectId);if(!alive.current||sequence!==serial.current)return;
   setData(next);setClips(previous=>{const existing=previous.filter(c=>next.segments.some(s=>s.segment_id===c.segment_id));return [...existing,...next.segments.filter(s=>!existing.some(c=>c.segment_id===s.segment_id)).map(s=>s.selected??{segment_id:s.segment_id,asset_id:'',version_id:''})];});
   setKeep([]);setAck(false);setBlocked(false);setError('');
  }catch(e){if(alive.current&&sequence===serial.current){setBlocked(true);setError(e instanceof Error?e.message:'读取失败');}}
  finally{if(alive.current&&sequence===serial.current)lock.current=false;}
 },[projectId]);
 const invalidate=useCallback(()=>{alive.current=false;++serial.current;},[]);
 useEffect(()=>{alive.current=true;const timer=window.setTimeout(()=>void reload(),0);return()=>{invalidate();window.clearTimeout(timer);};},[reload,invalidate]);
 useEffect(()=>{if(!data?.jobs.some(j=>['queued','rendering'].includes(j.status)))return;const timer=window.setInterval(()=>void reload(),2500);return()=>window.clearInterval(timer);},[data,reload]);
 useEffect(()=>{if(!dirty&&!busy)return;const before=(e:BeforeUnloadEvent)=>e.preventDefault();window.addEventListener('beforeunload',before);return()=>window.removeEventListener('beforeunload',before);},[dirty,busy]);
 const operationId=(signature:string)=>{let id=ids.current.get(signature);if(!id){id=uid();ids.current.set(signature,id);}return id;};
 const edit=()=>{setDirty(true);setAck(false);setSelected('');};
 const choose=(segment:string,version:string)=>{const option=data?.options.find(o=>o.version_id===version);setClips(current=>current.map(c=>c.segment_id===segment?{segment_id:segment,asset_id:option?.asset_id??'',version_id:option?.version_id??''}:c));setKeep(current=>current.filter(s=>s!==segment));edit();};
 const move=(index:number,direction:number)=>{setClips(current=>{const next=[...current];[next[index],next[index+direction]]=[next[index+direction],next[index]];return next;});edit();};
 const total=clips.reduce((n,c)=>n+(data?.options.find(o=>o.asset_id===c.asset_id&&o.version_id===c.version_id)?.media.duration??0),0);
 const complete=Boolean(data?.segments.length&&clips.length===data.segments.length&&clips.every(c=>{const option=data.options.find(o=>o.asset_id===c.asset_id&&o.version_id===c.version_id);return option&&(!option.stale||keep.includes(c.segment_id));})&&total<=600);
 const run=async(operation:'prepare'|'confirm'|'stop')=>{
  if(lock.current||blocked||!data||operation==='confirm'&&!ack)return;
  if(operation==='stop'&&!window.confirm('停止这份尚未开始的免费合成计划？原片段与旧成片继续保留。'))return;
  lock.current=true;setBusy(true);setError('');const sequence=++serial.current;
  try{
   const result=operation==='prepare'?await prepareFilm(projectId,data,clips,keep,operationId(JSON.stringify({token:data.expected_workspace,clips,keep}))):job?await filmOperation(projectId,job,operation,operationId(operation+job.job_id+job.plan_hash)):null;
   if(!result||!alive.current||sequence!==serial.current)return;
   const next=await readFilms(projectId);if(!alive.current||sequence!==serial.current)return;
   setData(next);setSelected(result.job_id);setAck(false);setKeep([]);setDirty(false);
  }catch(e){if(alive.current&&sequence===serial.current){setBlocked(true);setAck(false);setError(e instanceof Error?e.message:'结果未确认，请读取原任务');}}
  finally{if(alive.current&&sequence===serial.current){lock.current=false;setBusy(false);}}
 };
 const leave=(e:React.MouseEvent<HTMLAnchorElement>)=>{if((dirty||busy)&&!window.confirm(busy?'正在保存或读取，请返回后核对原任务，确认离开？':'片段选择或顺序尚未保存，确认离开？'))e.preventDefault();};
 return <main className="asset-workspace media-workspace composition-workspace">
 <nav className="asset-breadcrumb"><Link onClick={leave} href={`/?session=${encodeURIComponent(projectId)}`}>返回项目</Link><Link onClick={leave} href={`/projects/${encodeURIComponent(projectId)}/assets`}>素材与版本</Link><Link onClick={leave} href={`/projects/${encodeURIComponent(projectId)}/media`}>媒体修改与选用</Link><button className="asset-button asset-refresh" disabled={busy} onClick={()=>void reload()}>重新读取成片</button></nav>
 <header className="asset-heading"><div><span className="studio-eyebrow">PROJECT · {projectId}</span><h1>合成与<span>成片导出</span></h1><p>确认每段视频、版本与顺序，把这次选择另存为一版成片。</p></div></header>
 {data&&selected&&!job&&<p className="media-alert" role="alert">地址中的任务未在这个项目中找到，请重新读取并核对原任务。</p>}{error&&<p className="media-alert" role="alert">{error} 请重新读取并核对，当前片段输入保留。</p>}{busy&&<p role="status">正在保存或读取计划…</p>}
 {data&&!data.tools_available&&<p className="media-alert">本机合成工具尚不可用。计划可以保留，合成等待工具恢复。</p>}
 <p className="media-muted">本机合成 · 不调用模型 · 模型费用0元。输出720P、30帧，按项目比例补边；保留所选视频原声音，无声音的片段补静音。旧成片继续保留。</p>
 <div className="media-columns"><section className="asset-detail"><h2>本次选用片段与顺序</h2><p>整片目标：{data?.target_duration?secs(data.target_duration):'尚未设置'} · 比例：{data?.video_ratio??'读取中'} · 当前所选总时长：{secs(total)}</p>
 {data&&!data.segments.length&&<p className="media-alert">还没有分镜片段，请先保存分镜，再逐段选用视频。</p>}
 {clips.map((c,index)=>{const option=data?.options.find(o=>o.asset_id===c.asset_id&&o.version_id===c.version_id);return <article className="composition-clip" key={c.segment_id}>
 <div className="composition-order"><h3>{index+1} · {c.segment_id}</h3><div><button className="asset-button" aria-label={`上移${c.segment_id}`} disabled={busy||index===0} onClick={()=>move(index,-1)}>上移</button><button className="asset-button" aria-label={`下移${c.segment_id}`} disabled={busy||index===clips.length-1} onClick={()=>move(index,1)}>下移</button></div></div>
 <p className="media-muted">{data?.segments.find(s=>s.segment_id===c.segment_id)?.label}</p>
 <label>选用归档视频<select aria-label={`${c.segment_id}选用归档视频`} value={c.version_id} disabled={busy} onChange={e=>choose(c.segment_id,e.target.value)}><option value="">请选择一个明确版本</option>{data?.options.map(o=><option key={o.version_id} value={o.version_id}>{o.name} · v{o.number} · {secs(o.media.duration!)}{o.simulation_only?' · 人工任务样例':''}</option>)}</select></label>
 {option&&<><video controls preload="metadata" src={mediaUrl(option.asset_id,option.version_id)}/><p className="media-muted">{option.media.width}×{option.media.height} · 来源：{option.simulation_only?'人工任务样例':option.source_type==='upload'?'本地上传':'项目生成产物'} · {option.source_project??'本地素材库'} · v{option.number}</p>
 {option.stale&&<label className="media-check media-alert"><input type="checkbox" checked={keep.includes(c.segment_id)} disabled={busy||blocked} onChange={e=>{setKeep(current=>e.target.checked?[...current,c.segment_id]:current.filter(s=>s!==c.segment_id));setDirty(true);setAck(false);}}/>上游依据已改变，我已预览，仍确认保留此片段旧版本</label>}</>}
 </article>;})}
 {total>600&&<p className="media-alert">本次合成超过600秒，请先核对分镜与片段范围。</p>}
 <button className="asset-button primary" disabled={busy||blocked||!complete} onClick={()=>void run('prepare')}>准备合成计划（不执行）</button></section>
 <section className="asset-detail"><h2>成片计划与版本</h2><label>合成历史<select value={selected} disabled={busy} onChange={e=>{setSelected(e.target.value);setAck(false);}}><option value="">请选择一份计划或成片</option>{data?.jobs.slice().reverse().map(j=><option key={j.job_id} value={j.job_id}>#{j.number} · {FILM_STATES[j.status]} · {secs(j.plan.total_duration)}</option>)}</select></label>
 {!job&&<p className="media-muted">准备后在这里核对固定版本与顺序。刷新或重启后，可继续查看原计划和成片。</p>}
 {job&&<><p className="media-state">#{job.number} · {FILM_STATES[job.status]}</p><p>输出 {job.plan.width}×{job.plan.height} · {job.plan.fps}帧 · {secs(job.plan.total_duration)} · 本机合成免费</p><ol className="composition-manifest">{job.plan.clips.map(c=><li key={c.segment_id}>{c.segment_id} · {c.name} · v{c.version_number} · {secs(c.media.duration!)}{c.stale?' · 已明确保留旧版本':''}{c.simulation_only?' · 人工任务样例':''}</li>)}</ol>
 {!job.current&&<p className="media-alert">分镜、素材选择或项目目标已变化，旧计划／成片仍保留。新合成请重新准备并确认。</p>}
 {job.plan.clips.some(c=>c.simulation_only)&&<p className="media-alert">包含人工任务样例，不代表真实模型效果。</p>}
 {job.error&&<p className="media-alert">{job.error}</p>}
 {job.status==='draft'&&<><label className="media-check"><input type="checkbox" checked={ack} disabled={busy||blocked||!job.current||!data?.tools_available} onChange={e=>setAck(e.target.checked)}/>我已核对这份计划的全部片段、固定版本、顺序、总时长与输出规格，确认本机合成</label><button className="asset-button primary" disabled={busy||blocked||!ack||!job.current||!data?.tools_available} onClick={()=>void run('confirm')}>确认并在本机合成</button></>}
 {['draft','queued'].includes(job.status)&&<button className="asset-button" disabled={busy||blocked} onClick={()=>void run('stop')}>停止尚未开始的合成</button>}
 {job.status==='succeeded'&&job.output&&<><video className="composition-output" controls preload="metadata" src={filmUrl(projectId,job.job_id)}/><a className="asset-button primary" href={filmUrl(projectId,job.job_id,true)} download>下载这一版MP4成片</a><p className="media-muted">本次另存为第{job.number}份合成结果；旧片段与旧成片继续保留。实际输出{secs(job.output.duration!)}，按所选内容合成。</p></>}
 </>}
 </section></div></main>;
}
