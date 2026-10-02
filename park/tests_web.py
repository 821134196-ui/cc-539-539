"""HTTP 层集成测试：启动真实服务器（临时端口 + 临时库），
重点验证角色权限与单位隔离在 Web 层同样成立。
"""
from __future__ import annotations

import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import unittest
from http.client import RemoteDisconnected
from pathlib import Path
import tempfile

from .db import connect, init_db
from .web import make_server


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def req(method, url, data=None, cookie=None):
    headers = {}
    if cookie:
        headers["Cookie"] = cookie
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = _OPENER.open(r)
        return resp.status, resp.read().decode(), resp.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers.get("Location", "")


class WebCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        cls.httpd, cls.svc = make_server(
            "127.0.0.1", 0, str(cls.dir / "w.db"), str(cls.dir / "photos"))
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.t = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.t.start()
        conn = cls.svc.conn
        conn.executemany("INSERT INTO units(code,name) VALUES(?,?)",
                         [("A", "A栋"), ("B", "B栋")])
        from park import clock
        from datetime import timedelta
        now = clock.now()
        cls.inv_b = cls.svc.create_invitation(
            plate="京B·22222", unit_code="B", building="B栋101", contact="李",
            enter_at=clock.fmt(now),
            leave_at=clock.fmt(now + timedelta(hours=2)), created_by="B")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_guard_cannot_create_invitation(self):
        st, body, _ = req("GET", self.base + "/invitations", cookie="role=guard")
        self.assertIn("门岗身份不能申请邀请", body)

    def test_unit_a_cannot_modify_b_invitation_http(self):
        st, body, loc = req("POST", self.base + "/invite/revoke",
                            {"inv_id": self.inv_b["id"], "actor_unit": "A",
                             "reason": "越权修改"}, cookie="role=unit:A")
        self.assertEqual(st, 400)
        self.assertIn("单位隔离", body)
        # B 的邀请状态不变
        self.assertEqual(
            self.svc.get_invitation(self.inv_b["id"])["status"], "active")

    def test_admin_only_exception_page(self):
        st, _, _ = req("GET", self.base + "/exceptions", cookie="role=unit:B")
        self.assertEqual(st, 403)
        st, _, _ = req("GET", self.base + "/exceptions", cookie="role=admin")
        self.assertEqual(st, 200)

    def test_full_http_flow_dedup(self):
        from park import clock
        from datetime import timedelta
        now = clock.now()
        inv = self.svc.create_invitation(
            plate="京A·33333", unit_code="A", building="A栋201", contact="王",
            enter_at=clock.fmt(now - timedelta(hours=4)),
            leave_at=clock.fmt(now - timedelta(hours=2)), created_by="A")
        self.svc.verify(inv["code"], "京A·33333",
                        at=now - timedelta(hours=3, minutes=50))
        vid = self.svc.conn.execute(
            "SELECT id FROM visits WHERE inv_id=?", (inv["id"],)).fetchone()["id"]
        st, _, loc = req("POST", self.base + "/pay/pay", {"visit_id": vid},
                         cookie="role=guard")
        self.assertEqual(st, 303)
        self.assertIn(f"/receipt?id={vid}", loc)
        # 重复回执
        st2, body2, _ = req("POST", self.base + "/pay/pay", {"visit_id": vid},
                            cookie="role=guard")
        n = self.svc.conn.execute(
            "SELECT COUNT(*) c FROM payments WHERE visit_id=?", (vid,)).fetchone()["c"]
        self.assertEqual(n, 1)
        # 凭证可查
        st, body, _ = req("GET", f"{self.base}/receipt?id={vid}", cookie="role=guard")
        self.assertEqual(st, 200)
        self.assertIn("离场电子凭证", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
