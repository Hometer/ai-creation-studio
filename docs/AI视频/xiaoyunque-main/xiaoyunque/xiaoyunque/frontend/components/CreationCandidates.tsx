'use client';
import {useCallback,useEffect,useRef,useState} from 'react';
import {readCandidates,prepareCandidate,confirmCandidate,advanceCandidate,type CandidateWorkspace,type CandidateJob} from '@/lib/creationCandidates';
const states={awaiting_confirmation:'等待本次确认',queued:'已确认，等待执行',submitting:'模型正在生成',succeeded:'候选已保存',failed:'尚未执行成功',manual_review:'需要人工核对原任务'};
const money=(fen:number)=>(fen/100).toFixed(2)+' 元';
export default function CreationCandidates({projectId,intentId,expectedToken,disabled,onLoad,onBusy,readOnly=false}:{projectId:string;intentId:string;expectedToken:string;disabled:boolean;onLoad:(value:string)=>void;onBusy:(busy:boolean)=>void;readOnly?:boolean}){
  const [data,setData]=useState<CandidateWorkspace|null>(null),[selected,setSelected]=useState(''),[ack,setAck]=useState(false),[material,setMaterial]=useState(false);
  const [busy,setBusy]=useState(false),[blocked,setBlocked]=useState(false),[error,setError]=useState('');
  const [now,setNow]=useState(0);
  const alive=useRef(true),lock=useRef(false),serial=useRef(0);
  const invalidate=useCallback(()=>{alive.current=false;++serial.current;},[]);
  const jobs=data?.jobs.filter(j=>j.plan.intent_id===intentId)??[],job=jobs.find(j=>j.job_id===selected);
  const reload=useCallback(async()=>{
    if(lock.current)return;lock.current=true;const generation=++serial.current;
    try{const next=await readCandidates(projectId);if(alive.current&&generation===serial.current){setData(next);setBlocked(false);setError('');setAck(false);setMaterial(false);}}
    catch(e){if(alive.current&&generation===serial.current){setBlocked(true);setError(e instanceof Error?e.message:'候选记录读取失败');}}
    finally{if(alive.current&&generation===serial.current)lock.current=false;}
  },[projectId]);
  useEffect(()=>{alive.current=true;const timer=window.setTimeout(()=>void reload(),0);const clock=window.setInterval(()=>setNow(Date.now()/1000),1000);return()=>{invalidate();window.clearTimeout(timer);window.clearInterval(clock);};},[reload,invalidate]);
  useEffect(()=>{if(!data?.jobs.some(j=>j.plan.intent_id===intentId&&['queued','submitting'].includes(j.status)))return;const timer=window.setInterval(()=>void reload(),2500);return()=>window.clearInterval(timer);},[data,intentId,reload]);
  const run=async(kind:'prepare'|'confirm'|'advance')=>{
    if(lock.current||disabled||blocked||readOnly)return;lock.current=true;setBusy(true);onBusy(true);setError('');const generation=++serial.current;
    try{
      let result:CandidateJob;
      if(kind==='prepare')result=await prepareCandidate(projectId,{expected_token:expectedToken,intent_id:intentId,request_id:crypto.randomUUID().replaceAll('-','')});
      else {if(!job||kind==='confirm'&&(!ack||!material))return;result=kind==='confirm'?await confirmCandidate(projectId,job,{expected_token:expectedToken,request_id:crypto.randomUUID().replaceAll('-',''),acknowledged:true,material_reviewed:true}):await advanceCandidate(projectId,job);}
      if(!alive.current||generation!==serial.current)return;
      const next=await readCandidates(projectId);if(!alive.current||generation!==serial.current)return;
      setData(next);setSelected(result.job_id);setAck(false);setMaterial(false);
    }catch(e){if(alive.current&&generation===serial.current){setBlocked(true);setError(e instanceof Error?e.message:'操作结果尚未确认，请读取原任务');setAck(false);setMaterial(false);}}
    finally{if(alive.current&&generation===serial.current){lock.current=false;setBusy(false);onBusy(false);}}
  };
  const basis=data?.expected_token===expectedToken&&job?.plan.expected_token===expectedToken&&job.current;
  return <section className="creation-apply-preview"><h3>AI 修改候选</h3>
    <p className="revision-muted">准备计划不调用模型。确认生成会发送当前剧本与分镜文字上下文到境内百炼；候选不会自动改写正文。</p>
    {data?<><p>单次上限 {money(data.single_limit_fen)} · 本轮总上限 {money(data.round_limit_fen)} · 已占用 {money(data.occupied_fen)} · 剩余 {money(data.available_fen)}</p>{(!data.credential_present||!data.endpoint_allowed)&&<p className="revision-alert">后端模型配置尚不可用，可准备计划；真实生成待配置后核对。</p>}{!data.price_current&&<p className="revision-alert">价格依据已过期，暂不能生成。</p>}</>:<p>正在读取模型配置与本轮额度…</p>}
    <div className="revision-actions"><button disabled={disabled||busy||blocked||readOnly||!data||data.expected_token!==expectedToken||!data.price_current} onClick={()=>void run('prepare')}>准备 AI 候选计划</button><button disabled={busy||disabled} onClick={()=>void reload()}>重新读取候选任务</button></div>
    {jobs.length>0&&<label className="revision-field">已保存的候选任务<select value={selected} disabled={busy||disabled} onChange={e=>{setSelected(e.target.value);setAck(false);setMaterial(false);}}><option value="">请选择一份计划或候选</option>{jobs.map(j=><option key={j.job_id} value={j.job_id}>第 {j.number} 份 · {states[j.status]}</option>)}</select></label>}
    {job&&<><h4>{states[job.status]}{job.simulation_only?' · 人工接口样例，不代表模型结果':''}</h4><p>{job.plan.scope}</p><p>模型：{job.plan.model} · 保守估算不超过 {money(job.plan.estimate_fen)} · 最多 {job.plan.max_output_tokens} 个输出 Token</p><p className="revision-muted">价格依据：{job.plan.price_version}。实际账单金额尚未知；已发送但结果未知时保留占用。</p>
      {(!basis||readOnly)&&<p className="revision-alert">当前正文或请求依据与计划不同，或请求已应用；可以查看历史，不能确认或载入。</p>}
      {job.status==='awaiting_confirmation'&&<><label className="revision-check"><input type="checkbox" checked={material} disabled={busy||disabled||blocked||!basis} onChange={e=>setMaterial(e.target.checked)}/>我确认本次文字有权使用、不含敏感信息，可在境内处理</label><label className="revision-check"><input type="checkbox" checked={ack} disabled={busy||disabled||blocked||!basis} onChange={e=>setAck(e.target.checked)}/>我已核对输入范围与估算，确认本次生成费用</label><button className="revision-primary" disabled={busy||disabled||blocked||!basis||!ack||!material||!data?.credential_present||!data.endpoint_allowed||!data.price_current||job.plan.expires_at<=now||job.plan.estimate_fen>data.available_fen} onClick={()=>void run('confirm')}>确认费用并生成候选</button></>}
      {job.status==='queued'&&<button disabled={busy||disabled||blocked||readOnly} onClick={()=>void run('advance')}>继续已确认任务</button>}
      {job.error&&<p className="revision-alert">{job.error}</p>}
      {job.result&&<><h4>候选文字</h4><pre className="revision-text">{job.result.value}</pre><h4>模型修改说明</h4><p>{job.result.explanation}</p><h4>模型对保留要素的说明（需人工核对）</h4><p>{job.result.preservation}</p><button disabled={busy||disabled||blocked||readOnly||!basis} onClick={()=>onLoad(job.result!.value)}>载入候选到修改框</button><p className="revision-muted">载入后仍需预览差异、核对保留要求并明确保存正式版本。</p></>}
    </>}
    {busy&&<p role="status">正在保存候选任务，请稍候…</p>}{error&&<p role="alert" className="revision-alert">{error} 输入保留，请先重新读取，不会自动提交。</p>}
  </section>;
}
