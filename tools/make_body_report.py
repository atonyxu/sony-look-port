#!/usr/bin/env python3
"""Build sources/bodies.json: how far each Sony body sits from the A7M5 the tables were baked on.

For every body that has Adobe's Standard profile installed:

    M = inv(FM_A7M5) @ FM_body      (row-normalised; M·(1,1,1) = (1,1,1))

then the ST look table is evaluated twice on a real photograph — once with the body's own camera
RGB, once with M applied — and the two results are compared in L*a*b*. That ΔE is the amount the
cross-body transfer would change the picture by, which is what decides whether it is worth turning on.

Needs rawpy (dev-time only) and a sample RAW; the shipped json lets the tool answer instantly
without either.

usage: make_body_report.py [--raw SAMPLE.ARW] [--look ST] [--out sources/bodies.json]
"""
import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import acr_encoding as E          # noqa: E402
import dcp                        # noqa: E402
import look_port as LP            # noqa: E402

ADOBE_DIRS = [Path("C:/ProgramData/Adobe/CameraRaw/CameraProfiles/Adobe Standard"),
              Path.home() / "AppData/Roaming/Adobe/CameraRaw/CameraProfiles",
              Path("/Library/Application Support/Adobe/CameraRaw/CameraProfiles")]

SRGB_TO_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                        [0.2126729, 0.7151522, 0.0721750],
                        [0.0193339, 0.1191920, 0.9503041]])
SRGB = np.array([[3.2404542, -1.5371385, -0.4985314],
                 [-0.9692660, 1.8760108, 0.0415560],
                 [0.0556434, -0.2040259, 1.0572252]])
D50 = np.array([[0.9555766, -0.0230393, 0.0631636],
                [-0.0282895, 1.0099416, 0.0210077],
                [0.0122982, -0.0204830, 1.3299098]])
TO_SRGB = SRGB @ D50 @ LP.PROPHOTO_TO_XYZ


def lab(rgb_lin):
    xyz = rgb_lin @ SRGB_TO_XYZ.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(np.clip(xyz, 0, None)), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16,
                     500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], -1)


def _as3x3(v):
    """Accept 3x3 or the legacy 3x4 form (matrix + translation column, offsets unused here)."""
    v = np.array(v, float)
    if v.size == 9:
        return v.reshape(3, 3)
    if v.size == 12:
        return v.reshape(3, 4)[:, :3]
    return None


def load_matrices():
    """model -> (FM1, FM2) from Adobe's Standard profiles (skipping hand-made linear clones)."""
    out = {}
    for d in ADOBE_DIRS:
        if not d.exists():
            continue
        for f in glob.glob(str(d / "*.dcp")):
            if "Linear" in Path(f).name:
                continue
            try:
                a = dcp.read(f)
            except Exception:
                continue
            if "ForwardMatrix1" not in a or "ForwardMatrix2" not in a or "ProfileToneCurve" in a:
                continue
            model = a.get("UniqueCameraModel")
            if not model or not str(model).startswith("Sony"):
                continue
            f1, f2 = _as3x3(a["ForwardMatrix1"]), _as3x3(a["ForwardMatrix2"])
            if f1 is None or f2 is None:
                continue
            out.setdefault(model, (f1, f2))
    return out


def sample_camera_rgb(raw_path, step=16):
    import rawpy
    with rawpy.imread(raw_path) as r:
        rgb = r.postprocess(use_camera_wb=True, gamma=(1, 1), output_bps=16,
                            output_color=rawpy.ColorSpace.raw, no_auto_bright=True, half_size=True)
        white = r.white_level
    return (((rgb.astype(np.float32)[::step, ::step] / 65535.0) * white) / E.CLIP).reshape(-1, 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="E:/furry/20261025白芒驿/_DSC6885.ARW", help="采样用的实拍 RAW")
    ap.add_argument("--look", default="ST", help="用哪张外观表比较")
    ap.add_argument("--out", default=str(ROOT / "sources" / "bodies.json"))
    a = ap.parse_args()

    mats = load_matrices()
    print(f"Adobe 标准配置里读到 {len(mats)} 个 Sony 机型")
    src = mats.get(LP.SRC_MODEL)
    if src is None:
        raise SystemExit(f"找不到源机 {LP.SRC_MODEL} 的 ForwardMatrix")

    table = LP.source_tables()[a.look]
    n = table.shape[0]
    print(f"用 {a.look} 表（{n}³）作比较基准")

    cam = sample_camera_rgb(a.raw)
    print(f"采样 {len(cam)} 个像素（{a.raw}）")

    def render(lin):
        pp = np.clip(LP.interp(table, E.forward(np.clip(lin, 0, 1) * E.CLIP)), 0, 1)
        return lab(np.clip((pp ** 1.8) @ TO_SRGB.T, 0, 1))

    ref = render(cam)
    bodies = {}
    for model, (f1, f2) in sorted(mats.items()):
        if model == LP.SRC_MODEL:
            bodies[model] = dict(max_mi=0.0, de_mean=0.0, de_p95=0.0, advice="skip")
            continue
        m = np.linalg.inv(src[0]) @ f1
        m = m / m.sum(1, keepdims=True)
        moved = (m @ cam.T).T
        d = np.sqrt(((render(moved) - ref) ** 2).sum(1))
        max_mi = float(np.abs(m - np.eye(3)).max())
        de_mean, de_p95 = float(d.mean()), float(np.percentile(d, 95))
        if de_mean < 1.0:
            advice = "skip"
        elif de_mean < 2.5:
            advice = "optional"
        elif max_mi > 0.5:
            advice = "yes-soft"
        else:
            advice = "yes"
        bodies[model] = dict(max_mi=round(max_mi, 4), de_mean=round(de_mean, 3),
                             de_p95=round(de_p95, 3), advice=advice)

    doc = dict(_about=dict(
        reference=LP.SRC_MODEL,
        look=a.look,
        sample=Path(a.raw).name,
        method="M = inv(FM_ref) @ FM_body, row-normalised; ST table evaluated on a real photo with "
               "and without M; dE76 in Lab. advice: skip <1, optional 1-2.5, yes >2.5, "
               "yes-soft when max|M-I|>0.5 (gamut clipping).",
        generated_by="tools/make_body_report.py", count=len(bodies)),
        bodies=bodies)
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写入 {a.out}（{len(bodies)} 个机型，{Path(a.out).stat().st_size // 1024} KB）")
    for model in ("Sony ILCE-7RM3", "Sony ILCE-9", "Sony ILCE-6000", "Sony ILCE-7RM5"):
        if model in bodies:
            b = bodies[model]
            print(f"  {model:18s} max|M-I| {b['max_mi']:.3f}  ΔE均 {b['de_mean']:5.2f}  p95 {b['de_p95']:5.2f}  {b['advice']}")


if __name__ == "__main__":
    main()
