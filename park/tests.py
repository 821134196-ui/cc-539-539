"""测试：码防重、跨夜计费、重复回执不重复收费、单位隔离。

运行：python -m unittest park.tests -v
（全部使用临时目录下的 SQLite，不依赖网络与第三方库）
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from . import billing, clock
from .db import connect, init_db
from .service import DomainError, Service


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.conn = connect(self.dir / "test.db")
        init_db(self.conn)
        self.conn.executemany(
            "INSERT INTO units(code,name) VALUES(?,?)",
            [("A", "A栋研发"), ("B", "B栋行政"), ("C", "C栋制造")])
        self.svc = Service(self.conn, str(self.dir / "photos"))
        self.now = datetime(2026, 10, 2, 12, 0)

    def invite(self, plate="京A·12345", unit="A", enter=None, leave=None,
               building="A栋301", contact="王工"):
        enter = enter or self.now - timedelta(hours=1)
        leave = leave or self.now + timedelta(hours=1)
        return self.svc.create_invitation(
            plate=plate, unit_code=unit, building=building, contact=contact,
            enter_at=clock.fmt(enter), leave_at=clock.fmt(leave),
            created_by=f"{unit}单位")

    def visit_of(self, inv):
        return self.conn.execute(
            "SELECT * FROM visits WHERE inv_id=?", (inv["id"],)).fetchone()


# ---------------------------------------------------------------- 码防重

class TestCodeOneTime(Case):
    def test_one_code_one_entry(self):
        inv = self.invite()
        r1 = self.svc.verify(inv["code"], "京A·12345", at=self.now)
        self.assertEqual(r1["verdict"], "ok")
        r2 = self.svc.verify(inv["code"], "京A·12345", at=self.now)
        self.assertEqual(r2["verdict"], "denied")
        self.assertIn("一个邀请只能对应一次有效入场", r2["reason"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM visits").fetchone()["c"], 1)

    def test_forwarded_code_wrong_plate_denied(self):
        inv = self.invite()
        r = self.svc.verify(inv["code"], "京A·99999", at=self.now)
        self.assertEqual(r["verdict"], "denied")
        self.assertIn("车牌不符", r["reason"])
        # 被拒绝后不能"消耗"掉，正确车主仍可入场
        r2 = self.svc.verify(inv["code"], "京A·12345", at=self.now)
        self.assertEqual(r2["verdict"], "ok")

    def test_revoked_code_denied(self):
        inv = self.invite()
        self.svc.revoke_invitation(inv["id"], "A", "访客取消", "A单位")
        r = self.svc.verify(inv["code"], "京A·12345", at=self.now)
        self.assertEqual(r["verdict"], "denied")
        self.assertIn("已被撤销", r["reason"])

    def test_unknown_code_denied(self):
        r = self.svc.verify("VDEADBEEF", "京A·12345", at=self.now)
        self.assertEqual(r["verdict"], "denied")
        self.assertIn("无效或不存在", r["reason"])

    def test_early_arrival_needs_confirmation(self):
        inv = self.invite(enter=self.now + timedelta(hours=2),
                          leave=self.now + timedelta(hours=4))
        r = self.svc.verify(inv["code"], "京A·12345", at=self.now)
        self.assertEqual(r["verdict"], "early")
        # 未确认前不产生 visit
        self.assertIsNone(self.visit_of(inv))
        r2 = self.svc.verify(inv["code"], "京A·12345", at=self.now,
                             confirm_early=True)
        self.assertEqual(r2["verdict"], "ok")

    def test_late_after_window_denied(self):
        inv = self.invite(enter=self.now - timedelta(hours=4),
                          leave=self.now - timedelta(hours=2))
        r = self.svc.verify(inv["code"], "京A·12345", at=self.now)
        self.assertEqual(r["verdict"], "denied")
        self.assertIn("批准时段已结束", r["reason"])

    def test_same_plate_multiple_invites_logged(self):
        i1 = self.invite(plate="京A·12345", unit="A",
                         leave=self.now + timedelta(hours=2))
        self.invite(plate="京A·12345", unit="B",
                    enter=self.now + timedelta(days=1),
                    leave=self.now + timedelta(days=1, hours=2))
        r = self.svc.verify(i1["code"], "京A·12345", at=self.now)
        self.assertEqual(r["verdict"], "ok")
        warn = self.conn.execute(
            "SELECT reason FROM events WHERE kind='verify' AND result='warning' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(warn)
        self.assertIn("其他有效邀请", warn["reason"])
        # 两份邀请各自独立：第二份仍有效
        self.assertEqual(
            self.conn.execute(
                "SELECT status FROM invitations WHERE unit_code='B'").fetchone()["status"],
            "active")


# ------------------------------------------------------------ 跨夜计费

class TestBilling(Case):
    def test_within_window_free(self):
        bd = billing.calc_fee(
            datetime(2026, 10, 2, 9), datetime(2026, 10, 2, 12),
            datetime(2026, 10, 2, 9, 5), datetime(2026, 10, 2, 12, 0))
        self.assertEqual(bd.amount, 0.0)
        self.assertEqual(bd.overtime_minutes, 0)

    def test_grace_15_minutes_free(self):
        bd = billing.calc_fee(
            datetime(2026, 10, 2, 9), datetime(2026, 10, 2, 12),
            datetime(2026, 10, 2, 9), datetime(2026, 10, 2, 12, 14))
        self.assertEqual(bd.amount, 0.0)

    def test_overtime_rounds_up_per_hour(self):
        # 180 分钟批准 + 15 宽限 = 195 免费；停 200 分钟 → 超时 5 分钟按 1 小时
        bd = billing.calc_fee(
            datetime(2026, 10, 2, 9), datetime(2026, 10, 2, 12),
            datetime(2026, 10, 2, 9), datetime(2026, 10, 2, 12, 20))
        self.assertEqual(bd.overtime_minutes, 5)
        self.assertEqual(bd.billed_units, 1)
        self.assertEqual(bd.amount, billing.RATE_PER_HOUR)

    def test_overnight_continuous_no_reset(self):
        # 批准 22:00~次日01:00（180 分），实际 22:00 入场，次日 03:00 离场
        # 停留 300 分钟，免费 195，超时 105 → 2 单位 = 4 元；
        # 关键：零点不重置，超时不是按"第二天 03:00 起算"的 3 小时
        bd = billing.calc_fee(
            datetime(2026, 10, 2, 22), datetime(2026, 10, 3, 1),
            datetime(2026, 10, 2, 22), datetime(2026, 10, 3, 3))
        self.assertEqual(bd.overtime_minutes, 105)
        self.assertEqual(bd.billed_units, 2)
        self.assertEqual(bd.amount, 4.0)

    def test_multi_night_24h_cap(self):
        # 超时 30 小时：未封顶 60 元；封顶 30 + 6 个零头单位（12 元）= 42 元
        bd = billing.calc_fee(
            datetime(2026, 10, 2, 9), datetime(2026, 10, 2, 10),
            datetime(2026, 10, 2, 9),
            datetime(2026, 10, 2, 9) + timedelta(hours=31, minutes=15))
        # 总停 1875 分，免费 75，超时 1800 分 = 30 小时
        self.assertEqual(bd.overtime_minutes, 1800)
        self.assertTrue(bd.capped)
        self.assertEqual(bd.cap_amount, 30.0 + 6 * billing.RATE_PER_HOUR)
        self.assertEqual(bd.amount, 42.0)

    def test_overnight_service_flow(self):
        inv = self.invite(enter=self.now - timedelta(days=1, hours=2),
                          leave=self.now - timedelta(days=1) + timedelta(hours=1))
        self.svc.verify(inv["code"], "京A·12345",
                        at=self.now - timedelta(days=1, hours=2))
        v = self.visit_of(inv)
        s = self.svc.settle(v, at=self.now)
        self.assertIn("跨夜", self.conn.execute(
            "SELECT detail_json FROM events WHERE kind='settle'").fetchone()[0])
        # 免费 180+15=195 分；昨天 10:00 到今天 12:00 停留 1560 分；超时 1365 分
        self.assertEqual(s["breakdown"].overtime_minutes, 1365)

    def test_settlement_frozen_gate_retry_keeps_fee(self):
        inv = self.invite(enter=self.now - timedelta(hours=4),
                          leave=self.now - timedelta(hours=2))
        self.svc.verify(inv["code"], "京A·12345",
                        at=self.now - timedelta(hours=3, minutes=50))
        v = self.visit_of(inv)
        s = self.svc.settle(v, at=self.now)
        amount = s["breakdown"].amount
        # 结算时点之后再"过很久"重试开闸，金额不得变化
        self.svc.retry_exit_gate  # sanity: method exists
        again = self.svc.settle(
            self.visit_of(inv), at=self.now + timedelta(hours=9))
        self.assertTrue(again["already"])
        self.assertEqual(float(again["visit"]["amount"]), amount)


# ----------------------------------------------------- 重复回执不重复收费

class TestIdempotentPay(Case):
    def _parked_overtime(self, plate="京A·12345", unit="A"):
        inv = self.invite(plate=plate, unit=unit,
                          enter=self.now - timedelta(hours=4),
                          leave=self.now - timedelta(hours=2))
        self.svc.verify(inv["code"], plate,
                        at=self.now - timedelta(hours=3, minutes=50))
        return inv, self.visit_of(inv)

    def test_duplicate_receipt_single_charge(self):
        inv, v = self._parked_overtime()
        r1 = self.svc.pay(v["id"])
        self.assertEqual(r1["gate"], "opened")
        self.assertFalse(r1["duplicate"])
        r2 = self.svc.pay(v["id"])
        r3 = self.svc.pay(v["id"])
        self.assertTrue(r2["duplicate"])
        self.assertEqual(r2["trade_no"], r1["trade_no"])
        self.assertEqual(r3["trade_no"], r1["trade_no"])
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM payments WHERE visit_id=?",
            (v["id"],)).fetchone()["c"]
        self.assertEqual(n, 1)
        # 每次重复回执都有处置记录
        dup_logs = self.conn.execute(
            "SELECT COUNT(*) c FROM events WHERE kind='pay' AND result='warning'"
        ).fetchone()["c"]
        self.assertGreaterEqual(dup_logs, 2)

    def test_payment_failure_no_charge_then_success(self):
        inv, v = self._parked_overtime()
        self.svc.payment.inject_failure(1)
        with self.assertRaises(DomainError):
            self.svc.pay(v["id"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM payments").fetchone()["c"], 0)
        r = self.svc.pay(v["id"])
        self.assertEqual(r["gate"], "opened")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM payments").fetchone()["c"], 1)

    def test_paid_but_gate_stuck_then_retry_no_recharge(self):
        inv, v = self._parked_overtime()
        self.svc.gate.inject_failures(1)
        r = self.svc.pay(v["id"])
        self.assertEqual(r["gate"], "failed")
        amount = r["amount"]
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM payments").fetchone()["c"], 1)
        # 支付完成但车没出场
        self.assertIsNone(self.visit_of(inv)["actual_exit"])
        rr = self.svc.retry_exit_gate(v["id"])
        self.assertTrue(rr["ok"])
        self.assertIsNotNone(self.visit_of(inv)["actual_exit"])
        # 再收到出场回执仍是原交易
        again = self.svc.pay(v["id"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM payments").fetchone()["c"], 1)
        self.assertEqual(float(self.visit_of(inv)["amount"]), amount)

    def test_free_within_window_payment_zero(self):
        inv = self.invite(enter=self.now - timedelta(hours=1),
                          leave=self.now + timedelta(hours=1))
        self.svc.verify(inv["code"], "京A·12345", at=self.now - timedelta(minutes=5))
        v = self.visit_of(inv)
        r = self.svc.pay(v["id"], at=self.now + timedelta(minutes=10))
        self.assertEqual(r["amount"], 0.0)
        pay = self.conn.execute(
            "SELECT * FROM payments WHERE visit_id=?", (v["id"],)).fetchone()
        self.assertEqual(pay["amount"], 0.0)


# --------------------------------------------------------------- 单位隔离

class TestUnitIsolation(Case):
    def test_cannot_revoke_other_unit(self):
        inv = self.invite(unit="B")
        with self.assertRaises(DomainError) as cm:
            self.svc.revoke_invitation(inv["id"], "A", "代为修改", "A单位")
        self.assertIn("单位隔离", str(cm.exception))
        # 邀请未受影响
        self.assertEqual(self.svc.get_invitation(inv["id"])["status"], "active")

    def test_cannot_create_for_other_unit(self):
        with self.assertRaises(DomainError):
            self.svc.create_invitation(
                plate="京A·11111", unit_code="ZZ", building="X", contact="y",
                enter_at=clock.fmt(self.now),
                leave_at=clock.fmt(self.now + timedelta(hours=1)),
                created_by="A单位")

    def test_unit_list_scoped(self):
        self.invite(plate="京A·11111", unit="A")
        self.invite(plate="京B·22222", unit="B")
        a_rows = self.svc.list_invitations("A")
        self.assertTrue(all(r["unit_code"] == "A" for r in a_rows))
        self.assertEqual(len(a_rows), 1)

    def test_owner_revoke_own_ok(self):
        inv = self.invite(unit="B")
        self.svc.revoke_invitation(inv["id"], "B", "本单位取消", "B单位")
        self.assertEqual(self.svc.get_invitation(inv["id"])["status"], "revoked")

    def test_exception_requires_admin_review(self):
        inv = self.invite(enter=self.now - timedelta(hours=4),
                          leave=self.now - timedelta(hours=2))
        self.svc.verify(inv["code"], "京A·12345",
                        at=self.now - timedelta(hours=3, minutes=50))
        v = self.visit_of(inv)
        rv = self.svc.request_exception(v["id"], "设备故障", "门岗")
        # 复核前：车辆仍在园、费用未免
        self.assertEqual(len(self.svc.pending_exceptions()), 1)
        self.assertIsNone(self.visit_of(inv)["actual_exit"])
        out = self.svc.review_exception(rv["id"], True, "园区管理员", "属实")
        self.assertEqual(out["gate"], "opened")
        self.assertEqual(float(self.visit_of(inv)["amount"]), 0.0)
        self.assertIsNotNone(self.visit_of(inv)["actual_exit"])
        # 免费放行进的是复核流，不产生收费
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM payments").fetchone()["c"], 0)

    def test_exception_reject_then_normal_pay(self):
        inv = self.invite(enter=self.now - timedelta(hours=4),
                          leave=self.now - timedelta(hours=2))
        self.svc.verify(inv["code"], "京A·12345",
                        at=self.now - timedelta(hours=3, minutes=50))
        v = self.visit_of(inv)
        rv = self.svc.request_exception(v["id"], "理由不充分的申请", "门岗")
        out = self.svc.review_exception(rv["id"], False, "园区管理员", "不属实")
        self.assertFalse(out["approved"])
        r = self.svc.pay(v["id"])
        self.assertEqual(r["gate"], "opened")
        self.assertGreater(r["amount"], 0.0)


# --------------------------------------------------------------- 离线与重放

class TestOfflineReplay(Case):
    def test_offline_queue_then_replay_once(self):
        inv = self.invite()
        self.svc.set_gate_online(False)
        r = self.svc.verify(inv["code"], "京A·12345", at=self.now)
        self.assertEqual(r["verdict"], "offline")
        # 离线期间重复出示不重复排队
        r2 = self.svc.verify(inv["code"], "京A·12345", at=self.now)
        self.assertEqual(r2["verdict"], "offline")
        self.assertEqual(len(self.svc.pending_queue()), 1)
        self.assertIsNone(self.visit_of(inv))

        self.svc.set_gate_online(True)
        res = self.svc.replay_pending()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["verdict"], "ok")
        # 入场时间保留实际到场时间
        v = self.visit_of(inv)
        self.assertEqual(v["actual_enter"], clock.iso(self.now))
        # 重放后队列清空，再来一次不会补第二条 visit
        self.assertEqual(self.svc.replay_pending(), [])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM visits").fetchone()["c"], 1)

    def test_replay_blocked_while_still_offline(self):
        inv = self.invite()
        self.svc.set_gate_online(False)
        self.svc.verify(inv["code"], "京A·12345", at=self.now)
        with self.assertRaises(DomainError):
            self.svc.replay_pending()
        # 队列原样保留、没有产生 visit
        self.assertEqual(len(self.svc.pending_queue()), 1)
        self.assertIsNone(self.visit_of(inv))
        self.svc.set_gate_online(True)
        res = self.svc.replay_pending()
        self.assertEqual(res[0]["verdict"], "ok")

    def test_replay_denial_recorded(self):
        inv = self.invite()
        self.svc.set_gate_online(False)
        self.svc.verify(inv["code"], "京A·12345", at=self.now)
        self.svc.set_gate_online(True)
        # 重放前邀请被撤销：重放应失败且不产生 visit
        self.svc.revoke_invitation(inv["id"], "A", "离线期间访客取消", "A单位")
        res = self.svc.replay_pending()
        self.assertEqual(res[0]["verdict"], "denied")
        self.assertIsNone(self.visit_of(inv))
        q = self.conn.execute("SELECT result FROM offline_queue").fetchone()
        self.assertEqual(q["result"], "failed")


# --------------------------------------------------------------- 其他留痕

class TestRecords(Case):
    def test_every_outcome_has_event(self):
        inv = self.invite()
        self.svc.verify("VBADCODE", "京A·00000", at=self.now)          # 无效码
        self.svc.verify(inv["code"], "京A·99999", at=self.now)        # 车牌不符
        self.svc.verify(inv["code"], "京A·12345", at=self.now)        # 通过
        self.svc.verify(inv["code"], "京A·12345", at=self.now)        # 重复
        kinds = [r["kind"] for r in self.svc.events(inv["id"])]
        self.assertIn("deliver", kinds)
        denials = self.conn.execute(
            "SELECT COUNT(*) c FROM events WHERE result='denied'").fetchone()["c"]
        self.assertGreaterEqual(denials, 2)

    def test_photo_masked_and_saved(self):
        inv = self.invite()
        self.svc.verify(inv["code"], "京A·12345", at=self.now)
        ev = self.conn.execute(
            "SELECT photo_path FROM events WHERE photo_path<>'' LIMIT 1").fetchone()
        self.assertIsNotNone(ev)
        p = Path(self.dir / "photos" / ev["photo_path"])
        self.assertTrue(p.is_file())
        self.assertTrue(p.read_bytes().startswith(b"\x89PNG"))
        from .adapters import mask_plate
        self.assertEqual(mask_plate("京A·12345"), "京A·***45")
        self.assertEqual(mask_plate("沪B·88"), "沪B·**")


if __name__ == "__main__":
    unittest.main(verbosity=2)
