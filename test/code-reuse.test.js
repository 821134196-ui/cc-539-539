import { test, before } from 'node:test';
import assert from 'node:assert/strict';
import { sandbox, offsetIso } from './helpers.js';

let ctx;
before(async () => { ctx = await sandbox(); });

test('一张邀请只能一次有效入场，通行码转发再用被拒绝', () => {
  const { db, invitations, access } = ctx;
  const inv = invitations.createInvitation({
    unitId: 'u-alpha', plate: '京A11111', contactName: '甲', contactPhone: '138',
    building: 'A座101', plannedArrival: offsetIso(-5), plannedDeparture: offsetIso(120),
  }, db);

  const first = access.admitEntry({ code: inv.code, at: offsetIso(0) }, db);
  assert.equal(first.gate.opened, true);
  assert.equal(first.duplicated, false);

  // 转发给别人/再次扫码：必须拒绝
  assert.throws(
    () => access.admitEntry({ code: inv.code, at: offsetIso(10) }, db),
    (e) => e.code === 'CODE_REUSED',
  );

  // 只有一条入场事件，邀请状态为 used
  assert.equal(db.prepare("SELECT COUNT(*) n FROM events WHERE event_type='entry'").get().n, 1);
  assert.equal(db.prepare('SELECT status FROM invitations WHERE id=?').get(inv.id).status, 'used');

  // 有处置记录：异常（rejected）+ 闸机拒绝日志
  const ex = db.prepare("SELECT * FROM exception_releases WHERE reason_code='CODE_REUSED'").get();
  assert.ok(ex);
  assert.equal(ex.status, 'rejected');
  assert.match(ex.reason_text, /一次有效入场/);
  const denied = db.prepare("SELECT COUNT(*) n FROM gate_logs WHERE action='denied'").get().n;
  assert.ok(denied >= 1);
});

test('离线重放重复码同样被拦截，不产生第二入场', () => {
  const { db, invitations, access } = ctx;
  const inv = invitations.createInvitation({
    unitId: 'u-beta', plate: '沪AD22222', contactName: '乙', contactPhone: '139',
    building: 'B座202', plannedArrival: offsetIso(-5), plannedDeparture: offsetIso(120),
  }, db);

  // 先离线排队，随后在线正常入场
  access.queueOfflineEvent({ kind: 'entry', code: inv.code, gateId: 'GATE-01', at: offsetIso(0) }, db);
  access.admitEntry({ code: inv.code, gateId: 'GATE-02', at: offsetIso(1) }, db);

  const results = access.replayOffline(db);
  assert.equal(results[0].ok, false);
  assert.equal(results[0].code, 'CODE_REUSED');
  assert.equal(db.prepare("SELECT COUNT(*) n FROM events WHERE event_type='entry'").get().n, 2, '两辆车各一条');
  assert.equal(
    db.prepare("SELECT COUNT(*) n FROM events WHERE invitation_id=?").get(inv.id).n, 1,
    '该邀请仍只有一条事件',
  );

  // 再重放一次不重复处理
  assert.equal(access.replayOffline(db).length, 0);
});

test('已取消邀请的码不能入场', () => {
  const { db, invitations, access } = ctx;
  const inv = invitations.createInvitation({
    unitId: 'u-alpha', plate: '京A33333', contactName: '丙', contactPhone: '137',
    building: 'A座102', plannedArrival: offsetIso(-5), plannedDeparture: offsetIso(120),
  }, db);
  invitations.cancelInvitation(inv.id, 'u-alpha', db);
  assert.throws(() => access.admitEntry({ code: inv.code }, db), (e) => e.code === 'INV_CANCELLED');
});
