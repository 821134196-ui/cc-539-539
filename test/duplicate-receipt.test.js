import { test, before } from 'node:test';
import assert from 'node:assert/strict';
import { sandbox, offsetIso } from './helpers.js';

let ctx;
before(async () => { ctx = await sandbox(); });

test('出场回执重复提交：只生成一张回执、不重复收费', () => {
  const { db, invitations, access } = ctx;
  const inv = invitations.createInvitation({
    unitId: 'u-alpha', plate: '京A55555', contactName: '戊', contactPhone: '135',
    building: 'A座201', plannedArrival: offsetIso(-180), plannedDeparture: offsetIso(-60),
  }, db);
  access.admitEntry({ code: inv.code, at: offsetIso(-170) }, db);

  const first = access.processExit({ code: inv.code, at: offsetIso(30), requestId: 'req-1' }, db);
  assert.equal(first.duplicated, false);
  assert.ok(first.receipt.amount_cents > 0);
  assert.equal(first.receipt.status, 'pending');

  // 网络抖动，门岗重试两次
  const retry1 = access.processExit({ code: inv.code, at: offsetIso(31), requestId: 'req-1' }, db);
  const retry2 = access.processExit({ code: inv.code, at: offsetIso(32), requestId: 'req-2' }, db);
  assert.equal(retry1.duplicated, true);
  assert.equal(retry2.duplicated, true);
  assert.equal(retry1.receipt.id, first.receipt.id);

  assert.equal(db.prepare('SELECT COUNT(*) n FROM exit_receipts').get().n, 1);
  assert.equal(db.prepare("SELECT COUNT(*) n FROM events WHERE event_type='exit'").get().n, 1);
  assert.equal(db.prepare('SELECT COUNT(*) n FROM payments').get().n, 0, '未支付前重试不产生支付');
});

test('支付成功后闸机未开：重试只开闸、绝不二次扣款', () => {
  const { db, invitations, access, gate } = ctx;
  const inv = invitations.createInvitation({
    unitId: 'u-beta', plate: '沪AD66666', contactName: '己', contactPhone: '134',
    building: 'B座101', plannedArrival: offsetIso(-200), plannedDeparture: offsetIso(-80),
  }, db);
  access.admitEntry({ code: inv.code, at: offsetIso(-190) }, db);
  const { receipt } = access.processExit({ code: inv.code, at: offsetIso(40), requestId: 'exit-6' }, db);
  assert.ok(receipt.amount_cents > 0);

  // 第一次支付：模拟闸机故障未开
  gate._simulateGateFailOnce();
  const pay1 = access.payAndOpen({ receiptId: receipt.id, requestId: 'pay-6' }, db);
  assert.equal(pay1.gate.opened, false);
  assert.equal(db.prepare('SELECT COUNT(*) n FROM payments').get().n, 1);
  assert.equal(db.prepare('SELECT status FROM exit_receipts WHERE id=?').get(receipt.id).status, 'paid');

  // 车主点击重试：不得二次扣款，只重新开闸
  const pay2 = access.payAndOpen({ receiptId: receipt.id, requestId: 'pay-6' }, db);
  assert.equal(pay2.retried, true);
  assert.equal(pay2.gate.opened, true);
  assert.equal(db.prepare('SELECT COUNT(*) n FROM payments').get().n, 1, '仍只有一笔支付');

  // 同一支付 request_id 再次走适配器：返回原支付（渠道侧幂等）
  const pay3 = access.payAndOpen({ receiptId: receipt.id, requestId: 'pay-6' }, db);
  assert.equal(pay3.gate.opened, true);
  assert.equal(db.prepare('SELECT COUNT(*) n FROM payments').get().n, 1);
});

test('按时离场免费放行直接开闸，且重试仍幂等', () => {
  const { db, invitations, access } = ctx;
  const inv = invitations.createInvitation({
    unitId: 'u-alpha', plate: '京C77777', contactName: '庚', contactPhone: '133',
    building: 'A座108', plannedArrival: offsetIso(-60), plannedDeparture: offsetIso(60),
  }, db);
  access.admitEntry({ code: inv.code, at: offsetIso(-50) }, db);
  const out = access.processExit({ code: inv.code, at: offsetIso(0) }, db);
  assert.equal(out.receipt.amount_cents, 0);
  assert.equal(out.receipt.status, 'free_gate');
  assert.equal(out.gate.opened, true);
  const again = access.processExit({ code: inv.code, at: offsetIso(1) }, db);
  assert.equal(again.duplicated, true);
  assert.equal(
    db.prepare('SELECT COUNT(*) n FROM payments p JOIN exit_receipts r ON r.id = p.receipt_id WHERE r.invitation_id = ?').get(inv.id).n,
    0, '本车无支付记录');
});
