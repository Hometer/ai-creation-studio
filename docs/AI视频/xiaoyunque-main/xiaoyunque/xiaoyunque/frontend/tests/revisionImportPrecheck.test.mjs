import fs from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {File} from 'node:buffer';
import {loadTypeScript} from './loadTypeScript.mjs';
const f=JSON.parse(fs.readFileSync(new URL('./fixtures/revision-import-precheck.json',import.meta.url)));
const globals={setTimeout,clearTimeout,URLSearchParams,crypto:webcrypto};
const api=loadTypeScript('lib/revisionImportPrecheck.ts',globals);
const file=()=>new File([f.backup],'人工文字备份.json',{type:'application/json'});
const parse=x=>api.parsePrecheckReport(x,f.workspace,f.report.file_bytes,f.report.file_sha256);
const response=()=>new Response(JSON.stringify(f.report),{headers:{'Content-Type':'application/json'}});
test('actual HTTP report parses with scoped stages and eight historical versions',()=>{const x=parse(f.report);assert.equal(x.versions.length,8);assert.equal(x.mode,'read_only');assert.equal(x.stages.storyboard.version_count,3);});
for(const key of ['project_id','token','mode','file_bytes','file_sha256','schema_version','same_project','source_project_id','extra','stage_count','version_count','version_id','current_ref','untracked','date','restore'])test('inconsistent or unbound report is refused: '+key,()=>{
 const x=structuredClone(f.report);
 if(key==='project_id')x.project_id='other';else if(key==='token')x.token='a'.repeat(64);else if(key==='mode')x.mode='write';else if(key==='file_bytes')x.file_bytes++;
 else if(key==='file_sha256')x.file_sha256='f'.repeat(64);else if(key==='schema_version')x.schema_version=true;else if(key==='same_project')x.same_project=false;else if(key==='source_project_id')x.source_project_id='../other';else if(key==='extra')x.content='secret';
 else if(key==='stage_count')x.stages.storyboard.field_count=501;else if(key==='version_count')x.stages.storyboard.version_count++;
 else if(key==='version_id')x.versions[1].version_id=x.versions[0].version_id;else if(key==='current_ref')x.stages.script_generation.version_id=x.versions[0].version_id;
 else if(key==='untracked')x.stages.script_generation.untracked=true;else if(key==='date')x.versions[0].created_at='invalid';else if(key==='restore')x.versions[1].restored_from=x.versions[0].version_id;
 assert.throws(()=>parse(x));
});
test('foreign source, empty snapshots and untracked current are explicit',()=>{
 const x=structuredClone(f.report);x.source_project_id='foreign';x.same_project=false;assert.equal(parse(x).same_project,false);
 x.versions=[];for(const s of Object.values(x.stages)){s.field_count=0;s.version_count=0;s.version_id=null;}assert.equal(parse(x).versions.length,0);
 const y=structuredClone(f.report);y.stages.script_generation.untracked=true;y.stages.script_generation.version_id=null;assert.equal(parse(y).stages.script_generation.untracked,true);
});
for(const [name,size,type] of [['../x.json',10,'application/json'],['a\\x.json',10,''],['x.txt',10,'text/plain'],['x.json',0,''],['x.json',64*1024*1024+1,''],['x.json',10,'image/png'],[' x.json',10,''],['x\n.json',10,'']])test('unsupported filename/type/size is rejected before I/O: '+JSON.stringify([name,size,type]),()=>{assert.throws(()=>api.validateBackupFile({name,size,type}));});
test('manual inspection posts exact bytes once with encoded filename and bound token',async()=>{
 let calls=0;const b=file();const result=await api.checkBackup(f.workspace,b,async(url,options)=>{calls++;const q=new URL(url,'http://local');assert.equal(q.pathname,'/api/project/comparison-demo/content-revisions/import-precheck');assert.equal(q.searchParams.get('filename'),b.name);assert.equal(q.searchParams.get('expected_token'),f.workspace.token);assert.equal(options.method,'POST');assert.equal(options.headers['Content-Type'],'application/json');assert.equal(new TextDecoder().decode(options.body),f.backup);return response();});assert.equal(result.file_sha256,f.report.file_sha256);assert.equal(calls,1);
});
for(const status of [404,409,413,415,422,503,500])test('safe status error, no automatic resend: '+status,async()=>{let calls=0;await assert.rejects(api.checkBackup(f.workspace,file(),async()=>{calls++;return new Response('private error',{status});}),e=>e.status===status&&!e.message.includes('private'));assert.equal(calls,1);});
test('manual retry sends one new read-only request after real error semantics',async()=>{let calls=0;const fetcher=async()=>++calls===1?Promise.reject(new Error('private stack')):response();await assert.rejects(api.checkBackup(f.workspace,file(),fetcher),e=>e.status===0&&!e.message.includes('private'));assert.equal(calls,1);assert.equal((await api.checkBackup(f.workspace,file(),fetcher)).mode,'read_only');assert.equal(calls,2);});
test('empty MIME is accepted, malformed or wrong MIME response refused',async()=>{
 const b=new File([f.backup],'backup.json');assert.equal((await api.checkBackup(f.workspace,b,async()=>response())).mode,'read_only');
 for(const r of [new Response('broken',{headers:{'Content-Type':'application/json'}}),new Response(JSON.stringify(f.report),{headers:{'Content-Type':'text/html'}})])await assert.rejects(api.checkBackup(f.workspace,b,async()=>r));
});
test('bounded response rejects excessive report and cancels body',async()=>{let canceled=false;const r=new Response(new ReadableStream({start(c){c.enqueue(new Uint8Array(8*1024*1024+1));},cancel(){canceled=true;}}),{headers:{'Content-Type':'application/json'}});await assert.rejects(api.checkBackup(f.workspace,file(),async()=>r));assert.equal(canceled,true);});
test('external cancel never posts selected file after cancellation',async()=>{const c=new AbortController();c.abort();let calls=0;await assert.rejects(api.checkBackup(f.workspace,file(),async()=>{calls++;return response();},c.signal));assert.equal(calls,0);});
test('20 second deadline aborts pending request without retry',async()=>{
 let trigger,calls=0;const timed=loadTypeScript('lib/revisionImportPrecheck.ts',{...globals,setTimeout(fn,ms){assert.equal(ms,20000);trigger=fn;return 1;},clearTimeout(){}});
 await assert.rejects(timed.checkBackup(f.workspace,file(),async(_u,o)=>{calls++;return await new Promise((_r,reject)=>{o.signal.addEventListener('abort',()=>reject(new Error('aborted')));trigger();});}),e=>e.status===0);assert.equal(calls,1);
});
