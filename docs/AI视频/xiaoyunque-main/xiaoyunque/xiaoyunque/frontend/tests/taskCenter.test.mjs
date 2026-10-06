import assert from 'node:assert/strict';
import { test } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { loadTypeScript } from './loadTypeScript.mjs';

const api = loadTypeScript('lib/taskCenter.ts', { setTimeout, clearTimeout });
const a = 'a'.repeat(32), b = 'b'.repeat(32), c = 'c'.repeat(32);
const time = '2026-10-05T00:00:00+00:00';
const normalize = x => JSON.parse(JSON.stringify(x));
const generation = () => ({ task_id: a, project_id: 'project-a', segment_id: 'seg-1', revision: 1, status: 'blocked', created_at: time, updated_at: time, simulation_only: false, real_execution_allowed: false, blockers: ['quote_unavailable', 'provider_unavailable'], billing: { state: 'unreserved', reserved_minor: 0, billed_minor: null }, result: null, result_url: null, error: null });
const source = (s, value) => api.parseTaskSource(s, value)[0];

test('unknown costs stay unknown and blocked tasks explain what is missing', () => {
  const task = source('generation', [generation()]);
  assert.equal(task.status, 'waiting'); assert.equal(task.billing, '真实费用未确认');
  assert.deepEqual(normalize(task.reasons), ['费用尚未确认', '生成执行尚未接入']);
  assert.equal(task.progress, null, 'Fixed stage percentages must not impersonate actual model progress');
});
test('SIM result uses matching controlled media URL and explicit artificial billing', () => {
  const x = { ...generation(), status: 'succeeded', simulation_only: true, result: { asset_id: b, version_id: c }, result_url: `/api/assets/${b}/versions/${c}/media`, billing: { state: 'settled', reserved_minor: 10, billed_minor: 6 } };
  const task = source('generation', [x]);
  assert.equal(task.status, 'completed'); assert.equal(task.billing, '演练已结算 6 SIM');
  assert.ok(task.note.includes('不代表模型效果')); assert.equal(task.resultUrl, x.result_url);
});
for (const [name, mutate] of [
  ['external result URL', x => { x.status = 'succeeded'; x.result = { asset_id: b, version_id: c }; x.result_url = 'https://example.com/result.mp4'; }],
  ['mismatching media version', x => { x.status = 'succeeded'; x.result = { asset_id: b, version_id: c }; x.result_url = `/api/assets/${b}/versions/${a}/media`; }],
  ['success without result', x => { x.status = 'succeeded'; }],
  ['unsafe project ID', x => { x.project_id = '../config'; }],
  ['string simulation flag', x => { x.simulation_only = 'false'; }],
  ['enabled real execution', x => { x.real_execution_allowed = true; }],
  ['unbounded billing', x => { x.billing.reserved_minor = Infinity; }],
  ['incomplete settlement', x => { x.billing.state = 'settled'; }],
  ['reservation marked unreserved', x => { x.billing.reserved_minor = 10; }],
  ['unfinished task marked billed', x => { x.billing.state = 'reserved'; x.billing.billed_minor = 0; }],
  ['invalid time', x => { x.created_at = 'bad'; x.updated_at = 'bad'; }],
  ['duplicate identifier', x => { x.task_id = '../unsafe'; }],
]) test(`reject unsafe history: ${name}`, () => { const x = generation(); mutate(x); assert.throws(() => source('generation', [x])); });
test('provider raw errors and prompts never enter the display model', () => {
  const x = { ...generation(), status: 'manual_review', error: { code: 'SUBMISSION_UNCERTAIN', message: 'private-path-secret' }, input: { prompt: 'private-prompt' }, output_dir: '/private/secret' };
  const task = source('generation', [x]);
  assert.equal(task.status, 'review'); assert.ok(task.reasons[0].includes('核对原任务'));
  assert.ok(!JSON.stringify(task).includes('private'));
});
test('project workflow uses six real business stages, excludes init and preserves unknown states', () => {
  const stages = ['script_generation', 'character_design', 'storyboard', 'reference_generation', 'video_generation', 'post_production'];
  const status = Object.fromEntries(stages.map(s => [s, 'completed'])); status.init = 'pending';
  const parse = statuses => source('workflow', { sessions: [{ id: 'p1', title: '人工项目', status: statuses, current_stage: 'storyboard', date: 1791158400 }] });
  assert.equal(parse(status).status, 'completed'); assert.equal(parse(status).phase, '分镜 · 已完成 6 / 6 个阶段');
  assert.equal(parse({}).status, 'draft'); assert.equal(parse({ storyboard: 'future_status' }).status, 'unknown');
  assert.equal(parse({ storyboard: 'stopped' }).status, 'review'); assert.equal(parse({ storyboard: 'error' }).status, 'failed');
  assert.equal(parse({ storyboard: 'waiting' }).status, 'waiting');
});
test('legacy sources keep missing progress missing and do not display raw prompts', () => {
  const task = source('pipeline', { tasks: [{ task_id: 'old-task', pipeline: 'standard', status: 'running', input: { prompt_text: 'do-not-display' } }] });
  assert.equal(task.title, '文艺短视频'); assert.equal(task.progress, null); assert.equal(task.href, '/pipelines/standard?task=old-task');
  const sandbox = source('sandbox', { tasks: [{ id: 'temp-1', tool: 'video', status: 'running', input: { prompt: 'do-not-display' } }] });
  assert.ok(sandbox.note.includes('重启后不保证保留')); assert.ok(!JSON.stringify([task, sandbox]).includes('do-not-display'));
});
test('future pipelines have no guessed route and unknown statuses never become complete', () => {
  const task = source('pipeline', { tasks: [{ task_id: 'future-1', pipeline: 'future', status: 'future' }] });
  assert.equal(task.href, null); assert.equal(task.status, 'unknown');
});
test('invalid envelopes, duplicate source IDs, and out-of-range progress are visible failures', () => {
  assert.throws(() => api.parseTaskSource('pipeline', { tasks: {} }));
  assert.throws(() => api.parseTaskSource('generation', [generation(), generation()]));
  assert.throws(() => source('sandbox', { tasks: [{ id: 's1', tool: 'video', status: 'running', progress: 101 }] }));
});
test('sorting, filtering, and prefixed keys preserve distinct source records', () => {
  const snapshot = api.emptySnapshot();
  snapshot.generation.records = api.parseTaskSource('generation', [generation()]);
  snapshot.pipeline.records = api.parseTaskSource('pipeline', { tasks: [{ task_id: a, pipeline: 'standard', status: 'failed', created_at: '2026-10-04T00:00:00Z' }] });
  const rows = api.taskRecords(snapshot);
  assert.equal(rows.length, 2); assert.notEqual(rows[0].key, rows[1].key);
  assert.equal(api.filterTasks(rows, 'all', 'all', `  ${a.toUpperCase()} `).length, 2);
  assert.equal(api.filterTasks(rows, 'generation', 'waiting', 'SEG-1').length, 1);
  assert.equal(api.filterTasks(rows, 'all', 'failed', '').length, 1);
});
test('read-only source isolation retains prior failed records and recovers without mutations', async () => {
  const requests = []; let fail = true;
  const previous = api.emptySnapshot(); previous.generation = { records: [source('generation', [generation()])], failed: false, updatedAt: time };
  const client = loadTypeScript('lib/taskCenter.ts', { setTimeout, clearTimeout, fetch: async (url, options) => {
    requests.push({ url, options });
    if (url === '/api/generation-tasks') return new Response(fail ? '{"error":{"message":"private-secret"}}' : '[]', { status: fail ? 503 : 200 });
    return new Response(JSON.stringify(url === '/api/sessions' ? { sessions: [] } : { tasks: [] }));
  } });
  const result = await client.readTaskCenter(previous);
  assert.equal(result.generation.failed, true); assert.equal(result.generation.records[0].id, a); assert.equal(result.generation.updatedAt, time);
  assert.equal(result.workflow.failed, false); assert.ok(!JSON.stringify(result).includes('private-secret'));
  fail = false; const recovered = await client.readTaskCenter(result); assert.equal(recovered.generation.failed, false); assert.equal(recovered.generation.records.length, 0);
  assert.equal(requests.length, 10); assert.ok(requests.every(r => !r.options.method && r.options.cache === 'no-store'));
});
test('request cancellation propagates to every source', async () => {
  const signals = [], controller = new AbortController();
  const client = loadTypeScript('lib/taskCenter.ts', { setTimeout, clearTimeout, fetch: (_url, options) => new Promise((_resolve, reject) => { signals.push(options.signal); options.signal.addEventListener('abort', () => reject(new Error('cancelled')), { once: true }); }) });
  const request = client.readTaskCenter(api.emptySnapshot(), controller.signal); controller.abort();
  const result = await request;
  assert.equal(signals.length, 5); assert.ok(signals.every(s => s.aborted)); assert.ok(Object.values(result).every(s => s.failed));
});
test('every source enforces the documented timeout instead of waiting forever', async () => {
  const delays = [];
  const client = loadTypeScript('lib/taskCenter.ts', { setTimeout: (callback, delay) => { delays.push(delay); queueMicrotask(callback); return 1; }, clearTimeout: () => {}, fetch: (_url, options) => new Promise((_resolve, reject) => { options.signal.addEventListener('abort', () => reject(new Error('timeout')), { once: true }); }) });
  const result = await client.readTaskCenter(api.emptySnapshot());
  assert.deepEqual(delays, [10000, 10000, 10000, 10000, 10000]);
  assert.ok(Object.values(result).every(s => s.failed));
});
test('history polling slows on terminal states and uses bounded failure backoff', () => {
  const snapshot = api.emptySnapshot(); assert.equal(api.nextReadDelay(snapshot, 0), 30000);
  snapshot.generation.records = [{ ...source('generation', [generation()]), status: 'running' }];
  assert.equal(api.nextReadDelay(snapshot, 0), 5000); snapshot.generation.failed = true;
  assert.equal(api.nextReadDelay(snapshot, 1), 10000); assert.equal(api.nextReadDelay(snapshot, 9), 60000);
});
test('initial rendering keeps loading distinct from empty and exposes labeled filters', () => {
  const Component = loadTypeScript('components/TaskCenter.tsx', { URLSearchParams }).default;
  const html = renderToStaticMarkup(React.createElement(Component));
  for (const label of ['正在读取项目与任务', '任务状态', '搜索任务', '刷新记录']) assert.ok(html.includes(label));
  assert.ok(!html.includes('还没有任务记录'));
});
test('orphan SIM result remains downloadable without a broken project action', () => {
  const Component = loadTypeScript('components/TaskCenter.tsx', { URLSearchParams }).TaskDetail;
  const task = { ...source('generation', [generation()]), status: 'completed', simulation: true, resultUrl: `/api/assets/${b}/versions/${c}/media` };
  const html = renderToStaticMarkup(React.createElement(Component, { task, projectExists: false }));
  for (const label of ['人工演练视频', '下载归档视频', '所属项目暂未读到']) assert.ok(html.includes(label));
  assert.ok(!html.includes('打开项目')); assert.ok(!html.includes('取消任务')); assert.ok(!html.includes('重试生成'));
});
