/** Shared timestamped artifact directories for frontend integration tools. */

import { mkdir, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const TEST_RUNS_DIR = join(
  fileURLToPath(new URL('.', import.meta.url)),
  'test_run',
);

export function timestamp() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
    + `_${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}`;
}

export async function createRunDirectory(testName, { root = TEST_RUNS_DIR } = {}) {
  if (!/^[a-z0-9_-]+$/.test(testName)) {
    throw new Error('test name must contain only lowercase letters, digits, underscores, or hyphens');
  }
  await mkdir(root, { recursive: true });
  const base = `${testName}_${timestamp()}`;
  for (let suffix = 0; suffix < 1000; suffix += 1) {
    const name = suffix === 0 ? base : `${base}-${String(suffix).padStart(2, '0')}`;
    const directory = join(root, name);
    try {
      await mkdir(directory);
      return directory;
    } catch (error) {
      if (error?.code !== 'EEXIST') throw error;
    }
  }
  throw new Error(`could not create a unique run directory for ${base}`);
}

export async function writeJson(directory, filename, value) {
  const path = join(directory, filename);
  await (await import('node:fs/promises')).writeFile(path, `${JSON.stringify(value, null, 2)}\n`);
  return path;
}

export async function nonEmptyDirectory(directory) {
  return (await readdir(directory)).length > 0;
}
