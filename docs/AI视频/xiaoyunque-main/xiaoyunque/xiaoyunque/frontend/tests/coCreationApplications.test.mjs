import fs from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadTypeScript} from './loadTypeScript.mjs';
const api=loadTypeScript('lib/coCreationApplications.ts',{setTimeout,clearTimeout});
const raw=JSON.parse(fs.readFileSync(new URL('./fixtures/co-creation-applications.json',import.meta.url)));
const f=()=>structuredClone(raw),P='comparison-demo';
const json=x=>new Response(JSON.stringify(x),{headers:{'Content-Type':'application/json'}});
test('actual HTTP fixture: compatible empty, confirmed and atomic applied contracts',()=>{
 for(const x of [raw.empty,raw.confirmed,raw.saved.data])assert.equal(api.parseApplications(x,P).workspace.model_enabled,false);
 assert.equal(api.parseApplications(raw.saved.data,P).applications[0].version_id,raw.saved.version_id);
 const input=api.applyInput(raw.confirmed,raw.input.intent_id,raw.input.value);assert.equal(JSON.stringify(input),JSON.stringify(raw.input));
 assert.equal(api.parseApplicationPreview(raw.preview,input,raw.confirmed).revision.changes.length,1);
});
const corrupt=[
 ['unknown root',x=>x.secret='never'],['foreign project',x=>x.workspace.project_id='foreign'],['different text',x=>x.revisions.token='a'.repeat(64)],
 ['different running',x=>x.revisions.running=true],['different field',x=>x.workspace.fields.script_generation[0].value='bad'],
 ['unknown association',x=>x.applications[0].digest='a'.repeat(64)],['wrong sequence',x=>x.applications[0].number=2],
 ['unknown version',x=>x.applications[0].version_id='b'.repeat(32)],['unknown intent',x=>x.applications[0].intent_id='b'.repeat(32)],
 ['bad date',x=>x.applications[0].created_at='bad'],['wrong date',x=>x.applications[0].created_at='2000-01-01T00:00:00Z'],
 ['reused request',x=>x.applications[0].request_id=x.workspace.records[0].request_id],['duplicate',x=>x.applications.push(x.applications[0])],
 ['unconfirmed',x=>x.workspace.decisions=[]],['wrong stage',x=>x.revisions.versions.at(-1).stage='storyboard'],
 ['wrong before',x=>x.revisions.versions.at(-1).changes[0].before='bad'],['wrong after',x=>x.revisions.versions.at(-1).changes[0].after='bad'],
 ['wrong path',x=>x.revisions.versions.at(-1).changes[0].path=['logline']],['wrong source',x=>x.revisions.versions.at(-1).source='external'],
];
for(const [name,mutate] of corrupt)test('corrupt association refused: '+name,()=>{const x=f().saved.data;mutate(x);assert.throws(()=>api.parseApplications(x,P));});
test('input uses confirmed object only; pending/stale/withdrawn/running/applied/no-change/size rejected',()=>{
 const x=f();assert.equal(api.applyInput(x.confirmed,x.input.intent_id,'').value,'');
 for(const state of ['pending','withdrawn','stale']){const d=structuredClone(x.confirmed);d.workspace.records[0].status=state;assert.throws(()=>api.applyInput(d,x.input.intent_id,'new'));}
 const running=structuredClone(x.confirmed);running.workspace.running=true;assert.throws(()=>api.applyInput(running,x.input.intent_id,'new'));
 for(const value of [x.confirmed.workspace.records[0].target.value,'字'.repeat(10001),null])assert.throws(()=>api.applyInput(x.confirmed,x.input.intent_id,value));
 assert.throws(()=>api.applyInput(x.saved.data,x.input.intent_id,'new'));assert.throws(()=>api.applyInput(x.confirmed,'b'.repeat(32),'new'));
});
test('preview binds co token, text token, intent, target, before and candidate',()=>{
 const x=f();for(const mutate of [p=>p.expected_token='a'.repeat(64),p=>p.intent_id='a'.repeat(32),p=>p.preview_token='bad',p=>p.revision.expected_token='a'.repeat(64),p=>p.revision.changes[0].before='bad',p=>p.revision.changes[0].after='bad',p=>p.revision.changes[0].path=['logline'],p=>p.revision.changes[0].after_present=false]){
  const pv=structuredClone(x.preview);mutate(pv);assert.throws(()=>api.parseApplicationPreview(pv,x.input,x.confirmed));
 }
});
test('bounded no-store GET/preview/explicit save use their actual endpoints',async()=>{
 const x=f(),calls=[];const fetcher=async(url,opts)=>{calls.push({url,opts});return json(calls.length===1?x.confirmed:calls.length===2?x.preview:x.saved);};
 await api.readApplications(P,fetcher);await api.previewApplication(P,x.input,x.confirmed,fetcher);const result=await api.saveApplication(P,x.request,fetcher);
 assert.equal(result.version_id,x.saved.version_id);assert.equal(calls.length,3);
 assert.equal(calls[0].url,`/api/project/${P}/co-creation/applications`);assert.equal(calls[0].opts.method,'GET');assert.equal(calls[1].url,calls[0].url+'/preview');assert.equal(calls[2].opts.body,JSON.stringify(x.request));
 for(const c of calls)assert.equal(c.opts.cache,'no-store');
});
for(const status of [404,409,422,503,500])test('safe failure without retry '+status,async()=>{
 let calls=0;await assert.rejects(api.saveApplication(P,f().request,async()=>{calls++;return new Response('private-body-secret',{status});}),e=>e.status===status&&e.uncertain===(status>=500)&&!e.message.includes('private'));assert.equal(calls,1);
});
test('lost write or malformed success is uncertain and read can recover request association',async()=>{
 const x=f();let calls=0;await assert.rejects(api.saveApplication(P,x.request,async()=>{calls++;throw new Error('private transport');}),e=>e.uncertain);assert.equal(calls,1);
 for(const mutate of [r=>r.application_id='a'.repeat(32),r=>r.version_id='a'.repeat(32),r=>r.data.applications[0].request_id='a'.repeat(32)]){const result=f().saved;mutate(result);await assert.rejects(api.saveApplication(P,x.request,async()=>json(result)),e=>e.uncertain);}
 const recovered=await api.readApplications(P,async()=>json(x.saved.data));assert.equal(recovered.applications.find(a=>a.request_id===x.request.request_id).version_id,x.saved.version_id);
});
test('deadline aborts one write and invalid project makes no request',async()=>{
 let callback,calls=0,cleared=0;const timed=loadTypeScript('lib/coCreationApplications.ts',{setTimeout:fn=>{callback=fn;return 1},clearTimeout:()=>{cleared++;}});
 const waiting=timed.saveApplication(P,raw.request,async(url,opts)=>{calls++;return new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(new Error('private'))));});callback();await assert.rejects(waiting,e=>e.uncertain);assert.equal(calls,1);assert.equal(cleared,1);
 await assert.rejects(api.readApplications('../private',async()=>{calls++;return json(raw.empty);}));assert.equal(calls,1);
});
