import { mkdir, copyFile } from 'node:fs/promises';
await mkdir('dist/server', { recursive: true });
await mkdir('dist/client', { recursive: true });
await copyFile('worker.mjs', 'dist/server/index.js');
