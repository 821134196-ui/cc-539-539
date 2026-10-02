"""超时计费（纯函数，不依赖数据库与当前时间，便于测试）。

规则（园区公示）：
- 邀请批准时段 enter_at ~ leave_at 内免费；
- 批准离场时间之后给 GRACE_MINUTES 分钟免费宽限；
- 超出宽限的部分按 RATE_PER_HOUR 元/小时计，不足 1 小时按 1 小时；
- 跨夜停留按连续时长累计，每满 24 小时封顶 DAILY_CAP 元。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

GRACE_MINUTES = 15
RATE_PER_HOUR = 2.00
DAILY_CAP = 30.00


@dataclass
class FeeBreakdown:
    approved_minutes: int       # 批准时段分钟数（免费）
    grace_minutes: int          # 宽限分钟数（免费）
    free_minutes: int           # 合计免费分钟数
    overtime_minutes: int       # 实际超时分钟数（扣过宽限）
    billed_units: int           # 计费单位数（每单位 1 小时，向上取整）
    raw_amount: float           # 未封顶金额
    cap_amount: float           # 24 小时封顶后金额
    amount: float              # 应收金额
    capped: bool

    def as_dict(self) -> dict:
        return {
            "approved_minutes": self.approved_minutes,
            "grace_minutes": self.grace_minutes,
            "free_minutes": self.free_minutes,
            "overtime_minutes": self.overtime_minutes,
            "billed_units": self.billed_units,
            "raw_amount": f"{self.raw_amount:.2f}",
            "cap_amount": f"{self.cap_amount:.2f}",
            "amount": f"{self.amount:.2f}",
            "capped": self.capped,
        }


def _minutes(a: datetime, b: datetime) -> int:
    return int((b - a).total_seconds() // 60)


def calc_fee(enter_at: datetime, leave_at: datetime,
             actual_enter: datetime, actual_exit: datetime) -> FeeBreakdown:
    """按批准时段与实际进出时间计算超时费用。

    跨夜只是"批准/实际时间跨越零点"，计费以离场批准时间为起点连续计算，
    不因为零点而重新起算免费时段。
    """
    approved = max(0, _minutes(enter_at, leave_at))
    # 免费额度 = 批准时段 + 离场宽限。提前到达不额外赠送免费时间，
    # 宽限只挂在批准离场时间之后。
    free_total = approved + GRACE_MINUTES

    actual = _minutes(actual_enter, actual_exit)
    overtime = max(0, actual - free_total)

    units = -(-overtime // 60) if overtime > 0 else 0  # ceil
    raw = round(units * RATE_PER_HOUR, 2)

    # 封顶：超时部分每满 24 小时按 DAILY_CAP 计，剩余零头按小时单价计。
    full_days, rem_minutes = divmod(overtime, 24 * 60)
    rem_units = -(-rem_minutes // 60) if rem_minutes > 0 else 0
    cap = round(full_days * DAILY_CAP + rem_units * RATE_PER_HOUR, 2)
    capped = cap < raw
    amount = min(raw, cap)

    return FeeBreakdown(
        approved_minutes=approved,
        grace_minutes=GRACE_MINUTES,
        free_minutes=free_total,
        overtime_minutes=overtime,
        billed_units=units,
        raw_amount=raw,
        cap_amount=cap,
        amount=amount,
        capped=capped,
    )


def money(x) -> str:
    return f"{float(x):.2f}"
