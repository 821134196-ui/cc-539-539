"""种子数据：重置/初始化一个可直接演示的园区状态。

包含：有效待入场邀请（含同车牌多邀请）、在园车辆（即将到期/已超时/跨夜）、
已缴费出场车辆、一条门禁离线待重放队列。
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from . import clock
from .adapters import take_plate_photo
from .db import connect, init_db
from .service import Service

UNITS = [("A", "A栋 · 研发中心"), ("B", "B栋 · 行政中心"),
         ("C", "C栋 · 制造中心")]


def seed(db_path: str = "data/park.db", photo_dir: str = "data/photos",
         reset: bool = True) -> Service:
    if reset:
        for p in Path(db_path).parent.glob("park.db*"):
            p.unlink(missing_ok=True)
    conn = connect(db_path)
    init_db(conn)
    for code, name in UNITS:
        conn.execute("INSERT OR IGNORE INTO units(code,name) VALUES(?,?)",
                     (code, name))
    svc = Service(conn, photo_dir)
    now = clock.now()
    f = clock.fmt

    # 1) 待入场：标准有效邀请
    inv1 = svc.create_invitation(
        plate="京A·12345", unit_code="A", building="A栋301会议室",
        contact="王工 138****0001",
        enter_at=f(now - timedelta(minutes=20)),
        leave_at=f(now + timedelta(hours=2, minutes=40)),
        created_by="A单位-前台")

    # 2) 同车牌多份邀请（演示门岗留痕：按出示码入场）
    svc.create_invitation(
        plate="京A·12345", unit_code="B", building="B栋2层报告厅",
        contact="王秘书 138****0002",
        enter_at=f(now + timedelta(days=1, hours=1)),
        leave_at=f(now + timedelta(days=1, hours=3)),
        created_by="B单位-行政")

    # 3) 即将到期的在园车辆（剩余时间高亮）
    inv3 = svc.create_invitation(
        plate="沪B·60606", unit_code="B", building="B栋108",
        contact="李经理 138****0003",
        enter_at=f(now - timedelta(hours=1, minutes=50)),
        leave_at=f(now + timedelta(minutes=8)),
        created_by="B单位-前台")
    svc.verify(inv3["code"], "沪B·60606", at=now - timedelta(hours=1, minutes=50),
               operator="门岗-赵师傅")

    # 4) 已超时在园（收费页演示超时金额）
    inv4 = svc.create_invitation(
        plate="粤C·70707", unit_code="C", building="C栋一层接待室",
        contact="周厂长 138****0004",
        enter_at=f(now - timedelta(hours=5)),
        leave_at=f(now - timedelta(hours=2, minutes=40)),
        created_by="C单位-前台")
    svc.verify(inv4["code"], "粤C·70707", at=now - timedelta(hours=4, minutes=50),
               operator="门岗-赵师傅")

    # 5) 跨夜在园（跨夜连续计费演示）
    inv5 = svc.create_invitation(
        plate="苏E·80808", unit_code="A", building="A栋夜间值班室",
        contact="孙工 138****0005",
        enter_at=f(now - timedelta(days=1, hours=1)),
        leave_at=f(now - timedelta(days=1) + timedelta(hours=2)),
        created_by="A单位-前台")
    svc.verify(inv5["code"], "苏E·80808",
               at=now - timedelta(days=1, hours=1),
               operator="门岗-夜班钱师傅")

    # 6) 已缴费出场（收费流水 + 电子凭证演示）
    inv6 = svc.create_invitation(
        plate="川D·90909", unit_code="C", building="C栋会议室",
        contact="吴主管 138****0006",
        enter_at=f(now - timedelta(days=1)),
        leave_at=f(now - timedelta(days=1) + timedelta(hours=2)),
        created_by="C单位-前台")
    svc.verify(inv6["code"], "川D·90909",
               at=now - timedelta(days=1), operator="门岗-赵师傅")
    v6 = conn.execute("SELECT id FROM visits WHERE inv_id=?",
                      (inv6["id"],)).fetchone()
    svc.pay(v6["id"], operator="车主自助")

    # 7) 一条已撤销邀请（演示码失效）
    inv7 = svc.create_invitation(
        plate="浙F·50505", unit_code="B", building="B栋301",
        contact="郑老师 138****0007",
        enter_at=f(now + timedelta(hours=3)),
        leave_at=f(now + timedelta(hours=5)),
        created_by="B单位-前台")
    svc.revoke_invitation(inv7["id"], "B", "访客行程取消，前台申请作废",
                          "B单位-前台")

    # 8) 离线待重放：门禁离线期间到场的车辆，恢复后在门岗页一键重放
    svc.gate.set_online(False)
    inv8 = svc.create_invitation(
        plate="鲁Q·30303", unit_code="A", building="A栋202",
        contact="冯工 138****0008",
        enter_at=f(now - timedelta(minutes=15)),
        leave_at=f(now + timedelta(hours=1, minutes=45)),
        created_by="A单位-前台")
    svc.verify(inv8["code"], "鲁Q·30303", at=now - timedelta(minutes=6),
               operator="门岗-赵师傅")
    # 保持离线状态，让启动后的门岗页直接呈现"离线+待重放"
    return svc


if __name__ == "__main__":
    svc = seed()
    print("种子数据已写入 data/park.db（门禁当前为离线，门岗页可一键重放）")
