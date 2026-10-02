"""模拟外设适配器：摄像头（车牌遮罩照片）、闸机、支付。

全部本地模拟，不访问任何真实设备或网络。
- 照片用 zlib+struct 直接编码 PNG（无第三方依赖），车牌中段做像素化遮罩；
- 闸机支持离线、卡阻注入与重试，每条指令落库；
- 支付以业务幂等键去重，回执重试返回原交易，绝不重复扣款。
"""
from __future__ import annotations

import json
import sqlite3
import zlib
from datetime import datetime
from pathlib import Path

from . import clock

# ---------------------------------------------------------------------------
# 迷你位图字库（只为在模拟照片上画出车牌字符，不追求字形美观）
# ---------------------------------------------------------------------------

_FONT5x7 = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01111", "10000", "10000", "10000", "10000", "10000", "01111"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01111", "10000", "10000", "10011", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "J": ["00111", "00010", "00010", "00010", "00010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "·": ["000", "000", "010", "000", "010", "000", "000"],
    "*": ["00000", "00000", "10101", "01110", "10101", "00000", "00000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
}

# 省份简称的 8x10 草图（不常见字用回退图案）
_FONT_CN = {
    "京": ["...#....", "..####..", "..####..", ".######.", "..####..",
           "..####..", ".######.", "##....##", ".#....#.", "..####.."],
    "沪": ["##......", "........", ".#......", "###..##.", ".#...#..",
           ".#...##.", ".#...#..", ".#...##.", "#####...", "........"],
    "粤": [".#####..", "#.....#.", "#.###...", "#...#.#.", "#####.#.",
           "#.....#.", "#.###.#.", "#####..#", "......#.", "######.."],
    "苏": ["..#####.", ".#....#.", "####....", "....##..", "#######.",
           "#......#", "#######.", "#......#", "#######.", "........"],
    "浙": ["##...#..", ".#...#..", "..#.#...", "###.###.", "#.#.....",
           "#.#####.", "##....#.", "#.....#.", "#####.#.", "........"],
    "川": ["#....##.", ".#...#..", "..#.#...", "###.#...", "..#.#...",
           ".#..###.", "#....#..", ".....#..", "....##..", "........"],
}
_FALLBACK_CN = ["########", "#......#", "#.####.#", "#.#..#.#", "#.#..#.#",
                "#.####.#", "#......#", "#.####.#", "#......#", "########"]


# ---------------------------------------------------------------------------
# PNG 编码与照片合成
# ---------------------------------------------------------------------------

def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    """rows: 每行 RGB 字节。"""
    raw = b"".join(b"\x00" + r for r in rows)  # filter 0
    comp = zlib.compress(raw, 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = len(data).to_bytes(4, "big") + tag + data
        return c + zlib.crc32(tag + data).to_bytes(4, "big")

    ihdr = (width.to_bytes(4, "big") + height.to_bytes(4, "big")
            + bytes([8, 2, 0, 0, 0]))  # 8-bit RGB
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", comp) + chunk(b"IEND", b""))


class _Canvas:
    def __init__(self, w: int, h: int, bg=(244, 245, 242)):
        self.w, self.h = w, h
        self.px = bytearray(bg) * (w * h)

    def set(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.px[i:i + 3] = bytes(c)

    def rect(self, x0, y0, x1, y1, c):
        for y in range(max(0, y0), min(self.h, y1)):
            for x in range(max(0, x0), min(self.w, x1)):
                self.set(x, y, c)

    def glyph(self, ch, x, y, scale, c):
        if ch in _FONT_CN or ord(ch[0]) > 127:
            pat = _FONT_CN.get(ch, _FALLBACK_CN)
        else:
            pat = _FONT5x7.get(ch.upper())
            if pat is None:
                return
        for ry, row in enumerate(pat):
            for rx, v in enumerate(row):
                if v == "1" or v == "#":
                    self.rect(x + rx * scale, y + ry * scale,
                              x + (rx + 1) * scale - 1,
                              y + (ry + 1) * scale - 1, c)

    def text(self, s, x, y, scale, c, gap=1):
        cx = x
        for ch in s:
            w = (len(_FONT_CN.get(ch, _FALLBACK_CN)[0]) if ord(ch[0]) > 127
                 else len(_FONT5x7.get(ch.upper(), [""])[0]))
            self.glyph(ch, cx, y, scale, c)
            cx += (w + gap) * scale


def mask_plate(plate: str) -> str:
    """车牌遮罩：保留省份字母与最后两位，中间用 * 替代，保留「·」分隔符。"""
    if "·" in plate:
        head, tail = plate.split("·", 1)
        if len(tail) <= 2:
            return head + "·" + "*" * len(tail)
        return head + "·" + "*" * (len(tail) - 2) + tail[-2:]
    if len(plate) <= 4:
        return plate[0] + "*" * (len(plate) - 1)
    return plate[:2] + "*" * (len(plate) - 4) + plate[-2:]


def take_plate_photo(plate: str, out_dir: str | Path,
                     at: datetime | None = None) -> str:
    """模拟门岗抓拍：车尾 + 车牌，车牌中段像素化遮罩。返回相对文件名。"""
    at = at or clock.now()
    W, H = 320, 170
    cv = _Canvas(W, H, (236, 238, 233))
    # 地面
    cv.rect(0, 128, W, H, (214, 217, 208))
    # 车身
    cv.rect(28, 52, 292, 130, (96, 104, 112))
    cv.rect(52, 30, 268, 66, (120, 128, 136))     # 车顶
    cv.rect(62, 36, 122, 62, (190, 214, 214))     # 后窗
    cv.rect(198, 36, 258, 62, (190, 214, 214))
    cv.rect(36, 70, 76, 86, (232, 220, 120))      # 尾灯
    cv.rect(244, 70, 284, 86, (232, 220, 120))
    cv.rect(58, 126, 96, 146, (48, 48, 48))       # 轮胎
    cv.rect(224, 126, 262, 146, (48, 48, 48))
    # 车牌（蓝底白字）；模拟牌面不画「·」分隔点以免文字挤溢牌面
    px0, py0, px1, py1 = 84, 86, 236, 116
    cv.rect(px0, py0, px1, py1, (28, 78, 156))
    masked = mask_plate(plate).replace("·", "")
    scale = 3
    tw = sum((len(_FONT_CN.get(c, _FALLBACK_CN)[0]) if ord(c[0]) > 127
              else len(_FONT5x7.get(c.upper(), [""])[0])) + 1 for c in masked) * scale
    tx = px0 + (px1 - px0 - tw) // 2
    ty = py0 + (py1 - py0 - 7 * scale) // 2
    cv.text(masked, tx, ty, scale, (245, 245, 245), gap=1)
    # 遮罩质感：在 * 位置叠加马赛克块
    cx = tx
    for ch in masked:
        gw = (len(_FONT_CN.get(ch, _FALLBACK_CN)[0]) if ord(ch[0]) > 127
              else len(_FONT5x7.get(ch.upper(), [""])[0]))
        if ch == "*":
            for by in range(ty - 2, ty + 7 * scale + 2, 5):
                for bx in range(cx, cx + gw * scale, 5):
                    cv.rect(bx, by, bx + 4, by + 4, (110, 150, 200))
        cx += (gw + 1) * scale
    # 抓拍时间水印
    cv.text(clock.iso(at), 8, 8, 1, (90, 96, 92))
    cv.text("VISITOR", W - 62, H - 16, 1, (120, 126, 120))

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = f"photo_{at.strftime('%Y%m%d_%H%M%S')}_{plate.replace('·', '')}.png"
    (out / name).write_bytes(_png(W, H, [bytes(cv.px[y * W * 3:(y + 1) * W * 3])
                                         for y in range(H)]))
    return name


# ---------------------------------------------------------------------------
# 闸机适配器
# ---------------------------------------------------------------------------

class GateOffline(Exception):
    """门禁离线，指令未能送达。"""


class GateAdapter:
    """模拟道闸。状态存 kv 表：
    - gate_online: '1'/'0'
    - gate_fail_next: 接下来多少次开闸强制卡阻（注入故障）
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def is_online(self) -> bool:
        row = self.conn.execute(
            "SELECT v FROM kv WHERE k='gate_online'").fetchone()
        return row is None or row["v"] == "1"

    def set_online(self, online: bool):
        self.conn.execute(
            "INSERT INTO kv(k,v) VALUES('gate_online',?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            ("1" if online else "0",))

    def inject_failures(self, n: int):
        self.conn.execute(
            "INSERT INTO kv(k,v) VALUES('gate_fail_next',?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(n),))

    def _take_failure_token(self) -> bool:
        row = self.conn.execute(
            "SELECT v FROM kv WHERE k='gate_fail_next'").fetchone()
        if row and int(row["v"]) > 0:
            left = int(row["v"]) - 1
            if left <= 0:
                self.conn.execute("DELETE FROM kv WHERE k='gate_fail_next'")
            else:
                self.conn.execute(
                    "UPDATE kv SET v=? WHERE k='gate_fail_next'", (str(left),))
            return True
        return False

    def _log(self, visit_id, direction, status, detail):
        cur = self.conn.execute(
            "INSERT INTO gate_commands(visit_id,direction,status,detail,created_at)"
            " VALUES(?,?,?,?,?)",
            (visit_id, direction, status, detail, clock.iso(clock.now())))
        return cur.lastrowid

    def open(self, visit_id, direction) -> tuple[bool, int, str]:
        """下发开闸指令。返回 (是否开闸, 指令ID, 说明)。离线抛 GateOffline。"""
        if not self.is_online():
            cmd_id = self._log(visit_id, direction, "offline",
                               "门禁控制器离线，开闸指令未送达，已登记待重发")
            raise GateOffline("门禁离线")
        if self._take_failure_token():
            cmd_id = self._log(visit_id, direction, "failed",
                               "支付已完成但道闸卡阻，闸机未开，需要重试")
            return False, cmd_id, "道闸卡阻，闸机未开"
        cmd_id = self._log(visit_id, direction, "sent", "闸机已开启放行")
        return True, cmd_id, "闸机已开启"

    def retry(self, command_id: int) -> tuple[bool, str]:
        """重试历史指令（离线恢复/卡阻后）。"""
        row = self.conn.execute(
            "SELECT * FROM gate_commands WHERE id=?", (command_id,)).fetchone()
        if row is None:
            return False, "找不到原开闸指令"
        if row["status"] in ("sent", "retried"):
            return True, "该指令此前已成功开闸，无需重试"
        if not self.is_online():
            return False, "门禁仍处于离线状态，暂不能重试"
        if self._take_failure_token():
            self._log(row["visit_id"], row["direction"], "failed",
                      "重试时道闸仍然卡阻")
            return False, "重试时道闸仍然卡阻"
        self.conn.execute(
            "UPDATE gate_commands SET status='retried', detail=? WHERE id=?",
            ("重试成功，闸机已开启", command_id))
        self._log(row["visit_id"], row["direction"], "retried",
                  f"对指令 #{command_id} 的重试成功")
        return True, "重试成功，闸机已开启"


# ---------------------------------------------------------------------------
# 支付适配器
# ---------------------------------------------------------------------------

class PayDeclined(Exception):
    pass


class PaymentAdapter:
    """模拟支付渠道。渠道侧自身也做幂等：同一键只产生一笔交易。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._seen: dict[str, str] = {}
        self._fail_left = 0

    def inject_failure(self, n: int = 1):
        self._fail_left += n

    def charge(self, idem_key: str, amount: float) -> tuple[str, bool]:
        """返回 (交易号, 是否本次实际扣款)。重复键返回原交易号、False。"""
        if idem_key in self._seen:
            return self._seen[idem_key], False
        if self._fail_left > 0:
            self._fail_left -= 1
            raise PayDeclined("模拟支付渠道返回失败，请稍后重试")
        trade = (f"MOCK{clock.now().strftime('%Y%m%d%H%M%S')}"
                 f"{len(self._seen) + 1:05d}")
        self._seen[idem_key] = trade
        return trade, True
