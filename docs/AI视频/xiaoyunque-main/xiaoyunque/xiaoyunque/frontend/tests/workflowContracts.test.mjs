import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadTypeScript } from './loadTypeScript.mjs';

const { parseProjectSnapshot, parseSessions, apiErrorMessage } = loadTypeScript('lib/workflowTypes.ts');
const snapshot = { session_id: 'artificial', current_stage: 'script_generation', status: { init: 'completed', script_generation: 'pending' }, error: null, meta: { idea: '人工回归样例', expand_idea: false, video_ratio: '16:9', video_generation_mode: 'first_frame' }, artifacts: {} };

test('restore both current and legacy snapshots without changing choices or extra fields', () => {
  const current = parseProjectSnapshot({ ...snapshot, schema_version: 1, future_field: { enabled: true } });
  assert.equal(current.meta.expand_idea, false);
  assert.equal(current.meta.video_ratio, '16:9');
  assert.equal(current.future_field.enabled, true);
  assert.equal(parseProjectSnapshot({ ...snapshot, meta: undefined, idea: '旧项目' }).idea, '旧项目');
});
for (const bad of [null, { ...snapshot, status: [] }, { ...snapshot, status: { script_generation: 1 } }, { ...snapshot, meta: { expand_idea: 'false' } }, { ...snapshot, meta: { video_generation_mode: 'unsupported' } }, { ...snapshot, artifacts: [] }, { ...snapshot, stage_progress: { script_generation: { percent: 'half' } } }]) {
  test(`reject malformed restoration envelope ${JSON.stringify(bad)}`, () => assert.throws(() => parseProjectSnapshot(bad)));
}
test('history accepts future status values but refuses invalid timestamps', () => {
  const sessions = parseSessions({ sessions: [{ id: 'test', date: 1735689600, status: { init: 'future' } }] });
  assert.equal(sessions[0].status.init, 'future');
  assert.throws(() => parseSessions({ sessions: [{ id: 'test', date: 'invalid' }] }));
});
test('safe structured and legacy error messages are readable', () => {
  assert.equal(apiErrorMessage({ error: { code: 'NOT_FOUND', message: '项目不存在' } }, '失败'), '项目不存在');
  assert.equal(apiErrorMessage({ detail: '旧错误' }, '失败'), '旧错误');
  assert.equal(apiErrorMessage({ detail: [{ private: 'must not show' }] }, '失败'), '失败');
});
test('HTTP client surfaces structured error without stack or raw payload', async () => {
  const api = loadTypeScript('lib/workflowApi.ts', { fetch: async () => new Response(JSON.stringify({ error: { code: 'NOT_FOUND', message: '项目不存在' }, private: 'must not show' }), { status: 404 }) });
  await assert.rejects(api.stopProject('missing'), /^Error: 项目不存在 \(HTTP 404\)$/);
});
const api = loadTypeScript('lib/workflowApi.ts');
async function events(text) { const result = []; for await (const event of api.parseStreamEvents(new Response(text))) result.push(event); return result; }
test('stream keeps content and final event with no trailing newline, ignoring heartbeats and corrupt lines', async () => {
  const result = await events('{"type":"heartbeat"}\ninvalid\n{"type":"content","content":"小云雀"}\n{"type":"stage_complete","status":"waiting"}');
  assert.equal(result.length, 2); assert.equal(result[0].content, '小云雀'); assert.equal(result[1].status, 'waiting');
});
test('truncated stream is an error, not success', async () => {
  await assert.rejects(events('{"type":"progress","percent":50}\n'), /提前结束/);
});
test('explicit terminal error remains visible', async () => {
  const result = await events('{"type":"error","message":"测试失败"}');
  assert.equal(result[0].message, '测试失败');
});

test('malformed terminal events do not declare successful completion', async () => {
  await assert.rejects(events('{"type":"stage_complete","data":[]}'), /提前结束/);
  const result = await events('{"type":"future_event"}\n{"type":"content","data":{"asset_complete":{"type":"scene","id":"x","versions":5}}}\n{"type":"stage_complete"}');
  assert.equal(result.length, 1);
});
