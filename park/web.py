"""Web 层：标准库 http.server，无第三方依赖。

角色通过 cookie ``role`` 切换（门岗 / 被访单位 A / 被访单位 B / 园区管理员），
用于演示单位隔离与复核权限。
"""
from __future__ import annotations

import html
import os
import sqlite3
import threading
import urllib.parse
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import billing, clock
from .adapters import mask_plate
from .db import connect, init_db
from .service import DomainError, Service

GREEN = "#2e7d4f"
GREEN_DARK = "#236b41"
GREEN_SOFT = "#e8f3ec"
GRAY = "#3a3f3d"
GRAY_SOFT = "#f5f6f5"
LINE = "#dfe3e0"
RED = "#b3402f"
RED_SOFT = "#fbeee9"
AMBER = "#9a6a00"
AMBER_SOFT = "#fdf4df"

ROLES = {
    "guard": "门岗",
    "unit:A": "A 单位",
    "unit:B": "B 单位",
    "admin": "园区管理员",
}

_lock = threading.RLock()


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def qs(url: str, **params) -> str:
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None})


# --------------------------------------------------------------------------- 样式

CSS = f"""
* {{ box-sizing: border-box; }}
body {{ margin:0; background:#fff; color:{GRAY};
  font:15px/1.6 "PingFang SC","Microsoft YaHei",system-ui,sans-serif; }}
a {{ color:{GREEN_DARK}; text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
header.top {{ background:#fff; border-bottom:2px solid {GREEN};
  padding:14px 28px; display:flex; align-items:center; gap:24px; flex-wrap:wrap; }}
header.top .brand {{ font-size:18px; font-weight:700; color:{GREEN_DARK}; }}
header.top nav {{ display:flex; gap:18px; flex-wrap:wrap; }}
header.top nav a {{ color:{GRAY}; }}
header.top .roles {{ margin-left:auto; display:flex; gap:6px; align-items:center;
  font-size:13px; }}
.rolchip {{ padding:3px 10px; border:1px solid {LINE}; border-radius:99px;
  color:{GRAY}; background:#fff; }}
.rolchip.on {{ background:{GREEN}; border-color:{GREEN}; color:#fff; }}
main {{ max-width:1080px; margin:26px auto 60px; padding:0 24px; }}
h1 {{ font-size:21px; margin:0 0 18px; }}
h2 {{ font-size:16px; margin:26px 0 10px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
  gap:14px; margin-bottom:22px; }}
.card {{ border:1px solid {LINE}; border-radius:10px; padding:16px 18px;
  background:#fff; }}
.card .n {{ font-size:30px; font-weight:700; color:{GREEN_DARK}; }}
.card .l {{ font-size:13px; color:#6b726e; }}
.panel {{ border:1px solid {LINE}; border-radius:10px; padding:20px 22px;
  margin-bottom:20px; }}
.panel.green {{ border-color:{GREEN}; background:{GREEN_SOFT}; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th,td {{ text-align:left; padding:9px 10px; border-bottom:1px solid {LINE};
  vertical-align:top; }}
th {{ background:{GRAY_SOFT}; font-weight:600; white-space:nowrap; }}
tr:last-child td {{ border-bottom:none; }}
.badge {{ display:inline-block; padding:1px 9px; border-radius:99px;
  font-size:12px; }}
.badge.ok {{ background:{GREEN_SOFT}; color:{GREEN_DARK}; }}
.badge.warn {{ background:{AMBER_SOFT}; color:{AMBER}; }}
.badge.bad {{ background:#fbeae7; color:{RED}; }}
.badge.gray {{ background:{GRAY_SOFT}; color:#6b726e; }}
.plate {{ font-size:30px; font-weight:700; letter-spacing:2px; color:{GRAY};
  font-family:ui-monospace,"Menlo",monospace; }}
.window {{ font-size:17px; font-weight:600; color:{GREEN_DARK}; }}
.btn {{ display:inline-block; border:1px solid {GREEN}; background:{GREEN};
  color:#fff; padding:7px 16px; border-radius:7px; cursor:pointer;
  font-size:14px; }}
.btn:hover {{ background:{GREEN_DARK}; text-decoration:none; }}
.btn.secondary {{ background:#fff; color:{GREEN_DARK}; }}
.btn.ghost {{ background:#fff; color:{GRAY}; border-color:{LINE}; }}
.btn.danger {{ background:#fff; color:{RED}; border-color:#e2b7af; }}
.btn.small {{ padding:3px 10px; font-size:13px; }}
form.inline {{ display:inline; }}
label {{ display:block; font-size:13px; color:#555c58; margin:10px 0 3px; }}
input[type=text],input[type=datetime-local],select,textarea {{
  width:100%; padding:8px 10px; border:1px solid #c7ccc8; border-radius:7px;
  font:inherit; background:#fff; }}
textarea {{ min-height:64px; }}
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:0 18px; }}
.flash {{ border:1px solid {GREEN}; background:{GREEN_SOFT}; color:{GREEN_DARK};
  padding:11px 16px; border-radius:8px; margin-bottom:16px; }}
.flash.err {{ border-color:#e2b7af; background:#fbeae7; color:{RED}; }}
.reason {{ }}
.muted {{ color:#8a918d; font-size:13px; }}
.fee-big {{ font-size:34px; font-weight:700; color:{RED}; }}
.feerow {{ display:flex; justify-content:space-between; padding:8px 0;
  border-bottom:1px dashed {LINE}; }}
.feerow:last-child {{ border-bottom:none; }}
.feerow.free span:last-child {{ color:{GREEN_DARK}; }}
.feerow.total {{ font-weight:700; font-size:16px; border-bottom:none; }}
.gate-dot {{ display:inline-block; width:9px; height:9px; border-radius:50%;
  margin-right:6px; }}
.gate-dot.on {{ background:{GREEN}; }} .gate-dot.off {{ background:{RED}; }}
.photo {{ border:1px solid {LINE}; border-radius:8px; max-width:300px; }}
.two {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
@media(max-width:760px){{ .two,.grid2{{grid-template-columns:1fr}} }}
.receipt {{ max-width:560px; margin:0 auto; }}
.stamp {{ color:{GREEN_DARK}; border:2px solid {GREEN}; border-radius:8px;
  display:inline-block; padding:4px 14px; transform:rotate(-4deg);
  font-weight:700; }}
"""


