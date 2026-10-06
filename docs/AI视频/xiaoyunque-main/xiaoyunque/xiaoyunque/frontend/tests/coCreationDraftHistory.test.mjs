import fs from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadTypeScript} from './loadTypeScript.mjs';
const api=loadTypeScript('lib/coCreationDraftHistory.ts',{setTimeout,clearTimeout});
const draftApi=loadTypeScript('lib/coCreationDrafts.ts',{setTimeout,clearTimeout});
const raw=JSON.parse(fs.readFileSync(new URL('./fixtures/co-creation-drafts.json',import.meta.url)));
const f=()=>structuredClone(raw.second.result),id=raw.request.intent_id;
test('request history has local ordinal despite interleaved global numbering and no mutations',()=>{
 const x=f(),rows=x.drafts,foreign={...rows[0],intent_id:x.data.workspace.records[1].intent_id,draft_id:'a'.repeat(32),number:2};rows[1].number=3;rows.splice(1,0,foreign);const before=JSON.stringify(rows);
 assert.deepEqual(JSON.parse(JSON.stringify(api.draftHistory(rows,id))).map(r=>[r.ordinal,r.draft.number]),[[1,1],[2,3]]);
 assert.equal(api.draftHistory(rows,foreign.intent_id).length,1);assert.equal(api.draftHistory(rows,'foreign').length,0);assert.equal(JSON.stringify(rows),before);
});
test('empty selection follows latest but exact older selection survives a later append',()=>{
 const x=f(),old=x.drafts[0],latest=x.drafts[1];assert.equal(api.selectedDraft(x.drafts,id,'').draft_id,latest.draft_id);assert.equal(api.selectedDraft(x.drafts,id,old.draft_id).draft_id,old.draft_id);
 const appended={...latest,draft_id:'a'.repeat(32),value:'another'};x.drafts.push(appended);assert.equal(api.selectedDraft(x.drafts,id,old.draft_id).draft_id,old.draft_id);assert.equal(api.selectedDraft(x.drafts,id,'').draft_id,appended.draft_id);
 assert.equal(api.selectedDraft(x.drafts,id,'bad'),undefined);assert.equal(api.selectedDraft(x.drafts,x.data.workspace.records[1].intent_id,old.draft_id),undefined);assert.equal(api.selectedDraft([],id,''),undefined);
});
test('restoring older saved text is local only, immutable and becomes an explicit new draft input',()=>{
 const x=f(),old=x.drafts[0],before=JSON.stringify(x),r=api.draftRestoration(x.data,x.drafts,id,old.draft_id,x.drafts[1].value);
 assert.equal(r.value,old.value);assert.equal(r.ordinal,1);assert.equal(r.requiresConfirmation,false);assert.equal(JSON.stringify(x),before);
 const input=draftApi.draftInput(x.data,id,r.value,'f'.repeat(32));assert.equal(input.expected_token,x.data.workspace.token);assert.equal(input.value,old.value);assert.notEqual(input.request_id,old.request_id);
});
for(const [current,confirm] of [['unsaved other text',true],['selected text',false],['latest text',false]])test('replace confirmation depends on unsaved distinct input: '+current,()=>{
 const x=f(),old=x.drafts[0],value=current==='selected text'?old.value:current==='latest text'?x.drafts[1].value:current;
 assert.equal(api.draftRestoration(x.data,x.drafts,id,old.draft_id,value).requiresConfirmation,confirm);
});
test('empty historical candidate restores exact empty string and demands confirmation for unsaved input',()=>{
 const x=f();x.drafts[0].value='';const r=api.draftRestoration(x.data,x.drafts,id,x.drafts[0].draft_id,'unsaved');assert.equal(r.value,'');assert.equal(r.requiresConfirmation,true);
});
for(const status of ['pending','withdrawn','stale'])test('unavailable request can be viewed but cannot restore: '+status,()=>{
 const x=f();x.data.workspace.records[0].status=status;assert.equal(api.draftHistory(x.drafts,id).length,2);assert.throws(()=>api.draftRestoration(x.data,x.drafts,id,x.drafts[0].draft_id,'new'),e=>e.status===422);
});
test('running and applied requests do not allow history to become a write candidate',()=>{
 const x=f();x.data.workspace.running=true;assert.throws(()=>api.draftRestoration(x.data,x.drafts,id,x.drafts[0].draft_id,'new'));
 x.data.workspace.running=false;x.data.applications.push({intent_id:id});assert.throws(()=>api.draftRestoration(x.data,x.drafts,id,x.drafts[0].draft_id,'new'));
});
test('foreign, missing, altered-basis and invalid saved text are refused without fallback',()=>{
 const x=f(),old=x.drafts[0],other=x.data.workspace.records[1].intent_id;
 for(const [intent,draft] of [[other,old.draft_id],[id,'a'.repeat(32)],['a'.repeat(32),old.draft_id]])assert.throws(()=>api.draftRestoration(x.data,x.drafts,intent,draft,'new'));
 old.text_token='a'.repeat(64);assert.throws(()=>api.draftRestoration(x.data,x.drafts,id,old.draft_id,'new'));
 old.text_token=x.data.workspace.records[0].text_token;old.value='字'.repeat(10001);assert.throws(()=>api.draftRestoration(x.data,x.drafts,id,old.draft_id,'new'));
});
