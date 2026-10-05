import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadTypeScript } from './loadTypeScript.mjs';

const api = loadTypeScript('lib/generationPlans.ts');
const a = 'a'.repeat(32), b = 'b'.repeat(32), c = 'c'.repeat(32), d = 'd'.repeat(32), hash = '0'.repeat(64), date = '2026-10-05T00:00:00Z';
const opts = { model: 'local-test-model', mode: 'first_frame', video_ratio: '16:9', resolution: '720P', sound: 'off', shot_type: 'multi', style: 'realistic' };
const version = () => ({ version_id: b, number: 2, name: '人工图片', definition: '人工色块', created_at: date, source: { type: 'upload', project_id: 'project-a', upload_id: d }, media: { file_id: c, mime_type: 'image/png', sha256: hash, byte_size: 100, width: 640, height: 360, duration: null } });
const binding = () => ({ reference_id: d, asset_id: a, revision: 2, role: 'first_frame', kind: 'image', version: version(), target: { project_id: 'project-a', segment_id: 'seg-1', shot_number: 1 } });
const plan = () => ({ plan_id: a, created_at: date, content_hash: hash, content: { project_id: 'project-a', template_version: 1, options: { ...opts }, targets: [{ segment_id: 'seg-1', duration: 5, source_hash: hash, shots: [{ shot_number: 1, duration: 5, content: '人工分镜' }], bindings: [binding()], prompt: '固定提示词' }] }, confirmation: null, status: 'draft', current: true, impacts: [{ segment_id: 'seg-1', reasons: [], newer_version_references: [] }], execution_allowed: false, cost: { status: 'pending_confirmation', amount: null, currency: null }, execution_blockers: ['pricing_unconfirmed', 'model_and_privacy_unconfirmed', 'execution_not_implemented'] });
const clean = x => JSON.parse(JSON.stringify(x));
const confirmed = () => { const p = plan(); p.status = 'confirmed_local'; p.confirmation = { content_hash: hash, confirmed_at: date, scope: 'local_input_only' }; return p; };
const workspace = () => ({ project: { session_id: 'project-a', current_stage: 'storyboard', status: {}, meta: { video_first_frame_model: 'existing-model' }, artifacts: { storyboard: { episodes: [{ episode_number: 1, segments: [{ segment_id: 'seg-1', shots: [{ shot_number: 1 }] }, { segment_id: 'seg-2', shots: [{ shot_number: 1 }] }] }] } } }, assets: [{ asset_id: a, kind: 'image', versions: [version()] }], refs: [{ reference_id: d, project_id: 'project-a', asset_id: a, version_id: b, revision: 2, segment_id: 'seg-1', shot_number: 1, target_exists: true, has_newer_version: true }], plans: [] });
test('a plan pins a v2 image independently of full history and costs stay unknown', () => {
  const p = plan(); p.impacts[0].newer_version_references = [d];
  const result = api.parsePlan(p);
  assert.equal(result.content.targets[0].bindings[0].version.number, 2);
  assert.equal(result.current, true); assert.equal(result.cost.amount, null); assert.equal(result.execution_allowed, false);
});
for (const [name, change] of [
  ['paid or enabled result', p => { p.execution_allowed = true; }],
  ['zero price presented as free', p => { p.cost.amount = 0; }],
  ['unknown blocker', p => { p.execution_blockers[0] = 'future'; }],
  ['missing blocker', p => { p.execution_blockers.pop(); }],
  ['duplicate target', p => { p.content.targets.push(p.content.targets[0]); }],
  ['duplicate shot', p => { p.content.targets[0].shots.push(p.content.targets[0].shots[0]); }],
  ['missing source hash', p => { p.content.targets[0].source_hash = ''; }],
  ['wrong image role', p => { p.content.targets[0].bindings[0].role = 'context'; }],
  ['wrong image media', p => { p.content.targets[0].bindings[0].version.media.mime_type = 'text/html'; }],
  ['foreign project binding', p => { p.content.targets[0].bindings[0].target.project_id = 'other'; }],
  ['foreign segment binding', p => { p.content.targets[0].bindings[0].target.segment_id = 'seg-2'; }],
  ['foreign shot binding', p => { p.content.targets[0].bindings[0].target.shot_number = 99; }],
  ['confirmation with no saved acknowledgement', p => { p.status = 'confirmed_local'; }],
  ['missing impact', p => { p.impacts = []; }],
  ['false freshness', p => { p.impacts[0].reasons = ['source_changed']; }],
  ['unknown newer reference', p => { p.impacts[0].newer_version_references = [c]; }],
  ['blank prompt', p => { p.content.targets[0].prompt = ' '; }],
]) test(`reject unsafe plan: ${name}`, () => { const p = plan(); change(p); assert.throws(() => api.parsePlan(p)); });
test('a saved confirmation must match content and local scope', () => {
  assert.equal(api.parsePlan(confirmed()).confirmation.scope, 'local_input_only');
  const p = confirmed(); p.confirmation.content_hash = '1'.repeat(64); assert.throws(() => api.parsePlan(p));
  p.confirmation.content_hash = hash; p.confirmation.scope = 'execute'; assert.throws(() => api.parsePlan(p));
});
test('plan lists cannot accept duplicates or foreign project rows', () => {
  assert.throws(() => api.parsePlans([plan(), plan()], 'project-a'));
  assert.throws(() => api.parsePlans([plan()], 'other'));
});
test('explicit applicable reference is preserved; newer versions never upgrade the input', () => {
  const data = workspace(), draft = api.newDraft(data); draft.roles[d] = 'first_frame';
  const input = api.buildInput(data, draft);
  assert.equal(input.options.model, 'existing-model');
  assert.deepEqual(clean(input.targets[0].bindings), [{ reference_id: d, role: 'first_frame' }]);
  assert.equal(data.refs[0].version_id, b);
  draft.segment = 'seg-2'; assert.throws(() => api.buildInput(data, draft));
});
test('duplicate roles, invalid duration and missing reference are rejected before writes', () => {
  const data = workspace(), draft = api.newDraft(data);
  assert.throws(() => api.buildInput(data, draft)); draft.roles[d] = 'first_frame';
  for (const duration of ['0', '61', '1.5', '', 'NaN']) { draft.duration = duration; assert.throws(() => api.buildInput(data, draft)); }
  draft.duration = '5'; draft.options.mode = 'start_end_frame'; assert.throws(() => api.buildInput(data, draft));
  draft.options.mode = 'reference'; draft.roles[d] = 'reference_image'; assert.equal(api.buildInput(data, draft).targets.length, 1);
  data.refs[0].target_exists = false; assert.throws(() => api.buildInput(data, draft));
});
test('reads are GET-only and do not confirm, prepare, price or invoke models', async () => {
  const paths = [], data = workspace();
  const io = loadTypeScript('lib/generationPlans.ts', { setTimeout, clearTimeout, fetch: async (path, init) => {
    paths.push([path, init.method]);
    const refs = [{ ...data.refs[0], revision: 1, history: [{ version_id: b, selected_at: date }] }];
    const value = path.endsWith('/generation-plans') ? [plan()] : path.endsWith('/status') ? data.project : path.endsWith('/asset-references') ? refs : [{ ...data.assets[0], versions: [{ ...version(), number: 1 }] }];
    return Response.json(value);
  } });
  const result = await io.readWorkspace('project-a'); assert.equal(result.plans.length, 1);
  assert.equal(paths.length, 4); assert.ok(paths.every(([, method]) => method === 'GET'));
});
test('missing project still displays durable historical plans', async () => {
  const io = loadTypeScript('lib/generationPlans.ts', { setTimeout, clearTimeout, fetch: async path => path.endsWith('/status') ? Response.json({ error: { code: 'NOT_FOUND', message: '项目不存在' } }, { status: 404 }) : Response.json(path.endsWith('/generation-plans') ? [plan()] : []) });
  const result = await io.readWorkspace('project-a'); assert.equal(result.project, null); assert.equal(result.plans.length, 1);
});
test('uncertain writes never retry, including malformed success and server error', async () => {
  const input = api.buildInput(workspace(), { ...api.newDraft(workspace()), roles: { [d]: 'first_frame' } });
  for (const response of [null, { status: 500 }, { status: 201 }]) {
    let calls = 0;
    const io = loadTypeScript('lib/generationPlans.ts', { setTimeout, clearTimeout, fetch: async () => { calls++; if (!response) throw new Error('private diagnostic'); return Response.json({ error: { message: '暂不可用' } }, response); } });
    await assert.rejects(io.savePlan(input), e => e.uncertain === true && !e.message.includes('private diagnostic'));
    assert.equal(calls, 1);
  }
});
test('confirm sends only the exact hash and local acknowledgement', async () => {
  let sent;
  const io = loadTypeScript('lib/generationPlans.ts', { setTimeout, clearTimeout, fetch: async (path, init) => { sent = [path, JSON.parse(init.body)]; return Response.json(confirmed()); } });
  await io.confirmPlan(api.parsePlan(plan())); assert.deepEqual(sent, [`/api/generation-plans/${a}/confirm`, { content_hash: hash, local_only: true }]);
  const stale = plan(); stale.current = false; stale.impacts[0].reasons = ['reference_changed']; assert.throws(() => io.confirmPlan(api.parsePlan(stale)));
});
test('input preview is content-bound and never exposes private paths', async () => {
  const p = api.parsePlan(confirmed());
  const value = { plan_id: a, content_hash: hash, options: opts, execution_allowed: false, cost: p.cost, targets: [{ segment_id: 'seg-1', duration: 5, prompt: '固定提示词', first_frame_url: '/private/artificial/file.png', last_frame_url: null, reference_image_urls: [] }] };
  const io = loadTypeScript('lib/generationPlans.ts', { setTimeout, clearTimeout, fetch: async () => Response.json(value) });
  const result = await io.checkPlan(p); assert.deepEqual(clean(result), { count: 1, images: 1 }); assert.ok(!JSON.stringify(result).includes('private'));
  value.targets[0].prompt = 'unexpected'; await assert.rejects(io.checkPlan(p), e => e.uncertain === true);
});
