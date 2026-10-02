#!/usr/bin/env node
// 门禁离线 → 本地排队 → 恢复后重放 的可复现样例（使用独立临时库，不污染正式数据）
// 运行：npm run demo
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// 必须在导入 config 之前指定独立数据目录（数据库 + 遮罩照片都隔离）
const demoDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pgv-demo-'));
process.env.PGV_DATA_DIR = demoDir;

const { openDb, closeDb } = await import('../src/db.js');
const inv = await import('../src/domain/invitations.js');
const acc = await import('../src/domain/access.js');

const dbPath = path.join(demoDir, 'park.db');
const db = openDb(dbPath);

const line = (s) => console.log(s);
const t = (off) => new Date(Date.now() + off * 60000).toISOString();

line('────────────────────────────────────────────────');
line('场景：门岗 GATE-01 网络中断，车辆先人工登记，恢复后重放');
line('────────────────────────────────────────────────');

const a = inv.createInvitation({
  unitId: 'u-alpha', plate: '京A10001', contactName: '离线访客甲', contactPhone: '13800001',
  building: 'A座 101', plannedArrival: t(-5), plannedDeparture: t(120),
}, db);
const b = inv.createInvitation({
  unitId: 'u-beta', plate: '沪AD22222', contactName: '离线访客乙', contactPhone: '13800002',
  building: 'B座 202', plannedArrival: t(-5), plannedDeparture: t(120),
}, db);
line(`已创建两张邀请：甲 ${a.code}（${a.plate}）、乙 ${b.code}（${b.plate}）`);

line('');
line('【1】门禁离线：两辆车到场，门岗只登记到本地离线队列（未核验、未开闸）');
const q1 = acc.queueOfflineEvent({ kind: 'entry', code: a.code, gateId: 'GATE-01', at: t(0) }, db);
const q2 = acc.queueOfflineEvent({ kind: 'entry', code: b.code, gateId: 'GATE-01', at: t(1) }, db);
line(`  入队：甲 event_id=${q1.eventId}`);
line(`  入队：乙 event_id=${q2.eventId}`);

line('');
line('【2】恢复瞬间，乙的访客已先在另一门岗完成一次在线入场（一邀一入）');
acc.admitEntry({ code: b.code, gateId: 'GATE-02', at: t(2) }, db);
line(`  乙已在线入场，邀请状态变为 used`);

line('');
line('【3】网络恢复，执行离线重放……');
const r1 = acc.replayOffline(db);
for (const r of r1) {
  line(`  队列项 ${r.id.slice(-6)}：${r.ok ? (r.duplicated ? '幂等去重，未重复入场' : '✅ 重放成功并开闸') : `⛔ 重放被拦截（${r.code}：${r.error}）`}`);
}

line('');
line('【4】再次重放（模拟恢复任务重复触发）——已处理项不会二次执行');
const r2 = acc.replayOffline(db);
line(`  本次新处理队列项：${r2.length} 条（应为 0）`);

line('');
line('【5】处置结果核对');
line(`  在园车辆：${acc.listInPark(db).map((c) => c.plate).join('、') || '无'}（甲、乙各只入场一次）`);
line(`  入场事件数：${db.prepare("SELECT COUNT(*) n FROM events WHERE event_type='entry'").get().n}（应为 2，乙没有被离线重放重复入场）`);
const blocked = acc.listExceptions('rejected', db);
line(`  拦截处置记录：${blocked.length} 条`);
for (const x of blocked) line(`    · [${x.reason_code}] ${x.reason_text}`);
line(`  闸机日志：`);
for (const g of acc.listGateLogs(10, db)) line(`    · ${g.gate_id} ${g.action} — ${g.context}`);

line('');
line(`演示数据（SQLite 与遮罩照片）位于：${demoDir}（临时目录，可删除）`);
line('────────────────────────────────────────────────');
closeDb();
