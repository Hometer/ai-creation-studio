import fs from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadTypeScript} from './loadTypeScript.mjs';
const api=loadTypeScript('lib/contentRevisions.ts',{setTimeout,clearTimeout});
const raw=JSON.parse(fs.readFileSync(new URL('./fixtures/revisions.json',import.meta.url)));
const fixture=()=>structuredClone(raw);const plain=x=>JSON.parse(JSON.stringify(x));
test('real offline HTTP fixture supports edit preview and preserved history',()=>{
 const f=fixture(),w=api.parseWorkspace(f.before,'revision-demo');assert.equal(w.versions.length,0);
 assert.deepEqual(plain(api.editInput(w,'storyboard',JSON.stringify(f.input.path),f.input.value)),f.input);
 assert.equal(api.parsePreview(f.preview,f.input).impacts.length,1);
 const s=api.parseWorkspace(f.saved.workspace,'revision-demo');assert.equal(s.versions.length,3);assert.equal(s.versions[2].current,true);
 assert.equal(api.restoreInput(s,s.versions[1]).restore_version_id,s.versions[1].version_id);
});
for(const [name,mutate] of [
 ['foreign project',x=>x.project_id='foreign'],['bad token',x=>x.token='bad'],['missing stage',x=>delete x.fields.storyboard],
 ['unsafe path',x=>x.fields.storyboard[0].path=[false]],['duplicate field',x=>x.fields.storyboard.push(x.fields.storyboard[0])],
 ['duplicate version',x=>x.versions.push(x.versions[0])],['bad order',x=>x.versions[0].number=4],['unknown source',x=>x.versions[0].source='model'],
 ['unknown stage',x=>x.versions[0].stage='video_generation'],['fake current',x=>x.versions[1].current=true],
 ['changed current',x=>x.versions[2].fields[0].value='other'],['missing restore',x=>{x.versions[2].source='restore';x.versions[2].restored_from='f'.repeat(32);}],
 ['ambiguous restore',x=>x.versions[2].restored_from=x.versions[0].version_id],['invalid time',x=>x.versions[0].created_at='bad'],
 ['negative shot',x=>x.impacts[0].shot_number=-1],['untracked current',x=>x.untracked_stages=['storyboard']]
])test(`reject damaged contract: ${name}`,()=>{const f=fixture();mutate(f.saved.workspace);assert.throws(()=>api.parseWorkspace(f.saved.workspace,'revision-demo'));});
test('input refuses unchanged, missing, oversized or running edits',()=>{
 const f=fixture(),w=api.parseWorkspace(f.before,'revision-demo'),field=w.fields.storyboard[0];
 for(const [key,value] of [['bad','changed'],[JSON.stringify(field.path),field.value],[JSON.stringify(field.path),'x'.repeat(10001)]])assert.throws(()=>api.editInput(w,'storyboard',key,value));
 w.running=true;assert.throws(()=>api.editInput(w,'storyboard',JSON.stringify(field.path),'changed'));
 const s=api.parseWorkspace(f.saved.workspace,'revision-demo');assert.throws(()=>api.restoreInput(s,s.versions[2]));
});
for(const [name,mutate] of [
 ['wrong stage',x=>x.stage='script_generation'],['wrong basis',x=>x.expected_token='0'.repeat(64)],['wrong value',x=>x.changes[0].after='other'],
 ['wrong object',x=>x.changes[0].path=['title']],['empty changes',x=>x.changes=[]],['restoration mismatch',x=>x.restore_version_id='f'.repeat(32)]
])test(`reject changed preview: ${name}`,()=>{const f=fixture();mutate(f.preview);assert.throws(()=>api.parsePreview(f.preview,f.input));});
test('GET and preview do not save; save sends exact reviewed body once',async()=>{
 const f=fixture(),calls=[],save={...f.input,preview_token:f.preview.preview_token,request_id:'a'.repeat(32),acknowledged:true};
 const fetcher=async(url,opts)=>{calls.push({url,opts});return new Response(JSON.stringify(opts.method==='GET'?f.before:url.endsWith('/preview')?f.preview:f.saved));};
 await api.readRevisions('revision-demo',fetcher);await api.previewRevision('revision-demo',f.input,fetcher);await api.saveRevision('revision-demo',save,fetcher);
 assert.equal(calls.length,3);assert.equal(calls[0].opts.method,'GET');assert.ok(calls[1].url.endsWith('/preview'));assert.deepEqual(JSON.parse(calls[2].opts.body),save);
});
test('lost reply has one POST and uncertain outcome; later GET restores history',async()=>{
 const f=fixture();let calls=0;
 await assert.rejects(()=>api.saveRevision('revision-demo',{...f.input,preview_token:f.preview.preview_token,request_id:'a'.repeat(32),acknowledged:true},async()=>{calls++;throw new Error('private-network');}),e=>e.uncertain===true&&!e.message.includes('private-network'));
 assert.equal(calls,1);assert.equal((await api.readRevisions('revision-demo',async()=>new Response(JSON.stringify(f.saved.workspace)))).versions.length,3);
});
test('missing, conflict, validation and storage errors retain safe distinctions',async()=>{
 const f=fixture();for(const status of [404,409,422,503])await assert.rejects(()=>api.saveRevision('revision-demo',{},async()=>new Response('private',{status})),e=>e.status===status&&e.uncertain===(status>=500)&&!e.message.includes('private'));
 await assert.rejects(()=>api.readRevisions('revision-demo',async()=>new Response('missing',{status:404})),e=>e.status===404);
 await assert.rejects(()=>api.saveRevision('revision-demo',{},async()=>new Response(JSON.stringify({version_id:'f'.repeat(32),workspace:f.saved.workspace}))),e=>e.uncertain===true);
});
test('timeout aborts one attempt without retry',async()=>{
 let calls=0;const quick=loadTypeScript('lib/contentRevisions.ts',{setTimeout:fn=>setTimeout(fn,1),clearTimeout});
 await assert.rejects(()=>quick.saveRevision('revision-demo',{},async(_,opts)=>{calls++;return new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(new Error('aborted'))));}),e=>e.uncertain===true);
 assert.equal(calls,1);
});
