import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadTypeScript } from './loadTypeScript.mjs';

const api = loadTypeScript('lib/workflowApi.ts');
async function events(text) { const rows = []; for await (const row of api.parseStreamEvents(new Response(text))) rows.push(row); return rows; }

test('actual backend completion dictionary survives chunk boundaries and done marker', async () => {
  const complete = { type: 'stage_complete', stage: 'script_generation', status: { init: 'completed', script_generation: 'waiting' }, requires_intervention: true, openclaw: '人工协议样例' };
  const text = `${JSON.stringify(complete)}\n{"type":"done"}\n`;
  const bytes = new TextEncoder().encode(text);
  const response = new Response(new ReadableStream({ start(controller) { for (const chunk of [bytes.slice(0, 7), bytes.slice(7, 27), bytes.slice(27)]) controller.enqueue(chunk); controller.close(); } }));
  const rows = []; for await (const row of api.parseStreamEvents(response)) rows.push(row);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].status.script_generation, 'waiting');
});
for (const status of [[], { script_generation: 1 }, { script_generation: null }]) {
  test(`malformed completion status is rejected ${JSON.stringify(status)}`, async () => {
    await assert.rejects(events(JSON.stringify({ type: 'stage_complete', status })), /提前结束/);
  });
}

const project = loadTypeScript('lib/projectStatus.ts');
const shell = loadTypeScript('components/AppShell.tsx');
const center = loadTypeScript('lib/taskCenter.ts');
const done = Object.fromEntries(project.BUSINESS_STAGES.map(id => [id, 'completed']));
test('six completed business stages appear in sidebar without legacy init', () => {
  const record = shell.projectReviewTaskFromSession({ id: 'artificial', status: done });
  assert.equal(record.status, 'completed'); assert.equal(record.progress, 100);
  assert.ok(record.href.endsWith('&stage=post_production'));
});
test('init and terminal marker cannot complete a partial project or inflate progress', () => {
  const status = { init: 'completed', completed: 'completed', script_generation: 'completed', character_design: 'running' };
  assert.equal(shell.projectReviewTaskFromSession({ id: 'artificial', status }), null);
  assert.equal(shell.projectTaskFromSession({ id: 'artificial', status }).progress, 17);
});
test('legacy status aliases agree across sidebar, homepage, and task center', () => {
  const status = { ...done, storyboard: 'waiting_intervention' };
  assert.equal(shell.projectReviewTaskFromSession({ id: 'artificial', status }).status, 'waiting');
  assert.equal(center.parseTaskSource('workflow', { sessions: [{ id: 'artificial', status }] })[0].status, 'waiting');
  const stages = Object.fromEntries(Object.entries(status).map(([id, status]) => [id, { status }]));
  assert.equal(project.getProjectStatus(stages, project.BUSINESS_STAGES), 'waiting');
  const complete = Object.fromEntries(project.BUSINESS_STAGES.map(id => [id, 'session_completed']));
  assert.equal(center.parseTaskSource('workflow', { sessions: [{ id: 'artificial', status: complete }] })[0].status, 'completed');
});
test('unknown business stage remains unknown instead of completed', () => {
  const status = { ...done, storyboard: 'future' };
  assert.equal(shell.projectReviewTaskFromSession({ id: 'artificial', status }), null);
  assert.equal(center.parseTaskSource('workflow', { sessions: [{ id: 'artificial', status }] })[0].status, 'unknown');
});

const json = loadTypeScript('lib/legacyJson.ts');
test('legacy reader exposes structured error as text rather than a React object', async () => {
  await assert.rejects(json.readLegacyJsonResponse(new Response('{"error":{"code":"FAIL","message":"安全提示"}}', { status: 500 })), /^Error: 安全提示$/);
  await assert.rejects(json.readLegacyJsonResponse(new Response('{"success":false,"error":{"message":"安全提示"}}')), /^Error: 安全提示$/);
});
test('legacy reader never echoes upstream HTML or arbitrary private fields', async () => {
  await assert.rejects(json.readLegacyJsonResponse(new Response('<html>artificial-private-detail</html>', { status: 502 })), e => !e.message.includes('artificial-private-detail'));
  await assert.rejects(json.readLegacyJsonResponse(new Response('{"private":"artificial-private-detail"}', { status: 500 })), e => !e.message.includes('artificial-private-detail'));
});
test('legacy success format still works', async () => {
  const response = await json.readLegacyJsonResponse(new Response('{"success":true,"result":"artificial"}'));
  assert.equal(response.result, 'artificial');
});
test('unattributed media is a candidate version and uploaded filenames retain source', () => {
  const utils = loadTypeScript('components/stages/utils.ts');
  assert.equal(utils.assetVersionLabel('artificial_v2.png', 0), '候选版本: v2');
  assert.equal(utils.assetVersionLabel('artificial_upload_v2.png', 0), '用户上传: v2');
});
