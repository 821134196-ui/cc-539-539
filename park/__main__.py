"""启动入口：python -m park  [--port 8000] [--reset]"""
from __future__ import annotations

import argparse

from .db import connect, init_db
from .seed import seed
from .web import make_server


def main():
    ap = argparse.ArgumentParser(description="园区访客车辆临时通行系统")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default="data/park.db")
    ap.add_argument("--photos", default="data/photos")
    ap.add_argument("--reset", action="store_true",
                    help="重置数据库并写入演示种子数据")
    ap.add_argument("--no-seed", action="store_true",
                    help="数据库不存在时也不写入种子数据")
    args = ap.parse_args()

    import os
    fresh = args.reset or (not args.no_seed and not os.path.exists(args.db))
    if fresh:
        seed(args.db, args.photos, reset=args.reset)
        print("已写入演示种子数据（含一辆离线待重放车辆）")

    httpd, _ = make_server(args.host, args.port, args.db, args.photos)
    print(f"园区访客车辆临时通行系统已启动： http://{args.host}:{args.port}")
    print("右上角可切换身份：门岗 / A 单位 / B 单位 / 园区管理员")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
