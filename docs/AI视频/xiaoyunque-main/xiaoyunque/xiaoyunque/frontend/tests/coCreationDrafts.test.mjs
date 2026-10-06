import fs from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadTypeScript} from './loadTypeScript.mjs';
const api=loadTypeScript('lib/coCreationDrafts.ts',{setTimeout,clearTimeout});
const raw=JSON.parse(fs.readFileSync(new URL('./fixtures/co-creation-drafts.json',import.meta.url))),P='comparison-demo';
const f=()=>structuredClone(raw),json=x=>new Response(JSON.stringify(x));
test('actual HTTP fixtures preserve empty, saved, sequential drafts and unchanged text versions',()=>{
 const empty=api.parseDrafts(raw.empty,P),saved=api.parseDrafts(raw.saved.result,P),second=api.parseDrafts(raw.second.result,P);
 assert.equal(empty.drafts.length,0);assert.equal(saved.drafts.length,1);assert.equal(second.drafts.length,2);
 assert.equal(api.latestDraft(second.drafts,raw.request.intent_id).draft_id,raw.second.draft_id);
 assert.equal(saved.data.revisions.token,empty.data.revisions.token);assert.equal(JSON.stringify(saved.data.revisions.versions),JSON.stringify(empty.data.revisions.versions));
 assert.notEqual(saved.data.workspace.token,empty.data.workspace.token);
 assert.equal(JSON.stringify(api.draftInput(empty.data,raw.request.intent_id,raw.request.value,raw.request.request_id)),JSON.stringify(raw.request));
});
const corrupt=[['unknown root',x=>x.secret='bad'],['unknown draft key',x=>x.drafts[0].secret='bad'],['duplicate',x=>x.drafts.push(x.drafts[0])],['sequence',x=>x.drafts[0].number=2],['unknown intent',x=>x.drafts[0].intent_id='a'.repeat(32)],['basis',x=>x.drafts[0].text_token='a'.repeat(64)],['request reused',x=>x.drafts[0].request_id=x.data.workspace.records[0].request_id],['bad id',x=>x.drafts[0].draft_id='bad'],['bad date',x=>x.drafts[0].created_at='bad'],['before confirm',x=>x.drafts[0].created_at='2000-01-01T00:00:00Z'],['unconfirmed',x=>x.data.workspace.decisions=[]],['value type',x=>x.drafts[0].value=null],['oversize',x=>x.drafts[0].value='字'.repeat(10001)],['foreign project',x=>x.data.workspace.project_id='foreign']];
for(const [name,mutate] of corrupt)test('damaged response refused: '+name,()=>{const x=f().saved.result;mutate(x);assert.throws(()=>api.parseDrafts(x,P));});
test('draft inputs allow empty, original and bounded multiline; guard invalid and unavailable intents',()=>{
 const data=f().empty.data,id=raw.request.intent_id,r='f'.repeat(32);
 for(const value of ['',data.workspace.records[0].target.value,'字'.repeat(10000),'人工\n草稿'])assert.equal(api.draftInput(data,id,value,r).value,value);
 for(const value of [null,42,'字'.repeat(10001)])assert.throws(()=>api.draftInput(data,id,value,r));
 for(const state of ['pending','stale','withdrawn']){const x=structuredClone(data);x.workspace.records[0].status=state;assert.throws(()=>api.draftInput(x,id,'draft',r));}
 const running=structuredClone(data);running.workspace.running=true;assert.throws(()=>api.draftInput(running,id,'draft',r));
 assert.throws(()=>api.draftInput(data,'a'.repeat(32),'draft',r));assert.throws(()=>api.draftInput(data,id,'draft','bad'));
});
test('GET and explicit POST use bounded no-store endpoint and one request',async()=>{
 const calls=[],fetcher=async(url,opts)=>{calls.push({url,opts});return json(calls.length===1?raw.empty:raw.saved);};
 await api.readDrafts(P,fetcher);const saved=await api.saveDraft(P,raw.request,fetcher);assert.equal(saved.draft_id,raw.saved.draft_id);assert.equal(calls.length,2);
 for(const c of calls){assert.equal(c.url,`/api/project/${P}/co-creation/drafts`);assert.equal(c.opts.cache,'no-store');assert.ok(c.opts.signal);}
 assert.equal(calls[0].opts.method,'GET');assert.equal(calls[1].opts.method,'POST');assert.equal(calls[1].opts.body,JSON.stringify(raw.request));
});
for(const status of [404,409,422,503,500])test('safe server failure, single write: '+status,async()=>{let calls=0;await assert.rejects(api.saveDraft(P,raw.request,async()=>{calls++;return new Response('private-body',{status});}),e=>e.status===status&&e.uncertain===(status>=500)&&!e.message.includes('private'));assert.equal(calls,1);});
test('lost receipt and malformed success are uncertain; read recovers exact request without resend',async()=>{
 let calls=0;await assert.rejects(api.saveDraft(P,raw.request,async()=>{calls++;throw new Error('private transport');}),e=>e.uncertain&&!e.message.includes('private'));assert.equal(calls,1);
 for(const mutate of [x=>x.draft_id='a'.repeat(32),x=>x.result.drafts[0].value='other',x=>x.result.drafts[0].request_id='a'.repeat(32),x=>x.result.drafts[0].intent_id=x.result.data.workspace.records[1].intent_id,x=>x.secret='bad']){const x=f().saved;mutate(x);await assert.rejects(api.saveDraft(P,raw.request,async()=>json(x)),e=>e.uncertain);}
 const read=await api.readDrafts(P,async()=>json(raw.saved.result));assert.equal(read.drafts.find(d=>d.request_id===raw.request.request_id).draft_id,raw.saved.draft_id);
});
test('20 second deadline aborts one request; unsafe project sends none',async()=>{
 let callback,calls=0,cleared=0;const timed=loadTypeScript('lib/coCreationDrafts.ts',{setTimeout:(fn,ms)=>{assert.equal(ms,20000);callback=fn;return 1;},clearTimeout:()=>cleared++});
 const waiting=timed.saveDraft(P,raw.request,async(url,opts)=>{calls++;return new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(new Error('private timeout'))));});callback();await assert.rejects(waiting,e=>e.uncertain);assert.equal(calls,1);assert.equal(cleared,1);
 await assert.rejects(api.readDrafts('../private',async()=>{calls++;return json(raw.empty);}));assert.equal(calls,1);
});
