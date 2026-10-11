import fs from 'node:fs';
import { webcrypto } from 'node:crypto';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadTypeScript } from './loadTypeScript.mjs';

const globals={setTimeout,clearTimeout,Blob,crypto:webcrypto,TextEncoder,ReadableStream,URL};
const api=loadTypeScript('lib/revisionExport.ts',globals), revisions=loadTypeScript('lib/contentRevisions.ts',globals);
const raw=JSON.parse(fs.readFileSync(new URL('./fixtures/revision-export.json',import.meta.url)));
const fixture=()=>structuredClone(raw), workspace=()=>revisions.parseWorkspace(fixture().workspace,'comparison-demo');
const response=(f,format)=>new Response(f[format].body,{headers:f[format].headers});
const document=()=>JSON.parse(fixture().json.body);

test('real HTTP JSON has all eight complete snapshots, current structure, types and sources',()=>{
 const x=api.parseExportDocument(document(),workspace());
 assert.equal(x.versions.length,8);assert.equal(x.versions[0].content['制作批次'],'1');assert.equal(x.current.script_generation.content['制作批次'],1);
 assert.equal(x.current.script_generation.content['新增说明'],null);assert.ok(!('待补说明' in x.current.script_generation.content));
 assert.equal(x.current.storyboard.content.episodes[0].unknown,'人工章节附加字段');
});
for(const format of ['json','text'])test('actual attachment '+format+' is one bounded GET and produces the original file bytes',async()=>{
 const f=fixture(),w=workspace(),calls=[];
 const result=await api.readExport(w,format,async(url,opts)=>{calls.push({url,opts});return response(f,format)});
 assert.equal(await result.blob.text(),f[format].body);assert.equal(result.bytes,new TextEncoder().encode(f[format].body).length);
 assert.equal(result.versions,8);assert.equal(result.filename,api.exportFilename(w,format));
 assert.equal(calls.length,1);assert.equal(calls[0].opts.method,'GET');assert.equal(calls[0].opts.body,undefined);assert.equal(calls[0].opts.cache,'no-store');
 assert.equal(new URL(calls[0].url,'http://localhost').searchParams.get('expected_token'),w.token);
});
for(const [name,mutate] of [
 ['foreign project',x=>x.project_id='other'],['foreign token',x=>x.token='0'.repeat(64)],['future schema',x=>x.schema_version=2],['wrong format',x=>x.format='session'],
 ['raw session metadata',x=>x.meta={private:'hidden'}],['missing stage',x=>delete x.current.storyboard],['missing history',x=>x.versions.pop()],
 ['reordered history',x=>x.versions.reverse()],['unknown history',x=>x.versions[0].version_id='f'.repeat(32)],['incorrect source',x=>x.versions[0].source='edit'],
 ['wrong fields',x=>x.versions[0].content.title='wrong'],['incorrect current content',x=>x.current.script_generation.content.title='wrong'],
 ['false current version',x=>x.current.script_generation.version_id=x.versions[0].version_id],['bad digest',x=>x.versions[0].digest='bad'],
 ['current digest mismatch',x=>x.current.script_generation.digest='0'.repeat(64)],['wrong changes',x=>x.versions.at(-1).changes=[]],
 ['wrong impacts',x=>x.versions.at(-1).impacts=[]],['invented restore',x=>x.versions[0].restored_from='0'.repeat(32)],
 ['internal request fields',x=>x.versions[0].request_id='secret'],['false untracked state',x=>x.untracked_stages=['script_generation']]
])test('refuses malformed export document: '+name,()=>{const x=document();mutate(x);assert.throws(()=>api.parseExportDocument(x,workspace()))});
test('JSON object key order is irrelevant and added structures survive validation',()=>{
 const x=document();x.versions[0].changes=x.versions[0].changes.map(c=>Object.fromEntries(Object.entries(c).reverse()));
 assert.equal(api.parseExportDocument(x,workspace()).versions.length,8);
});
for(const [name,mutate] of [
 ['wrong MIME',f=>f.json.headers['content-type']='text/html'],['wrong filename',f=>f.json.headers['content-disposition']='attachment; filename="../private.json"'],
 ['wrong project token',f=>f.json.headers['x-revision-token']='0'.repeat(64)],['wrong format',f=>f.json.headers['x-export-format']='text'],
 ['missing checksum',f=>delete f.json.headers['x-export-sha256']],['wrong checksum',f=>f.json.headers['x-export-sha256']='0'.repeat(64)],
 ['wrong count',f=>f.json.headers['x-export-versions']='7'],['missing length',f=>delete f.json.headers['x-export-bytes']],
 ['huge length',f=>f.json.headers['x-export-bytes']=String(api.MAX_EXPORT_BYTES+1)],['invalid length',f=>f.json.headers['x-export-bytes']='1e3'],
 ['truncated',f=>f.json.body=f.json.body.slice(0,-4)],['extra bytes',f=>f.json.body+='extra'],['empty file',f=>f.json.body='']
])test('refuses bad download before creating a file: '+name,async()=>{const f=fixture();mutate(f);let calls=0;await assert.rejects(()=>api.readExport(workspace(),'json',async()=>{calls++;return response(f,'json')}),e=>!e.uncertain);assert.equal(calls,1)});
test('invalid TXT header, validly hashed bad JSON and invalid UTF-8 all fail safely',async()=>{
 for(const [format,body] of [['text',fixture().text.body.replace('comparison-demo','foreign-project')],['json','{broken private content'],['text',new Uint8Array([255,254,255])]]){
  const f=fixture(),bytes=typeof body==='string'?new TextEncoder().encode(body):body;
  f[format].headers['content-length']=String(bytes.length);
  f[format].headers['x-export-bytes']=String(bytes.length);
  f[format].headers['x-export-sha256']=[...new Uint8Array(await webcrypto.subtle.digest('SHA-256',bytes))].map(b=>b.toString(16).padStart(2,'0')).join('');
  await assert.rejects(()=>api.readExport(workspace(),format,async()=>new Response(bytes,{headers:f[format].headers})),e=>!e.message.includes('private content')&&!e.uncertain);
 }
});
test('chunked stream is bounded by declared bytes and is cancelled on failure',async()=>{
 const f=fixture();let cancelled=false;
 const stream=new ReadableStream({pull(controller){controller.enqueue(new Uint8Array(10))},cancel(){cancelled=true}});
 f.json.headers['content-length']='5';f.json.headers['x-export-bytes']='5';
 await assert.rejects(()=>api.readExport(workspace(),'json',async()=>new Response(stream,{headers:f.json.headers})));
 assert.ok(cancelled);
});
test('error bodies are never displayed or automatically retried',async()=>{
 for(const status of [404,409,422,503,500]){let calls=0;await assert.rejects(()=>api.readExport(workspace(),'json',async()=>{calls++;return new Response('private trace',{status})}),e=>e.status===status&&!e.uncertain&&!e.message.includes('private trace'));assert.equal(calls,1)}
});
test('20-second deadline aborts one request and later explicit read recovers',async()=>{
 const quick=loadTypeScript('lib/revisionExport.ts',{...globals,setTimeout:fn=>setTimeout(fn,1)});let calls=0;
 await assert.rejects(()=>quick.readExport(workspace(),'json',async(_,opts)=>{calls++;return new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(new Error('private timeout'))))}),e=>e.status===0&&!e.message.includes('private timeout'));
 assert.equal(calls,1);assert.equal((await api.readExport(workspace(),'json',async()=>response(fixture(),'json'))).versions,8);
});
test('unsafe project or format is rejected before requesting',async()=>{
 for(const [w,format] of [[{...workspace(),project_id:'../private'},'json'],[workspace(),'zip']]){let calls=0;await assert.rejects(()=>api.readExport(w,format,async()=>{calls++}));assert.equal(calls,0)}
});
test('compressed proxy may remove Content-Length while logical byte count and checksum stay required',async()=>{
 const f=fixture();delete f.json.headers['content-length'];f.json.headers['content-encoding']='gzip';
 assert.equal((await api.readExport(workspace(),'json',async()=>response(f,'json'))).versions,8);
 f.json.headers['content-length']='123';
 assert.equal((await api.readExport(workspace(),'json',async()=>response(f,'json'))).bytes,new TextEncoder().encode(f.json.body).length);
 f.json.body=f.json.body.slice(0,-2);
 await assert.rejects(()=>api.readExport(workspace(),'json',async()=>response(f,'json')));
});
test('download anchor and object URL are cleaned on success and click failure',()=>{
 for(const fail of [false,true]){
  const events=[],timers=[];let anchor;
  const impl=loadTypeScript('lib/revisionExport.ts',{...globals,URL:{createObjectURL:()=>{events.push('create');return 'blob:local'},revokeObjectURL:x=>events.push('revoke '+x)},setTimeout:fn=>{timers.push(fn)},document:{createElement:()=>anchor={click(){events.push('click');if(fail)throw Error('browser failed')},remove(){events.push('remove')}},body:{appendChild:()=>events.push('append')}}});
  const run=()=>impl.saveExport({filename:'test.json',blob:new Blob(['{}'])});if(fail)assert.throws(run);else run();
  assert.equal(anchor.download,'test.json');assert.equal(anchor.href,'blob:local');assert.deepEqual(events,['create','append','click','remove']);assert.equal(timers.length,1);timers[0]();assert.equal(events.at(-1),'revoke blob:local');
 }
});

test('new 映序 text export validates while old recorded export remains accepted',async()=>{
 const f=fixture();f.text.body=f.text.body.replace('创作工作室 · 文字版本导出','映序 · 文字版本导出');
 const bytes=new TextEncoder().encode(f.text.body);f.text.headers['x-export-bytes']=String(bytes.length);f.text.headers['content-length']=String(bytes.length);
 f.text.headers['x-export-sha256']=Buffer.from(await webcrypto.subtle.digest('SHA-256',bytes)).toString('hex');
 const r=await api.readExport(workspace(),'text',async()=>response(f,'text'));
 assert.ok((await r.blob.text()).startsWith('映序 · 文字版本导出'));
});
