"""核心业务服务。

所有业务规则集中在这里，Web 层与离线重放、测试都只调用本类：
- 邀请一次性：通行码核验通过即创建 visits（inv_id 唯一），重复出示一律拒绝；
- 出场结算按结算时点计费并落盘，支付/回执以 visit 为幂等键，重试不重复收费；
- 单位隔离：被访单位只能查看/修改本单位邀请，跨单位操作拒绝；
- 异常免费放行必须园区管理员复核；
- 每种异常情形都写 events 处置记录（理由用完整文字）。
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

from . import billing, clock
from .adapters import (GateAdapter, GateOffline, PaymentAdapter, PayDeclined,
                       take_plate_photo)


class DomainError(Exception):
    """业务规则拒绝（文字可直接展示给操作员）。"""


class Service:
    def __init__(self, conn: sqlite3.Connection, photo_dir: str = "data/photos"):
        self.conn = conn
        self.gate = GateAdapter(conn)
        self.payment = PaymentAdapter(conn)
        self.photo_dir = photo_dir

    # ------------------------------------------------------------------ 基础

    def _event(self, kind: str, result: str, reason: str,
               inv=None, plate: str | None = None, detail: dict | None = None,
               operator: str = "", photo: str = ""):
        self.conn.execute(
            "INSERT INTO events(inv_id,code,plate,unit_code,kind,result,reason,"
            "detail_json,operator,photo_path,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (inv["id"] if inv is not None else None,
             inv["code"] if inv is not None else None,
             plate if plate is not None else (inv["plate"] if inv is not None else None),
             inv["unit_code"] if inv is not None else None,
             kind, result, reason,
             json.dumps(detail or {}, ensure_ascii=False),
             operator, photo, clock.iso(clock.now())))

    def find_code(self, code: str):
        return self.conn.execute(
            "SELECT * FROM invitations WHERE code=? ORDER BY id DESC",
            (code.strip().upper(),)).fetchone()

    def get_invitation(self, inv_id: int):
        inv = self.conn.execute(
            "SELECT * FROM invitations WHERE id=?", (inv_id,)).fetchone()
        if inv is None:
            raise DomainError("邀请不存在")
        return inv

    def ensure_owner(self, inv, actor_unit: str | None):
        """被访单位不能操作其他单位的邀请。"""
        if actor_unit and inv["unit_code"] != actor_unit:
            raise DomainError(
                f"单位隔离：该邀请属于 {inv['unit_code']} 单位，"
                f"当前账号 {actor_unit} 单位无权修改其他单位的邀请")

    # -------------------------------------------------------------- 邀请交付

    def create_invitation(self, *, plate: str, unit_code: str, building: str,
                          contact: str, enter_at: str, leave_at: str,
                          created_by: str) -> sqlite3.Row:
        plate = plate.strip().upper().replace("*", "·")
        building = building.strip()
        contact = contact.strip()
        if not plate or not building or not contact:
            raise DomainError("车牌、访问楼栋、联系人为必填项")
        try:
            ent = clock.parse(enter_at)
            lev = clock.parse(leave_at)
        except ValueError as e:
            raise DomainError(str(e))
        if lev <= ent:
            raise DomainError("批准离场时间必须晚于批准入场时间")
        unit = self.conn.execute(
            "SELECT code FROM units WHERE code=?", (unit_code,)).fetchone()
        if unit is None:
            raise DomainError(f"被访单位 {unit_code} 不存在")

        code = "V" + secrets.token_hex(4).upper()
        cur = self.conn.execute(
            "INSERT INTO invitations(code,plate,unit_code,building,contact,"
            "enter_at,leave_at,status,created_at,created_by) "
            "VALUES(?,?,?,?,?,?,?, 'active', ?,?)",
            (code, plate, unit_code, building, contact,
             clock.iso(ent), clock.iso(lev), clock.iso(clock.now()), created_by))
        inv = self.get_invitation(cur.lastrowid)
        self._event("deliver", "ok",
                    f"邀请已交付：{unit_code} 单位为车牌 {plate} 申请临时通行，"
                    f"批准时段 {clock.fmt(ent)} 至 {clock.fmt(lev)}",
                    inv=inv, operator=created_by)
        return inv

    def revoke_invitation(self, inv_id: int, actor_unit: str | None,
                          reason: str, by: str):
        inv = self.get_invitation(inv_id)
        self.ensure_owner(inv, actor_unit)
        if inv["status"] != "active":
            raise DomainError(f"邀请当前状态为 {inv['status']}，不能撤销")
        if not reason.strip():
            raise DomainError("撤销邀请必须填写完整理由")
        self.conn.execute(
            "UPDATE invitations SET status='revoked', revoke_reason=?, "
            "revoke_at=?, revoke_by=? WHERE id=?",
            (reason.strip(), clock.iso(clock.now()), by, inv_id))
        self._event("revoke", "ok", f"邀请已撤销：{reason.strip()}",
                    inv=inv, operator=by)

    def list_invitations(self, unit_code: str | None = None):
        if unit_code:
            return self.conn.execute(
                "SELECT * FROM invitations WHERE unit_code=? ORDER BY id DESC",
                (unit_code,)).fetchall()
        return self.conn.execute(
            "SELECT * FROM invitations ORDER BY id DESC").fetchall()

    # ---------------------------------------------------------------- 门岗核验

    def verify(self, code: str, plate: str, operator: str = "门岗",
               at: datetime | None = None, confirm_early: bool = False,
               _replay_of: int | None = None) -> dict:
        """核验通行码并放行。返回处置说明字典；各种结果都有处置记录。"""
        at = at or clock.now()
        plate_in = plate.strip().upper().replace("*", "·")
        inv = self.find_code(code)
        tag = f"（离线重放队列#{_replay_of}）" if _replay_of else ""

        def deny(reason, detail=None, photo=""):
            self._event("verify", "denied", reason + tag, inv=inv,
                        plate=plate_in or None, detail=detail,
                        operator=operator, photo=photo)
            return {"verdict": "denied", "reason": reason, "inv": inv}

        if inv is None:
            return deny(f"通行码 {code.strip().upper()} 无效或不存在，"
                        "请勿放行，提示访客联系被访单位重新获取邀请")
        if not plate_in:
            return deny("未识别到车牌，无法核验，请勿放行")

        photo = take_plate_photo(plate_in, self.photo_dir, at)

        if inv["status"] == "revoked":
            return deny(f"该邀请已被撤销（理由：{inv['revoke_reason']}），"
                        "通行码失效，请勿放行", photo=photo)
        visit = self.conn.execute(
            "SELECT * FROM visits WHERE inv_id=?", (inv["id"],)).fetchone()
        if visit is not None or inv["status"] in ("entered", "exited", "exception"):
            return deny("该通行码已使用过：一个邀请只能对应一次有效入场，"
                        "重复出示不予放行（防止通行码转发复用）",
                        detail={"已入场时间": visit["actual_enter"] if visit else None},
                        photo=photo)
        if plate_in != inv["plate"]:
            return deny(
                f"车牌不符：通行码登记车牌为 {inv['plate']}，到场车牌为 {plate_in}。"
                "可能是车牌录错或通行码被转发给他人，暂不放行，"
                "请由被访单位核对并重新申请邀请",
                photo=photo)

        enter_at = clock.parse_iso(inv["enter_at"])
        leave_at = clock.parse_iso(inv["leave_at"])
        early = (enter_at - at).total_seconds()
        late = (at - leave_at).total_seconds()

        if late > 0:
            return deny(
                f"车辆在批准离场时间 {clock.fmt(leave_at)} 之后才到场，"
                "批准时段已结束，不予放行，请重新申请邀请", photo=photo)

        warnings: list[str] = []
        if early > 60 and not confirm_early:
            mins = int(early // 60)
            reason = (f"车辆提前 {mins} 分钟到达（批准入场时间 {clock.fmt(enter_at)}），"
                      "门岗已暂缓放行；与被访单位联系人确认后可点击"
                      "「确认提前到达放行」")
            self._event("verify", "warning", reason + tag, inv=inv, plate=plate_in,
                        detail={"提前分钟": mins}, operator=operator, photo=photo)
            return {"verdict": "early", "reason": reason, "inv": inv,
                    "early_minutes": int(early // 60)}

        # 同车牌多份有效邀请：不影响本次放行，但必须留痕，按出示的码对应邀请入场
        others = self.conn.execute(
            "SELECT code,unit_code,enter_at,leave_at FROM invitations "
            "WHERE plate=? AND id<>? AND status='active' ORDER BY id",
            (plate_in, inv["id"])).fetchall()
        if others:
            desc = "；".join(
                f"{o['code']}（{o['unit_code']} 单位，{o['enter_at']}~{o['leave_at']}）"
                for o in others)
            w = (f"该车牌同时存在 {len(others)} 份其他有效邀请：{desc}；"
                 f"本次按访客出示的通行码 {inv['code']} 对应邀请入场，"
                 "其余邀请不受影响")
            warnings.append(w)
            self._event("verify", "warning", w + tag, inv=inv, plate=plate_in,
                        detail={"其他邀请": [dict(o) for o in others]},
                        operator=operator, photo=photo)

        if early > 60 and confirm_early:
            warnings.append(
                f"提前到达已经被访单位联系人 {inv['contact']} 电话确认，门岗放行")
        reason_pass = (f"核验通过，放行入场。批准时段 "
                       f"{clock.fmt(enter_at)} 至 {clock.fmt(leave_at)}"
                       + ("；" + "；".join(warnings) if warnings else ""))
        return self._admit(inv, at, operator, reason_pass + tag,
                           photo=photo, warnings=warnings, replay_of=_replay_of)

    def _admit(self, inv, at, operator, reason, *, photo, warnings, replay_of):
        """核验通过后的实际入场：闸机在线则开闸；离线则进重放队列。"""
        tag = f"（离线重放队列#{replay_of}）" if replay_of else ""
        try:
            opened, cmd_id, detail = self.gate.open(None, "enter")
        except GateOffline:
            dup = self.conn.execute(
                "SELECT id FROM offline_queue WHERE result='pending' "
                "AND json_extract(payload_json,'$.code')=?",
                (inv["code"],)).fetchone()
            if dup is not None:
                reason = (f"门禁仍离线，该通行码已在离线队列 #{dup['id']} 中，"
                          "不重复登记，恢复后按原到场时间重放")
                self._event("offline_queue", "warning", reason + tag,
                            inv=inv, plate=inv["plate"], operator=operator,
                            photo=photo)
                return {"verdict": "offline", "reason": reason, "inv": inv,
                        "queue_id": dup["id"], "warnings": warnings}
            payload = {"code": inv["code"], "plate": inv["plate"],
                       "queued_at": clock.iso(at), "operator": operator}
            cur = self.conn.execute(
                "INSERT INTO offline_queue(payload_json,queued_at) VALUES(?,?)",
                (json.dumps(payload, ensure_ascii=False), clock.iso(at)))
            self._event("offline_queue", "pending",
                        f"门禁离线，车辆 {inv['plate']} 的核验结果已暂存"
                        f"（队列#{cur.lastrowid}），门岗人工核对后待网络恢复重放",
                        inv=inv, plate=inv["plate"],
                        detail={"queue_id": cur.lastrowid}, operator=operator,
                        photo=photo)
            return {"verdict": "offline",
                    "reason": "门禁控制器离线：核验信息已进入本地离线队列，"
                              f"队列编号 #{cur.lastrowid}，恢复在线后将自动重放",
                    "inv": inv, "queue_id": cur.lastrowid, "warnings": warnings}

        cur = self.conn.execute(
            "INSERT INTO visits(inv_id,code,plate,unit_code,actual_enter,"
            "gate_enter_cmd) VALUES(?,?,?,?,?,?)",
            (inv["id"], inv["code"], inv["plate"], inv["unit_code"],
             clock.iso(at), f"#{cmd_id} {detail}"))
        visit_id = cur.lastrowid
        self.conn.execute(
            "UPDATE gate_commands SET visit_id=? WHERE id=?", (visit_id, cmd_id))
        self.conn.execute(
            "UPDATE invitations SET status='entered' WHERE id=?", (inv["id"],))
        enter_result = "ok" if opened else "warning"
        if not opened:
            reason += ("；道闸卡阻未自动开启，门岗已人工抬杆放行并登记指令"
                       f" #{cmd_id}，设备恢复后可在闸机记录中重试核对")
        self._event("gate_enter", enter_result, reason, inv=inv, plate=inv["plate"],
                    detail={"visit_id": visit_id, "gate_command": cmd_id,
                            "提前确认": early_note(warnings)},
                    operator=operator, photo=photo)
        return {"verdict": "ok", "reason": reason, "inv": inv,
                "visit_id": visit_id, "warnings": warnings}

    # ------------------------------------------------------------ 离线与重放

    def set_gate_online(self, online: bool, by: str = "管理员"):
        self.gate.set_online(online)
        state = "在线" if online else "离线"
        self._event("replay", "ok" if online else "warning",
                    f"门禁控制器已切换为{state}", operator=by)

    def pending_queue(self):
        return self.conn.execute(
            "SELECT * FROM offline_queue WHERE result='pending' ORDER BY id"
        ).fetchall()

    def replay_pending(self, by: str = "系统") -> list[dict]:
        """按入队顺序重放；用车辆实际到场（入队）时间作为入场时间。"""
        if not self.gate.is_online():
            self._event("replay", "warning",
                        "门禁仍处于离线状态，本次重放未执行，离线队列原样保留，"
                        "待恢复在线后再重放", operator=by)
            raise DomainError("门禁仍处于离线状态，请先恢复门禁在线，再重放离线队列")
        results = []
        for q in self.pending_queue():
            payload = json.loads(q["payload_json"])
            queued_at = clock.parse_iso(payload["queued_at"])
            r = self.verify(payload["code"], payload["plate"],
                            operator=payload.get("operator", "门岗"),
                            at=queued_at, confirm_early=True,
                            _replay_of=q["id"])
            ok = r["verdict"] in ("ok",)
            self.conn.execute(
                "UPDATE offline_queue SET replayed_at=?, result=? WHERE id=?",
                (clock.iso(clock.now()), "done" if ok else "failed", q["id"]))
            self._event("replay", "ok" if ok else "denied",
                        f"离线队列 #{q['id']} 重放完成：{r['reason']}",
                        inv=r.get("inv"), plate=payload["plate"],
                        detail={"queue_id": q["id"], "verdict": r["verdict"]},
                        operator=by)
            results.append({"queue_id": q["id"], **r})
        return results

    # ---------------------------------------------------------------- 在园车辆

    def open_visits(self) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT v.*, i.building, i.contact, i.enter_at AS approved_enter, "
            "i.leave_at AS approved_leave FROM visits v "
            "JOIN invitations i ON i.id=v.inv_id "
            "WHERE v.actual_exit IS NULL ORDER BY v.actual_enter").fetchall()
        out = []
        now = clock.now()
        for r in rows:
            d = dict(r)
            d["remaining_seconds"] = int(
                (clock.parse_iso(r["approved_leave"]) - now).total_seconds())
            d["review"] = self.conn.execute(
                "SELECT * FROM exception_reviews WHERE visit_id=? "
                "ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
            d["last_gate"] = self.conn.execute(
                "SELECT * FROM gate_commands WHERE visit_id=? "
                "ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
            out.append(d)
        return out

    def get_visit_by_code(self, code: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT v.*, i.building, i.contact, i.enter_at AS approved_enter, "
            "i.leave_at AS approved_leave, i.status AS inv_status "
            "FROM visits v JOIN invitations i ON i.id=v.inv_id "
            "WHERE v.code=?", (code.strip().upper(),)).fetchone()
        if row is None:
            raise DomainError("找不到该通行码对应的入场记录")
        return row

    # -------------------------------------------------------- 超时计费与收费

    def settle(self, visit, at: datetime | None = None) -> dict:
        """结算（计算并冻结超时金额）。幂等：重复结算返回原结果。"""
        inv = self.get_invitation(visit["inv_id"])
        existing = self.conn.execute(
            "SELECT * FROM payments WHERE visit_id=?", (visit["id"],)).fetchone()
        if visit["settle_at"]:
            bd = self._breakdown_for(visit, inv,
                                     clock.parse_iso(visit["settle_at"]))
            return {"visit": visit, "breakdown": bd, "already": True,
                    "payment": existing}

        at = at or clock.now()
        ent = clock.parse_iso(visit["actual_enter"])
        # 结算后闸门可能卡阻，actual_exit 以结算时点为准，重试开闸不改计费
        bd = billing.calc_fee(clock.parse_iso(inv["enter_at"]),
                              clock.parse_iso(inv["leave_at"]),
                              ent, at)
        overnight = at.date() > clock.parse_iso(inv["leave_at"]).date()
        self.conn.execute(
            "UPDATE visits SET settle_at=?, amount=? WHERE id=?",
            (clock.iso(at), bd.amount, visit["id"]))
        note = "停留跨夜，按连续时长累计计费，不因零点重新起算" if overnight else ""
        self._event("settle", "ok",
                    f"出场结算完成：免费 {bd.free_minutes} 分钟"
                    f"（批准 {bd.approved_minutes} 分钟 + 离场宽限 {bd.grace_minutes} 分钟），"
                    f"超时 {bd.overtime_minutes} 分钟，应收 {bd.amount:.2f} 元。" + note,
                    inv=inv,
                    plate=visit["plate"],
                    detail={"breakdown": bd.as_dict(), "跨夜": overnight,
                            "结算时间": clock.iso(at)},
                    operator="门岗")
        visit = self.conn.execute("SELECT * FROM visits WHERE id=?",
                                  (visit["id"],)).fetchone()
        return {"visit": visit, "breakdown": bd, "already": False,
                "payment": None}

    def _breakdown_for(self, visit, inv, at: datetime):
        return billing.calc_fee(clock.parse_iso(inv["enter_at"]),
                                clock.parse_iso(inv["leave_at"]),
                                clock.parse_iso(visit["actual_enter"]), at)

    def pay(self, visit_id: int, operator: str = "车主自助",
            at: datetime | None = None) -> dict:
        """支付并尝试出场开闸。支付幂等；闸机卡阻时保留重试入口。"""
        visit = self.conn.execute(
            "SELECT * FROM visits WHERE id=?", (visit_id,)).fetchone()
        if visit is None:
            raise DomainError("入场记录不存在")
        s = self.settle(visit, at=at)
        visit, bd = s["visit"], s["breakdown"]
        idem = f"pay-visit-{visit['id']}"

        try:
            trade_no, charged = self.payment.charge(idem, bd.amount)
        except PayDeclined as e:
            self._event("pay", "denied", f"支付未成功：{e}，未扣款，请重试",
                        inv=self.get_invitation(visit["inv_id"]),
                        plate=visit["plate"], operator=operator)
            raise DomainError(str(e))

        if charged:
            self.conn.execute(
                "INSERT INTO payments(idem_key,inv_id,visit_id,amount,status,"
                "channel,trade_no,created_at) VALUES(?,?,?,?,'success',?,?,?)",
                (idem, visit["inv_id"], visit["id"], bd.amount, "模拟支付",
                 trade_no, clock.iso(clock.now())))
            self._event("pay", "ok",
                        f"支付成功：超时费 {bd.amount:.2f} 元，交易号 {trade_no}",
                        inv=self.get_invitation(visit["inv_id"]),
                        plate=visit["plate"],
                        detail={"trade_no": trade_no, "amount": bd.amount},
                        operator=operator)
        else:
            self._event("pay", "warning",
                        f"收到重复出场回执：该车辆费用已收取（交易号 {trade_no}），"
                        "按幂等规则直接返回原交易，未重复扣款",
                        inv=self.get_invitation(visit["inv_id"]),
                        plate=visit["plate"],
                        detail={"trade_no": trade_no, "重复回执": True},
                        operator=operator)

        return self._open_exit(visit, bd, operator,
                               paid_note=(not charged), trade_no=trade_no)

    def _open_exit(self, visit, bd, operator, *, paid_note, trade_no):
        try:
            opened, cmd_id, detail = self.gate.open(visit["id"], "exit")
        except GateOffline:
            self._event("gate_exit", "pending",
                        "支付已完成但门禁离线，出场开闸指令已登记，恢复后重试",
                        inv=self.get_invitation(visit["inv_id"]),
                        plate=visit["plate"], operator=operator)
            return {"paid": True, "gate": "offline", "amount": bd.amount,
                    "trade_no": trade_no, "duplicate": paid_note,
                    "reason": "支付成功；门禁离线，开闸指令待恢复后重试"}
        if opened:
            self._finish_exit(visit, cmd_id, detail, operator, bd, trade_no)
            base = "支付成功，闸机已开启，祝一路平安"
            if paid_note:
                base += "（本次为重复回执，未重复扣款）"
            return {"paid": True, "gate": "opened", "amount": bd.amount,
                    "trade_no": trade_no, "duplicate": paid_note,
                    "reason": base}
        return {"paid": True, "gate": "failed", "amount": bd.amount,
                "trade_no": trade_no, "duplicate": paid_note,
                "command_id": cmd_id,
                "reason": "支付成功，但道闸卡阻、闸机未开。费用已结清，"
                          "请按「重试开闸」，重试不会重新计费"}

    def _finish_exit(self, visit, cmd_id, detail, operator, bd, trade_no):
        self.conn.execute(
            "UPDATE visits SET actual_exit=COALESCE(actual_exit, settle_at), "
            "gate_exit_cmd=? WHERE id=?",
            (f"#{cmd_id} {detail}", visit["id"]))
        self.conn.execute(
            "UPDATE invitations SET status='exited' WHERE id=?", (visit["inv_id"],))
        self._event("gate_exit", "ok", f"车辆出场，{detail}",
                    inv=self.get_invitation(visit["inv_id"]),
                    plate=visit["plate"],
                    detail={"gate_command": cmd_id, "trade_no": trade_no},
                    operator=operator)

    def retry_exit_gate(self, visit_id: int, operator: str = "门岗") -> dict:
        """支付后闸机未开（卡阻/离线）的重试；不重新计费、不重复扣款。"""
        visit = self.conn.execute(
            "SELECT * FROM visits WHERE id=?", (visit_id,)).fetchone()
        if visit is None:
            raise DomainError("入场记录不存在")
        last = self.conn.execute(
            "SELECT * FROM gate_commands WHERE visit_id=? AND direction='exit' "
            "ORDER BY id DESC LIMIT 1", (visit_id,)).fetchone()
        if last is None:
            raise DomainError("没有待重试的出场开闸指令")
        ok, msg = self.gate.retry(last["id"])
        if ok and visit["actual_exit"] is None:
            self.conn.execute(
                "UPDATE visits SET actual_exit=COALESCE(settle_at, ?) WHERE id=?",
                (clock.iso(clock.now()), visit["id"]))
            self.conn.execute(
                "UPDATE invitations SET status='exited' WHERE id=?",
                (visit["inv_id"],))
        self._event("gate_retry", "ok" if ok else "warning",
                    f"出场开闸重试：{msg}。按原结算金额 {visit['amount']:.2f} 元执行，"
                    "未重新计费、未重复扣款",
                    inv=self.get_invitation(visit["inv_id"]),
                    plate=visit["plate"],
                    detail={"原指令": last["id"], "结果": msg}, operator=operator)
        if not ok:
            raise DomainError(msg)
        return {"ok": True, "reason": msg}

    # ----------------------------------------------------------- 异常免费放行

    def request_exception(self, visit_id: int, reason: str,
                          requested_by: str) -> sqlite3.Row:
        visit = self.conn.execute(
            "SELECT * FROM visits WHERE id=?", (visit_id,)).fetchone()
        if visit is None:
            raise DomainError("入场记录不存在")
        if not reason.strip():
            raise DomainError("异常放行必须填写完整理由，供园区管理员复核")
        pend = self.conn.execute(
            "SELECT * FROM exception_reviews WHERE visit_id=? AND status='pending'",
            (visit_id,)).fetchone()
        if pend is not None:
            raise DomainError("该车辆已有一条待复核的异常放行申请，请勿重复提交")
        cur = self.conn.execute(
            "INSERT INTO exception_reviews(visit_id,inv_id,plate,reason,"
            "requested_by,requested_at) VALUES(?,?,?,?,?,?)",
            (visit_id, visit["inv_id"], visit["plate"], reason.strip(),
             requested_by, clock.iso(clock.now())))
        self._event("exception", "pending",
                    f"门岗申请异常免费放行：{reason.strip()}；已提交园区管理员复核",
                    inv=self.get_invitation(visit["inv_id"]),
                    plate=visit["plate"], detail={"review_id": cur.lastrowid},
                    operator=requested_by)
        return self.conn.execute(
            "SELECT * FROM exception_reviews WHERE id=?", (cur.lastrowid,)).fetchone()

    def pending_exceptions(self):
        return self.conn.execute(
            "SELECT r.*, i.building, i.contact, i.unit_code, v.actual_enter, "
            "v.actual_exit, v.settle_at FROM exception_reviews r "
            "JOIN invitations i ON i.id=r.inv_id JOIN visits v ON v.id=r.visit_id "
            "WHERE r.status='pending' ORDER BY r.id").fetchall()

    def review_exception(self, review_id: int, approve: bool,
                         reviewer: str, note: str) -> dict:
        r = self.conn.execute(
            "SELECT * FROM exception_reviews WHERE id=?", (review_id,)).fetchone()
        if r is None:
            raise DomainError("复核单不存在")
        if r["status"] != "pending":
            raise DomainError("该复核单已处理")
        status = "approved" if approve else "rejected"
        self.conn.execute(
            "UPDATE exception_reviews SET status=?, reviewer=?, reviewed_at=?, "
            "review_note=? WHERE id=?",
            (status, reviewer, clock.iso(clock.now()), note.strip(), review_id))
        visit = self.conn.execute(
            "SELECT * FROM visits WHERE id=?", (r["visit_id"],)).fetchone()
        inv = self.get_invitation(r["inv_id"])
        if not approve:
            self._event("admin_review", "denied",
                        f"园区管理员驳回异常免费放行（理由：{r['reason']}）"
                        + (f"；批示：{note.strip()}" if note.strip() else ""),
                        inv=inv, plate=r["plate"], operator=reviewer)
            return {"ok": True, "approved": False,
                    "reason": "已驳回，车辆需正常结算缴费后出场"}

        # 批准：金额置 0 并冻结结算，再按闸机状态放行
        self.conn.execute(
            "UPDATE visits SET exception=1, settle_at=COALESCE(settle_at,?), "
            "amount=0 WHERE id=?",
            (clock.iso(clock.now()), r["visit_id"]))
        self.conn.execute(
            "UPDATE invitations SET status='exception' WHERE id=?", (inv["id"],))
        self._event("admin_review", "ok",
                    f"园区管理员批准异常免费放行：{r['reason']}"
                    + (f"；批示：{note.strip()}" if note.strip() else "")
                    + "。超时费全免（0.00 元）",
                    inv=inv, plate=r["plate"], operator=reviewer,
                    detail={"review_id": review_id})
        visit = self.conn.execute("SELECT * FROM visits WHERE id=?",
                                  (visit["id"],)).fetchone()
        try:
            opened, cmd_id, detail = self.gate.open(visit["id"], "exit")
        except GateOffline:
            self._event("gate_exit", "pending",
                        "异常放行已批准但门禁离线，开闸指令待恢复后重试",
                        inv=inv, plate=r["plate"], operator=reviewer)
            return {"ok": True, "approved": True, "gate": "offline",
                    "reason": "已批准免费放行；门禁离线，恢复后请在在园车辆页重试开闸"}
        if opened:
            self._finish_exit(visit, cmd_id, detail, reviewer,
                              SimpleNamespace(amount=0.0), trade_no="免费放行")
            return {"ok": True, "approved": True, "gate": "opened",
                    "reason": "已批准免费放行，闸机已开启"}
        return {"ok": True, "approved": True, "gate": "failed",
                "command_id": cmd_id,
                "reason": "已批准免费放行，但道闸卡阻；请在在园车辆页重试开闸"}

    # --------------------------------------------------------------- 查询流水

    def payments(self):
        return self.conn.execute(
            "SELECT p.*, i.plate, i.building, i.contact FROM payments p "
            "JOIN invitations i ON i.id=p.inv_id ORDER BY p.id DESC").fetchall()

    def events(self, inv_id: int | None = None, limit: int = 200):
        q = ("SELECT e.*, i.building FROM events e "
             "LEFT JOIN invitations i ON i.id=e.inv_id "
             + ("WHERE e.inv_id=? " if inv_id else "")
             + "ORDER BY e.id DESC LIMIT ?")
        args = (inv_id, limit) if inv_id else (limit,)
        return self.conn.execute(q, args).fetchall()

    def receipt(self, visit_id: int) -> dict:
        visit = self.conn.execute(
            "SELECT v.*, i.building, i.contact, i.unit_code, i.enter_at "
            "AS approved_enter, i.leave_at AS approved_leave "
            "FROM visits v JOIN invitations i ON i.id=v.inv_id "
            "WHERE v.id=?", (visit_id,)).fetchone()
        if visit is None:
            raise DomainError("凭证不存在")
        pay = self.conn.execute(
            "SELECT * FROM payments WHERE visit_id=? ORDER BY id LIMIT 1",
            (visit_id,)).fetchone()
        review = self.conn.execute(
            "SELECT * FROM exception_reviews WHERE visit_id=? AND status='approved'",
            (visit_id,)).fetchone()
        bd = None
        if visit["settle_at"]:
            inv = self.conn.execute(
                "SELECT * FROM invitations WHERE id=?",
                (visit["inv_id"],)).fetchone()
            bd = self._breakdown_for(visit, inv,
                                     clock.parse_iso(visit["settle_at"]))
        return {"visit": visit, "payment": pay, "review": review,
                "breakdown": bd}


def early_note(warnings):
    return any("提前到达" in w for w in (warnings or []))
