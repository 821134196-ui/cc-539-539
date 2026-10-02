"""时间工具。

系统内部统一使用"朴素本地时间"（naive datetime），格式 ISO 字符串
``YYYY-MM-DD HH:MM:SS`` 存入 SQLite。提供一个可注入的 Clock，
方便测试提前到达、跨夜停留等时间相关场景。
"""
from __future__ import annotations

from datetime import datetime, timedelta

FMT = "%Y-%m-%d %H:%M"
FMT_SEC = "%Y-%m-%d %H:%M:%S"


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def parse(s: str) -> datetime:
    """解析前端表单或种子数据里的时间字符串。"""
    s = s.strip().replace("T", " ")
    for fmt in (FMT_SEC, FMT):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间: {s!r}")


def fmt(dt: datetime, with_seconds: bool = False) -> str:
    return dt.strftime(FMT_SEC if with_seconds else FMT)


def iso(dt: datetime) -> str:
    return dt.strftime(FMT_SEC)


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, FMT_SEC)


def remaining_text(seconds: int) -> str:
    """剩余时间的中文短文本，负数表示已超时。"""
    if seconds is None:
        return "—"
    overdue = seconds < 0
    seconds = abs(int(seconds))
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if h and m:
        text = f"{h}小时{m}分"
    elif h:
        text = f"{h}小时"
    else:
        text = f"{m}分钟"
    return ("已超时 " + text) if overdue else ("剩余 " + text)


def ceil_minutes(seconds: int) -> int:
    """不足 1 分钟按 1 分钟计。"""
    if seconds <= 0:
        return 0
    return (seconds + 59) // 60
