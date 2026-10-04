import assert from 'node:assert/strict';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { test } from 'node:test';
import typescript from 'typescript';

// Test the actual TypeScript module using the already-installed compiler.
const source = fs.readFileSync(fileURLToPath(new URL('../lib/projectStatus.ts', import.meta.url)), 'utf8');
const compiled = typescript.transpileModule(source, {
  compilerOptions: { module: typescript.ModuleKind.CommonJS },
}).outputText;
const context = { exports: {} };
vm.runInNewContext(compiled, context);
const { getProjectStatus } = context.exports;
const order = ['script', 'character', 'storyboard', 'reference', 'video', 'post'];
const stages = status => Object.fromEntries(order.map(id => [id, { status }]));

test('a new project and an empty snapshot are pending', () => {
  assert.equal(getProjectStatus(stages('pending'), order), 'pending');
  assert.equal(getProjectStatus({}, order), 'pending');
  assert.equal(getProjectStatus({}, []), 'pending');
});
test('init and partial completion do not complete the project', () => {
  const state = { ...stages('pending'), init: { status: 'completed' }, script: { status: 'completed' } };
  assert.equal(getProjectStatus(state, order), 'pending');
});
test('only every business stage completed means completed', () => {
  assert.equal(getProjectStatus(stages('completed'), order), 'completed');
  const state = stages('completed');
  delete state.video;
  assert.equal(getProjectStatus(state, order), 'pending');
});
for (const status of ['running', 'waiting', 'error', 'stopped']) {
  test(`unfinished stage shows ${status}`, () => {
    assert.equal(getProjectStatus({ ...stages('completed'), video: { status } }, order), status);
  });
}
test('a failure is visible when another stage awaits confirmation', () => {
  assert.equal(getProjectStatus({ ...stages('waiting'), video: { status: 'error' } }, order), 'error');
});
