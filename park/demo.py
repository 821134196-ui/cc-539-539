"""离线重放样例（命令行）。

一条命令展示完整故事：
  访客提前到场 → 门禁离线、核验入本地队列 → 网络恢复 →
  按原到场时间重放 → 跨夜超时结算 → 支付（含重复回执幂等）→ 出场。

用法：python -m park.demo
数据写入 data/demo.db，不影响正式库 data/park.db。
"""
from __future__ import annotations

from datetime import timedelta

from . import clock
from .db import connect, init_db
from .seed import seed
from .service import Service


def line(t=""):
    print(t)


def main():
    svc = seed("data/demo.db", "data/photos_demo", reset=True)
    conn = svc.conn

    line("=" * 64)
    line("步骤 1：离线队列中已有种子车辆 鲁Q·30303（门岗离线时到场）")
    for q in svc.pending_queue():
        payload = __import__("json").loads(q["payload_json"])
        line(f"  待重放 #{q['id']}  车牌 {payload['plate']}  "
             f"到场时间 {payload['queued_at']}")

    line("=" * 64)
    line("步骤 2：网络恢复，门岗一键重放（按入队时的实际到场时间入场）")
    svc.set_gate_online(True, "门岗-赵师傅")
    for r in svc.replay_pending("门岗-赵师傅"):
        line(f"  队列 #{r['queue_id']} → {r['verdict']}：{r['reason']}")

    line("=" * 64)
    line("步骤 3：跨夜车辆 苏E·80808 离场结算（跨夜按连续时长累计）")
    v5 = conn.execute(
        "SELECT v.id FROM visits v JOIN invitations i ON i.id=v.inv_id "
        "WHERE i.plate='苏E·80808'").fetchone()
    s = svc.settle(conn.execute("SELECT * FROM visits WHERE id=?",
                                (v5["id"],)).fetchone())
    bd = s["breakdown"]
    line(f"  免费 {bd.free_minutes} 分钟（批准 {bd.approved_minutes} + 宽限 {bd.grace_minutes}）")
    line(f"  超时 {bd.overtime_minutes} 分钟，计费单位 {bd.billed_units}，"
         f"应收 ¥{bd.amount:.2f}{'（已触发24小时封顶）' if bd.capped else ''}")

    line("=" * 64)
    line("步骤 4：模拟支付，随后重复发送出场回执（不得重复收费）")
    r1 = svc.pay(v5["id"], "车主自助")
    line(f"  首次：{r1['reason']}　交易号 {r1['trade_no']}")
    r2 = svc.pay(v5["id"], "车主自助")
    line(f"  重试：{r2['reason']}")
    n = conn.execute(
        "SELECT COUNT(*) c FROM payments WHERE visit_id=?", (v5["id"],)).fetchone()["c"]
    line(f"  该车辆支付流水笔数：{n}（必须为 1）")

    line("=" * 64)
    line("完成。处置记录与收费流水可在网页端查看：")
    line("  ./run.sh  然后打开 http://127.0.0.1:8000")


if __name__ == "__main__":
    main()