def layout(title: str, body: str, role: str, flash: str = "",
           flash_kind: str = "ok") -> bytes:
    nav = [("/", "总览"), ("/invitations", "邀请交付"), ("/guard", "门岗核验"),
           ("/parked", "在园车辆"), ("/charge", "出场收费"),
           ("/exceptions", "异常复核"), ("/payments", "收费流水"),
           ("/events", "处置记录")]
    chips = "".join(
        f'<a class="rolchip {"on" if role == k else ""}" '
        f'href="/role?r={urllib.parse.quote(k)}">{esc(v)}</a>'
        for k, v in ROLES.items())
    flash_html = (f'<div class="flash {"err" if flash_kind == "err" else ""}">'
                  f'{esc(flash)}</div>' if flash else "")
    page = f"""<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · 园区访客车辆通行</title><style>{CSS}</style></head><body>
<header class="top">
  <span class="brand">🅿 园区访客车辆临时通行</span>
  <nav>{''.join(f'<a href="{u}">{esc(t)}</a>' for u, t in nav)}</nav>
  <span class="roles">当前身份：{chips}</span>
</header>
<main><h1>{esc(title)}</h1>{flash_html}{body}</main></body></html>"""
    return page.encode("utf-8")


def badge(result: str) -> str:
    m = {"ok": ("ok", "通过/成功"), "denied": ("bad", "拒绝"),
         "warning": ("warn", "警示"), "pending": ("warn", "待处理"),
         "early": ("warn", "提前到达"), "offline": ("bad", "门禁离线")}
    cls, txt = m.get(result, ("gray", result))
    return f'<span class="badge {cls}">{esc(txt)}</span>'


STATUS_TEXT = {"active": "有效", "entered": "已入场", "exited": "已出场",
               "revoked": "已撤销", "exception": "异常放行"}
STATUS_BADGE = {"active": "ok", "entered": "warn", "exited": "gray",
                "revoked": "bad", "exception": "ok"}


