import { test, before } from 'node:test';
import assert from 'node:assert/strict';
import { sandbox, iso } from './helpers.js';
import { calcCharge } from '../src/domain/billing.js';

let ctx;
before(async () => { ctx = await sandbox(); });

test('批准时段内 + 15 分钟出场缓冲 = 免费', () => {
  const d = calcCharge({
    enteredAt: iso('2026-10-01T09:00'),
    exitedAt: iso('2026-10-01T11:10'),
    plannedArrival: iso('2026-10-01T09:00'),
    plannedDeparture: iso('2026-10-01T11:00'),
  });
  assert.equal(d.overtimeMinutes, 0);
  assert.equal(d.amountCents, 0);
  assert.equal(d.freeMinutes, d.totalMinutes);
});

test('跨夜停留：20:00 入场批准到 22:00，次日 09:00 离场按超时计费且识别跨夜', () => {
  const { db, invitations, access } = ctx;
  const inv = invitations.createInvitation({
    unitId: 'u-alpha', plate: '粤B99999D', contactName: '跨夜', contactPhone: '136',
    building: 'A座305',
    plannedArrival: iso('2026-10-01T20:00'),
    plannedDeparture: iso('2026-10-01T22:00'),
  }, db);

  const d = calcCharge({
    enteredAt: iso('2026-10-01T20:00'),
    exitedAt: iso('2026-10-02T09:00'),
    plannedArrival: inv.planned_arrival,
    plannedDeparture: inv.planned_departure,
  });

  assert.equal(d.overnight, true);
  // 超时：22:15（缓冲后）至次日 09:00 = 645 分钟
  assert.equal(d.lateOvertime, 645);
  // 645 分钟向上取整 11 小时
  assert.equal(d.billableHours, 11);
  // 跨两个自然日，封顶 120 元；11*5=55 元，未触顶
  assert.equal(d.capDays, 2);
  assert.equal(d.amountCents, 5500);
  assert.equal(d.capped, false);

  // 端到端：入场 → 跨夜出场 → 回执落库
  access.admitEntry({ code: inv.code, at: inv.planned_arrival }, db);
  const out = access.processExit({ code: inv.code, at: iso('2026-10-02T09:00') }, db);
  assert.equal(out.receipt.amount_cents, 5500);
  assert.equal(out.receipt.overtime_minutes, 645);
  assert.equal(JSON.parse(out.receipt.calc_detail).overnight, true);
});

test('超长跨夜触发单日封顶', () => {
  const d = calcCharge({
    enteredAt: iso('2026-10-01T08:00'),
    exitedAt: iso('2026-10-03T20:00'),
    plannedArrival: iso('2026-10-01T08:00'),
    plannedDeparture: iso('2026-10-01T10:00'),
  });
  // 超时区间 10:15 → 10-03 20:00 覆盖 10/01、10/02、10/03 三天
  assert.deepEqual(d.overtimeDays, ['2026-10-01', '2026-10-02', '2026-10-03']);
  assert.equal(d.capDays, 3);
  assert.equal(d.capCents, 18000);
  assert.ok(d.rawAmountCents > d.capCents);
  assert.equal(d.capped, true);
  assert.equal(d.amountCents, 18000);
});

test('提前到达：30 分钟内免费，超出部分计入超时', () => {
  const d = calcCharge({
    enteredAt: iso('2026-10-01T08:20'),       // 提前 40 分钟
    exitedAt: iso('2026-10-01T11:00'),
    plannedArrival: iso('2026-10-01T09:00'),
    plannedDeparture: iso('2026-10-01T11:00'),
  });
  assert.equal(d.earlyArrivalMinutes, 40);
  assert.equal(d.earlyFreeGrace, 30);
  assert.equal(d.earlyOvertime, 10);
  assert.equal(d.billableHours, 1);
  assert.equal(d.amountCents, 500);
});

test('免费时间与超时金额清楚拆分，免费 >= 批准停留', () => {
  const d = calcCharge({
    enteredAt: iso('2026-10-01T09:00'),
    exitedAt: iso('2026-10-01T12:40'),
    plannedArrival: iso('2026-10-01T09:00'),
    plannedDeparture: iso('2026-10-01T11:00'),
  });
  assert.equal(d.approvedStayMinutes, 120);
  assert.equal(d.lateFreeGrace, 15);
  assert.equal(d.lateOvertime, 85);              // 11:15 → 12:40
  assert.equal(d.freeMinutes, 135);             // 120 + 15
  assert.equal(d.overtimeMinutes, 85);
  assert.equal(d.freeMinutes + d.overtimeMinutes, d.totalMinutes);
});
