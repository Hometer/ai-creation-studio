import fs from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {File} from 'node:buffer';
import {loadTypeScript} from './loadTypeScript.mjs';
const f=JSON.parse(fs.readFileSync(new URL('./fixtures/revision-import-preview.json',import.meta.url)));
const globals={setTimeout,clearTimeout,URLSearchParams,crypto:webcrypto};
const api=loadTypeScript('lib/revisionImportPreview.ts',globals);
const file=()=>new File([f.backup],'人工旧备份.json',{type:'application/json'});
const parse=x=>api.parseRestorePreview(x,f.workspace,f.check,f.check.file_bytes,f.check.file_sha256);
const response=(x=f.preview)=>new Response(JSON.stringify(x),{headers:{'Content-Type':'application/json'}});
test('actual old HTTP backup against two saved edits preserves direction and has no write authority',()=>{const x=parse(f.preview);assert.equal(x.scope,'backup_current');assert.equal(x.total_changes,2);assert.equal(x.stages.script_generation.changes[0].before,'人工备份之后的新标题');assert.equal(x.preview_token,undefined);});
test('actual matching backup and empty projections parse as no changes',()=>{const z=f.zero;const x=api.parseRestorePreview(z.preview,z.workspace,z.check,z.check.file_bytes,z.check.file_sha256);assert.equal(x.total_changes,0);assert.equal(x.stages.storyboard.impacts.length,0);});
for(const kind of ['project','token','source','mode','scope','size','hash','extra','stage','total','before_count','after_count','after_digest','before_digest','path','duplicate_path','unknown_type','missing_type','numeric_type','unchanged_row','impact','impact_extra','extra_row','extra_stage'])test('unbound or inconsistent restore report refused: '+kind,()=>{
 const x=structuredClone(f.preview),s=x.stages.script_generation,c=s.changes[0];
 if(kind==='project')x.project_id='other';else if(kind==='token')x.token='a'.repeat(64);else if(kind==='source')x.source_project_id='other';else if(kind==='mode')x.mode='write';else if(kind==='scope')x.scope='history';else if(kind==='size')x.file_bytes++;else if(kind==='hash')x.file_sha256='f'.repeat(64);else if(kind==='extra')x.preview_token='a'.repeat(64);else if(kind==='stage')delete x.stages.storyboard;else if(kind==='total')x.total_changes++;else if(kind==='before_count')s.before_field_count++;else if(kind==='after_count')s.after_field_count++;else if(kind==='after_digest')s.after_digest='a'.repeat(64);else if(kind==='before_digest')s.before_digest=s.after_digest;else if(kind==='path')c.path=[-1];else if(kind==='duplicate_path'){s.changes.push(structuredClone(c));x.total_changes++;}else if(kind==='unknown_type')c.before_type='html';else if(kind==='missing_type')c.before_present=false;else if(kind==='numeric_type')c.after_type='number';else if(kind==='unchanged_row')c.after=c.before;else if(kind==='impact')s.impacts[0].shot_number=-1;else if(kind==='impact_extra')s.impacts[0].secret='private';else if(kind==='extra_row')c.secret='private';else s.secret='private';
 assert.throws(()=>parse(x));
});
test('missing versus null, added fields, numeric types and blank text retained safely',()=>{
 const x=structuredClone(f.preview),s=x.stages.script_generation;
 s.changes=[{path:['removed'],label:'移除',before:'null',after:'null',before_present:true,after_present:false,before_type:'null',after_type:'missing'},{path:['added'],label:'新增',before:'null',after:'7',before_present:false,after_present:true,before_type:'missing',after_type:'number'},{path:['blank'],label:'清空',before:'<script>plain text</script>',after:'',before_present:true,after_present:true,before_type:'text',after_type:'text'}];x.total_changes=4;assert.equal(parse(x).stages.script_generation.changes[2].after,'');
});
test('no changes cannot claim impact, changed digest or disagreement with precheck',()=>{
 const z=structuredClone(f.zero);z.preview.stages.storyboard.impacts=f.preview.stages.storyboard.impacts;assert.throws(()=>api.parseRestorePreview(z.preview,z.workspace,z.check,z.check.file_bytes,z.check.file_sha256));
 const checked=structuredClone(f.check);checked.stages.script_generation.matches_current=true;assert.throws(()=>api.parseRestorePreview(f.preview,f.workspace,checked,checked.file_bytes,checked.file_sha256));
});
test('explicit preview posts original file once to scoped endpoint with bound query',async()=>{
 let calls=0;const b=file();const x=await api.previewBackup(f.workspace,b,f.check,async(url,o)=>{calls++;const q=new URL(url,'http://local');assert.equal(q.pathname,'/api/project/comparison-demo/content-revisions/import-preview');assert.equal(q.searchParams.get('filename'),b.name);assert.equal(q.searchParams.get('expected_token'),f.workspace.token);assert.equal(o.method,'POST');assert.equal(o.headers['Content-Type'],'application/json');assert.equal(new TextDecoder().decode(o.body),f.backup);return response();});assert.equal(x.total_changes,2);assert.equal(calls,1);
});
for(const kind of ['foreign','running','stale','unbound_size','bad_file'])test('invalid prerequisite refuses before upload: '+kind,async()=>{const w=structuredClone(f.workspace),check=structuredClone(f.check);let b=file(),calls=0;
 if(kind==='foreign'){check.source_project_id='other';check.same_project=false;}else if(kind==='running')w.running=true;else if(kind==='stale')check.token='a'.repeat(64);else if(kind==='unbound_size')check.file_bytes++;else b=new File([f.backup],'unsafe.txt');
 await assert.rejects(api.previewBackup(w,b,check,async()=>{calls++;return response();}));assert.equal(calls,0);
});
for(const status of [404,409,413,415,422,503,500])test('failure gives safe error and never automatically retries: '+status,async()=>{let calls=0;await assert.rejects(api.previewBackup(f.workspace,file(),f.check,async()=>{calls++;return new Response('private stack',{status});}),e=>e.status===status&&!e.message.includes('private'));assert.equal(calls,1);});
test('network failure keeps workflow manually retryable with one new request',async()=>{let calls=0;const fetcher=async()=>++calls===1?Promise.reject(new Error('private error')):response();await assert.rejects(api.previewBackup(f.workspace,file(),f.check,fetcher));assert.equal(calls,1);assert.equal((await api.previewBackup(f.workspace,file(),f.check,fetcher)).total_changes,2);assert.equal(calls,2);});
test('wrong MIME, malformed JSON or oversized response refused with cancellation',async()=>{
 for(const r of [new Response('private',{headers:{'Content-Type':'application/json'}}),new Response(JSON.stringify(f.preview),{headers:{'Content-Type':'text/html'}})])await assert.rejects(api.previewBackup(f.workspace,file(),f.check,async()=>r));
 let canceled=false;const r=new Response(new ReadableStream({start(c){c.enqueue(new Uint8Array(8*1024*1024+1));},cancel(){canceled=true;}}),{headers:{'Content-Type':'application/json'}});await assert.rejects(api.previewBackup(f.workspace,file(),f.check,async()=>r));assert.equal(canceled,true);
});
test('external cancel never sends a preview request',async()=>{const c=new AbortController();c.abort();let calls=0;await assert.rejects(api.previewBackup(f.workspace,file(),f.check,async()=>{calls++;return response();},c.signal));assert.equal(calls,0);});
test('20 second preview deadline aborts pending HTTP exactly once',async()=>{let trigger,calls=0;const timed=loadTypeScript('lib/revisionImportPreview.ts',{...globals,setTimeout(fn,ms){assert.equal(ms,20000);trigger=fn;return 1;},clearTimeout(){}});await assert.rejects(timed.previewBackup(f.workspace,file(),f.check,async(_u,o)=>{calls++;return await new Promise((_r,reject)=>{o.signal.addEventListener('abort',()=>reject(new Error('private')));trigger();});}));assert.equal(calls,1);});
