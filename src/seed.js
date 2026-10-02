import { getDb, cid } from './db.js';
import * as inv from './domain/invitations.js';
import * as acc from './domain/access.js';
import { nowIso, addMinutes } from './domain/time.js';

// 可复现的演示数据：覆盖正常、提前到达、跨夜、同车牌多邀请、在园、已超时待支付
export function seed(db = getDb()) {
  const now = nowIso();
  const t = (mins) => addMinutes(now, mins);

  // 1) 正常在园车辆：10 分钟前入场，批准 2 小时
  const i1 = inv.createInvitation({
    unitId: 'u-alpha', plate: '京A12345', contactName: '张明', contactPhone: '13800001111',
    building: 'A座 101', plannedArrival: t(-20), plannedDeparture: t(100),
  }, db);
  acc.admitEntry({ code: i1.code, gateId: 'GATE-01', at: t(-10) }, db);

  // 2) 同车牌第二份邀请（同车牌多份邀请场景，尚未使用）
  inv.createInvitation({
    unitId: 'u-beta', plate: '京A12345', contactName: '李设计', contactPhone: '13900002222',
    building: 'B座 208', plannedArrival: t(240), plannedDeparture: t(360),
  }, db);

  // 3) 跨夜车辆：昨晚 20:00 入场，批准到 22:00，今早已超时（用于跨夜计费演示）
  const overnightStart = new Date();
  overnightStart.setHours(20, 0, 0, 0);
  overnightStart.setDate(overnightStart.getDate() - 1);
  const overnightEnd = new Date(overnightStart.getTime() + 2 * 3600000);
  const i3 = inv.createInvitation({
    unitId: 'u-alpha', plate: '粤B99999D', contactName: '王工', contactPhone: '13700003333',
    building: 'A座 305', plannedArrival: overnightStart.toISOString(),
    plannedDeparture: overnightEnd.toISOString(),
  }, db);
  acc.admitEntry({ code: i3.code, gateId: 'GATE-02', at: overnightStart.toISOString() }, db);

  // 4) 一张尚未到场的有效邀请（供离线重放演示）
  inv.createInvitation({
    unitId: 'u-beta', plate: '沪AD66888', contactName: '赵客', contactPhone: '13600004444',
    building: 'C座 大堂', plannedArrival: t(-5), plannedDeparture: t(120),
  }, db);

  // 5) 一张已完成出场、按时离场的免费回执
  const i5 = inv.createInvitation({
    unitId: 'u-alpha', plate: '津C12321', contactName: '陈晨', contactPhone: '13500005555',
    building: 'A座 201', plannedArrival: t(-180), plannedDeparture: t(-60),
  }, db);
  acc.admitEntry({ code: i5.code, at: t(-170) }, db);
  acc.processExit({ code: i5.code, at: t(-70) }, db);

  return {
    normalInPark: i1.code,
    overnight: i3.code,
    finished: i5.code,
  };
}
