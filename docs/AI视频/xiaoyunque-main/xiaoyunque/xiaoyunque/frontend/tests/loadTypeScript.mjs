import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import typescript from 'typescript';

const root = fileURLToPath(new URL('../', import.meta.url));
const nativeRequire = createRequire(import.meta.url);

// Load the real source with the installed compiler. Only external I/O is replaced by each test.
export function loadTypeScript(file, globals = {}) {
  const cache = new Map();
  const load = filename => {
    if (cache.has(filename)) return cache.get(filename).exports;
    const loadedModule = { exports: {} };
    cache.set(filename, loadedModule);
    const source = fs.readFileSync(filename, 'utf8');
    const compiled = typescript.transpileModule(source, {
      compilerOptions: { module: typescript.ModuleKind.CommonJS, target: typescript.ScriptTarget.ES2022, jsx: typescript.JsxEmit.ReactJSX, esModuleInterop: true },
    }).outputText;
    const require = specifier => {
      if (!specifier.startsWith('.') && !specifier.startsWith('@/')) return nativeRequire(specifier);
      const base = specifier.startsWith('@/') ? path.join(root, specifier.slice(2)) : path.resolve(path.dirname(filename), specifier);
      const target = [base, `${base}.ts`, `${base}.tsx`, path.join(base, 'index.ts')].find(candidate => fs.existsSync(candidate) && fs.statSync(candidate).isFile());
      if (!target) throw new Error(`Cannot resolve ${specifier}`);
      return load(target);
    };
    vm.runInNewContext(compiled, { module: loadedModule, exports: loadedModule.exports, require, process, console, Response, TextDecoder, AbortController, ...globals }, { filename });
    return loadedModule.exports;
  };
  return load(path.join(root, file));
}
