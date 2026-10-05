import { apiErrorMessage, isRecord } from './workflowTypes';
import { parsePlan, type Plan } from './generationPlans';

export const MODELS = ['wan2.7-i2v', 'wan2.7-i2v-2026-04-25'] as const;
export const REASONS: Record<string, string> = {
  PLAN_NOT_CONFIRMED: '回到计划页确认本地输入', PLAN_SOURCE_CHANGED: '分镜或素材引用已变化，请创建新计划',
  INPUT_PROTOCOL_INVALID: '当前模型输入规格未通过本地检查，请核对模式、声音、时长与图片', PRICE_UNAVAILABLE: '当前计划没有适用的价格依据',
  PRICE_CHANGED: '价格依据已更新，请重新估算', PRICE_EXPIRED: '价格依据已过期', QUOTE_EXPIRED: '这份估算已过期，请重新估算',
  SAMPLE_REVIEW_REQUIRED: '本次素材声明尚未全部确认', SAMPLE_REVIEW_CHANGED: '素材声明已更新，请使用新声明',
  BUDGET_CHANGED: '本地额度设置已更新', PRICING_SOURCE_MISMATCH: '价格与额度来源不一致',
  BUDGET_SINGLE_EXCEEDED: '估算超过单次上限', BUDGET_DAILY_EXCEEDED: '本日剩余额度不足', BUDGET_MONTHLY_EXCEEDED: '本月剩余额度不足',
  PREPARATION_CANCELLED: '本地准备已取消', PREPARATION_PERIOD_CHANGED: '准备日期已变化，请取消后重新准备',
  REAL_EXECUTION_DISABLED: '真实执行尚未开放', REAL_TRANSPORT_UNAVAILABLE: '真实发送流程尚未接通', AUTHENTICATION_UNVERIFIED: '平台账户认证未核验',
  REAL_BILLING_UNCONNECTED: '实际计费未接通', FIXTURE_ONLY: '人工演练记录不能用于真实生成', KEY_MISSING: '模型服务尚未配置',
  ENDPOINT_NOT_MAINLAND: '服务地址未通过境内检查', MODEL_NOT_ADAPTED: '模型尚未适配', MODE_NOT_ADAPTED: '输入模式尚未适配',
  DURATION_UNSUPPORTED: '时长不符合本地适配规格', RESOLUTION_UNSUPPORTED: '分辨率不符合本地适配规格',
  SOUND_OFF_UNSUPPORTED: '此适配规格要求开启声音', MEDIA_UNAVAILABLE: '固定素材暂不可读', IMAGE_DIMENSIONS_UNSUPPORTED: '图片尺寸不符合适配规格',
  IMAGE_RATIO_UNSUPPORTED: '图片比例不符合适配规格', PNG_ALPHA_UNSUPPORTED: '图片透明通道不符合适配规格', PROMPT_TOO_LONG: '提示词超过适配长度',
};
export const reason = (code: string) => REASONS[code] ?? '有准备条件尚未满足，请重新读取并核对计划';
interface Closed { real_execution_allowed: false }
export interface Price extends Closed { price_id: string; model: string; source_kind: 'manual_account' | 'offline_fixture'; rate_720p_minor: number; rate_1080p_minor: number; expires_at: number; created_at: number; current: boolean; expired: boolean; account_price_verified: false }
export interface Policy { budget_id: string; created_at: number; source_kind: 'manual_setting' | 'offline_fixture'; single_limit_minor: number; daily_limit_minor: number; monthly_limit_minor: number; preparation_only: true }
export interface Budget extends Closed { policy: Policy | null; day: string; month: string; daily_planning_held_minor: number; monthly_planning_held_minor: number; daily_available_minor: number | null; monthly_available_minor: number | null; actual_charged_minor: null; real_money_reserved: false }
export interface Review extends Closed { review_id: string; plan_id: string; content_hash: string; rights_confirmed: boolean; non_sensitive_confirmed: boolean; mainland_only: boolean; preparation_only: true; reviewed_at: number; accepted: boolean; current: boolean }
export interface Quote extends Closed { quote_id: string; quote_hash: string; created_at: number; terms: { plan_id: string; project_id: string; content_hash: string; model: string; resolution: string; lines: { segment_id: string; duration: number }[]; price_id: string | null; pricing_kind: 'unavailable' | 'manual_estimate' | 'offline_fixture'; rate_minor: number | null; amount_minor: number | null; currency: 'CNY' | null; expires_at: number }; current: boolean; expired: boolean; blockers: string[]; account_price_verified: false }
export interface Preparation extends Closed { preparation_id: string; quote_id: string; quote_hash: string; review_id: string; budget_id: string; request_id: string; preparation_only: true; created_at: number; updated_at: number; revision: 1 | 2; state: 'prepared' | 'cancelled'; planning_held_minor: number; active_planning_held_minor: number; period_day: string; period_month: string; submit_attempts: 0; provider_task_id: null; actual_charged_minor: null; real_money_reserved: false; local_preparation_ready: boolean; pricing_kind: 'manual_estimate' | 'offline_fixture'; blockers: string[]; execution_blockers: string[] }
export interface Check extends Closed { check_id: string; preparation_id: string; preparation_revision: number; plan_id: string; quote_id: string; review_id: string; budget_id: string; content_hash: string; quote_hash: string; checked_at: number; basis_sha256: string; target_count: number; request_sha256: Record<string,string>; conditions_ready: boolean; inputs_compiled: boolean; blockers: string[]; execution_blockers: string[]; current: boolean; current_blockers: string[]; preparation_only: true; real_money_reserved: false; submit_attempts: 0; provider_task_id: null; actual_charged_minor: null; network_used: false }
export interface Readiness { plan: Plan; project_exists: boolean; current_price: Price | null; budget: Budget; reviews: Review[]; quotes: Quote[]; preparations: Preparation[]; checks: Check[] }
export interface PriceInput { model: string; source_kind: 'manual_account'; rate_720p_minor: number; rate_1080p_minor: number; expires_at: number }
export interface BudgetInput { source_kind: 'manual_setting'; single_limit_minor: number; daily_limit_minor: number; monthly_limit_minor: number; preparation_only: true }
export interface ReviewInput { content_hash: string; rights_confirmed: boolean; non_sensitive_confirmed: boolean; mainland_only: boolean; preparation_only: true }
export interface PreparationInput { quote_id: string; quote_hash: string; review_id: string; budget_id: string; request_id: string; preparation_only: true }
const id = (x: unknown): x is string => typeof x === 'string' && /^[a-f0-9]{32}$/.test(x);
const hash = (x: unknown): x is string => typeof x === 'string' && /^[a-f0-9]{64}$/.test(x);
const integer = (x: unknown, max = 1_000_000_000, min = 0): x is number => typeof x === 'number' && Number.isSafeInteger(x) && x >= min && x <= max;
const stamp = (x: unknown) => integer(x, 4_102_444_800, 1);
const bool = (x: unknown) => typeof x === 'boolean';
const codes = (x: unknown): x is string[] => Array.isArray(x) && x.every(c => typeof c === 'string' && /^[A-Z][A-Z0-9_]{0,79}$/.test(c)) && new Set(x).size === x.length;
const execution = (x: unknown) => codes(x) && ['REAL_EXECUTION_DISABLED', 'REAL_TRANSPORT_UNAVAILABLE', 'AUTHENTICATION_UNVERIFIED', 'REAL_BILLING_UNCONNECTED'].every(c => x.includes(c));
const closed = (x: Record<string, unknown>) => x.real_execution_allowed === false;
const allocated = (x: Record<string, unknown>) => closed(x) && x.preparation_only === true && x.real_money_reserved === false && x.submit_attempts === 0 && x.provider_task_id === null && x.actual_charged_minor === null;
const period = (day: unknown, month: unknown) => typeof day === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(day) && typeof month === 'string' && /^\d{4}-\d{2}$/.test(month) && day.slice(0, 7) === month;
function invalid(): never { throw new Error('准备记录不完整，请重新读取核对'); }
export function parsePrice(x: unknown): Price {
  if (!isRecord(x) || !closed(x) || !id(x.price_id) || !MODELS.some(m => m === x.model) || !['manual_account','offline_fixture'].includes(String(x.source_kind)) || !integer(x.rate_720p_minor,1_000_000,1) || !integer(x.rate_1080p_minor,1_000_000,1) || !stamp(x.created_at) || !stamp(x.expires_at) || x.expires_at <= x.created_at || !bool(x.current) || !bool(x.expired) || x.account_price_verified !== false) return invalid();
  return x as unknown as Price;
}
export function parsePolicy(x: unknown): Policy {
  if (!isRecord(x) || !id(x.budget_id) || !stamp(x.created_at) || !['manual_setting','offline_fixture'].includes(String(x.source_kind)) || x.preparation_only !== true || !integer(x.single_limit_minor) || !integer(x.daily_limit_minor) || !integer(x.monthly_limit_minor) || x.single_limit_minor > x.daily_limit_minor || x.daily_limit_minor > x.monthly_limit_minor) return invalid();
  return x as unknown as Policy;
}
function parseBudget(x: unknown): Budget {
  if (!isRecord(x) || !closed(x) || !period(x.day, x.month) || x.actual_charged_minor !== null || x.real_money_reserved !== false || !integer(x.daily_planning_held_minor) || !integer(x.monthly_planning_held_minor) || x.daily_planning_held_minor > x.monthly_planning_held_minor) return invalid();
  const p = x.policy === null ? null : parsePolicy(x.policy);
  if (p ? x.daily_available_minor !== p.daily_limit_minor - x.daily_planning_held_minor || x.monthly_available_minor !== p.monthly_limit_minor - x.monthly_planning_held_minor || !integer(x.daily_available_minor) || !integer(x.monthly_available_minor) : x.daily_available_minor !== null || x.monthly_available_minor !== null) return invalid();
  return { ...x, policy: p } as unknown as Budget;
}
export function parseReview(x: unknown): Review {
  if (!isRecord(x) || !closed(x) || !id(x.review_id) || !id(x.plan_id) || !hash(x.content_hash) || !stamp(x.reviewed_at) || !bool(x.current) || !bool(x.rights_confirmed) || !bool(x.non_sensitive_confirmed) || !bool(x.mainland_only) || x.preparation_only !== true || x.accepted !== (x.rights_confirmed && x.non_sensitive_confirmed && x.mainland_only)) return invalid();
  return x as unknown as Review;
}
export function parseQuote(x: unknown): Quote {
  if (!isRecord(x) || !closed(x) || !id(x.quote_id) || !hash(x.quote_hash) || !stamp(x.created_at) || !bool(x.current) || !bool(x.expired) || !codes(x.blockers) || x.account_price_verified !== false || !isRecord(x.terms)) return invalid();
  const t = x.terms;
  if (!id(t.plan_id) || typeof t.project_id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(t.project_id) || !hash(t.content_hash) || typeof t.model !== 'string' || !t.model || typeof t.resolution !== 'string' || !t.resolution || !stamp(t.expires_at) || t.expires_at <= x.created_at || !Array.isArray(t.lines) || t.lines.length < 1 || t.lines.length > 20) return invalid();
  const lines = t.lines.map(l => { if (!isRecord(l) || typeof l.segment_id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(l.segment_id) || !integer(l.duration,60,1)) return invalid(); return {segment_id:l.segment_id,duration:l.duration}; });
  if (new Set(lines.map(l=>l.segment_id)).size !== lines.length) return invalid();
  if (t.pricing_kind === 'unavailable') { if ([t.price_id,t.rate_minor,t.amount_minor,t.currency].some(v=>v!==null) || x.current !== false || !x.blockers.includes('PRICE_UNAVAILABLE')) return invalid(); }
  else if (!['manual_estimate','offline_fixture'].includes(String(t.pricing_kind)) || !id(t.price_id) || !integer(t.rate_minor,1_000_000,1) || !integer(t.amount_minor) || t.currency !== 'CNY' || t.amount_minor !== lines.reduce((n,l)=>n+l.duration,0)*t.rate_minor) return invalid();
  return x as unknown as Quote;
}
export function parsePreparation(x: unknown): Preparation {
  if (!isRecord(x) || !allocated(x) || !['preparation_id','quote_id','review_id','budget_id','request_id'].every(k=>id(x[k])) || !hash(x.quote_hash) || !stamp(x.created_at) || !stamp(x.updated_at) || x.updated_at < x.created_at || !period(x.period_day,x.period_month) || !integer(x.planning_held_minor) || !integer(x.active_planning_held_minor) || !['prepared','cancelled'].includes(String(x.state)) || x.revision !== (x.state==='prepared'?1:2) || x.active_planning_held_minor !== (x.state==='prepared'?x.planning_held_minor:0) || !codes(x.blockers) || x.local_preparation_ready !== (x.blockers.length===0) || !execution(x.execution_blockers) || !['manual_estimate','offline_fixture'].includes(String(x.pricing_kind)) || x.state==='cancelled' && !x.blockers.includes('PREPARATION_CANCELLED')) return invalid();
  return x as unknown as Preparation;
}
export function parseCheck(x: unknown): Check {
  if (!isRecord(x) || !allocated(x) || !['check_id','preparation_id','plan_id','quote_id','review_id','budget_id'].every(k=>id(x[k])) || !['content_hash','quote_hash','basis_sha256'].every(k=>hash(x[k])) || !stamp(x.checked_at) || !integer(x.preparation_revision,2,1) || !integer(x.target_count,20,1) || !bool(x.current) || !codes(x.blockers) || !codes(x.current_blockers) || !execution(x.execution_blockers) || x.conditions_ready !== (x.blockers.length===0) || x.inputs_compiled !== x.conditions_ready || x.network_used !== false || !isRecord(x.request_sha256) || !Object.entries(x.request_sha256).every(([k,v])=>/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(k)&&hash(v)) || Object.keys(x.request_sha256).length !== (x.inputs_compiled?x.target_count:0) || !isRecord(x.connection) || !bool(x.connection.credential_present) || !['beijing','singapore','unrecognized'].includes(String(x.connection.endpoint_region)) || x.connection.endpoint_allowed !== (x.connection.endpoint_region==='beijing') || x.connection.authentication_verified !== false) return invalid();
  // Connection classification and raw request digests are not product authorizations.
  const { connection: _connection, ...safe } = x;
  void _connection;
  return safe as unknown as Check;
}
function list<T>(x: unknown, parse: (x: unknown)=>T, identifier: keyof T): T[] {
  if (!Array.isArray(x)) return invalid(); const items=x.map(parse);
  if (new Set(items.map(i=>i[identifier])).size!==items.length) return invalid(); return items;
}
export function parseReadiness(x: unknown, project: string, planId: string): Readiness {
  if (!isRecord(x) || !bool(x.project_exists)) return invalid();
  const plan=parsePlan(x.plan), budget=parseBudget(x.budget), price=x.current_price===null?null:parsePrice(x.current_price);
  const reviews=list(x.reviews,parseReview,'review_id'), quotes=list(x.quotes,parseQuote,'quote_id'), preparations=list(x.preparations,parsePreparation,'preparation_id'), checks=list(x.checks,parseCheck,'check_id');
  if (plan.plan_id!==planId || plan.content.project_id!==project || price && !price.current || reviews.filter(r=>r.current).length>1 || reviews.some(r=>r.plan_id!==planId || r.content_hash!==plan.content_hash || r.current && !plan.current)) return invalid();
  if (quotes.some(q=>q.terms.plan_id!==planId || q.terms.project_id!==project || q.terms.content_hash!==plan.content_hash || q.terms.model!==plan.content.options.model || q.terms.resolution!==plan.content.options.resolution || q.terms.lines.length!==plan.content.targets.length || q.terms.lines.some(l=>!plan.content.targets.some(t=>t.segment_id===l.segment_id&&t.duration===l.duration)) || q.current && (!plan.current||q.terms.price_id!==price?.price_id))) return invalid();
  if (preparations.some(p=>{ const q=quotes.find(q=>q.quote_id===p.quote_id), r=reviews.find(r=>r.review_id===p.review_id); return !q || !r || !r.accepted || p.quote_hash!==q.quote_hash || p.planning_held_minor!==q.terms.amount_minor || p.pricing_kind!==q.terms.pricing_kind; }) || new Set(preparations.map(p=>p.request_id)).size!==preparations.length || new Set(preparations.map(p=>p.quote_id)).size!==preparations.length) return invalid();
  if (checks.some(c=>{const p=preparations.find(p=>p.preparation_id===c.preparation_id);return !p||c.plan_id!==planId||c.content_hash!==plan.content_hash||c.quote_id!==p.quote_id||c.quote_hash!==p.quote_hash||c.review_id!==p.review_id||c.budget_id!==p.budget_id||c.preparation_revision>p.revision||c.target_count!==plan.content.targets.length||c.inputs_compiled&&Object.keys(c.request_sha256).some(k=>!plan.content.targets.some(t=>t.segment_id===k));})) return invalid();
  return {plan,project_exists:x.project_exists as boolean,current_price:price,budget,reviews,quotes,preparations,checks};
}
export function money(value: number | null) { return value === null ? '待确认' : `¥${(value/100).toFixed(2)}`; }
export function minor(value: string, max=1_000_000_000, positive=false) {
  const s=value.trim(); if (!/^\d{1,8}(\.\d{1,2})?$/.test(s)) throw new Error('金额请填写最多两位小数，不使用负数或科学计数法');
  const [whole,decimal='']=s.split('.'), amount=Number(whole)*100+Number(decimal.padEnd(2,'0'));
  if (!integer(amount,max,positive?1:0)) throw new Error(positive?'价格需大于零，且不超过每秒 10,000 元':'额度需在 0—10,000,000 元之间'); return amount;
}
export function priceInput(model: string, low: string, high: string, expires: string): PriceInput {
  const date=Date.parse(expires); if (!MODELS.some(m=>m===model) || !Number.isFinite(date) || date<=Date.now() || !stamp(Math.floor(date/1000))) throw new Error('请选择已适配的模型并填写未来的有效期');
  return {model,source_kind:'manual_account',rate_720p_minor:minor(low,1_000_000,true),rate_1080p_minor:minor(high,1_000_000,true),expires_at:Math.floor(date/1000)};
}
export function budgetInput(single: string, day: string, month: string): BudgetInput {
  const values=[single,day,month].map(v=>minor(v)); if (values[0]>values[1]||values[1]>values[2]) throw new Error('额度需满足单次不高于每日、每日不高于每月');
  return {source_kind:'manual_setting',single_limit_minor:values[0],daily_limit_minor:values[1],monthly_limit_minor:values[2],preparation_only:true};
}
export function prepareBlockers(data: Readiness, quote: Quote | undefined, now=Date.now()/1000): string[] {
  const result=new Set<string>();
  if (!data.project_exists || !data.plan.current) result.add('PLAN_SOURCE_CHANGED'); if (!data.plan.confirmation) result.add('PLAN_NOT_CONFIRMED');
  if (!quote) result.add('PRICE_UNAVAILABLE'); else { quote.blockers.forEach(c=>result.add(c)); if (now>=quote.terms.expires_at) result.add('QUOTE_EXPIRED'); if (data.preparations.some(p=>p.quote_id===quote.quote_id)) result.add('QUOTE_ALREADY_PREPARED'); }
  const review=data.reviews.find(r=>r.current); if (!review?.accepted) result.add('SAMPLE_REVIEW_REQUIRED');
  const policy=data.budget.policy; if (!policy) result.add('BUDGET_REQUIRED');
  if (quote && policy) { if ((quote.terms.pricing_kind==='offline_fixture') !== (policy.source_kind==='offline_fixture')) result.add('PRICING_SOURCE_MISMATCH'); const amount=quote.terms.amount_minor; if (amount!==null) { if (amount>policy.single_limit_minor) result.add('BUDGET_SINGLE_EXCEEDED'); if(amount>(data.budget.daily_available_minor??0))result.add('BUDGET_DAILY_EXCEEDED'); if(amount>(data.budget.monthly_available_minor??0))result.add('BUDGET_MONTHLY_EXCEEDED'); } }
  return [...result];
}
REASONS.BUDGET_REQUIRED='请先设置本地额度'; REASONS.QUOTE_ALREADY_PREPARED='这份估算已有准备记录，请查看历史或重新估算';
export class ReadinessApiError extends Error { constructor(message:string, public uncertain=false, public status=0) {super(message);} }
async function request(path:string, method='GET',body?:unknown):Promise<unknown> {
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),method==='GET'?20000:60000);
  try { const response=await fetch(path,{method,signal:controller.signal,cache:'no-store',...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}); const value:unknown=await response.json().catch(()=>null);
    if(!response.ok)throw new ReadinessApiError(apiErrorMessage(value,'本地准备操作暂不可用'),method!=='GET'&&response.status>=500,response.status);return value;
  } catch(e) {if(e instanceof ReadinessApiError)throw e;throw new ReadinessApiError(method==='GET'?'无法读取准备记录，请恢复本地服务后重新读取':'提交结果未确认，请重新读取历史核对，不要重复提交',method!=='GET');}
  finally{clearTimeout(timer);}
}
export async function readReadiness(project:string, planId:string) { if(!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(project)||!id(planId))return invalid();return parseReadiness(await request(`/api/generation-plans/${planId}/readiness`),project,planId); }
async function write<T>(path:string,body:unknown,parse:(x:unknown)=>T,verify:(x:T)=>boolean) {const value=await request(path,'POST',body);try{const result=parse(value);if(!verify(result))return invalid();return result;}catch{throw new ReadinessApiError('提交结果未确认，请重新读取历史核对，不要重复提交',true);}}
const matches=(value:object,input:object)=>Object.entries(input).every(([k,v])=>(value as Record<string,unknown>)[k]===v);
export const savePrice=(input:PriceInput)=>write('/api/wan-prices',input,parsePrice,p=>matches(p,input)&&p.current);
export const saveBudget=(input:BudgetInput)=>write('/api/wan-budget-policies',input,parsePolicy,p=>matches(p,input));
export const saveReview=(plan:Plan,input:ReviewInput)=>write(`/api/generation-plans/${plan.plan_id}/sample-reviews`,input,parseReview,r=>matches(r,input)&&r.plan_id===plan.plan_id&&r.current);
export const saveQuote=(plan:Plan)=>write('/api/wan-quotes',{plan_id:plan.plan_id},parseQuote,q=>q.terms.plan_id===plan.plan_id&&q.terms.project_id===plan.content.project_id&&q.terms.content_hash===plan.content_hash);
export const savePreparation=(input:PreparationInput)=>write('/api/wan-preparations',input,parsePreparation,p=>matches(p,input));
export const cancelPreparation=(p:Preparation)=>write(`/api/wan-preparations/${p.preparation_id}/cancel`,{expected_revision:p.revision},parsePreparation,r=>r.preparation_id===p.preparation_id&&r.quote_id===p.quote_id&&r.state==='cancelled');
export const checkPreparation=(p:Preparation)=>write(`/api/wan-preparations/${p.preparation_id}/execution-check`,{},parseCheck,c=>c.preparation_id===p.preparation_id&&c.preparation_revision===p.revision&&c.quote_id===p.quote_id&&c.quote_hash===p.quote_hash&&c.review_id===p.review_id&&c.budget_id===p.budget_id);
