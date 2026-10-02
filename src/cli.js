#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { DATA_DIR, DB_PATH, PORT } from './config.js';
import { openDb, closeDb } from './db.js';
import { start } from './server.js';
import { seed } from './seed.js';

const cmd = process.argv[2] || 'start';

if (cmd === 'start') {
  const db = openDb(DB_PATH);
  start(PORT, db);
} else if (cmd === 'seed') {
  // 幂等：仅当没有邀请时写入演示数据
  const db = openDb(DB_PATH);
  const n = db.prepare('SELECT COUNT(*) AS n FROM invitations').get().n;
  if (n > 0) {
    console.log(`数据库已有 ${n} 张邀请，跳过种子。如需重置：npm run reset && npm run seed`);
  } else {
    const codes = seed(db);
    console.log('演示数据已写入：');
    console.log(`  在园车辆邀请码：${codes.normalInPark}`);
    console.log(`  跨夜超时邀请码：${codes.overnight}`);
    console.log(`  已完成免费出场：${codes.finished}`);
    console.log('  启动服务：npm start');
  }
  closeDb();
} else if (cmd === 'reset') {
  closeDb();
  fs.rmSync(DATA_DIR, { recursive: true, force: true });
  console.log(`已清空 ${DATA_DIR}（数据库与遮罩照片）`);
} else {
  console.log('用法: node src/cli.js [start|seed|reset]');
  process.exit(1);
}
