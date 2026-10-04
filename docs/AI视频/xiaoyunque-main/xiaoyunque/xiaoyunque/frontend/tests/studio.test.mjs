import assert from 'node:assert/strict';
import fs from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { test } from 'node:test';
import { loadTypeScript } from './loadTypeScript.mjs';

const studio = loadTypeScript('lib/studio.ts');
const order = ['script_generation', 'character_design', 'storyboard', 'reference', 'video', 'post'];
test('inspiration filters retain original prompts and every local cover exists', () => {
  const original = loadTypeScript('config/examples.ts').PROMPT_EXAMPLES;
  assert.equal(studio.filterPresets('全部').length, original.length);
  for (const [index, preset] of studio.STUDIO_PRESETS.entries()) {
    assert.equal(preset.text, original[index].text);
    assert.ok(fs.existsSync(new URL(`../public${preset.image}`, import.meta.url)));
    assert.ok(studio.filterPresets(preset.category).some(item => item.text === preset.text));
  }
  assert.equal(studio.filterPresets('治愈').length, 1);
});
test('project search trims whitespace and handles Chinese and Latin names', () => {
  assert.equal(studio.matchesProject('太空短片 Studio', ' STUDIO '), true);
  assert.equal(studio.matchesProject('太空短片 Studio', '短片'), true);
  assert.equal(studio.matchesProject('太空短片 Studio', '悬疑'), false);
});
test('project badges distinguish drafts, partial progress, unknown and business completion', () => {
  assert.equal(studio.projectSummary({ init: 'completed' }, order).label, '创作草稿');
  assert.equal(studio.projectSummary({ script_generation: 'completed' }, order).label, '待继续');
  assert.equal(studio.projectSummary({ script_generation: 'future' }, order).label, '状态待同步');
  assert.equal(studio.projectSummary({ script_generation: 'running', character_design: 'error' }, order).label, '创作中');
  const done = studio.projectSummary(Object.fromEntries(order.map(id => [id, 'session_completed'])), order);
  assert.equal(done.label, '已完成'); assert.equal(done.completed, order.length);
});
const params = { idea: '人工界面验收故事', episodes: 2, style: 'realistic', video_ratio: '16:9', file_path: 'uploads/artificial.txt', expand_idea: true, web_search: true, enable_concurrency: false, video_generation_mode: 'first_frame' };
test('draft sends exactly one persistence request and cannot expand, search or execute stages', async () => {
  const requests = [];
  const client = loadTypeScript('lib/studio.ts', { fetch: async (url, options) => {
    requests.push({ url, options });
    return new Response(JSON.stringify({ session_id: 'a'.repeat(32), status: { script_generation: 'pending' }, params }));
  } });
  const result = await client.saveStudioDraft(params);
  assert.equal(result.session_id, 'a'.repeat(32));
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/api/project/start'); assert.equal(requests[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(requests[0].options.body), { ...params, expand_idea: false, web_search: false });
  assert.equal(params.expand_idea, true, 'Caller choices must not be mutated');
});
test('failed draft preserves a readable API error and never automatically retries a write', async () => {
  let count = 0;
  const client = loadTypeScript('lib/studio.ts', { fetch: async () => {
    count++; return new Response(JSON.stringify({ error: { code: 'UNAVAILABLE', message: '保存暂不可用' }, stack: 'private' }), { status: 503 });
  } });
  await assert.rejects(client.saveStudioDraft(params), /保存暂不可用 \(HTTP 503\)/); assert.equal(count, 1);
});
for (const id of ['', 'invalid', undefined]) test(`malformed draft id ${id} cannot declare saved`, async () => {
  const client = loadTypeScript('lib/studio.ts', { fetch: async () => new Response(JSON.stringify({ session_id: id })) });
  await assert.rejects(client.saveStudioDraft(params), /保存结果暂时无法确认/);
});
for (const status of [undefined, null, [], { script_generation: 1 }]) test(`malformed draft status ${JSON.stringify(status)} cannot declare saved`, async () => {
  const client = loadTypeScript('lib/studio.ts', { fetch: async () => new Response(JSON.stringify({ session_id: 'a'.repeat(32), status })) });
  await assert.rejects(client.saveStudioDraft(params), /保存结果暂时无法确认/);
});
const HomePage = loadTypeScript('components/HomePage.tsx').default;
const base = { onStartProject() {}, onSaveDraft: async () => {}, onResumeProject() {}, onDeleteSession: async () => {}, onRefreshHistory() {}, history: [], historyLoading: false, historyError: '' };
for (const [name, props, expected] of [
  ['loading', { historyLoading: true }, '正在读取项目'],
  ['empty', {}, '你的下一部作品，从这里开始'],
  ['error', { historyError: '读取项目失败' }, '重新读取'],
  ['draft', { history: [{ id: 'b'.repeat(32), idea: '人工验收草稿', style: 'realistic', date: 1, stages: { init: 'completed', script_generation: 'pending' } }] }, '创作草稿'],
]) test(`home renders truthful ${name} state and keeps existing creation entries`, () => {
  const html = renderToStaticMarkup(React.createElement(HomePage, { ...base, ...props }));
  assert.ok(html.includes(expected));
  for (const text of ['保存创作草稿', '逐步创作', '一键生成', '临时工作台', '数字人口播', '我的项目']) assert.ok(html.includes(text));
  assert.ok(html.includes('id="studio-idea"')); assert.ok(html.includes('aria-pressed="true"'));
  if (name === 'error') assert.ok(!html.includes('你的下一部作品，从这里开始'), 'API failure must not appear as empty history');
});
