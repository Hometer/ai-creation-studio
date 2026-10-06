import {RevisionError} from './contentRevisions';
import {draftInput,latestDraft,type Draft} from './coCreationDrafts';
import type {ApplicationWorkspace} from './coCreationApplications';

export function draftHistory(drafts:Draft[],intentId:string){
  return drafts.filter(d=>d.intent_id===intentId).map((draft,index)=>({draft,ordinal:index+1}));
}
export function selectedDraft(drafts:Draft[],intentId:string,draftId:string){
  return draftId?drafts.find(d=>d.intent_id===intentId&&d.draft_id===draftId):latestDraft(drafts,intentId);
}
/** Restore only an exact saved draft from this confirmed request; never performs I/O. */
export function draftRestoration(data:ApplicationWorkspace,drafts:Draft[],intentId:string,draftId:string,currentValue:string){
  const draft=drafts.find(d=>d.intent_id===intentId&&d.draft_id===draftId);
  const intent=data.workspace.records.find(r=>r.intent_id===intentId);
  if(!draft||!intent||draft.text_token!==intent.text_token)throw new RevisionError('所选草稿不属于当前请求或依据，请重新读取核对',422);
  draftInput(data,intentId,draft.value,draft.request_id);
  const latest=latestDraft(drafts,intentId);
  return {value:draft.value,ordinal:draftHistory(drafts,intentId).find(r=>r.draft.draft_id===draftId)!.ordinal,
    requiresConfirmation:currentValue!==draft.value&&currentValue!==(latest?.value??intent.target.value)};
}
