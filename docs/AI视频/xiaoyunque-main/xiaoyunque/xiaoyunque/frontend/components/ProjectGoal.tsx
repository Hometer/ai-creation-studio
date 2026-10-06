'use client';
import Link from 'next/link';
import {useCallback,useEffect,useRef,useState} from 'react';
import {readGoal,saveGoal,type ProjectGoal as Goal} from '@/lib/projectGoals';
export default function ProjectGoal({projectId}:{projectId:string}){
 const [data,setData]=useState<Goal|null>(null),[name,setName]=useState(''),[duration,setDuration]=useState('60'),[busy,setBusy]=useState(false),[blocked,setBlocked]=useState(true),[error,setError]=useState(''),[message,setMessage]=useState('');
 const alive=useRef(true),lock=useRef(false),serial=useRef(0),dirty=useRef(false),attempt=useRef({signature:'',id:''});
 const invalidate=useCallback(()=>{alive.current=false;++serial.current;},[]);
 const reload=useCallback(async()=>{
  if(lock.current)return;lock.current=true;const sequence=++serial.current;setBusy(true);
  try{const next=await readGoal(projectId);if(!alive.current||sequence!==serial.current)return;setData(next);setBlocked(false);setError('');if(!dirty.current){setName(next.project_name??'');setDuration(String(next.target_duration??60));}else setMessage('当前目标已重新读取，输入保留；保存前请核对。');}
  catch(e){if(alive.current&&sequence===serial.current){setBlocked(true);setError(e instanceof Error?e.message:'读取失败');}}
  finally{if(alive.current&&sequence===serial.current){lock.current=false;setBusy(false);}}
 },[projectId]);
 useEffect(()=>{alive.current=true;const timer=window.setTimeout(()=>void reload(),0);return()=>{invalidate();window.clearTimeout(timer);};},[reload,invalidate]);
 useEffect(()=>{const before=(e:BeforeUnloadEvent)=>{if(dirty.current||lock.current)e.preventDefault();};window.addEventListener('beforeunload',before);return()=>window.removeEventListener('beforeunload',before);},[]);
 const save=async()=>{
  if(lock.current||blocked||!data)return;lock.current=true;setBusy(true);setError('');setMessage('');const sequence=++serial.current;
  try{const signature=JSON.stringify({name,duration,token:data.expected_token});if(attempt.current.signature!==signature)attempt.current={signature,id:crypto.randomUUID().replaceAll('-','')};const next=await saveGoal(projectId,data,name,duration,attempt.current.id);if(!alive.current||sequence!==serial.current)return;setData(next);setName(next.project_name??'');setDuration(String(next.target_duration??60));dirty.current=false;setMessage('项目名称与目标时长已保存，没有执行模型。');}
  catch(e){if(alive.current&&sequence===serial.current){setBlocked(true);setError(e instanceof Error?e.message:'保存尚未确认');}}
  finally{if(alive.current&&sequence===serial.current){lock.current=false;setBusy(false);}}
 };
 const leave=(e:React.MouseEvent<HTMLAnchorElement>)=>{if((dirty.current||busy)&&!window.confirm('输入尚未确认保存，确认离开？'))e.preventDefault();};
 return <main className="asset-workspace media-workspace"><nav className="asset-breadcrumb"><Link onClick={leave} href={`/?session=${encodeURIComponent(projectId)}`}>返回项目</Link><Link onClick={leave} href={`/projects/${encodeURIComponent(projectId)}/assets`}>素材与版本</Link><button className="asset-button" disabled={busy} onClick={()=>void reload()}>重新读取目标</button></nav><header className="asset-heading"><div><span className="studio-eyebrow">PROJECT · {projectId}</span><h1>项目名称与<span>创作目标</span></h1><p>给作品命名，设定整片目标时长，继续保留已有创作内容。</p></div></header>
  {error&&<p role="alert" className="media-alert">{error} 输入保留，请先重新读取核对。</p>}{message&&<p role="status" className="media-muted">{message}</p>}
  <div className="media-columns"><section className="asset-detail asset-editor"><h2>编辑创作目标</h2><label>项目名称<input value={name} maxLength={100} disabled={busy} onChange={e=>{dirty.current=true;setName(e.target.value);setMessage('');}} placeholder="为这部作品命名"/></label><label>整片目标时长（秒）<input value={duration} inputMode="numeric" maxLength={3} disabled={busy} onChange={e=>{dirty.current=true;setDuration(e.target.value);setMessage('');}}/></label><p className="media-muted">1—600秒，默认60秒。这是整片创作目标；每次实际生成仍按片段核对规格与费用。</p>{data?.running&&<p className="media-alert">项目正在执行，完成后再编辑。</p>}<button className="asset-button primary" disabled={busy||blocked||!name.trim()||!/^\d+$/.test(duration)||Number(duration)<1||Number(duration)>600||data?.running} onClick={()=>void save()}>保存名称与目标（不调用模型）</button></section>
   <section className="asset-detail"><h2>当前已保存参数</h2>{data?<><p>项目名称：{data.project_name??'尚未单独命名'}</p><p>整片目标时长：{data.target_duration===null?'尚未设置':data.target_duration+' 秒'}</p><p>风格：{data.style} · 比例：{data.video_ratio}</p><h3>故事摘要</h3><pre className="media-prompt">{data.story_excerpt||'尚无故事文字'}</pre><p className="media-muted">仅展示故事前2000字；完整故事在项目中保留。保存名称与目标不会改写剧本、分镜或素材。</p></>:<p>正在读取已有参数…</p>}</section>
  </div></main>;
}