# --------------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "ParkGate/1.0"
    svc: Service = None          # 由 make_server 注入
    photo_dir: str = "data/photos"

    def log_message(self, fmt, *args):
        pass

    # -- 框架 --
    def _cookies(self) -> SimpleCookie:
        c = SimpleCookie()
        if self.headers.get("Cookie"):
            c.load(self.headers["Cookie"])
        return c

    @property
    def role(self) -> str:
        c = self._cookies()
        return c["role"].value if "role" in c else "guard"

    def _send(self, content: bytes, status=200, ctype="text/html; charset=utf-8",
              extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(content)

    def _html(self, title, body, flash="", flash_kind="ok", status=200):
        self._send(layout(title, body, self.role, flash, flash_kind), status)

    def _redirect(self, path):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", path)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _form(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        return {k: v[0] for k, v in
                urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

    def _require(self, *roles) -> bool:
        if self.role not in roles:
            allowed = "、".join(ROLES[r] for r in roles)
            self._html("无权操作",
                       f'<div class="flash err">该操作仅 {esc(allowed)} 可执行。'
                       f'请在右上角切换身份。</div>', status=403)
            return False
        return True

    def _flash_redirect(self, path, msg, kind="ok"):
        self._redirect(qs(path, msg=msg, kind=kind))

    def do_GET(self):
        with _lock:
            try:
                self._route_get()
            except DomainError as e:
                self._html("操作未完成",
                           f'<div class="flash err">{esc(str(e))}</div>'
                           '<p><a class="btn secondary" href="javascript:history.back()">'
                           '返回上一页</a></p>', flash_kind="err", status=400)
            except (ValueError, KeyError) as e:
                self._html("请求参数有误",
                           f'<div class="flash err">{esc(str(e))}</div>'
                           '<p><a class="btn secondary" href="/">返回总览</a></p>',
                           flash_kind="err", status=400)

    def do_POST(self):
        with _lock:
            try:
                self._route_post()
            except DomainError as e:
                self._html("操作未完成",
                           f'<div class="flash err">{esc(str(e))}</div>'
                           '<p><a class="btn secondary" href="javascript:history.back()">'
                           '返回上一页</a></p>', flash_kind="err", status=400)
            except (ValueError, KeyError) as e:
                self._html("请求参数有误",
                           f'<div class="flash err">{esc(str(e))}</div>'
                           '<p><a class="btn secondary" href="/">返回总览</a></p>',
                           flash_kind="err", status=400)

    # -- GET 路由 --
    def _route_get(self):
        p = urllib.parse.urlparse(self.path)
        path, q = p.path, urllib.parse.parse_qs(p.query)
        flash = q.get("msg", [""])[0]
        fkind = q.get("kind", ["ok"])[0]

        if path == "/":
            return self.page_home(flash, fkind)
        if path == "/role":
            r = q.get("r", ["guard"])[0]
            self.send_response(302)
            self.send_header("Set-Cookie", f"role={r}; Path=/")
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            return self.end_headers()
        if path == "/invitations":
            return self.page_invitations(flash, fkind)
        if path == "/guard":
            return self.page_guard(flash, fkind)
        if path == "/parked":
            return self.page_parked(flash, fkind)
        if path == "/charge":
            return self.page_charge(q, flash, fkind)
        if path == "/exceptions":
            return self.page_exceptions(flash, fkind)
        if path == "/payments":
            return self.page_payments(flash, fkind)
        if path == "/events":
            return self.page_events(q)
        if path.startswith("/receipt"):
            return self.page_receipt(q)
        if path.startswith("/photos/"):
            return self.serve_photo(
                urllib.parse.unquote(os.path.basename(path)))
        self._html("页面不存在", '<div class="flash err">404</div>', status=404)

    # -- POST 路由 --
    def _route_post(self):
        path = urllib.parse.urlparse(self.path).path
        f = self._form()

        if path == "/invite/create":
            return self.create_invitation(f)
        if path == "/invite/revoke":
            return self.revoke_invitation(f)
        if path == "/guard/verify":
            return self.verify(f)
        if path == "/guard/confirm-early":
            return self.verify(f, confirm=True)
        if path == "/gate/offline":
            self.svc.set_gate_online(False, ROLES[self.role])
            return self._flash_redirect("/guard", "已模拟门禁离线：此后的核验进入本地离线队列")
        if path == "/gate/online":
            self.svc.set_gate_online(True, ROLES[self.role])
            return self._flash_redirect("/guard", "门禁已恢复在线")
        if path == "/gate/replay":
            res = self.svc.replay_pending(ROLES[self.role])
            if not res:
                return self._flash_redirect("/guard", "离线队列为空，没有待重放记录")
            lines = "；".join(f"#{x['queue_id']}：{x['reason']}" for x in res)
            return self._flash_redirect("/guard", f"离线重放完成（{len(res)} 条）：{lines}")
        if path == "/gate/inject-fail":
            self.svc.gate.inject_failures(1)
            return self._flash_redirect("/guard",
                                        "已注入一次道闸卡阻故障：下一次开闸将失败，可演示支付后闸机未开")
        if path == "/pay/inject-fail":
            self.svc.payment.inject_failure(1)
            return self._flash_redirect("/charge",
                                        "已注入一次支付渠道失败：下一次支付将返回失败（不扣款）",
                                        "err")
        if path == "/pay/pay":
            return self.do_pay(f)
        if path == "/gate/retry":
            r = self.svc.retry_exit_gate(int(f["visit_id"]), ROLES[self.role])
            return self._flash_redirect(f"/receipt?id={f['visit_id']}",
                                        f"重试成功：{r['reason']}，费用未重复收取")
        if path == "/visit/exception":
            return self.request_exception(f)
        if path == "/review":
            return self.review(f)
        self._html("页面不存在", '<div class="flash err">404</div>', status=404)

    # --------------------------------------------------------------- 页面：总览

    def page_home(self, flash, kind):
        conn = self.svc.conn
        n_open = conn.execute(
            "SELECT COUNT(*) c FROM visits WHERE actual_exit IS NULL").fetchone()["c"]
        n_pend = conn.execute(
            "SELECT COUNT(*) c FROM exception_reviews WHERE status='pending'"
        ).fetchone()["c"]
        n_queue = conn.execute(
            "SELECT COUNT(*) c FROM offline_queue WHERE result='pending'"
        ).fetchone()["c"]
        paid = conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM payments WHERE status='success'"
        ).fetchone()["s"]
        online = self.svc.gate.is_online()
        dot = ('<span class="gate-dot on"></span>在线' if online
               else '<span class="gate-dot off"></span>离线')
        gate_failed = conn.execute(
            "SELECT COUNT(*) c FROM gate_commands WHERE status='failed'"
        ).fetchone()["c"]
        body = f"""
        <div class="cards">
          <div class="card"><div class="n">{n_open}</div><div class="l">在园访客车辆</div></div>
          <div class="card"><div class="n">{n_pend}</div><div class="l">异常放行待复核</div></div>
          <div class="card"><div class="n">{n_queue}</div><div class="l">离线待重放</div></div>
          <div class="card"><div class="n">¥{paid:.2f}</div><div class="l">超时费收入</div></div>
        </div>
        <div class="panel">
          <p>门禁控制器状态：<b>{dot}</b>
             {'（有 ' + str(gate_failed) + ' 条卡阻指令待重试）' if gate_failed else ''}</p>
          <p class="muted">业务流程：被访单位在「邀请交付」申请 → 访客到场，门岗在
          「门岗核验」核车牌与批准时段 → 在「在园车辆」查看剩余时间 →
          离场在「出场收费」按批准时段结算 → 车主取得电子凭证。
          所有异常情形都在「处置记录」中留有完整文字记录。</p>
          <p>
            <a class="btn" href="/guard">去门岗核验</a>
            <a class="btn secondary" href="/invitations">申请临时通行</a>
            <a class="btn ghost" href="/events">查看处置记录</a>
          </p>
        </div>
        <div class="panel">
          <h2 style="margin-top:0">模拟外设控制（演示用）</h2>
          <form class="inline" method="post" action="/gate/offline">
            <button class="btn ghost small">模拟门禁离线</button></form>
          <form class="inline" method="post" action="/gate/online">
            <button class="btn ghost small">恢复门禁在线</button></form>
          <form class="inline" method="post" action="/gate/inject-fail">
            <button class="btn ghost small">注入道闸卡阻</button></form>
          <form class="inline" method="post" action="/pay/inject-fail">
            <button class="btn ghost small">注入支付失败</button></form>
          <p class="muted">建议演示路径：先「模拟门禁离线」并核验一辆车 → 恢复在线后
          在门岗页点「重放离线队列」→ 再用「注入道闸卡阻」演示支付后闸机未开与重试。</p>
        </div>"""
        self._html("总览", body, flash, kind)

    # ------------------------------------------------------------ 页面：邀请交付

    def page_invitations(self, flash, kind):
        unit = self.role.split(":", 1)[1] if self.role.startswith("unit:") else None
        is_unit = unit is not None
        rows = self.svc.list_invitations(unit if is_unit else None)
        units = self.svc.conn.execute("SELECT * FROM units ORDER BY code").fetchall()
        opts = "".join(f'<option value="{esc(u["code"])}">{esc(u["code"])} · {esc(u["name"])}</option>'
                       for u in units)

        if is_unit:
            form = f"""
            <div class="panel green">
              <h2 style="margin-top:0">为访客申请临时通行（{esc(unit)} 单位）</h2>
              <form method="post" action="/invite/create">
                <input type="hidden" name="unit_code" value="{esc(unit)}">
                <div class="grid2">
                  <div><label>车牌（如 京A·12345）</label>
                    <input type="text" name="plate" required placeholder="京A·12345"></div>
                  <div><label>访问楼栋</label>
                    <input type="text" name="building" required placeholder="A栋301"></div>
                  <div><label>联系人</label>
                    <input type="text" name="contact" required placeholder="王工 138****0000"></div>
                  <div><label>申请人（本单位经办人）</label>
                    <input type="text" name="created_by" required value="{esc(unit)}单位经办人"></div>
                  <div><label>预计到达时间</label>
                    <input type="datetime-local" name="enter_at" required></div>
                  <div><label>预计离场时间</label>
                    <input type="datetime-local" name="leave_at" required></div>
                </div>
                <p><button class="btn">交付邀请并生成一次性通行码</button></p>
              </form>
            </div>"""
        elif self.role == "admin":
            form = f"""
            <div class="panel">
              <h2 style="margin-top:0">代被访单位申请（园区管理员视角）</h2>
              <form method="post" action="/invite/create">
                <div class="grid2">
                  <div><label>被访单位</label><select name="unit_code">{opts}</select></div>
                  <div><label>车牌</label><input type="text" name="plate" required></div>
                  <div><label>访问楼栋</label><input type="text" name="building" required></div>
                  <div><label>联系人</label><input type="text" name="contact" required></div>
                  <div><label>预计到达时间</label>
                    <input type="datetime-local" name="enter_at" required></div>
                  <div><label>预计离场时间</label>
                    <input type="datetime-local" name="leave_at" required></div>
                </div>
                <input type="hidden" name="created_by" value="园区管理员">
                <p><button class="btn">交付邀请</button></p>
              </form>
            </div>"""
        else:
            form = ('<div class="flash err">门岗身份不能申请邀请，'
                    '请在右上角切换为被访单位（A 单位 / B 单位）。</div>')

        trs = []
        for r in rows:
            revoke = ""
            if r["status"] == "active" and (is_unit or self.role == "admin"):
                revoke = f'''<form class="inline" method="post" action="/invite/revoke">
                  <input type="hidden" name="inv_id" value="{r["id"]}">
                  <input type="hidden" name="actor_unit" value="{unit or ""}">
                  <input type="hidden" name="reason" value="">
                  <button class="btn danger small"
                    onclick="var t=prompt('请填写撤销的完整理由');if(!t)return false;this.form.reason.value=t;return true">撤销</button></form>'''
            sb = STATUS_BADGE.get(r["status"], "gray")
            trs.append(f"""<tr>
              <td><b>{esc(r['code'])}</b><div class="muted">一次性通行码</div></td>
              <td>{esc(r['plate'])}</td>
              <td>{esc(r['unit_code'])} 单位</td>
              <td>{esc(r['building'])}<div class="muted">{esc(r['contact'])}</div></td>
              <td>{esc(r['enter_at'][5:16])}<br>～ {esc(r['leave_at'][5:16])}</td>
              <td><span class="badge {sb}">{esc(STATUS_TEXT.get(r['status'], r['status']))}</span>{('<div class="muted">理由：' + esc(r['revoke_reason'])) if r['revoke_reason'] else ""}</td>
              <td>{revoke}</td></tr>""")
        scope = f"（仅 {unit} 单位）" if is_unit else ""
        body = f"""{form}
        <h2>邀请列表{scope}</h2>
        <div class="panel" style="padding:6px 10px">
        <table><thead><tr><th>通行码</th><th>车牌</th><th>被访单位</th>
        <th>楼栋/联系人</th><th>批准时段</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{''.join(trs) or '<tr><td colspan=7 class="muted">暂无邀请</td></tr>'}</tbody>
        </table></div>"""
        self._html("邀请交付", body, flash, kind)

    def create_invitation(self, f):
        unit = self.role.split(":", 1)[1] if self.role.startswith("unit:") else None
        if self.role == "admin":
            unit = f.get("unit_code", "")
        elif not unit:
            raise DomainError("仅被访单位可以申请邀请")
        elif f.get("unit_code") and f["unit_code"] != unit:
            raise DomainError("不能替其他单位申请邀请")
        inv = self.svc.create_invitation(
            plate=f["plate"], unit_code=unit, building=f["building"],
            contact=f["contact"], enter_at=f["enter_at"],
            leave_at=f["leave_at"], created_by=f.get("created_by") or ROLES[self.role])
        self._flash_redirect(
            "/invitations",
            f"邀请已交付：通行码 {inv['code']}（车牌 {inv['plate']}），"
            f"批准时段 {inv['enter_at'][5:16]} 至 {inv['leave_at'][5:16]}。"
            "该码仅可入场一次，转发他人将因车牌不符被拒绝。")

    def revoke_invitation(self, f):
        actor = self.role.split(":", 1)[1] if self.role.startswith("unit:") else None
        if self.role not in ("admin",) and not actor:
            raise DomainError("当前身份无权撤销邀请")
        by = ROLES[self.role]
        self.svc.revoke_invitation(int(f["inv_id"]), actor, f.get("reason", ""), by)
        self._flash_redirect("/invitations", "邀请已撤销，通行码即刻失效")

    # ------------------------------------------------------------ 页面：门岗核验

    def page_guard(self, flash, kind):
        if not self._require("guard", "admin"):
            return
        online = self.svc.gate.is_online()
        dot = ('<span class="gate-dot on"></span>在线' if online
               else '<span class="gate-dot off"></span><b>离线（核验将进入本地队列）</b>')
        queue = self.svc.pending_queue()
        qrows = "".join(
            f"<li>#{q['id']} 入队 {esc(q['queued_at'][5:16])}</li>"
            for q in queue) or "<li class='muted'>无待重放记录</li>"
        recent = self.svc.conn.execute(
            "SELECT * FROM events WHERE kind IN ('verify','gate_enter',"
            "'offline_queue','replay') ORDER BY id DESC LIMIT 8").fetchall()
        rr = "".join(
            f'<tr><td>{esc(e["created_at"][5:19])}</td><td>{badge(e["result"])}</td>'
            f'<td>{esc(e["plate"] or "—")}</td><td>{esc(e["reason"])}</td></tr>'
            for e in recent)
        if online:
            replay_btn = (f'<form class="inline" method="post" action="/gate/replay">'
                          f'<button class="btn">重放离线队列（{len(queue)} 条）</button></form>'
                          if queue else '<span class="muted">队列已清空</span>')
        else:
            replay_btn = ('<span class="muted">门禁离线中：先点上方「恢复在线」，'
                          '再按入队顺序重放</span>')
        body = f"""
        <div class="panel">
          <p style="margin-top:0">门禁控制器：{dot}
             <form class="inline" method="post" action="/gate/offline">
               <button class="btn ghost small">模拟离线</button></form>
             <form class="inline" method="post" action="/gate/online">
               <button class="btn ghost small">恢复在线</button></form>
          </p>
          <form method="post" action="/guard/verify">
            <div class="grid2">
              <div><label>访客通行码</label>
                <input type="text" name="code" required autofocus
                  style="font-size:18px;letter-spacing:2px" placeholder="VXXXXXXXX"></div>
              <div><label>到场车牌（门岗核对）</label>
                <input type="text" name="plate" required style="font-size:18px"
                  placeholder="京A·12345"></div>
            </div>
            <p><button class="btn" style="font-size:16px;padding:10px 26px">核 验</button>
               <span class="muted">车牌不符、码已使用、已撤销均会拒绝并留处置记录</span></p>
          </form>
        </div>
        <div class="two">
          <div class="panel">
            <h2 style="margin-top:0">离线队列</h2>
            <ul>{qrows}</ul>
            <p>{replay_btn}</p>
            <p class="muted">恢复在线后按入队顺序重放，入场时间以车辆实际到场时间为准；
            同一通行码重复出示不会重复排队。</p>
          </div>
          <div class="panel">
            <h2 style="margin-top:0">最近核验处置</h2>
            <table><tbody>{rr or '<tr><td class=muted>暂无</td></tr>'}</tbody></table>
          </div>
        </div>"""
        self._html("门岗核验", body, flash, kind)

    def verify(self, f, confirm=False):
        if not self._require("guard", "admin"):
            return
        r = self.svc.verify(f["code"], f["plate"],
                            operator=ROLES[self.role],
                            confirm_early=confirm)
        inv = r.get("inv")
        inv_box = ""
        if inv is not None:
            color_cls = "green" if r["verdict"] == "ok" else ""
            photo_row = ""
            last_ev = self.svc.conn.execute(
                "SELECT photo_path FROM events WHERE inv_id=? "
                "AND photo_path<>'' ORDER BY id DESC LIMIT 1",
                (inv["id"],)).fetchone()
            if last_ev and last_ev["photo_path"]:
                photo_row = (f'<div><label>门岗抓拍（车牌中段遮罩）</label>'
                             f'<img class="photo" src="/photos/{esc(last_ev["photo_path"])}"></div>')
            early_btn = ""
            if r["verdict"] == "early":
                early_btn = f"""<form method="post" action="/guard/confirm-early">
                  <input type="hidden" name="code" value="{esc(f['code'])}">
                  <input type="hidden" name="plate" value="{esc(f['plate'])}">
                  <button class="btn">已与联系人 {esc(inv['contact'])} 确认，提前放行</button>
                </form>"""
            inv_box = f"""
            <div class="panel {color_cls}">
              <div class="two">
                <div>
                  <div class="muted">登记车牌</div>
                  <div class="plate">{esc(inv['plate'])}</div>
                  <div class="muted" style="margin-top:10px">批准时段</div>
                  <div class="window">{esc(inv['enter_at'])}<br>至 {esc(inv['leave_at'])}</div>
                  <p class="muted" style="margin-top:10px">{esc(inv['unit_code'])} 单位 ·
                    {esc(inv['building'])} · 联系人 {esc(inv['contact'])}</p>
                </div>
                {photo_row}
              </div>
            </div>"""
        more = ""
        if r["verdict"] == "ok":
            more = '<p><a class="btn secondary" href="/guard">继续核验下一辆</a></p>'
        elif r["verdict"] == "offline":
            more = ('<p><a class="btn secondary" href="/guard">返回门岗</a> '
                    '恢复在线后在此页点「重放离线队列」</p>')
        cls = {"ok": "ok", "early": "warn"}.get(r["verdict"], "err")
        body = (f'<div class="flash {"err" if cls == "err" else ""}">'
                f'{badge(r["verdict"])} {esc(r["reason"])}</div>'
                f'{inv_box}{early_btn}{more}')
        self._html("核验结果", body)

    # ------------------------------------------------------------ 页面：在园车辆

    def page_parked(self, flash, kind):
        if not self._require("guard", "admin"):
            return
        rows = self.svc.open_visits()
        trs = []
        for r in rows:
            rem = r["remaining_seconds"]
            cls = "badge ok" if rem > 900 else ("badge warn" if rem > 0 else "badge bad")
            txt = clock.remaining_text(rem)
            actions = []
            actions.append(f'<a class="btn small" href="/charge?visit_id={r["id"]}">结算收费</a>')
            actions.append(f'''<form class="inline" method="post" action="/visit/exception">
              <input type="hidden" name="visit_id" value="{r["id"]}">
              <button class="btn ghost small"
                onclick="this.form.reason.value=prompt('请填写异常免费放行的完整理由（将提交园区管理员复核）')||'';return !!this.form.reason.value">申请异常放行</button>
              <input type="hidden" name="reason" value=""></form>''')
            last = r["last_gate"]
            if last and last["status"] in ("failed", "offline") and r["settle_at"] is not None:
                actions.append(f'''<form class="inline" method="post" action="/gate/retry">
                  <input type="hidden" name="visit_id" value="{r["id"]}">
                  <button class="btn secondary small">重试开闸</button></form>''')
            note = ""
            if r["review"] and r["review"]["status"] == "pending":
                note = '<div class="badge warn">异常放行待管理员复核</div>'
            elif r["review"] and r["review"]["status"] == "approved":
                note = '<div class="badge ok">已批准免费放行</div>'
            if r["settle_at"]:
                note += f'<div class="muted">已结算 ¥{r["amount"]:.2f}，待出场</div>'
            trs.append(f"""<tr>
              <td><b>{esc(r['plate'])}</b><div class="muted">{esc(r['code'])}</div></td>
              <td>{esc(r['unit_code'])} 单位<br>{esc(r['building'])}</td>
              <td>{esc(r['actual_enter'][5:16])}</td>
              <td>{esc(r['approved_enter'][5:16])}～{esc(r['approved_leave'][5:16])}</td>
              <td><span class="{cls}">{esc(txt)}</span></td>
              <td>{note}</td>
              <td>{' '.join(actions)}</td></tr>""")
        body = f"""
        <table><thead><tr><th>车牌/通行码</th><th>单位/楼栋</th><th>实际入场</th>
        <th>批准时段</th><th>剩余时间</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{''.join(trs) or '<tr><td colspan=7 class=muted>当前没有在园车辆</td></tr>'}</tbody>
        </table>"""
        self._html("在园车辆", body, flash, kind)

    def request_exception(self, f):
        if not self._require("guard", "admin"):
            return
        self.svc.request_exception(int(f["visit_id"]), f.get("reason", ""),
                                   ROLES[self.role])
        self._flash_redirect("/parked",
                            "异常免费放行申请已提交，等待园区管理员复核后才能放行")

    # ------------------------------------------------------------ 页面：出场收费

    def page_charge(self, q, flash, kind):
        if not self._require("guard", "admin"):
            return
        visit_id = q.get("visit_id", [""])[0]
        code = q.get("code", [""])[0]
        body = f"""
        <div class="panel">
          <form method="get" action="/charge">
            <label>输入访客通行码查询应缴费用</label>
            <div style="display:flex;gap:10px">
              <input type="text" name="code" value="{esc(code)}" placeholder="VXXXXXXXX"
                style="flex:1">
              <button class="btn">查询</button>
            </div>
          </form>
        </div>"""
        if not visit_id and not code:
            return self._html("出场收费", body, flash, kind)
        try:
            visit = (self.svc.conn.execute(
                "SELECT * FROM visits WHERE id=?", (int(visit_id),)).fetchone()
                     if visit_id else self.svc.get_visit_by_code(code))
        except (ValueError, DomainError) as e:
            return self._html("出场收费", body, str(e), "err", status=400)
        if visit is None:
            return self._html("出场收费", body, "找不到入场记录", "err", status=400)

        s = self.svc.settle(visit)
        visit, bd = s["visit"], s["breakdown"]
        inv = self.svc.get_invitation(visit["inv_id"])
        pay = self.svc.conn.execute(
            "SELECT * FROM payments WHERE visit_id=? ORDER BY id LIMIT 1",
            (visit["id"],)).fetchone()
        review = self.svc.conn.execute(
            "SELECT * FROM exception_reviews WHERE visit_id=? ORDER BY id DESC LIMIT 1",
            (visit["id"],)).fetchone()
        exempt = review is not None and review["status"] == "approved"
        overnight = clock.parse_iso(visit["settle_at"]).date() > \
            clock.parse_iso(inv["leave_at"]).date()

        head = f"""
        <div class="panel">
          <div class="plate">{esc(visit['plate'])}</div>
          <p>{esc(inv['unit_code'])} 单位 · {esc(inv['building'])} ·
             联系人 {esc(inv['contact'])}<br>
             实际入场 {esc(visit['actual_enter'])}
             批准离场 {esc(inv['leave_at'])}
             结算时间 {esc(visit['settle_at'])}
             {'<span class="badge warn">跨夜停留：按连续时长累计</span>' if overnight else ''}
          </p>
        </div>"""

        rows_fee = f"""
          <div class="feerow free"><span>批准时段（免费）</span>
            <span>{bd.approved_minutes} 分钟 · ¥0.00</span></div>
          <div class="feerow free"><span>离场宽限（免费）</span>
            <span>{bd.grace_minutes} 分钟 · ¥0.00</span></div>
          <div class="feerow"><span>免费时间合计</span>
            <span>{bd.free_minutes} 分钟 · ¥0.00</span></div>
          <div class="feerow"><span>超时停留</span>
            <span>{bd.overtime_minutes} 分钟（{bd.billed_units} 个计费单位）</span></div>
          <div class="feerow"><span>超时单价</span>
            <span>¥{billing.RATE_PER_HOUR:.2f}/小时，不足 1 小时按 1 小时</span></div>
          {'<div class="feerow"><span>24 小时连续封顶</span><span>已按 ¥' + f'{billing.DAILY_CAP:.2f}' + '/24小时封顶</span></div>' if bd.capped else ''}
          <div class="feerow total"><span>超时金额合计</span>
            <span class="fee-big">¥{bd.amount:.2f}</span></div>"""
        panel = f"""
        <div class="panel">
          <h2 style="margin-top:0">费用明细（免费时间与超时金额分列）</h2>
          {rows_fee}
        </div>"""

        action = ""
        if exempt:
            action = ('<div class="flash">园区管理员已批准异常免费放行，'
                      '应缴 ¥0.00，等待/重试开闸即可出场。</div>')
        elif pay is not None:
            action = (f'<div class="flash">费用已结清：交易号 {esc(pay["trade_no"])}。'
                      f'重复提交出场回执不会重复扣款。 '
                      f'<a class="btn secondary small" href="/receipt?id={visit["id"]}">查看电子凭证</a></div>')
        else:
            action = f"""<div class="panel">
              <form method="post" action="/pay/pay">
                <input type="hidden" name="visit_id" value="{visit['id']}">
                <button class="btn" style="font-size:16px">
                  模拟支付 ¥{bd.amount:.2f} 并开闸出场</button>
                <span class="muted">支付渠道以业务单号幂等，可放心重试</span>
              </form></div>"""
        body += head + panel + action
        self._html("出场收费", body, flash, kind)

    def do_pay(self, f):
        if not self._require("guard", "admin"):
            return
        vid = int(f["visit_id"])
        r = self.svc.pay(vid, ROLES[self.role])
        if r["gate"] == "opened":
            return self._redirect(f"/receipt?id={vid}")
        # 卡阻/离线：留在收费页给出重试入口
        extra = ""
        if r["gate"] == "failed":
            extra = f"""
            <div class="flash err">{esc(r['reason'])}</div>
            <form method="post" action="/gate/retry">
              <input type="hidden" name="visit_id" value="{vid}">
              <button class="btn">重试开闸（不重新计费）</button>
              <a class="btn secondary" href="/charge?visit_id={vid}">返回收费页</a>
            </form>"""
        else:
            extra = (f'<div class="flash err">{esc(r["reason"])}</div>'
                     f'<a class="btn secondary" href="/parked">去在园车辆页稍后重试</a>')
        self._html("支付完成 · 等待开闸", extra)

    # ------------------------------------------------------------ 异常放行复核

    def page_exceptions(self, flash, kind):
        if not self._require("admin"):
            return
        rows = self.svc.pending_exceptions()
        cards = []
        for r in rows:
            cards.append(f"""
            <div class="panel">
              <div class="plate" style="font-size:24px">{esc(r['plate'])}</div>
              <p>{esc(r['unit_code'])} 单位 · {esc(r['building'])} ·
                 联系人 {esc(r['contact'])}<br>
                 入场 {esc(r['actual_enter'])}
                 申请时间 {esc(r['requested_at'])}　申请人：{esc(r['requested_by'])}</p>
              <div class="flash err" style="background:{AMBER_SOFT};border-color:#e3c98e;color:{AMBER}">
                <b>申请免费放行的完整理由：</b>{esc(r['reason'])}</div>
              <form method="post" action="/review">
                <input type="hidden" name="review_id" value="{r['id']}">
                <label>复核批示（可选）</label>
                <input type="text" name="note" placeholder="如：核实系统故障，同意免收">
                <p>
                  <button name="decision" value="approve" class="btn">批准免费放行并开闸</button>
                  <button name="decision" value="reject" class="btn danger">驳回（需正常缴费）</button>
                </p>
              </form>
            </div>""")
        done = self.svc.conn.execute(
            "SELECT r.*, i.unit_code FROM exception_reviews r "
            "JOIN invitations i ON i.id=r.inv_id "
            "WHERE r.status<>'pending' ORDER BY r.id DESC LIMIT 10").fetchall()
        done_rows = "".join(
            f'<tr><td>{esc(r["plate"])}</td><td>{esc(r["unit_code"])}</td>'
            f'<td>{esc(r["reason"])}</td><td>{badge("ok" if r["status"]=="approved" else "denied")}</td>'
            f'<td>{esc(r["reviewer"])}<div class=muted>{esc(r["review_note"])}</div></td></tr>'
            for r in done)
        body = f"""
        <p class="muted">异常免费放行一律须园区管理员复核；门岗只能提交申请、不能自行放行。</p>
        {''.join(cards) or '<div class="flash">当前没有待复核的申请。</div>'}
        <h2>已处理</h2>
        <div class="panel" style="padding:6px 10px"><table>
        <thead><tr><th>车牌</th><th>单位</th><th>完整理由</th><th>结论</th><th>复核人/批示</th></tr></thead>
        <tbody>{done_rows or '<tr><td colspan=5 class=muted>暂无</td></tr>'}</tbody></table></div>"""
        self._html("异常放行复核", body, flash, kind)

    def review(self, f):
        if not self._require("admin"):
            return
        approve = f["decision"] == "approve"
        r = self.svc.review_exception(int(f["review_id"]), approve,
                                      ROLES[self.role], f.get("note", ""))
        if approve:
            self._flash_redirect("/exceptions",
                                 f"已批准免费放行：{r['reason']}", "ok")
        self._flash_redirect("/exceptions", "已驳回，车辆需正常结算缴费", "err")

    # ------------------------------------------------------------ 收费流水

    def page_payments(self, flash, kind):
        rows = self.svc.payments()
        trs = "".join(f"""<tr>
          <td>{esc(p['created_at'][5:19])}</td>
          <td><b>{esc(p['plate'])}</b><div class=muted>{esc(p['building'])} · {esc(p['contact'])}</div></td>
          <td>{esc(p['trade_no'])}</td>
          <td>¥{p['amount']:.2f}</td>
          <td>{badge('ok' if p['status']=='success' else 'denied')} {esc(p['status'])}</td>
          <td><a href="/receipt?id={p['visit_id']}">凭证</a></td></tr>"""
          for p in rows)
        body = f"""<p class="muted">以业务单号（pay-visit-序号）为幂等键：
        出场回执重试只返回原交易号，不会产生第二笔收费。</p>
        <div class="panel" style="padding:6px 10px"><table>
        <thead><tr><th>时间</th><th>车牌/楼栋</th><th>交易号</th><th>金额</th>
        <th>状态</th><th>凭证</th></tr></thead>
        <tbody>{trs or '<tr><td colspan=6 class=muted>暂无收费记录</td></tr>'}</tbody>
        </table></div>"""
        self._html("收费流水", body, flash, kind)

    # ------------------------------------------------------------ 处置记录

    def page_events(self, q):
        inv_id = q.get("inv", [""])[0]
        rows = self.svc.events(int(inv_id) if inv_id.isdigit() else None,
                               limit=300)
        trs = "".join(f"""<tr>
          <td style="white-space:nowrap">{esc(e['created_at'][5:19])}</td>
          <td>{esc(e['kind'])}</td>
          <td>{badge(e['result'])}</td>
          <td><b>{esc(e['plate'] or '—')}</b></td>
          <td>{esc(e['reason'])}</td>
          <td>{esc(e['operator'])}</td></tr>"""
          for e in rows)
        body = f"""<p class="muted">车牌录错、提前到达、跨夜停留、同车牌多份邀请、
        通行码转发、门禁离线、支付后闸机未开等情形均在此留下完整文字处置记录。</p>
        <div class="panel" style="padding:6px 10px"><table>
        <thead><tr><th>时间</th><th>类型</th><th>结果</th><th>车牌</th>
        <th>处置说明（完整文字）</th><th>操作员</th></tr></thead>
        <tbody>{trs or '<tr><td colspan=6 class=muted>暂无记录</td></tr>'}</tbody>
        </table></div>"""
        self._html("处置记录", body)

    # ------------------------------------------------------------ 电子凭证

    def page_receipt(self, q):
        vid = q.get("id", [""])[0]
        if not vid.isdigit():
            return self._html("电子凭证", '<div class="flash err">缺少凭证编号</div>',
                              status=400)
        r = self.svc.receipt(int(vid))
        v, pay, rv, bd = r["visit"], r["payment"], r["review"], r["breakdown"]
        if v["actual_exit"] is None:
            foot = '<div class="badge warn">车辆尚未完成出场（可能等待开闸重试）</div>'
        elif rv is not None and rv["status"] == "approved":
            foot = '<div class="stamp">异常免费放行 · 已出场</div>'
        else:
            foot = '<div class="stamp">已缴费 · 已出场</div>'
        fee_rows = ""
        if bd is not None:
            fee_rows = f"""
              <div class="feerow free"><span>免费时间（批准 {bd.approved_minutes} 分
                + 宽限 {bd.grace_minutes} 分）</span><span>¥0.00</span></div>
              <div class="feerow"><span>超时 {bd.overtime_minutes} 分钟</span>
                <span>¥{bd.amount:.2f}</span></div>"""
        body = f"""
        <div class="panel receipt">
          <h2 style="text-align:center;margin-top:0">园区访客车辆离场电子凭证</h2>
          <div class="feerow"><span>车牌</span><b>{esc(v['plate'])}</b></div>
          <div class="feerow"><span>被访单位 / 楼栋</span>
            <span>{esc(v['unit_code'])} · {esc(v['building'])}（{esc(v['contact'])}）</span></div>
          <div class="feerow"><span>通行码</span><span>{esc(v['code'])}</span></div>
          <div class="feerow"><span>批准时段</span>
            <span>{esc(v['approved_enter'])} ～ {esc(v['approved_leave'])}</span></div>
          <div class="feerow"><span>实际入场</span><span>{esc(v['actual_enter'])}</span></div>
          <div class="feerow"><span>结算/离场</span>
            <span>{esc(v['settle_at'] or '—')}　{esc(v['actual_exit'] or '')}</span></div>
          {fee_rows}
          <div class="feerow total"><span>实缴金额</span>
            <span class="fee-big" style="font-size:26px">¥{v['amount']:.2f}</span></div>
          <div class="feerow"><span>支付交易号</span>
            <span>{esc(pay['trade_no'] if pay else '（免费放行）')}</span></div>
          <p style="text-align:center;margin-top:16px">{foot}</p>
        </div>"""
        self._html("电子凭证", body)

    def serve_photo(self, name):
        p = Path(self.photo_dir) / name
        if not p.is_file():
            return self._send(b"not found", 404, "text/plain")
        self._send(p.read_bytes(), ctype="image/png",
                   extra_headers=[("Cache-Control", "max-age=3600")])


def make_server(host: str, port: int, db_path: str, photo_dir: str):
    conn = connect(db_path)
    init_db(conn)
    svc = Service(conn, photo_dir)
    Handler.svc = svc
    Handler.photo_dir = photo_dir
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd, svc
