import assert from 'node:assert/strict';
import { test } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { loadTypeScript } from './loadTypeScript.mjs';

const a = 'a'.repeat(32), b = 'b'.repeat(32), c = 'c'.repeat(32), d = 'd'.repeat(32);
const time = '2026-10-05T00:00:00+00:00';
const version = (number = 1) => ({ version_id: number === 1 ? b : c, number, name: number === 1 ? '林舟 · 旧名称' : '林舟 · 新名称', definition: '人工人物设定', created_at: time, source: { type: 'manual', project_id: null, item_id: null, stage: null }, media: null });
const asset = () => ({ asset_id: a, kind: 'character', versions: [version(1), version(2)] });
const reference = () => ({ reference_id: d, project_id: 'project-a', asset_id: a, version_id: b, segment_id: null, shot_number: null, revision: 1, history: [{ version_id: b, selected_at: time }], has_newer_version: true, target_exists: true });
const api = loadTypeScript('lib/assets.ts');
const globals = { setTimeout, clearTimeout, FormData, File };
const normalize = v => JSON.parse(JSON.stringify(v));

test('script definitions render readable content while retaining unknown business settings', () => {
  const v = { ...version(), source: { type: 'script' }, definition: JSON.stringify({ id: 'role-1', name: '林舟', description: '年轻信使', appearance: '浅色外套', favorite: ['读书', '远行'] }) };
  assert.equal(api.displayDefinition(v), '描述：年轻信使\n外观：浅色外套\n其他设定：读书、远行');
  assert.ok(v.definition.includes('role-1'), 'The persisted source stays intact');
});
test('manual JSON and legacy plain script definitions remain unchanged', () => {
  const v = { ...version(), definition: '{"description":"用户文字"}' };
  assert.equal(api.displayDefinition(v), v.definition);
  assert.equal(api.displayDefinition({ ...v, source: { type: 'script' }, definition: '原文字定义' }), '原文字定义');
});

