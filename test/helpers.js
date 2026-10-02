import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export function makeDataDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'pgv-test-'));
}

// 每个测试文件是独立进程；先指定数据目录再导入，保证照片/数据库全部隔离
export async function sandbox() {
  const dir = makeDataDir();
  process.env.PGV_DATA_DIR = dir;
  const dbMod = await import('../src/db.js');
  const [invitations, access, gate] = await Promise.all([
    import('../src/domain/invitations.js'),
    import('../src/domain/access.js'),
    import('../src/adapters/gate.js'),
  ]);
  const db = dbMod.openDb(path.join(dir, 'park.db'));
  return { dir, db, invitations: invitations.default || invitations, access: access.default || access, gate: gate.default || gate };
}

export function iso(local) {
  return new Date(local).toISOString();
}

export function offsetIso(minutes, base = Date.now()) {
  return new Date(base + minutes * 60000).toISOString();
}
