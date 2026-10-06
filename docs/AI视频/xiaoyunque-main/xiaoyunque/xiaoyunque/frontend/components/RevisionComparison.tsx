'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeftRight, GitCompareArrows } from 'lucide-react';
import { STAGES, pathKey, type Stage, type Workspace } from '@/lib/contentRevisions';
import { comparisonInput, comparisonSelection, readComparison, type Comparison, type ValueType } from '@/lib/revisionComparison';

import {LOCATION_EVENT} from '@/lib/restoreHistory';

const sources={baseline:'首次归档',external:'原入口更新归档',edit:'手动修改',restore:'恢复旧版'};
const kinds: Record<ValueType,string>={text:'文字',number:'数字',boolean:'是／否',object:'对象结构',array:'列表结构',null:'空值',missing:'无此字段'};

export default function RevisionComparison({workspace,disabled}:{workspace:Workspace;disabled:boolean}) {
  const [stage,setStage]=useState<Stage>('script_generation');
  const [from,setFrom]=useState('');
  const [to,setTo]=useState('');
  const [result,setResult]=useState<Comparison|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const generation=useRef(0);
  const cancelRequests=useCallback(()=>{++generation.current;},[]);
  const compare=useCallback(async(left:string,right:string)=>{
    const request=++generation.current;setResult(null);setError('');setBusy(true);
    try { const data=await readComparison(workspace,left,right);if(request===generation.current)setResult(data); }
    catch(e) { if(request===generation.current)setError(e instanceof Error?e.message:'对比未能读取，请重试'); }
    finally { if(request===generation.current)setBusy(false); }
  },[workspace]);
  const defaults=useCallback((selected:Stage)=>{
    const versions=workspace.versions.filter(v=>v.stage===selected);
    return {from:versions[0]?.version_id??'',to:versions.at(-1)?.version_id??''};
  },[workspace]);
  useEffect(()=>{
    const readAddress=()=>{
      ++generation.current;setResult(null);setError('');setBusy(false);
      try {
        const pair=comparisonSelection(workspace,location.search);
        if(pair){setStage(pair.stage);setFrom(pair.from);setTo(pair.to);void compare(pair.from,pair.to);}
        else {
          const initial:Stage=workspace.versions.some(v=>v.stage==='script_generation')||!workspace.versions.some(v=>v.stage==='storyboard')?'script_generation':'storyboard';
          const pair=defaults(initial);setStage(initial);setFrom(pair.from);setTo(pair.to);
        }
      } catch(e) { setFrom('');setTo('');setError(e instanceof Error?e.message:'地址中的版本不可读取，请重新选择'); }
    };
    const timer=window.setTimeout(readAddress,0);window.addEventListener('popstate',readAddress);window.addEventListener(LOCATION_EVENT,readAddress);
    return()=>{cancelRequests();window.clearTimeout(timer);window.removeEventListener('popstate',readAddress);window.removeEventListener(LOCATION_EVENT,readAddress);};
  },[workspace,compare,defaults,cancelRequests]);
  const address=(left:string,right:string)=>{
    const url=new URL(location.href);
    if(left&&right){url.searchParams.set('compare_from',left);url.searchParams.set('compare_to',right);}
    else {url.searchParams.delete('compare_from');url.searchParams.delete('compare_to');}
    history.replaceState(history.state,'',url);
  };
  const select=(selected:Stage,left:string,right:string)=>{
    ++generation.current;setBusy(false);setError('');setResult(null);setStage(selected);setFrom(left);setTo(right);address(left,right);
  };
  const run=(left=from,right=to)=>{
    if(disabled||busy)return;
    try {comparisonInput(workspace,left,right);address(left,right);void compare(left,right);}
    catch(e){setError(e instanceof Error?e.message:'请选择两个版本');}
  };
  const versions=workspace.versions.filter(v=>v.stage===stage);
  const left=workspace.versions.find(v=>v.version_id===result?.before_version_id);
  const right=workspace.versions.find(v=>v.version_id===result?.after_version_id);
  const label=(id:string)=>{const v=versions.find(v=>v.version_id===id);return v?'v'+v.number+' · '+sources[v.source]:'';};
  return <section id="revision-comparison" tabIndex={-1} className="revision-card revision-compare" aria-label="两版文字对比">
    <h2><GitCompareArrows size={20}/>两版文字对比</h2>
    <p className="revision-muted">任选同一阶段的两个归档版本，查看变化。对比只读，不修改当前文字或编辑草稿。</p>
    <div className="revision-tabs">{(Object.keys(STAGES) as Stage[]).map(s=><button key={s} aria-pressed={s===stage} disabled={disabled||busy} onClick={()=>{const pair=defaults(s);select(s,pair.from,pair.to);}}>{STAGES[s]}版本</button>)}</div>
    <div className="revision-compare-controls">
      <label className="revision-field">左版 · 对比起点<select aria-label="对比起点版本" disabled={disabled||busy||!versions.length} value={from} onChange={e=>select(stage,e.target.value,to)}><option value="">请选择版本</option>{versions.map(v=><option key={v.version_id} value={v.version_id}>{label(v.version_id)}{v.current?' · 当前文字':''}</option>)}</select></label>
      <label className="revision-field">右版 · 对比终点<select aria-label="对比终点版本" disabled={disabled||busy||!versions.length} value={to} onChange={e=>select(stage,from,e.target.value)}><option value="">请选择版本</option>{versions.map(v=><option key={v.version_id} value={v.version_id}>{label(v.version_id)}{v.current?' · 当前文字':''}</option>)}</select></label>
    </div>
    <div className="revision-actions"><button disabled={disabled||busy||!from||!to} onClick={()=>{const left=to,right=from;select(stage,left,right);run(left,right);}}><ArrowLeftRight size={16}/>交换左右</button><button className="revision-primary" disabled={disabled||busy||!from||!to} onClick={()=>run()}>{busy?'正在对比…':'查看两版差异'}</button></div>
    {!versions.length&&<p className="revision-empty">此阶段还没有归档版本。保存文字后再来对比；读取不会创建版本。</p>}
    {busy&&<p role="status" className="revision-muted">正在读取所选版本的差异…</p>}
    {error&&<p role="alert" className="revision-alert">{error}</p>}
    {result&&left&&right&&<div className="revision-compare-result">
      <h3>{STAGES[result.stage]} · v{left.number} → v{right.number}</h3>
      <p className="revision-muted">左版：{sources[left.source]} · 右版：{sources[right.source]}</p>
      {!result.changes.length?<p role="status" className="revision-notice">{left.version_id===right.version_id?'选择的是同一版本，内容相同。':'这两版内容相同，没有字段或结构差异；版本记录仍分别保留。'}</p>:<>
        <p role="status">共 {result.changes.length} 处字段或结构变化</p>
        <p className="revision-muted">章节或镜头增删可能整体展示列表变化，条目数不是修改字数。</p>
        {result.changes.map(c=><div className="revision-change" key={pathKey(c.path)}><p>{c.label}</p><div>{(['before','after'] as const).map(side=><section key={side}><h4>{side==='before'?'左版 v'+left.number:'右版 v'+right.number}{c.before_type!==c.after_type?' · '+kinds[c[side+'_type' as 'before_type'|'after_type']]:''}</h4><pre>{c[side+'_present' as 'before_present'|'after_present']===false?'（此版无该字段）':c[side]||'（空白文字）'}</pre></section>)}</div></div>)}
        <h4>可能需要核对的镜头</h4><p className="revision-muted">{result.stage==='script_generation'?'按读取时的当前分镜提示；':'按两份分镜的前后目标提示；'}不表示媒体已重做或已获执行批准。</p>
        {result.impacts.length?<ul className="revision-impacts">{result.impacts.map((i,n)=><li key={n}><strong>{i.segment_id}{i.shot_number===null?'':' · 镜头 '+i.shot_number}</strong><span>{i.reason}</span></li>)}</ul>:<p className="revision-muted">暂无可关联镜头，后续制作前仍请核对文字。</p>}
      </>}
    </div>}
  </section>;
}
