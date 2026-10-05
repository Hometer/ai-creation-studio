import fs from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {loadTypeScript} from './loadTypeScript.mjs';
const api=loadTypeScript('lib/generationReadiness.ts');
const source=JSON.parse(fs.readFileSync(new URL('./fixtures/readiness.json',import.meta.url)));
const fixture=()=>structuredClone(source);
const parse=x=>api.parseReadiness(x,'media-demo',source.plan.plan_id);
const plain=x=>JSON.parse(JSON.stringify(x));
test('actual offline HTTP fixture preserves estimates, closed execution and no connection details',()=>{
 const d=parse(fixture());assert.equal(d.quotes[0].terms.amount_minor,35);assert.equal(d.preparations[0].actual_charged_minor,null);assert.equal(d.checks[0].current,true);assert.equal(d.checks[0].network_used,false);assert.equal('connection' in d.checks[0],false);
});
for(const [name,change] of [
 ['enabled execution',d=>d.preparations[0].real_execution_allowed=true],
 ['real funds',d=>d.budget.real_money_reserved=true],
 ['premature dispatch',d=>d.checks[0].submit_attempts=1],
 ['network used',d=>d.checks[0].network_used=true],
 ['unknown charge called zero',d=>d.preparations[0].actual_charged_minor=0],
 ['verified account price',d=>d.current_price.account_price_verified=true],
 ['negative estimate',d=>d.quotes[0].terms.amount_minor=-1],
 ['incorrect math',d=>d.quotes[0].terms.amount_minor=36],
 ['foreign quote',d=>d.quotes[0].terms.plan_id='0'.repeat(32)],
 ['foreign project',d=>d.plan.content.project_id='other'],
 ['foreign declaration',d=>d.reviews[0].content_hash='0'.repeat(64)],
 ['missing review association',d=>d.reviews=[]],
 ['missing preparation association',d=>d.preparations=[]],
 ['duplicate preparation',d=>d.preparations.push(d.preparations[0])],
 ['missing execution blocker',d=>d.checks[0].execution_blockers=[]],
 ['misleading freshness',d=>{d.plan.current=false;d.plan.impacts[0].reasons=['reference_changed'];}],
 ['false acceptance',d=>d.reviews[0].rights_confirmed=false],
 ['wrong availability',d=>d.budget.daily_available_minor=200],
 ['fractional price',d=>d.current_price.rate_720p_minor=7.1],
 ['cancelled but held',d=>{d.preparations[0].state='cancelled';d.preparations[0].revision=2;}],
])test(`reject unsafe preparation contract: ${name}`,()=>{const d=fixture();change(d);assert.throws(()=>parse(d));});
test('unknown amount is null, never zero or free',()=>{
 const d=fixture();d.preparations=[];d.checks=[];const q=d.quotes[0];q.terms.pricing_kind='unavailable';q.terms.price_id=q.terms.rate_minor=q.terms.amount_minor=q.terms.currency=null;q.current=false;q.blockers=['PRICE_UNAVAILABLE'];
 assert.equal(parse(d).quotes[0].terms.amount_minor,null);assert.equal(api.money(null),'待确认');q.terms.amount_minor=0;assert.throws(()=>parse(d));
});
test('decimal input is exact in minor units and malformed money is rejected',()=>{
 for(const [input,expected] of [['0',0],['0.07',7],['0.29',29],['3.10',310],['10000000',1000000000]])assert.equal(api.minor(input),expected);
 for(const input of ['-1','1e2','1.001','Infinity','','+1','10000000.01'])assert.throws(()=>api.minor(input));
 assert.throws(()=>api.minor('0',1000000,true));assert.equal(api.money(29),'¥0.29');
});
test('shared limits and price expiry require valid explicit input',()=>{
 assert.deepEqual(plain(api.budgetInput('1','2','3')),{source_kind:'manual_setting',single_limit_minor:100,daily_limit_minor:200,monthly_limit_minor:300,preparation_only:true});
 assert.throws(()=>api.budgetInput('2','1','3'));assert.throws(()=>api.priceInput('unknown','0.07','0.11','2099-01-01T00:00'));assert.throws(()=>api.priceInput('wan2.7-i2v','0.07','0.11','2020-01-01T00:00'));
});
test('stale, negative reviews, expired quotes and limited budgets cannot prepare',()=>{
 const d=parse(fixture()),q=d.quotes[0];d.preparations=[];assert.deepEqual(plain(api.prepareBlockers(d,q,q.created_at)),[]);
 assert.ok(api.prepareBlockers(d,q,q.terms.expires_at).includes('QUOTE_EXPIRED'));d.reviews[0].accepted=false;assert.ok(api.prepareBlockers(d,q,q.created_at).includes('SAMPLE_REVIEW_REQUIRED'));
 d.reviews[0].accepted=true;d.budget.daily_available_minor=0;assert.ok(api.prepareBlockers(d,q,q.created_at).includes('BUDGET_DAILY_EXCEEDED'));
 d.plan.current=false;assert.ok(api.prepareBlockers(d,q,q.created_at).includes('PLAN_SOURCE_CHANGED'));
});
test('saved quote cannot create a second preparation, even after cancellation',()=>{
 const d=parse(fixture());assert.ok(api.prepareBlockers(d,d.quotes[0],d.quotes[0].created_at).includes('QUOTE_ALREADY_PREPARED'));d.preparations[0].state='cancelled';assert.ok(api.prepareBlockers(d,d.quotes[0],d.quotes[0].created_at).includes('QUOTE_ALREADY_PREPARED'));
});
test('opening workspace performs one GET, no writes or model requests',async()=>{
 const calls=[];const io=loadTypeScript('lib/generationReadiness.ts',{setTimeout,clearTimeout,fetch:async(path,init)=>{calls.push([path,init.method]);return Response.json(fixture());}});
 await io.readReadiness('media-demo',source.plan.plan_id);assert.deepEqual(calls,[[`/api/generation-plans/${source.plan.plan_id}/readiness`,'GET']]);
 await assert.rejects(io.readReadiness('other',source.plan.plan_id));assert.equal(calls.length,2);
});
test('uncertain writes are bounded, safe and never retried',async()=>{
 for(const response of [null,{status:500},{status:201}]){let count=0;const io=loadTypeScript('lib/generationReadiness.ts',{setTimeout,clearTimeout,fetch:async()=>{count++;if(!response)throw new Error('private path');return Response.json({},response);}});
 await assert.rejects(io.saveBudget(api.budgetInput('1','2','3')),e=>e.uncertain===true&&!e.message.includes('private'));assert.equal(count,1);}
});
test('preparation sends exact binding and caller request id, cancellation sends revision',async()=>{
 const d=parse(fixture()),p=d.preparations[0],calls=[];const io=loadTypeScript('lib/generationReadiness.ts',{setTimeout,clearTimeout,fetch:async(path,init)=>{calls.push([path,JSON.parse(init.body)]);return Response.json(source.preparations[0]);}});
 const input={quote_id:p.quote_id,quote_hash:p.quote_hash,review_id:p.review_id,budget_id:p.budget_id,request_id:p.request_id,preparation_only:true};await io.savePreparation(input);assert.deepEqual(calls[0],['/api/wan-preparations',input]);
 await assert.rejects(io.cancelPreparation(p),e=>e.uncertain===true);assert.deepEqual(calls[1],[`/api/wan-preparations/${p.preparation_id}/cancel`,{expected_revision:1}]);
});
test('successful but unrelated write responses are uncertain',async()=>{
 const d=fixture();d.reviews[0].plan_id='0'.repeat(32);const io=loadTypeScript('lib/generationReadiness.ts',{setTimeout,clearTimeout,fetch:async()=>Response.json(d.reviews[0])});
 await assert.rejects(io.saveReview(parse(fixture()).plan,{content_hash:d.plan.content_hash,rights_confirmed:true,non_sensitive_confirmed:true,mainland_only:true,preparation_only:true}),e=>e.uncertain===true);
});
test('unknown condition is displayed conservatively without raw code',()=>assert.equal(api.reason('NEW_CONDITION'),'有准备条件尚未满足，请重新读取并核对计划'));
test('missing record retains 404 classification without retrying or treating it as uncertain',async()=>{
 let calls=0;const io=loadTypeScript('lib/generationReadiness.ts',{setTimeout,clearTimeout,fetch:async()=>{calls++;return Response.json({error:{code:'PLAN_NOT_FOUND',message:'生成计划不存在'}},{status:404});}});
 await assert.rejects(io.readReadiness('media-demo',source.plan.plan_id),e=>e.status===404&&e.uncertain===false&&e.message==='生成计划不存在');assert.equal(calls,1);
});
