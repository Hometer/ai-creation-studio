import fs from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadTypeScript} from './loadTypeScript.mjs';
const raw=JSON.parse(fs.readFileSync(new URL('./fixtures/creation-candidates.json',import.meta.url))),P='comparison-demo';
const clone=()=>structuredClone(raw);
const api=loadTypeScript('lib/creationCandidates.ts',{setTimeout,clearTimeout});
test('artificial candidate preserves scope, ceiling, unknown invoice and typed model result',()=>{
  const result=api.parseCandidates(raw,P);
  assert.equal(result.single_limit_fen,500);assert.equal(result.round_limit_fen,2000);
  assert.equal(result.jobs[0].simulation_only,true);assert.equal(result.jobs[0].actual_billed_fen,null);
  assert.equal(result.jobs[0].result.value,raw.jobs[0].result.value);
});
for(const [name,change] of [
  ['foreign project',x=>x.project_id='foreign'],['budget reset',x=>x.available_fen=2000],['larger total cap',x=>x.round_limit_fen=5000],
  ['larger single cap',x=>x.single_limit_fen=1000],['duplicate job',x=>x.jobs.push(x.jobs[0])],['unknown key',x=>x.secret='untrusted'],
  ['unknown invoice zero',x=>x.jobs[0].actual_billed_fen=0],['missing provenance',x=>delete x.jobs[0].simulation_only],
  ['foreign intent',x=>x.jobs[0].plan.intent_id='bad'],['different model',x=>x.jobs[0].plan.model='unpriced'],
  ['unbounded outputs',x=>x.jobs[0].plan.max_output_tokens=8192],['free estimate',x=>x.jobs[0].plan.estimate_fen=0],
  ['reservation mismatch',x=>x.jobs[0].reserved_fen++],['no checkpoint',x=>x.jobs[0].attempts=0],
  ['bad model field',x=>x.jobs[0].result.path=['title']],['empty candidate',x=>x.jobs[0].result.value=' '],
  ['too long candidate',x=>x.jobs[0].result.value='字'.repeat(10001)],['premature result',x=>x.jobs[0].status='submitting'],
  ['charge estimate over cap',x=>x.jobs[0].usage_estimate_fen=500],['unsafe id',x=>x.jobs[0].job_id='../other']
])test('refuse damaged candidate: '+name,()=>{const x=clone();change(x);assert.throws(()=>api.parseCandidates(x,P));});
test('read uses no-store GET; network failure sends once and is recoverable',async()=>{
  const calls=[],reader=loadTypeScript('lib/creationCandidates.ts',{setTimeout,clearTimeout,fetch:async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify(raw));}});
  await reader.readCandidates(P);assert.equal(calls.length,1);assert.equal(calls[0].options.method,'GET');assert.equal(calls[0].options.cache,'no-store');assert.ok(calls[0].options.signal);
  let attempts=0;const writer=loadTypeScript('lib/creationCandidates.ts',{setTimeout,clearTimeout,fetch:async()=>{attempts++;throw new Error('private-network-detail');}});
  await assert.rejects(writer.prepareCandidate(P,{expected_token:raw.expected_token,intent_id:raw.jobs[0].plan.intent_id,request_id:'a'.repeat(32)}),e=>e.uncertain===true&&!e.message.includes('private-network-detail'));
  assert.equal(attempts,1);
});
test('confirmation refuses stale, already submitted, foreign and unacknowledged plans before fetch',async()=>{
  let count=0;const writer=loadTypeScript('lib/creationCandidates.ts',{setTimeout,clearTimeout,fetch:async()=>{count++;throw new Error();}});
  const job=clone().jobs[0];job.status='awaiting_confirmation';job.attempts=0;job.reserved_fen=0;job.result=null;job.usage_estimate_fen=null;
  const input={expected_token:job.plan.expected_token,request_id:'b'.repeat(32),acknowledged:true,material_reviewed:true};
  for(const [j,b] of [[{...job,current:false},input],[raw.jobs[0],input],[job,{...input,acknowledged:1}],[job,{...input,material_reviewed:false}],[job,{...input,expected_token:'a'.repeat(64)}]])await assert.rejects(writer.confirmCandidate(P,j,b));
  await assert.rejects(writer.confirmCandidate('foreign',job,input));assert.equal(count,0);
});
test('only queued jobs can continue; succeeded or uncertain tasks never repeat a POST',async()=>{
  let count=0;const writer=loadTypeScript('lib/creationCandidates.ts',{setTimeout,clearTimeout,fetch:async()=>{count++;throw new Error();}});
  for(const state of ['succeeded','manual_review','submitting']){
    const j=clone().jobs[0];j.status=state;if(state!=='succeeded'){j.result=null;j.usage_estimate_fen=null;}
    await assert.rejects(writer.advanceCandidate(P,j));
  }
  assert.equal(count,0);
});
