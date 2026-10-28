#!/usr/bin/env python3
"""把 sources/ 里的 12 个源表 xmp 打包成单个 sources/CL.data（维护者工具，用户不需要跑）。

    python tools/pack_sources.py            # 读 sources/*.xmp，写 sources/CL.data
    python tools/pack_sources.py --keep     # 不删除打包后的 xmp

打包后容器里只有：magic、网格尺寸、条数、每条的压缩长度与载荷。外观代码不写进文件，也不存任何
文件名或配置名；载荷先 zlib 压缩再异或打散，所以 strings / 文本编辑器看不出内容。

注意：xmp 一旦删掉，本脚本就无法再生成 CL.data——想重打包需要先把原始 xmp 放回 sources/。
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import look_port as LP  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留打包后的 xmp")
    ap.add_argument("--src", default=str(ROOT / "sources"))
    ap.add_argument("--out", default=str(ROOT / "sources" / "CL.data"))
    a = ap.parse_args()

    src = Path(a.src)
    xmps = sorted(src.glob("*.xmp"))
    if len(xmps) < len(LP.LOOKS):
        raise SystemExit(f"{src} 里只有 {len(xmps)} 个 xmp，无法打包（需要 {len(LP.LOOKS)} 个）")

    tables = {}
    for look in LP.LOOKS:
        cand = [p for p in xmps if p.stem.endswith(f"Look {look}")]
        if not cand:
            raise SystemExit(f"缺少 {look} 的源表")
        tables[look] = LP.read_table(cand[0])[0]

    out = LP.pack_tables(tables, a.out)
    print(f"written: {out} ({out.stat().st_size / 1e6:.1f} MB, {len(LP.LOOKS)} 个外观)")

    # 立刻回读校验：存储是 uint16 精确的，回读只是转成 float32，所以按量化后的整数比对
    import numpy as np
    back = LP.load_pack(out)
    worst_q = 0
    for look in LP.LOOKS:
        qa = np.round(np.clip(tables[look], 0, 1) * 65535).astype(np.int64)
        qb = np.round(np.clip(back[look], 0, 1) * 65535).astype(np.int64)
        worst_q = max(worst_q, int(np.abs(qa - qb).max()))
    print(f"回读校验：uint16 最大差 {worst_q}" + ("（无损）" if worst_q == 0 else " ⚠ 有损"))
    if worst_q:
        raise SystemExit("打包有损，已中止")

    if not a.keep:
        for p in xmps:
            p.unlink()
            print(f"removed: {p.name}")


if __name__ == "__main__":
    main()
