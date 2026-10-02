import { test, before } from 'node:test';
import assert from 'node:assert/strict';
import { sandbox, offsetIso } from './helpers.js';

let ctx;
before(async () => { ctx = await sandbox(); });

function makeInv(db, svc, unitId, plate) {
  return svc.createInvitation({
    unitId, plate, contactName: '联系人', contactPhone: '138',
    building: 'X座101', plannedArrival: offsetIso(10), plannedDeparture: offsetIso(120),
  }, db);
}

test('被访单位不能修改其他单位的邀请', () => {
  const { db, invitations } = ctx;
  const beta = makeInv(db, invitations, 'u-beta', '京B20001');

  // alpha 试图改 beta 的邀请：拒绝
  assert.throws(
    () => invitations.updateInvitation(beta.id, 'u-alpha', { building: 'A座999' }),
    (e) => e.code === 'FORBIDDEN_UNIT',
  );
  // 数据未被改动
  const after = invitations.getInvitation(beta.id, db);
  assert.equal(after.building, 'X座101');

  // 取消也不允许
  assert.throws(() => invitations.cancelInvitation(beta.id, 'u-alpha', db), (e) => e.code === 'FORBIDDEN_UNIT');
  assert.equal(invitations.getInvitation(beta.id, db).status, 'valid');
});

test('本单位可以修改自己的邀请（含入场前更正录错的车牌）', () => {
  const { db, invitations } = ctx;
  const alpha = makeInv(db, invitations, 'u-alpha', '京A20002');
  const fixed = invitations.updateInvitation(alpha.id, 'u-alpha', { plate: '京A20009' }, db);
  assert.equal(fixed.plate, '京A20009');

  const updated = invitations.updateInvitation(alpha.id, 'u-alpha', {
    building: 'A座888', contactName: '新联系人',
  }, db);
  assert.equal(updated.building, 'A座888');
  assert.equal(updated.contact_name, '新联系人');
});

test('单位只能列出本单位邀请', () => {
  const { db, invitations } = ctx;
  const alphaCount = invitations.listInvitations({ unitId: 'u-alpha' }, db).length;
  const betaCount = invitations.listInvitations({ unitId: 'u-beta' }, db).length;
  const all = invitations.listInvitations({ includeAll: true }, db).length;
  assert.ok(alphaCount >= 1 && betaCount >= 1);
  assert.equal(all, alphaCount + betaCount);
  assert.throws(() => invitations.listInvitations({}, db), (e) => e.code === 'FORBIDDEN');
});

test('越权修改有审计记录', () => {
  const { db } = ctx;
  const log = db.prepare("SELECT * FROM audit_log WHERE action='invitation-modify-denied'").all();
  assert.ok(log.length >= 1);
  assert.match(log[0].detail, /外单位/);
});
