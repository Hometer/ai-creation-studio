'use client';
import {useState} from 'react';
import {draftHistory,selectedDraft} from '@/lib/coCreationDraftHistory';
import type {Draft} from '@/lib/coCreationDrafts';

export default function CoCreationDraftHistory({drafts,intentId,disabled,readOnly,onLoad}:{drafts:Draft[];intentId:string;disabled:boolean;readOnly:boolean;onLoad:(draftId:string)=>void}){
  // Empty selection follows the latest record; an explicit selection stays on that immutable draft.
  const [selectedId,setSelectedId]=useState('');
  const rows=draftHistory(drafts,intentId),chosen=selectedDraft(drafts,intentId,selectedId),latest=rows.at(-1)?.draft;
  if(!rows.length)return null;
  const ordinal=rows.find(r=>r.draft.draft_id===chosen?.draft_id)?.ordinal;
  return <details className="creation-saved-draft creation-draft-history">
    <summary>已保存草稿 · 共 {rows.length} 次</summary>
    <label className="revision-field">选择已保存草稿<select className="creation-history-select" value={chosen?.draft_id??selectedId} disabled={disabled} onChange={e=>setSelectedId(e.target.value)}>
      {!chosen&&<option value={selectedId}>所选草稿暂不可用，请重新读取</option>}
      {[...rows].reverse().map(({draft,ordinal})=><option key={draft.draft_id} value={draft.draft_id}>第 {ordinal} 次 · {new Date(draft.created_at).toLocaleString('zh-CN')}{draft.draft_id===latest?.draft_id?' · 最近':''}</option>)}
    </select></label>
    {chosen?<><p className="revision-muted">正在查看第 {ordinal} 次已保存草稿{chosen.draft_id===latest?.draft_id?'（最近）':''}。选择只查看历史，编辑框保持原输入。</p><pre className="revision-text creation-history-value">{chosen.value||'（空白）'}</pre>
      {readOnly?<p className="revision-muted">当前请求不可继续修改，历史草稿仅供查看和复制。</p>:<><button disabled={disabled} onClick={()=>onLoad(chosen.draft_id)}>载入这份草稿到编辑框</button><p className="revision-muted">载入只改变本页输入；保存为新草稿后，刷新才恢复这次选择的内容。正式应用仍需重新预览确认。</p></>}
    </>:<p role="alert" className="revision-alert">所选历史草稿不可用；当前输入保持，请重新读取核对。</p>}
  </details>;
}
