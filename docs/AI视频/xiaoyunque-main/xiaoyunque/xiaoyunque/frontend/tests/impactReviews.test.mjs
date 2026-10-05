import fs from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadTypeScript} from './loadTypeScript.mjs';
const api=loadTypeScript('lib/impactReviews.ts',{setTimeout,clearTimeout});
const raw=JSON.parse(fs.readFileSync(new URL('./fixtures/impact-reviews.json',import.meta.url)));
const fixture=()=>structuredClone(raw),plain=x=>JSON.parse(JSON.stringify(x));
test('actual HTTP artificial fixture keeps independent version impacts and append-only decisions',()=>{
 const f=fixture(),w=api.parseReviews(f.initial,'impact-review-demo');assert.equal(w.items.length,3);assert.equal(w.records.length,0);
 assert.deepEqual(plain(api.reviewInput(w,f.input.item_id,'retain',f.input.note,f.input.request_id)),f.input);
 assert.equal(api.parseReviews(f.saved.workspace,'impact-review-demo').items[0].status,'retained');
});
for(const [name,mutate] of [
 ['wrong project',w=>w.project_id='foreign'],['wrong token',w=>w.token='bad'],['bad running',w=>w.running='false'],
 ['duplicate item',w=>w.items.push(w.items[0])],['unknown state',w=>w.items[0].status='done'],['fake retained',w=>w.items[1].status='retained'],
 ['missing record',w=>w.records=[]],['unknown target',w=>w.records[0].item_id='f'.repeat(64)],['repeat request',w=>w.records.push({...w.records[0],number:2,review_id:'b'.repeat(32)})],
 ['bad note',w=>w.records[0].note=' '],['wrong latest',w=>w.items[0].latest_review_id='f'.repeat(32)],['unsafe shot',w=>w.items[0].shot_number=true],
 ['untracked retained',w=>w.untracked_stages=['storyboard']],['stale retained',w=>w.records[0].applicable=false],['removed retained',w=>w.items[0].target_exists=false],
 ['bad fields',w=>w.items[0].current_fields[0].value={}],['bad difference',w=>w.sources[0].changes[0].before_present='no'],
])test(`unsafe review response rejected: ${name}`,()=>{const f=fixture();mutate(f.saved.workspace);assert.throws(()=>api.parseReviews(f.saved.workspace,'impact-review-demo'));});
test('review decisions refuse missing targets, running, blank notes and unsupported actions',()=>{
 const f=fixture(),w=api.parseReviews(f.initial,'impact-review-demo'),id=w.items[0].item_id;
 for(const note of [' ','x'.repeat(1001)])assert.throws(()=>api.reviewInput(w,id,'retain',note,'a'.repeat(32)));
 assert.throws(()=>api.reviewInput(w,id,'generate','note','a'.repeat(32)));w.running=true;assert.throws(()=>api.reviewInput(w,id,'retain','note','a'.repeat(32)));w.running=false;
 w.items[0].target_exists=false;assert.throws(()=>api.reviewInput(w,id,'retain','note','a'.repeat(32)));
 assert.equal(api.reviewInput(w,id,'follow_up','missing target needs attention','a'.repeat(32)).decision,'follow_up');
});
test('human HTTP uses readonly list and exactly one acknowledged ledger write',async()=>{
 const f=fixture(),calls=[],fetcher=async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify(options.method==='GET'?f.initial:f.saved));};
 const w=await api.readReviews('impact-review-demo',fetcher),p=api.reviewInput(w,f.input.item_id,'retain',f.input.note,f.input.request_id);
 const saved=await api.saveReview('impact-review-demo',p,fetcher);assert.equal(saved.workspace.records.length,1);assert.equal(calls.length,2);
 assert.ok(calls.every(c=>c.url.endsWith('/content-revisions/reviews')));assert.equal(JSON.parse(calls[1].options.body).acknowledged,true);
});
test('lost ledger reply never retries or assumes review succeeded',async()=>{
 const f=fixture();let count=0;await assert.rejects(()=>api.saveReview('impact-review-demo',f.input,async()=>{count++;throw Error('private exception');}),e=>e.uncertain&&!e.message.includes('private'));assert.equal(count,1);
 const restored=await api.readReviews('impact-review-demo',async()=>new Response(JSON.stringify(f.saved.workspace)));assert.equal(restored.records.length,1);
});
for(const [name,mutate] of [['note',f=>f.saved.workspace.records[0].note='different'],['request',f=>f.saved.workspace.records[0].request_id='b'.repeat(32)],['review',f=>f.saved.review_id='b'.repeat(32)]])test(`write reply bound to actual submitted ${name}`,async()=>{
 const f=fixture();mutate(f);await assert.rejects(()=>api.saveReview('impact-review-demo',f.input,async()=>new Response(JSON.stringify(f.saved))),e=>e.uncertain);
});
for(const status of [409,503])test(`safe ${status} errors preserve human recovery`,async()=>{
 const f=fixture();await assert.rejects(()=>api.saveReview('impact-review-demo',f.input,async()=>new Response('private traceback',{status})),e=>e.status===status&&!e.message.includes('traceback'));
});
