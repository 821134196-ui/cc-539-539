import { getDb, cid } from '../db.js';
import { nowIso } from '../domain/time.js';

// 模拟闸机适配器
// failNextOnce() 可制造“支付后闸机未开”场景；重试时同一动作应幂等。
let failNext = false;

export function _simulateGateFailOnce() { failNext = true; }
export function _resetGateSim() { failNext = false; }

export function commandGateOpen({ gateId = 'GATE-01', plate, context, db = getDb() }) {
  const opened = !failNext;
  failNext = false; // 故障只发生一次，下一次重试成功
  db.prepare(`INSERT INTO gate_logs (id, gate_id, action, plate, context, created_at)
              VALUES (?, ?, ?, ?, ?, ?)`)
    .run(cid('gl_'), gateId, opened ? 'open' : 'open_failed', plate, context, nowIso());
  return {
    gateId,
    opened,
    reason: opened ? null : '闸机控制器无应答（模拟故障），请重试或申请人工异常放行',
  };
}

// 拒绝/拦截：只记日志，绝不开闸
export function recordGateDenied({ gateId = 'GATE-01', plate, context, db = getDb() }) {
  db.prepare(`INSERT INTO gate_logs (id, gate_id, action, plate, context, created_at)
              VALUES (?, ?, 'denied', ?, ?, ?)`)
    .run(cid('gl_'), gateId, plate, context, nowIso());
  return { gateId, opened: false, reason: context };
}
