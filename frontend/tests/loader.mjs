import { readFile } from 'node:fs/promises';
import { transformWithOxc } from 'vite';

export async function resolve(specifier, context, next) {
  try { return await next(specifier, context); }
  catch (error) {
    if (specifier.startsWith('.') && context.parentURL?.includes('/src/')) {
      for (const suffix of ['.ts', '.tsx']) {
        try { return await next(specifier + suffix, context); } catch { /* try next suffix */ }
      }
    }
    throw error;
  }
}
export async function load(url, context, next) {
  if (/\/src\/.*\.tsx?$/.test(url)) {
    const source = (await transformWithOxc(await readFile(new URL(url), 'utf8'),
      new URL(url).pathname, { jsx: { runtime: 'automatic' } })).code;
    return { format: 'module', source, shortCircuit: true };
  }
  return next(url, context);
}
