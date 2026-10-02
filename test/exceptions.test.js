import { test, before } from 'node:test';
import assert from 'node:assert/strict';
import { sandbox, offsetIso } from './helpers.js';

let ctx;
before(async () => { ctx = await sandbox(); });

test('车牌录错：识别车牌与登记不符时拦截并登记异常', () => {
  const { db, invitations, access } = ctx;
  const inv = invitations.createInvitation({
    unitId: 'u-alpha', plate: '京A30001', contactName: '辛', contactPhone: '132',
    building: 'A座101', plannedArrival: offsetIso(-5), plannedDeparture: offsetIso(120),
  }, db);

  assert.throws(
    () => access.admitEntry({ code: inv.code, observedPlate: '京A30009' }, db),
    (e) => e.code === 'PLATE_MISMATCH',
  );
  const ex = db.prepare("SELECT * FROM exception_releases WHERE reason_code='PLATE_MISMATCH' AND status='pending'").get();
  assert.ok(ex, '产生待复核异常记录');
  assert.match(ex.reason_text, /车牌录错/);

  // 被访单位更正车牌后可正常入场
  invitations.updateInvitation(inv.id, 'u-alpha', { plate: '京A30009' }, db);
  const out = access.admitEntry({ code: inv.code, observedPlate: '京A30009' }, db);
  assert.equal(out.gate.opened, true);
});

test('异常免费放行必须园区管理员复核后才开闸', () => {
  const { db, invitations, access } = ctx;
  const inv = invitations.createInvitation({
    unitId: 'u-alpha', plate: '京A30002', contactName: '壬', contactPhone: '131',
    building: 'A座102', plannedArrival: offsetIso(-200), plannedDeparture: offsetIso(-100),
  }, db);
  access.admitEntry({ code: inv.code, at: offsetIso(-190) }, db);
  // 已远超批准时段

  const ex = access.recordException({
    invitationId: inv.id, plate: '京A30002', reasonCode: 'GATE_OFFLINE',
    direction: 'exit', gateId: 'GATE-01', requestedBy: '门岗值班员',
    note: '离线期间离场，申请免费',
  }, db);
  assert.equal(ex.status, 'pending');

  // 复核前车辆仍在园
  assert.equal(access.listInPark(db).some((c) => c.id === inv.id), true);

  // 非管理员不能复核
  assert.throws(() => access.reviewException(ex.id, 'u-alpha', true), (e) => e.code === 'ADMIN_NOT_FOUND');

  // 管理员驳回不开闸
  const rejected = access.reviewException(ex.id, 'admin-1', false, '材料不全', db);
  assert.equal(rejected.status, 'rejected');
  assert.equal(access.listInPark(db).some((c) => c.id === inv.id), true);

  // 重新登记一条并核准：零元回执、开闸、车辆出园
  const ex2 = access.recordException({
    invitationId: inv.id, plate: '京A30002', reasonCode: 'OVERTIME_WAIVER',
    direction: 'exit', gateId: 'GATE-01', requestedBy: '门岗值班员',
  }, db);
  const result = access.reviewException(ex2.id, 'admin-1', true, '情况属实', db);
  assert.equal(result.exception.status, 'approved');
  assert.equal(result.receipt.amount_cents, 0);
  assert.equal(result.receipt.status, 'free_gate');
  assert.equal(result.gate.opened, true);
  assert.equal(access.listInPark(db).some((c) => c.id === inv.id), false);
});

test('同一车牌可以存在多份邀请，互不影响一次入场规则', () => {
  const { db, invitations, access } = ctx;
  const common = { plate: '京A80088', contactName: '同车牌', contactPhone: '130' };
  const i1 = invitations.createInvitation(
    { ...common, unitId: 'u-alpha', building: 'A座101', plannedArrival: offsetIso(0), plannedDeparture: offsetIso(120) }, db);
  const i2 = invitations.createInvitation(
    { ...common, unitId: 'u-beta', building: 'B座202', plannedArrival: offsetIso(300), plannedDeparture: offsetIso(420) }, db);

  const found = invitations.findInvitationsByPlate('京a80088', db); // 小写/空格归一化
  assert.equal(found.length, 2);

  access.admitEntry({ code: i1.code, at: offsetIso(5) }, db);
  // 用第二份邀请码在其批准时段内核验，且门岗页能看到同车牌其他邀请
  const v = access.verifyAtGate({ code: i2.code, at: offsetIso(305) }, db);
  assert.equal(v.samePlateOthers.length, 1);
  assert.equal(v.samePlateOthers[0].id, i1.id);
  assert.equal(v.assessment.code, 'OK');
});
