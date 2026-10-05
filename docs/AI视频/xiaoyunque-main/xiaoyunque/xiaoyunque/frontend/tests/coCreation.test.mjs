import fs from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadTypeScript} from './loadTypeScript.mjs';
const api=loadTypeScript('lib/coCreation.ts',{setTimeout,clearTimeout});
const raw=JSON.parse(fs.readFileSync(new URL('./fixtures/co-creation.json',import.meta.url)));
const f=()=>structuredClone(raw),P='comparison-demo';
const json=x=>new Response(JSON.stringify(x),{headers:{'Content-Type':'application/json'}});

test('real HTTP history supports empty, pending, confirmed, withdrawn with original request and decisions',()=>{
 for(const name of ['empty','saved','confirmed','withdrawn']){
  const value=name==='empty'?f().empty:f()[name].workspace;
  const result=api.parseCreation(value,P);
  assert.equal(result.model_enabled,false);
  if(name!=='empty'){assert.equal(result.records[0].message,raw.request.message);assert.equal(result.records[0].preserve,raw.request.preserve);}
 }
 assert.equal(api.parseCreation(f().confirmed.workspace,P).records[0].status,'confirmed');
 assert.equal(api.parseCreation(f().withdrawn.workspace,P).decisions.length,2);
});
const mutations=[
 ['project',x=>x.project_id='foreign'],['token',x=>x.token='bad'],['model enabled',x=>x.model_enabled=true],
 ['unknown root',x=>x.private_config='never'],['duplicate path',x=>x.fields.storyboard.push(x.fields.storyboard[0])],
 ['bad path',x=>x.records[0].target.path=[true]],['empty request',x=>x.records[0].message=' '],
 ['oversize request',x=>x.records[0].message='字'.repeat(10001)],['oversize preserve',x=>x.records[0].preserve='字'.repeat(2001)],
 ['duplicate intent',x=>x.records.push(x.records[0])],['wrong status',x=>x.records[0].status='pending'],
 ['nonsequential',x=>x.records[0].number=2],['unknown record',x=>x.records[0].request_digest='a'.repeat(64)],
 ['bad date',x=>x.records[0].created_at='never'],['missing target',x=>x.records[0].target.path=['removed']],
 ['changed target',x=>x.records[0].target.value='other'],
 ['foreign decision',x=>x.decisions[0].intent_id='1'.repeat(32)],['duplicate decision',x=>x.decisions.push(x.decisions[0])],
 ['request reused',x=>x.decisions[0].request_id=x.records[0].request_id],['unknown decision',x=>x.decisions[0].decision='execute'],
];
for(const [name,mutate] of mutations)test('reject corrupt contract '+name,()=>{
 const x=f().confirmed.workspace;mutate(x);assert.throws(()=>api.parseCreation(x,P));
});
test('stale history keeps original target, blocks false confirmed status, withdrawn remains withdrawn',()=>{
 const x=f().confirmed.workspace;x.text_token='a'.repeat(64);x.records[0].status='stale';x.fields.storyboard=[];
 assert.equal(api.parseCreation(x,P).records[0].status,'stale');
 x.records[0].status='confirmed';assert.throws(()=>api.parseCreation(x,P));
 const y=f().withdrawn.workspace;y.text_token='a'.repeat(64);assert.equal(api.parseCreation(y,P).records[0].status,'withdrawn');
});
test('request builder binds exact field and token and retains raw multiline words',()=>{
 const w=api.parseCreation(f().empty,P),field=w.fields.storyboard[2],id='f'.repeat(32);
 const input=api.requestInput(w,'storyboard',JSON.stringify(field.path),'  意图\n第二行  ','保留',id);
 assert.equal(input.message,'  意图\n第二行  ');assert.equal(input.expected_token,w.token);
 assert.deepEqual(JSON.parse(JSON.stringify(input.path)),field.path);
 for(const args of [
  [w,'storyboard','[]','意图','',id],[w,'storyboard',JSON.stringify(field.path),' ','',id],
  [w,'storyboard',JSON.stringify(field.path),'意图','字'.repeat(2001),id],
  [{...w,running:true},'storyboard',JSON.stringify(field.path),'意图','',id],
 ])assert.throws(()=>api.requestInput(...args));
});
test('one real-shaped GET and one POST use no-store and safe exact endpoint',async()=>{
 const calls=[],x=f();
 const workspace=await api.readCreation(P,async(url,opts)=>{calls.push({url,opts});return json(x.empty);});
 const saved=await api.saveCreation(P,x.request,async(url,opts)=>{calls.push({url,opts});return json(x.saved);});
 assert.equal(saved.intent_id,x.saved.intent_id);assert.equal(workspace.project_id,P);
 assert.equal(calls.length,2);assert.equal(calls[0].url,'/api/project/'+P+'/co-creation');
 assert.equal(calls[0].opts.method,'GET');assert.equal(calls[1].opts.method,'POST');
 assert.equal(calls[1].opts.body,JSON.stringify(x.request));assert.equal(calls[0].opts.cache,'no-store');
});
test('decision POST validates actual durable decision and matching intent',async()=>{
 const x=f(),result=await api.saveCreation(P,x.decision,async(url,opts)=>{
  assert.equal(url,'/api/project/'+P+'/co-creation/decision');assert.equal(opts.method,'POST');return json(x.confirmed);
 });
 assert.equal(result.workspace.records[0].status,'confirmed');
});
for(const status of [404,409,422,503,500])test('safe error '+status+' never leaks body or retries',async()=>{
 let calls=0;
 await assert.rejects(api.saveCreation(P,f().request,async()=>{calls++;return new Response('private-secret-and-body',{status});}),
  e=>!e.message.includes('private-secret')&&e.status===status&&e.uncertain===(status>=500));
 assert.equal(calls,1);
});
test('network loss after write is uncertain and recovery uses durable request identity',async()=>{
 let calls=0;
 await assert.rejects(api.saveCreation(P,f().request,async()=>{calls++;throw new Error('private transport');}),e=>e.uncertain&&!e.message.includes('private'));
 assert.equal(calls,1);
 assert.equal(api.recoverRequest(f().confirmed.workspace,raw.request.request_id),raw.saved.intent_id);
 assert.equal(api.recoverRequest(f().confirmed.workspace,raw.decision.request_id),raw.saved.intent_id);
 assert.equal(api.recoverRequest(f().empty,raw.request.request_id),null);
});
test('bad success after write is uncertain, cannot pass off another intent as saved',async()=>{
 for(const mutate of [x=>x.intent_id='a'.repeat(32),x=>x.workspace.records[0].message='foreign',x=>x.workspace.project_id='foreign']){
  const x=f().saved;mutate(x);
  await assert.rejects(api.saveCreation(P,f().request,async()=>json(x)),e=>e.uncertain);
 }
 const x=f().confirmed;x.workspace.decisions[0].request_id='a'.repeat(32);
 await assert.rejects(api.saveCreation(P,f().decision,async()=>json(x)),e=>e.uncertain);
});
test('deadline aborts exactly one call and read errors remain safe',async()=>{
 let callback,cleared=0,calls=0;
 const timed=loadTypeScript('lib/coCreation.ts',{setTimeout:fn=>{callback=fn;return 1},clearTimeout:()=>{cleared++}});
 const waiting=timed.saveCreation(P,f().request,async(url,opts)=>{calls++;return new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(new Error('private'))));});
 callback();await assert.rejects(waiting,e=>e.uncertain);assert.equal(calls,1);assert.equal(cleared,1);
 await assert.rejects(api.readCreation(P,async()=>{throw new Error('private');}),e=>!e.uncertain&&!e.message.includes('private'));
});
test('invalid project rejected before IO',async()=>{
 let calls=0;await assert.rejects(api.readCreation('../private',async()=>{calls++;return json(raw.empty)}));assert.equal(calls,0);
});
