import fs from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadTypeScript} from './loadTypeScript.mjs';
const api=loadTypeScript('lib/contentRevisions.ts',{setTimeout,clearTimeout,TextEncoder});
const raw=JSON.parse(fs.readFileSync(new URL('./fixtures/manual-revisions.json',import.meta.url)));
const fixture=()=>structuredClone(raw);const plain=x=>JSON.parse(JSON.stringify(x));
test('real original editor HTTP contract keeps unknown fields and binds structural preview',()=>{
 const f=fixture(),editor=api.parseEditor(f.editor,'manual-edit-demo','storyboard');
 const p=api.manualInput(editor,'storyboard',f.input.replacement);
 assert.deepEqual(plain(p),f.input);assert.equal(p.replacement.episodes[0].unknown,'人工章节附加字段');
 assert.deepEqual(plain(api.parsePreview(f.preview,p,editor.artifact)),f.preview);assert.equal(f.saved.workspace.versions.length,3);
});
for(const [name,mutate] of [
 ['wrong content',x=>x.artifact.episodes[0].segments[0].shots[0].content='other'],['wrong shape',x=>x.artifact.episodes[0].segments='bad'],
 ['missing body',x=>x.artifact=null],['wrong project',x=>x.workspace.project_id='foreign'],
])test(`editor rejects unsafe reply: ${name}`,()=>{const f=fixture();mutate(f.editor);assert.throws(()=>api.parseEditor(f.editor,'manual-edit-demo','storyboard'));});
for(const [name,mutate] of [
 ['wrong value',x=>x.changes[0].after='other'],['missing diff',x=>x.changes.pop()],['wrong path',x=>x.changes[0].path=['extra']],
 ['wrong previous',x=>x.changes[0].before='other'],['wrong basis',x=>x.expected_token='f'.repeat(64)],['fake restore',x=>x.restore_version_id='a'.repeat(32)],
])test(`manual preview rejects altered diff: ${name}`,()=>{const f=fixture();mutate(f.preview);assert.throws(()=>api.parsePreview(f.preview,f.input,f.editor.artifact));});
test('manual input clones draft and refuses unchanged/running/unbounded data',()=>{
 const f=fixture(),editor=api.parseEditor(f.editor,'manual-edit-demo','storyboard');assert.throws(()=>api.manualInput(editor,'storyboard',editor.artifact));
 const p=api.manualInput(editor,'storyboard',f.input.replacement);f.input.replacement.episodes=[];assert.notEqual(p.replacement.episodes.length,0);
 editor.workspace.running=true;assert.throws(()=>api.manualInput(editor,'storyboard',p.replacement));
 assert.throws(()=>api.manualContent([]));assert.throws(()=>api.manualContent({x:NaN}));assert.throws(()=>api.manualContent({x:'x'.repeat(2*1024*1024)}));
});
for(const [name,stage,value] of [
 ['non-array episodes','storyboard',{episodes:'bad'}],['non-array segments','storyboard',{episodes:[{segments:{}}]}],
 ['duplicate id','storyboard',{episodes:[{segments:[{segment_id:'a'},{segment_id:'a'}]}]}],
 ['negative duration','storyboard',{episodes:[{segments:[{segment_id:'a',shots:[{shot_number:1,duration:-1}]}]}]}],
 ['boolean number','storyboard',{episodes:[{segments:[{segment_id:'a',shots:[{shot_number:true}]}]}]}],
 ['bad title','script_generation',{title:[]}],['bad scenes','script_generation',{scenes:'bad'}],
])test(`JSON cannot switch into an unsafe structured form: ${name}`,()=>assert.throws(()=>api.editorStructure(stage,value)));
test('manual HTTP follows readonly editor, preview and one explicit write',async()=>{
 const f=fixture(),calls=[],fetcher=async(url,opts)=>{calls.push({url,opts});return new Response(JSON.stringify(opts.method==='GET'?f.editor:url.endsWith('/preview')?f.preview:f.saved));};
 const editor=await api.readEditor('manual-edit-demo','storyboard',fetcher),input=api.manualInput(editor,'storyboard',f.input.replacement);
 const preview=await api.previewManualRevision('manual-edit-demo',editor,input,fetcher);
 const result=await api.saveRevision('manual-edit-demo',{...input,preview_token:preview.preview_token,request_id:'a'.repeat(32),acknowledged:true},fetcher);
 assert.deepEqual(plain(result.artifact),f.input.replacement);assert.equal(calls.length,3);assert.equal(calls[0].opts.method,'GET');assert.ok(calls[0].url.endsWith('/editor/storyboard'));
 assert.ok(calls.every(call=>!call.url.includes('intervene')&&!call.url.includes('execute')));
});
test('lost manual reply is uncertain and never retries',async()=>{
 const f=fixture();let calls=0;await assert.rejects(()=>api.saveRevision('manual-edit-demo',{...f.input,preview_token:f.preview.preview_token,request_id:'a'.repeat(32),acknowledged:true},async()=>{calls++;throw Error('private');}),e=>e.uncertain&&!e.message.includes('private'));
 assert.equal(calls,1);const editor=await api.readEditor('manual-edit-demo','storyboard',async()=>new Response(JSON.stringify({workspace:f.saved.workspace,artifact:f.saved.artifact})));assert.equal(editor.workspace.versions.length,3);
});
test('changed write reply never replaces editor with an unverified artifact',async()=>{
 const f=fixture();f.saved.artifact.extra='different';await assert.rejects(()=>api.saveRevision('manual-edit-demo',{...f.input,preview_token:f.preview.preview_token,request_id:'a'.repeat(32),acknowledged:true},async()=>new Response(JSON.stringify(f.saved))),e=>e.uncertain);
});
test('null addition remains visible and cannot be confused with an unchanged value',()=>{
 const f=fixture();f.input.replacement.episodes[0].nullable=null;
 f.preview.changes.unshift({path:['episodes',0,'nullable'],before:'null',after:'null',before_present:false,after_present:true});
 assert.equal(api.parsePreview(f.preview,f.input,f.editor.artifact).changes[0].before_present,false);
 f.preview.changes[0].before_present=true;assert.throws(()=>api.parsePreview(f.preview,f.input,f.editor.artifact));
});