test('a newer version remains distinct from the explicitly pinned project reference', () => {
  const result = api.parseAsset(asset()), ref = api.parseReference(reference());
  assert.equal(result.versions.at(-1).version_id, c);
  assert.equal(ref.version_id, b); assert.equal(ref.has_newer_version, true);
});
for (const [name, change] of [
  ['unknown kind', x => { x.kind = 'future'; }],
  ['missing history', x => { x.versions = []; }],
  ['duplicate version', x => { x.versions[1].version_id = b; }],
  ['nonsequential version', x => { x.versions[1].number = 9; }],
  ['empty definition', x => { x.versions[0].definition = ' '; }],
  ['unknown source', x => { x.versions[0].source.type = 'future'; }],
  ['invalid timestamp', x => { x.versions[0].created_at = 'invalid'; }],
  ['manual media placeholder', x => { x.kind = 'image'; }],
]) test(`reject unsafe asset response: ${name}`, () => { const x = asset(); change(x); assert.throws(() => api.parseAsset(x), /格式异常/); });
test('uploaded media has verified metadata and never includes arbitrary server paths', () => {
  const x = { asset_id: a, kind: 'image', versions: [{ ...version(), source: { type: 'upload', project_id: 'project-a', upload_id: d }, media: { file_id: b, mime_type: 'image/png', sha256: '0'.repeat(64), byte_size: 1024, width: 160, height: 90, duration: null, private_path: '/private/artificial' } }] };
  const parsed = api.parseAsset(x);
  assert.equal(parsed.versions[0].source.upload_id, d);
  assert.equal(parsed.versions[0].media.private_path, undefined);
  x.versions[0].media.duration = 1;
  assert.throws(() => api.parseAsset(x), /格式异常/);
});
for (const [name, change] of [
  ['shot without segment', x => { x.shot_number = 1; }],
  ['history revision mismatch', x => { x.revision = 2; }],
  ['last choice mismatch', x => { x.version_id = c; }],
  ['missing target truth', x => { delete x.target_exists; }],
]) test(`reject ambiguous reference: ${name}`, () => { const x = reference(); change(x); assert.throws(() => api.parseReference(x), /格式异常/); });
test('duplicate assets or references cannot silently overwrite one another', () => {
  assert.throws(() => api.parseAssets([asset(), asset()]));
  assert.throws(() => api.parseReferences([reference(), reference()]));
});
test('project filter includes imported origins or explicit references, not unrelated manual assets', () => {
  const other = { ...asset(), asset_id: d };
  assert.equal(api.filterAssets([asset(), other], [reference()], 'project-a', 'project', 'all', '').length, 1);
  assert.equal(api.filterAssets([asset(), other], [], 'project-a', 'project', 'all', '').length, 0);
  assert.equal(api.filterAssets([asset()], [], 'project-a', 'all', 'character', ' 旧名称 ').length, 1);
  assert.equal(api.filterAssets([asset()], [], 'project-a', 'all', 'image', '').length, 0);
  other.versions[0].source = { type: 'script', project_id: 'project-a', item_id: 'role-1', stage: 'script_generation' };
  assert.equal(api.filterAssets([other], [], 'project-a', 'project', 'all', '').length, 1);
});
test('targets match backend legacy segment fallback and exclude duplicate or nonexistent shots', () => {
  const snapshot = { artifacts: { storyboard: { episodes: [{ episode_number: 1, segments: [{ shots: [{ shot_number: 1 }, { shot_number: 2 }, { shot_number: 2 }] }, { segment_id: 'duplicate', shots: [] }, { segment_id: 'duplicate', shots: [] }] }] } } };
  const targets = api.targetOptions(snapshot);
  assert.deepEqual(normalize(targets.map(t => t.key)), ['project', 'seg_01_01', 'seg_01_01:1']);
  assert.deepEqual(normalize(api.targetOptions({})), [{ key: 'project', label: '整个项目', segment_id: null, shot_number: null }]);
});
test('imports include real selected sources, preserve aliases, and exclude missing selections', () => {
  const result = api.importOptions({ artifacts: { script_generation: { characters: [{ id: 'role-1', name: '林舟' }] }, reference: { scene_map: { 'seg-1': 'code/result/artificial.png' } }, video: { clips: [{ id: 'seg-1', selected: '' }] } } });
  assert.equal(result.length, 2);
  assert.equal(result[1].stage, 'reference_generation'); assert.equal(result[1].item_type, 'scenes');
});
test('reference update sends the captured revision once and preserves conflict errors', async () => {
  const requests = [];
  const client = loadTypeScript('lib/assets.ts', { ...globals, fetch: async (path, options) => {
    requests.push({ path, options }); return new Response(JSON.stringify({ error: { code: 'REFERENCE_CONFLICT', message: '引用已变化，请重新查看后确认' } }), { status: 409 });
  } });
  await assert.rejects(client.updateReference(reference(), c), e => e.code === 'REFERENCE_CONFLICT' && !e.uncertain);
  assert.equal(requests.length, 1); assert.equal(requests[0].options.method, 'PATCH');
  assert.deepEqual(JSON.parse(requests[0].options.body), { version_id: c, expected_revision: 1 });
});
test('disconnected non-idempotent saves are uncertain and never retried automatically', async () => {
  let calls = 0;
  const client = loadTypeScript('lib/assets.ts', { ...globals, fetch: async () => { calls++; throw new Error('private artificial transport'); } });
  await assert.rejects(client.createTextAsset('character', '林舟', '人工设定'), e => e.uncertain && !e.message.includes('private'));
  assert.equal(calls, 1);
});
test('a malformed successful write cannot be presented as saved', async () => {
  const client = loadTypeScript('lib/assets.ts', { ...globals, fetch: async () => new Response(JSON.stringify({ asset_id: a, versions: [] })) });
  await assert.rejects(client.createTextAsset('prop', '信件', '人工设定'), e => e.uncertain && e.code === 'INVALID_RESPONSE');
});
test('a mismatched reference response cannot claim the selected version is in use', async () => {
  const client = loadTypeScript('lib/assets.ts', { ...globals, fetch: async () => new Response(JSON.stringify(reference())) });
  await assert.rejects(client.bindAsset('project-a', a, c, { segment_id: null, shot_number: null }), e => e.uncertain);
});
test('binding a displayed target only sends the API fields, excluding its label and key', async () => {
  const requests = [];
  const client = loadTypeScript('lib/assets.ts', { ...globals, fetch: async (path, options) => { requests.push({ path, options }); return new Response(JSON.stringify(reference())); } });
  await client.bindAsset('project-a', a, b, { segment_id: null, shot_number: null, key: 'project', label: '整个项目' });
  assert.deepEqual(JSON.parse(requests[0].options.body), { project_id: 'project-a', asset_id: a, version_id: b, segment_id: null, shot_number: null });
});
test('upload preserves its idempotency ID without sending a client file path', async () => {
  const x = { asset_id: a, kind: 'image', versions: [{ ...version(), source: { type: 'upload', project_id: 'project-a', upload_id: d }, media: { file_id: b, mime_type: 'image/png', sha256: '0'.repeat(64), byte_size: 10, width: 160, height: 90, duration: null } }] };
  const requests = [];
  const client = loadTypeScript('lib/assets.ts', { ...globals, fetch: async (path, options) => { requests.push({ path, options }); return new Response(JSON.stringify(x)); } });
  await client.uploadAsset('project-a', d, new File(['artificial'], 'artificial.png', { type: 'image/png' }), '人工图', '人工验收');
  assert.equal(requests.length, 1); assert.equal(requests[0].options.body.get('upload_id'), d);
  assert.equal(requests[0].options.body.get('file_path'), null);
  assert.equal(requests[0].options.headers, undefined, 'Browser must set the multipart boundary');
});
test('initial rendering shows loading, project return, and named creation controls', () => {
  const Component = loadTypeScript('components/ProjectAssets.tsx').default;
  const html = renderToStaticMarkup(React.createElement(Component, { projectId: 'project-a' }));
  for (const label of ['正在读取项目与素材', '返回项目', '新建文字', '上传媒体', '项目素材', '全部素材']) assert.ok(html.includes(label));
  assert.ok(!html.includes('素材库还没有内容'), 'Pending reads must not masquerade as an empty library');
});
