import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { test } from 'node:test';
import { loadTypeScript } from './loadTypeScript.mjs';

// Artificial data aligned with existing backend payloads. No model calls or fabricated quality claims.
const asset = { id: 'Scene_1', name: '清晨山谷', description: '晨光穿过山谷', selected: 'result/test/v1.jpg', versions: ['result/test/v1.jpg'], status: 'done', episode: 1 };
const script = { title: '人工样例短片', logline: '一只鸟飞过山谷', characters: [{ name: '云雀', description: '小鸟', personality: ['勇敢'], role: '主角' }], settings: [{ name: '山谷', description: '清晨' }], episodes: [{ episode_number: 1, act_number: 1, act_title: '启程', content: '晨光里启程' }] };
const segment = { segment_id: 'Scene_1', segment_number: 1, episode_number: 1, location: '山谷', characters: ['云雀'], total_duration: 5, shots: [{ shot_number: 1, shot_type: '全景', duration: 5, content: '晨光穿过山谷' }] };
const fixtures = [
  ['ScriptStage', script, '人工样例短片'],
  ['ScriptStage', { phase: 'logline_selection', logline_options: [{ logline: '一只鸟飞过山谷', who: '云雀', goal: '启程', conflict: '雨', twist: '放晴', theme: '勇气' }] }, '一只鸟飞过山谷'],
  ['CharacterStage', { characters: [asset], settings: [] }, '清晨山谷'],
  ['StoryboardStage', { episodes: [{ episode_number: 1, episode_title: '启程', segments: [segment] }] }, '晨光穿过山谷'],
  ['ReferenceStage', { scenes: [asset] }, '清晨山谷'],
  ['ReferenceStage', { scene_images: { Scene_1: 'result/test/legacy.jpg' } }, '场景 1'],
  ['VideoStage', { clips: [{ ...asset, selected: 'result/test/v1.mp4', versions: ['result/test/v1.mp4'] }] }, '清晨山谷'],
  ['VideoStage', { video_clips: { Scene_1: 'result/test/legacy.mp4' } }, '片段 1'],
  ['PostProductionStage', { final_videos: [{ episode: 1, name: '启程', path: 'result/test/final.mp4' }] }, '启程'],
  ['PostProductionStage', { final_video: 'result/test/legacy.mp4' }, 'legacy.mp4'],
];
const noop = () => {};
for (const [name, artifact, expected] of fixtures) {
  test(`${name} renders ${expected} using actual component source`, () => {
    const Component = loadTypeScript(`components/stages/${name}.tsx`).default;
    const html = renderToStaticMarkup(React.createElement(Component, { state: { status: 'waiting', progress: 100, progressMessage: '', artifact, error: null }, sessionId: 'artificial', onConfirm: noop, onIntervene: noop, onRegenerate: noop, onSaveSelections: async () => {}, showConfirm: true, isRunning: false, scriptArtifact: script, referenceArtifact: { scenes: [asset] } }));
    assert.ok(html.includes(expected), `Missing content ${expected}`);
    if (name === 'ReferenceStage') assert.ok(html.includes('/result/test/'), 'Existing dynamic media URL must stay intact');
    if (name === 'VideoStage') assert.ok(html.includes('<video'), 'Video preview must stay available');
  });
}
