import fs from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadTypeScript} from './loadTypeScript.mjs';
const globals={setTimeout,clearTimeout,URLSearchParams};
const api=loadTypeScript('lib/revisionComparison.ts',globals),revisions=loadTypeScript('lib/contentRevisions.ts',globals);
const raw=JSON.parse(fs.readFileSync(new URL('./fixtures/revision-comparison.json',import.meta.url)));
const fixture=()=>structuredClone(raw),plain=x=>JSON.parse(JSON.stringify(x));
const workspace=()=>revisions.parseWorkspace(fixture().workspace,'revision-demo');
test('real HTTP fixture retains type, missing and null changes without migrating history',()=>{
 const f=fixture(),w=workspace(),result=api.parseComparison(f.comparison,w,f.input);
 assert.equal(result.changes.length,4);
 const type=result.changes.find(c=>c.path[0]==='extra');assert.equal(type.before,type.after);assert.equal(type.before_type,'text');assert.equal(type.after_type,'number');
 assert.equal(result.changes.find(c=>c.path[0]==='remove').after_type,'missing');assert.equal(result.changes.find(c=>c.path[0]==='added').after_type,'null');
});
test('old summary accepts genuine ambiguous JSON render, ordinary identical text stays invalid',()=>{
 const f=fixture();assert.equal(revisions.parseWorkspace(f.workspace,'revision-demo').versions.length,4);
 const bad=fixture().workspace;bad.versions.at(-1).changes[0]={path:['title'],before:'same plain text',after:'same plain text'};
 assert.throws(()=>revisions.parseWorkspace(bad,'revision-demo'));
});
test('reverse comparison and same-version empty result remain bound to selected direction',()=>{
 const f=fixture(),w=workspace(),p={...f.input,before_version_id:f.input.after_version_id,after_version_id:f.input.before_version_id};
 assert.equal(api.parseComparison(f.reverse,w,p).before_version_id,p.before_version_id);
 assert.equal(api.parseComparison(f.same,w,{...f.input,after_version_id:f.input.before_version_id}).changes.length,0);
 assert.throws(()=>api.parseComparison(f.reverse,w,f.input));
});
for(const [name,mutate] of [
 ['foreign project',x=>x.project_id='other'],['changed context',x=>x.token='f'.repeat(64)],['foreign stage',x=>x.stage='storyboard'],
 ['foreign from',x=>x.before_version_id='f'.repeat(32)],['missing right',x=>delete x.after_version_id],['missing list',x=>delete x.changes],
 ['duplicate diff',x=>x.changes.push(x.changes[0])],['missing type',x=>delete x.changes[0].before_type],['unknown type',x=>x.changes[0].after_type='model'],
 ['presence mismatch',x=>x.changes[0].before_present=true],['missing presence',x=>delete x.changes[0].before_present],['invalid path',x=>x.changes[0].path=[true]],
 ['missing label',x=>x.changes[0].label=''],['incorrect number rendering',x=>{const c=x.changes.find(c=>c.path[0]==='extra');c.after='secret string';}],
 ['erased type difference',x=>{const c=x.changes.find(c=>c.path[0]==='extra');c.before_type=c.after_type;}],
 ['bad shot',x=>x.impacts[0].shot_number=-1],['unknown impact',x=>x.impacts[0].segment_id=''],['over limit',x=>x.changes=Array(2001).fill(x.changes[0])]
])test('reject incorrect comparison: '+name,()=>{const f=fixture();mutate(f.comparison);assert.throws(()=>api.parseComparison(f.comparison,workspace(),f.input));});
test('deep read-only structure differences are not restricted to editable paths',()=>{
 const f=fixture();f.comparison.changes[0].path=Array(15).fill('nested');assert.equal(api.parseComparison(f.comparison,workspace(),f.input).changes[0].path.length,15);
});
test('empty comparison cannot invent mirror impacts or differences in the same version',()=>{
 const f=fixture(),w=workspace(),input={...f.input,after_version_id:f.input.before_version_id};
 f.same.impacts=f.comparison.impacts;assert.throws(()=>api.parseComparison(f.same,w,input));
 assert.throws(()=>api.parseComparison({...f.comparison,after_version_id:input.after_version_id},w,input));
});
test('address restores both versions and leaves absent comparison optional',()=>{
 const f=fixture(),w=workspace();assert.equal(api.comparisonSelection(w,'?version=other'),undefined);
 assert.deepEqual(plain(api.comparisonSelection(w,'?compare_from='+f.input.before_version_id+'&compare_to='+f.input.after_version_id)),{from:f.input.before_version_id,to:f.input.after_version_id,stage:'script_generation'});
});
for(const [name,search] of [
 ['one side','?compare_from=a'],['unknown versions','?compare_from='+'f'.repeat(32)+'&compare_to='+'a'.repeat(32)],
 ['duplicate','?compare_from=a&compare_from=b&compare_to=c'],['cross stage','?compare_from='+raw.workspace.versions[1].version_id+'&compare_to='+raw.input.after_version_id]
])test('invalid address is rejected rather than silently changing selected pair: '+name,()=>assert.throws(()=>api.comparisonSelection(workspace(),search)));
test('comparison is exactly one GET with no body, saving or retries',async()=>{
 const f=fixture(),w=workspace(),calls=[];
 const result=await api.readComparison(w,f.input.before_version_id,f.input.after_version_id,async(url,opts)=>{calls.push({url,opts});return new Response(JSON.stringify(f.comparison));});
 assert.equal(calls.length,1);assert.equal(calls[0].opts.method,'GET');assert.equal(calls[0].opts.body,undefined);assert.equal(calls[0].opts.cache,'no-store');assert.equal(result.changes.length,4);
 const query=new URLSearchParams(calls[0].url.split('?')[1]);assert.equal(query.get('expected_token'),w.token);assert.equal(query.get('before_version_id'),f.input.before_version_id);
 let count=0;await assert.rejects(()=>api.readComparison(w,'missing',f.input.after_version_id,async()=>{count++;}));assert.equal(count,0);
});
test('failed GET does not expose error body or retry',async()=>{
 const f=fixture(),w=workspace();for(const status of [404,409,422,503]){let calls=0;await assert.rejects(()=>api.readComparison(w,f.input.before_version_id,f.input.after_version_id,async()=>{calls++;return new Response('private trace',{status});}),e=>e.status===status&&!e.uncertain&&!e.message.includes('private trace'));assert.equal(calls,1);}
});
test('timeout cancels one read; later explicit GET recovers',async()=>{
 const f=fixture(),w=workspace();let calls=0;const quick=loadTypeScript('lib/revisionComparison.ts',{...globals,setTimeout:fn=>setTimeout(fn,1)});
 await assert.rejects(()=>quick.readComparison(w,f.input.before_version_id,f.input.after_version_id,async(_,opts)=>{calls++;return new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(new Error('private abort'))));}),e=>e.status===0&&!e.uncertain&&!e.message.includes('private abort'));
 assert.equal(calls,1);assert.equal((await api.readComparison(w,f.input.before_version_id,f.input.after_version_id,async()=>new Response(JSON.stringify(f.comparison)))).changes.length,4);
});
