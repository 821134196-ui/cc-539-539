import { PRICING } from '../config.js';
import { minutesBetween, isOvernight, parseTime } from './time.js';

// 日期键（本地时区）
function dayKey(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// 时间区间覆盖的自然日数量（跨夜 >=2）
function calendarDays(startIso, endIso) {
  const s = parseTime(startIso), e = parseTime(endIso);
  const days = new Set();
  const cur = new Date(s.getFullYear(), s.getMonth(), s.getDate());
  const last = new Date(e.getFullYear(), e.getMonth(), e.getDate());
  while (cur <= last) {
    days.add(dayKey(cur));
    cur.setDate(cur.getDate() + 1);
  }
  return { count: days.size, days: [...days] };
}

/**
 * 出场超时计费
 * 免费时间 = 批准时段内停留 + 提前到达免费等待(<=30分钟) + 出场缓冲(<=15分钟)
 * 超时分钟 = 超出免费等待的提前部分 + 超出出场缓冲的延后部分
 * 金额：超时不足 1 小时按 1 小时；跨自然日设单日封顶
 */
export function calcCharge({ enteredAt, exitedAt, plannedArrival, plannedDeparture }) {
  const totalMinutes = minutesBetween(enteredAt, exitedAt);

  const earlyArrivalMinutes = Math.max(0, minutesBetween(enteredAt, plannedArrival));
  const approvedStayMinutes = Math.max(0,
    minutesBetween(
      enteredAt < plannedArrival ? plannedArrival : enteredAt,
      exitedAt < plannedDeparture ? exitedAt : plannedDeparture
    ));
  const approvedEndToExit = Math.max(0, minutesBetween(plannedDeparture, exitedAt));

  const earlyFreeGrace = Math.min(earlyArrivalMinutes, PRICING.freeMinutesPerVisit);
  const lateFreeGrace = Math.min(approvedEndToExit, PRICING.freeExitGraceMinutes);

  const earlyOvertime = Math.max(0, earlyArrivalMinutes - PRICING.freeMinutesPerVisit);
  const lateOvertime = Math.max(0, approvedEndToExit - PRICING.freeExitGraceMinutes);
  const overtimeMinutes = earlyOvertime + lateOvertime;

  const freeMinutes = Math.max(0, totalMinutes - overtimeMinutes);

  const billableHours = overtimeMinutes > 0 ? Math.ceil(overtimeMinutes / 60) : 0;
  const rawAmountCents = billableHours * PRICING.ratePerHourCents;

  // 封顶按“超时区间”实际覆盖的自然日计算
  let overtimeDays = [dayKey(parseTime(plannedDeparture))];
  if (lateOvertime > 0) {
    const overStart = new Date(plannedDeparture);
    overStart.setMinutes(overStart.getMinutes() + PRICING.freeExitGraceMinutes);
    overtimeDays = calendarDays(overStart.toISOString(), exitedAt).days;
  }
  const capDays = overtimeDays.length;
  const capCents = capDays * PRICING.capPerDayCents;
  const amountCents = Math.min(rawAmountCents, capCents);
  const capped = rawAmountCents > capCents;

  const overnight = isOvernight(enteredAt, exitedAt)
    || isOvernight(plannedArrival, plannedDeparture);

  return {
    enteredAt, exitedAt, plannedArrival, plannedDeparture,
    freeMinutesPerVisit: PRICING.freeMinutesPerVisit,
    freeExitGraceMinutes: PRICING.freeExitGraceMinutes,
    ratePerHourCents: PRICING.ratePerHourCents,
    totalMinutes,
    approvedStayMinutes,
    earlyArrivalMinutes,
    earlyFreeGrace,
    lateFreeGrace,
    earlyOvertime,
    lateOvertime,
    overtimeMinutes,
    freeMinutes,
    billableHours,
    rawAmountCents,
    capDays,
    capCents,
    capped,
    overnight,
    overtimeDays,
    amountCents,
  };
}

// 异常理由完整文字（机读码 -> 页面直接展示的完整句子）
export const REASON_TEXTS = {
  PLATE_MISMATCH: '车牌录错：邀请登记车牌与门岗实际识别车牌不一致，需核对被访单位后放行',
  EARLY_ARRIVAL: `提前到达超出 ${PRICING.freeMinutesPerVisit} 分钟免费等待宽限，被访单位确认接待后放行`,
  LATE_ARRIVAL: '实际到达已晚于批准离场时间，邀请时段已失效，需被访单位确认后放行',
  CODE_REUSED: '通行码疑似被转发：该邀请已有一次有效入场，同一码不得再次入场',
  INV_CANCELLED: '邀请已被被访单位取消，持该通行码入场已拦截',
  GATE_OFFLINE: '门禁离线期间人工核对登记，系统恢复后补录，请求免费放行',
  PAY_GATE_STUCK: '支付成功但闸机未开启，设备故障，请求人工放行',
  SYSTEM_FAULT: '园区设备或系统故障导致无法正常核验，请求免费放行',
  EMERGENCY: '紧急公务/医疗等特殊事由，请求免费放行',
  OVERTIME_WAIVER: '车主对超时计费有异议，被访单位申请减免，需园区管理员复核',
  OFFLINE_REPLAY_REJECTED: '离线事件重放未通过校验（邀请已使用或已取消），已拦截未重复入场',
  OTHER: '其他异常情况，详见门岗说明',
};
