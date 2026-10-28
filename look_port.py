#!/usr/bin/env python3
"""Port the 12 Sony Creative Looks to *any* Sony body, as Camera Raw / Lightroom profiles.

The look chain itself is not re-derived here: the `SonyA7M5 Look *.xmp` tables in
`sources/` already contain it (LM16 per-hue matrices -> main gamma -> RGB2YCC -> Y gamma
-> YCC2RGB -> SSCS) and have been matched against Sony's own rendering. What changes per body is
only the header: model, base profile, names, UUID. The table is copied and, when needed, resampled.

Everything lands in a folder you choose; `install()` additionally copies it into Lightroom's user
profile roots and warns about same-named files in the shared `C:\\ProgramData` root, which silently
shadow the user copies (that cost a whole evening once).

CLI:
    python look_port.py --raw IMG.ARW --out ./out            # model auto-detected from the raw
    python look_port.py --model "Sony ILCE-7RM3" --out ./out
    python look_port.py --raw IMG.ARW --install              # straight into Lightroom
"""
import argparse
import hashlib
import re
import shutil
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
import acr_profiles as A  # noqa: E402
import crs_table as T  # noqa: E402
import dcp  # noqa: E402

LOOKS = ["ST", "VV", "NT", "PT", "FL", "VV2", "IN", "SH", "BW", "SE", "FL2", "FL3"]

# 机内菜单的中文名，写进显示名与描述，方便在 Lightroom 里按中文挑
LOOK_ZH = {"ST": "标准", "PT": "人像", "NT": "中性", "VV": "鲜艳", "VV2": "鲜艳 2",
           "FL": "胶片", "FL2": "胶片 2", "FL3": "胶片 3", "IN": "即时",
           "SH": "柔和高调", "BW": "黑白", "SE": "深褐色"}
SOURCE_DIR = HERE / "sources"
PROPHOTO_TO_XYZ = np.array([[0.7976749, 0.1351917, 0.0313534],
                            [0.2880402, 0.7118741, 0.0000857],
                            [0.0, 0.0, 0.8252100]])
ADOBE_DIRS = [
    Path("C:/ProgramData/Adobe/CameraRaw/CameraProfiles/Adobe Standard"),
    Path.home() / "AppData/Roaming/Adobe/CameraRaw/CameraProfiles",
    Path("/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Adobe Standard"),
    Path.home() / "Library/Application Support/Adobe/CameraRaw/CameraProfiles/Adobe Standard",
]
LR_ROOTS = [Path("C:/ProgramData/Adobe/CameraRaw"), Path.home() / "AppData/Roaming/Adobe/CameraRaw",
            Path.home() / "Library/Application Support/Adobe/CameraRaw"]

SHORT = {
    "ILCE-7M5": "A7M5", "ILCE-7M4": "A7M4", "ILCE-7M3": "A7M3", "ILCE-7M2": "A7M2",
    "ILCE-7RM5": "A7R5", "ILCE-7RM4": "A7R4", "ILCE-7RM4A": "A7R4A",
    "ILCE-7RM3": "A7R3", "ILCE-7RM3A": "A7R3A", "ILCE-7RM2": "A7R2",
    "ILCE-7SM3": "A7S3", "ILCE-7SM2": "A7S2", "ILCE-7C": "A7C", "ILCE-7CM2": "A7C2",
    "ILCE-7CR": "A7CR", "ILCE-1": "A1", "ILCE-1M2": "A1M2", "ILCE-9M3": "A9M3",
    "ILCE-9M2": "A9M2", "ILCE-9": "A9", "ILCE-6700": "A6700", "ILCE-6600": "A6600",
    "ILCE-6400": "A6400", "ILCE-6100": "A6100", "ILCE-ZV-E1": "ZVE1", "ILCE-ZV-E10": "ZVE10",
    "ILCE-FX3": "FX3", "ILCE-FX30": "FX30",
}

XMP = """<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 7.0-c000 1.000000, 0000/00/00-00:00:00        ">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   crs:PresetType="Look"
   crs:Cluster=""
   crs:UUID="{uuid}"
   crs:SupportsAmount="False"
   crs:SupportsColor="True"
   crs:SupportsMonochrome="False"
   crs:SupportsHighDynamicRange="True"
   crs:SupportsNormalDynamicRange="True"
   crs:SupportsSceneReferred="True"
   crs:SupportsOutputReferred="False"
   crs:CameraModelRestriction="{model}"
   crs:Copyright=""
   crs:ContactInfo=""
   crs:Version="13.0"
   crs:ProcessVersion="11.0"
   crs:ConvertToGrayscale="False"
   crs:CameraProfile="{base}"
   crs:RGBTable="{md5}"
   crs:Table_{md5}="{table}"
   crs:HasSettings="True">
   <crs:Name>
    <rdf:Alt>
     <rdf:li xml:lang="x-default">{name}</rdf:li>
    </rdf:Alt>
   </crs:Name>
   <crs:ShortName>
    <rdf:Alt>
     <rdf:li xml:lang="x-default"/>
    </rdf:Alt>
   </crs:ShortName>
   <crs:SortName>
    <rdf:Alt>
     <rdf:li xml:lang="x-default"/>
    </rdf:Alt>
   </crs:SortName>
   <crs:Group>
    <rdf:Alt>
     <rdf:li xml:lang="x-default">{group}</rdf:li>
    </rdf:Alt>
   </crs:Group>
   <crs:Description>
    <rdf:Alt>
     <rdf:li xml:lang="x-default">Sony {look} ported to {model}</rdf:li>
    </rdf:Alt>
   </crs:Description>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


# ---------------------------------------------------------------- model helpers

def model_from_raw(path):
    """Read IFD0 Model from a raw file, without third-party imaging libraries.

    Only the TIFF header and the first IFD are touched, so this works on 40-80 MB ARWs in
    microseconds and needs nothing beyond the standard library.
    """
    path = Path(path)
    with open(path, "rb") as f:
        head = f.read(8)
        if len(head) < 8 or head[:2] not in (b"II", b"MM"):
            raise ValueError(f"{path.name} 不是 TIFF/ARW（文件头不对）")
        e = "<" if head[:2] == b"II" else ">"
        ifd = struct.unpack(e + "I", head[4:8])[0]
        f.seek(ifd)
        count = struct.unpack(e + "H", f.read(2))[0]
        if count > 4096:
            raise ValueError(f"{path.name} 的 IFD 异常（{count} 项）")
        blob = f.read(count * 12)
        entries = {}
        for i in range(count):
            tag, typ, cnt = struct.unpack(e + "HHI", blob[i * 12:i * 12 + 8])
            raw = blob[i * 12 + 8:i * 12 + 12]
            if typ == 2:                                   # ASCII
                if cnt <= 4:
                    val = raw[:cnt]
                else:
                    off = struct.unpack(e + "I", raw)[0]
                    f.seek(off)
                    val = f.read(min(cnt, 64))
                entries[tag] = val.split(b"\x00")[0].decode("ascii", "ignore").strip()
        model = entries.get(0x0110)
    if not model:
        raise ValueError(f"{path.name} 里没有 Model 标签（不是 Sony ARW？）")
    return model if model.startswith("Sony") else f"Sony {model}"


def normalise_model(model):
    m = (model or "").strip()
    if not m:
        raise ValueError("机型不能为空")
    if m.upper().startswith("ILCE"):
        m = "Sony " + m
    if not m.startswith("Sony"):
        m = "Sony " + m
    return m


def clean_prefix(text):
    """文件前缀同时用作文件名与显示名：去掉路径分隔符和首尾空白。"""
    bad = '\\/:*?"<>|'
    out = "".join(ch for ch in str(text).strip() if ch not in bad)
    return out[:48]


def short_prefix(model):
    """'Sony ILCE-7RM3' -> 'SonyA7R3'；未知机型退化为去掉 ILCE- 的写法。"""
    body = model[5:] if model.startswith("Sony ") else model
    short = SHORT.get(body) or body.replace("ILCE-", "").replace("-", "")
    return f"Sony{short}"


# ---------------------------------------------------------------- tables

def read_table(path):
    s = Path(path).read_text(encoding="utf-8")
    m = re.search(r'crs:Table_([0-9A-F]{32})="([^"]+)"', s)
    if not m:
        raise ValueError(f"{path} 里没有 crs:Table_* 数据")
    data = T.decode(m.group(2))
    assert T.fingerprint(data) == m.group(1), f"{path} 的表校验和不符（文件被改过）"
    _, _, dims, n = struct.unpack("<4I", data[:16])
    body = np.frombuffer(data[16:16 + n ** 3 * 6], "<u2").reshape(n, n, n, 3).astype(np.int64)
    ident = np.round(np.arange(n) * 65535 / (n - 1)).astype(np.int64)
    I = np.stack(np.meshgrid(ident, ident, ident, indexing="ij"), -1)
    return ((body + I) % 65536) / 65535.0, n


def interp(table, v):
    n = table.shape[0]
    p = np.clip(v, 0, 1) * (n - 1)
    i = np.minimum(p.astype(int), n - 2)
    f = p - i
    out = 0
    for dr in (0, 1):
        for dg in (0, 1):
            for db in (0, 1):
                w = (f[..., 0] if dr else 1 - f[..., 0]) * (f[..., 1] if dg else 1 - f[..., 1]) * \
                    (f[..., 2] if db else 1 - f[..., 2])
                out = out + w[..., None] * table[i[..., 0] + dr, i[..., 1] + dg, i[..., 2] + db]
    return out


def resample(table, n):
    if table.shape[0] == n:
        return table
    x = np.arange(n) / (n - 1)
    g = np.stack(np.meshgrid(x, x, x, indexing="ij"), -1).reshape(-1, 3)
    return np.clip(interp(table, g), 0, 1).reshape(n, n, n, 3)


# ---------------------------------------------------------------- packed source (sources/CL.data)
#
# 12 张源表打成一个不透明容器：没有文件名、没有 "Sony"/"Look"/外观代码之类的字面量，载荷先 zlib
# 压缩再异或打散，用文本编辑器或 strings 看不出内容。顺序固定为 LOOKS，代码本身不写进文件。
CL_MAGIC = b"CLDATA01"
CL_KEY = b"look_port/CL/v1"


def _keystream(n, idx):
    import hashlib
    h = hashlib.sha256(CL_KEY + struct.pack("<I", idx))
    out = bytearray()
    while len(out) < n:
        out += h.digest()
        h = hashlib.sha256(h.digest())
    return bytes(out[:n])


def _scramble(data, idx):
    a = np.frombuffer(data, np.uint8)
    k = np.frombuffer(_keystream(len(a), idx), np.uint8)
    return (a ^ k).tobytes()


def pack_tables(tables, path):
    """Write {look: values} as sources/CL.data. Only LOOKS order is kept; no names are stored."""
    import zlib
    n = next(iter(tables.values())).shape[0]
    out = bytearray(CL_MAGIC + struct.pack("<HH", n, len(LOOKS)))
    for i, look in enumerate(LOOKS):
        q = np.round(np.clip(tables[look], 0, 1) * 65535).astype(np.uint16).tobytes()
        raw = q[1::2] + q[0::2]                       # 高字节平面在前，压得更狠
        blob = _scramble(zlib.compress(raw, 9), i)
        out += struct.pack("<I", len(blob)) + blob
    Path(path).write_bytes(bytes(out))
    return Path(path)


def load_pack(path):
    """Read sources/CL.data -> {look: values}."""
    import zlib
    data = Path(path).read_bytes()
    if data[:8] != CL_MAGIC:
        raise ValueError(f"{path} 不是 CL 素材文件")
    n, count = struct.unpack("<HH", data[8:12])
    if count != len(LOOKS):
        raise ValueError(f"素材里有 {count} 个外观，程序期望 {len(LOOKS)} 个")
    pos, out = 12, {}
    for i, look in enumerate(LOOKS):
        (ln,) = struct.unpack("<I", data[pos:pos + 4])
        blob = _scramble(data[pos + 4:pos + 4 + ln], i)
        pos += 4 + ln
        raw = zlib.decompress(blob)
        half = len(raw) // 2
        hi, lo = np.frombuffer(raw[:half], np.uint8), np.frombuffer(raw[half:], np.uint8)
        q = (hi.astype(np.uint16) << 8) | lo.astype(np.uint16)
        out[look] = (q.astype(np.float32).reshape(n, n, n, 3) / 65535.0)
    return out


def source_tables(src_dir=None):
    """All 12 A7M5 tables, as the ground truth for every port."""
    src_dir = Path(src_dir or SOURCE_DIR)
    packed = src_dir / "CL.data"
    if packed.exists():
        return load_pack(packed)
    out, missing = {}, []
    for look in LOOKS:
        p = src_dir / f"SonyA7M5 Look {look}.xmp"
        if not p.exists():
            missing.append(p.name)
            continue
        out[look] = read_table(p)[0]
    if missing:
        raise FileNotFoundError("缺少源表：" + ", ".join(missing) + f"（在 {src_dir}）")
    return out


# ---------------------------------------------------------------- writers

def write_look(look, values, model, prefix, out_dir, group=None, base=None):
    base = base or f"{prefix} Linear Base (Identity)"
    group = group or prefix.replace("Sony", "Sony ").replace("  ", " ").strip()
    tag = f"{prefix} {look}"
    zh = LOOK_ZH.get(look, "")
    data = A.table_bytes(values)
    md5 = T.fingerprint(data)
    uuid = hashlib.md5(f"{prefix}/{look}".encode()).hexdigest().upper()
    path = Path(out_dir) / f"{tag}.xmp"
    path.write_text(XMP.format(uuid=uuid, model=model, base=base, md5=md5, table=T.encode(data),
                               name=f"{tag} {zh}".strip(), group=group, look=f"{look}（{zh}）"),
            encoding="utf-8")
    return path


_ADOBE_INDEX = None


def find_adobe_dcp(model):
    """Adobe's untouched Standard profile for this body, if installed. Used only for its matrices.

    File names follow no strict rule (`Sony ZV-E10 …` vs `Sony ILCE-ZV-E10 …`), so after the direct
    name lookup we index the Adobe folders by UniqueCameraModel.
    """
    global _ADOBE_INDEX
    for d in ADOBE_DIRS:
        p = Path(d) / f"{model} Adobe Standard.dcp"
        if p.exists():
            a = dcp.read(p)
            if "ProfileToneCurve" not in a and "ColorMatrix1" in a:
                return p, a
    if _ADOBE_INDEX is None:
        _ADOBE_INDEX = {}
        for d in ADOBE_DIRS:
            d = Path(d)
            if not d.is_dir():
                continue
            for p in d.glob("*.dcp"):
                try:
                    a = dcp.read(p)
                except Exception:
                    continue
                if "ProfileToneCurve" in a or "ColorMatrix1" not in a:
                    continue                     # hand-edited linear clone, or not a camera profile
                _ADOBE_INDEX.setdefault(a["UniqueCameraModel"], (p, a))
    return _ADOBE_INDEX.get(model, (None, None))


# Sony's DRO (ZcTaskVatr) is a *local* tone mapping: Edit builds a low-resolution gain grid from the
# whole raw and applies it per pixel. A DCP tone curve is pointwise, so the spatial half cannot be
# reproduced. What can be reproduced is its global character — lift the shadows, leave midtones and
# highlights alone — which is what makes Edit's default render look brighter with cleaner shadows.
# Strengths roughly match DRO Lv1..Lv5.
DRO_LEVELS = {0: 0.0, 1: 0.30, 2: 0.48, 3: 0.66, 4: 0.86, 5: 1.08}


LUMA = np.array([0.2126, 0.7152, 0.0722], np.float64)


def tone_curve_xy(ev=0.0, toe=0.0, dro=0, points=96, x_hi=0.30):
    """The tone curve on its own: (x, y) arrays, monotone, (0,0) .. (1,1), x in linear/clip units.

    toe   clips the bottom `toe` of the linear range (sensor floor / noise);
    ev    global exposure trim, soft-shouldered so white stays at 1;
    dro   shadow lift: gain = 1 + s*(1 - min(x/x_hi,1))^2, i.e. strongest at black and zero by x_hi.

    Knots are packed towards black (x = t^2.2): a toe of a few thousandths disappears on an evenly
    spaced curve, because linear interpolation just smears it away.
    """
    s = DRO_LEVELS.get(dro, 0.0) if isinstance(dro, int) else float(dro)
    g = 2.0 ** ev
    x = np.linspace(0.0, 1.0, points) ** 2.2
    u = np.clip((x - toe) / (1.0 - toe), 0.0, 1.0)
    y = g * u / (1.0 + (g - 1.0) * u * u)
    if s > 0:
        y = y * (1.0 + s * (1.0 - np.clip(u / x_hi, 0.0, 1.0)) ** 2)
    y = np.clip(y, 0.0, 1.0)
    y[0], y[-1] = 0.0, 1.0
    assert np.all(np.diff(y) >= -1e-6), "curve 不单调"
    return x, y


def identity_curve(ev=0.0, toe=0.0, dro=0, points=96, x_hi=0.30):
    """Same curve as a flat (x0, y0, x1, y1, ...) list for the DCP ProfileToneCurve tag."""
    x, y = tone_curve_xy(ev, toe, dro, points, x_hi)
    return np.stack([x, y], -1).reshape(-1).tolist()


def tone_compose(table, ev=0.0, toe=0.0, dro=0, x_hi=0.30):
    """Rebake a look table with a tonemapping that keeps hue and saturation.

    A DCP ``ProfileToneCurve`` runs on R, G and B *separately*, so a level-dependent lift gives each
    channel a different multiplier: the channel ratios collapse towards 1 and the picture goes pale —
    measured on the A7R3 test shot, DRO Lv3 costs 10.8% of mean chroma, and 25% in the L* 65..85 band
    where most of the subject sits. Sony's own DRO is a gain map: one multiplier for all three
    channels, so colours survive.

    This function gets the same behaviour out of the RGBTable, which *is* a full RGB->RGB map. For
    every node of the table: decode to linear, read the target off the 1D curve using luma, turn that
    into a single gain, apply it to the linear triple, and look the original table up there:

        g = curve(Y) / Y            T_new(v) = T_old( encode(g * decode(v)) )

    The whole tone step then lives inside the table and the base profile returns to a plain identity
    curve, so nothing is applied twice.
    """
    import acr_encoding as E                          # ACR's linear <-> table-input encoding

    n = table.shape[0]
    q = np.arange(n) / (n - 1)
    v = np.stack(np.meshgrid(q, q, q, indexing="ij"), -1).reshape(-1, 3)
    lin = E.inverse(v)                                # table input -> Sony linear (DN)
    y = np.maximum(lin @ LUMA, 0.0)
    cx, cy = tone_curve_xy(ev, toe, dro, x_hi=x_hi)
    target = np.interp(np.clip(y / E.CLIP, 0.0, 1.0), cx, cy) * E.CLIP
    gain = np.where(y > 1e-6, target / np.maximum(y, 1e-6), 1.0)
    gain = np.clip(gain, 0.0, 8.0)
    moved = np.clip(E.forward(lin * gain[:, None]), 0.0, 1.0)
    return np.clip(interp(table, moved), 0.0, 1.0).reshape(n, n, n, 3)


def write_identity_dcp(model, prefix, out_dir, adobe=None, adobe_path=None, curve=None, tune=""):
    """Base whose working RGB *is* the white-balanced camera RGB — what the look tables expect.

    ForwardMatrix is linear ProPhoto -> XYZ D50 for both illuminants, so ACR hands the look table
    camera RGB. ColorMatrix is only there because the format wants it; ACR uses ForwardMatrix when
    present. When Adobe's profile for this body is missing we borrow the A7M5 matrices and say so.
    """
    name = f"{prefix} Linear Base (Identity)"
    if adobe is None:
        adobe, adobe_path = find_adobe_dcp(model)
    mats = f"matrices from {Path(adobe_path).name}" if adobe is not None else "ColorMatrix borrowed from A7M5"
    if tune:
        mats += f"; {tune}"
    cm1 = adobe["ColorMatrix1"] if adobe is not None else [1.1168, -0.6826, 0.0991,
                                                           -0.3276, 1.0617, 0.3080,
                                                           -0.0050, 0.0460, 0.7113]
    cm2 = adobe["ColorMatrix2"] if adobe is not None else [0.9089, -0.3577, -0.0787,
                                                           -0.3563, 1.1326, 0.2557,
                                                           -0.0114, 0.0928, 0.5904]
    cal1 = adobe["CalibrationIlluminant1"] if adobe is not None else [21]
    cal2 = adobe["CalibrationIlluminant2"] if adobe is not None else [17]
    entries = [("UniqueCameraModel", 2, model), ("ProfileName", 2, name),
               ("ColorMatrix1", 10, cm1), ("ColorMatrix2", 10, cm2),
               ("CalibrationIlluminant1", 3, cal1), ("CalibrationIlluminant2", 3, cal2),
               ("ForwardMatrix1", 10, PROPHOTO_TO_XYZ.tolist()),
               ("ForwardMatrix2", 10, PROPHOTO_TO_XYZ.tolist()),
               ("ProfileToneCurve", 11, curve or [0.0, 0.0, 1.0, 1.0]),
               ("ProfileEmbedPolicy", 4, [0]), ("DefaultBlackRender", 4, [1]),
               ("ProfileCopyright", 2, f"look_port; {mats}")]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.dcp"
    dcp.write(path, entries)
    return path


def build(model=None, raw=None, out_dir="out", grid=32, src_dir=None, log=print,
          ev=0.0, toe=0.0, dro=0, prefix=None):
    """Generate 12 look xmp + the identity base dcp for one body. Returns a result dict."""
    if raw:
        detected = model_from_raw(raw)
        if model and normalise_model(model) != detected:
            log(f"注意：RAW 是 {detected}，但指定了 {model}；按指定值生成")
        model = model or detected
    if not model:
        raise ValueError("需要 --model 或 --raw")
    model = normalise_model(model)
    prefix = clean_prefix(prefix) if prefix else short_prefix(model)
    if not prefix:
        raise ValueError("文件前缀不能为空")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tabs = source_tables(src_dir)
    src_n = next(iter(tabs.values())).shape[0]
    log(f"机型 {model} → 前缀 {prefix}；源表 {src_n}³ → 输出 {grid}³")

    files = []
    curved = bool(ev or toe or dro)
    if curved:
        cx, cy = tone_curve_xy(ev, toe, dro)
        pts = "  ".join(f'{l:.2f}→{np.interp(l, cx, cy):.3f}' for l in (0.01, 0.05, 0.18, 0.5))
        log(f"影调模仿：{pts}（线性→输出；烘进外观表，底座保持恒等曲线）")
    for look in LOOKS:
        tab = tabs[look]
        if curved:                                    # 在源网格上做，避免二次重采样的误差
            tab = tone_compose(tab, ev, toe, dro)
        tab = resample(tab, grid)
        p = write_look(look, tab, model, prefix, out_dir)
        files.append(p)
        log(f"  {look:4s} {LOOK_ZH.get(look, ''):<4s} 中灰 {np.round(tab[grid // 2, grid // 2, grid // 2], 3)}  {p.name}")

    adobe_path, adobe = find_adobe_dcp(model)
    # 影调已经烘进表里，底座的 ProfileToneCurve 必须回到恒等，否则会叠两次
    tune = f"tone in tables: ev {ev:+.2f} toe {toe:.3f} dro Lv{dro}" if curved else ""
    d = write_identity_dcp(model, prefix, out_dir, adobe, adobe_path, curve=None, tune=tune)
    files.append(d)
    log(f"  base  {d.name}（{'用 ' + Path(adobe_path).name if adobe is not None else '未找到 Adobe 标准配置，ColorMatrix 借用 A7M5'}）")
    return dict(model=model, prefix=prefix, files=files, out_dir=out_dir, adobe=str(adobe_path) if adobe_path else None)


def install(files, user_root=None, clean=False, log=print):
    """Copy into the *user* Camera Raw roots, and report same-named files in the shared root."""
    if user_root is None:
        user_root = next((r for r in LR_ROOTS if r.exists()), None)
        if user_root is None:
            raise FileNotFoundError("找不到 Adobe Camera Raw 目录，请先安装 Lightroom / Camera Raw")
        # prefer the user root: the shared ProgramData root shadows it when names collide
        if "AppData" in str(user_root) or "Application Support" in str(user_root):
            pass
        else:
            home = Path.home() / "AppData/Roaming/Adobe/CameraRaw"
            user_root = home if home.exists() else user_root
    user_root = Path(user_root)
    settings, profiles = user_root / "Settings", user_root / "CameraProfiles"
    settings.mkdir(parents=True, exist_ok=True)
    profiles.mkdir(parents=True, exist_ok=True)

    names = {p.name for p in files}
    shared = [r for r in LR_ROOTS if "ProgramData" in str(r) or "Library" in str(r)]
    conflicts = []
    for root in shared:
        for sub in ("Settings", "CameraProfiles", "CameraProfiles/Adobe Standard"):
            d = root / sub
            if d.exists():
                conflicts += [p for p in d.rglob("*") if p.is_file() and p.name in names]
    if conflicts:
        log(f"共享目录里有 {len(conflicts)} 个同名文件，会顶掉用户目录的副本：")
        for p in conflicts:
            log(f"  {p}")
        if clean:
            bak = Path.home() / ".workbuddy/tmp/look_port_backup"
            for p in conflicts:
                dest = bak / p.parent.name
                dest.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), dest / p.name)
            log(f"  已移到 {bak}")

    for p in files:
        dest = profiles if p.suffix.lower() == ".dcp" else settings
        shutil.copy2(p, dest / p.name)
        log(f"  → {dest / p.name}")
    log("装好了。完全退出 Lightroom 再打开，它只在启动时扫描这两个目录。")
    return dict(root=str(user_root), conflicts=[str(p) for p in conflicts])


def main():
    ap = argparse.ArgumentParser(description="把 12 个 Sony Creative Look 移植到任意机型")
    ap.add_argument("--raw", help="该机型的 RAW 文件，用于自动识别机型")
    ap.add_argument("--model", help='机型，例如 "Sony ILCE-7RM3"')
    ap.add_argument("--out", default="look_port_out", help="输出目录")
    ap.add_argument("--src", help="A7M5 源表所在目录（默认 sources/）")
    ap.add_argument("--dro", type=int, default=0, choices=range(0, 6),
                    help="影调模仿：DRO 暗部提亮（默认 0=关；推荐 3，1..5 近似 Sony Lv1..Lv5）")
    ap.add_argument("--ev", type=float, default=0.0, help="影调模仿：整体曝光微调，单位 EV")
    ap.add_argument("--prefix", help="文件前缀，默认按机型推导（如 SonyA7R3）")
    ap.add_argument("--toe", type=float, default=0.0,
                    help="黑位裁切量（线性 0..1），0.003~0.008 清阴影，0.031 可去掉一整个传感器黑电平")
    ap.add_argument("--install", action="store_true", help="同时装进 Lightroom")
    ap.add_argument("--clean", action="store_true", help="安装时把共享目录里的同名旧文件移到备份目录")
    a = ap.parse_args()
    r = build(model=a.model, raw=a.raw, out_dir=a.out, src_dir=a.src,
              ev=a.ev, toe=a.toe, dro=a.dro, prefix=a.prefix)
    if a.install:
        install(r["files"], clean=a.clean)
    print(f"\n生成 {len(r['files'])} 个文件到 {r['out_dir']}")


if __name__ == "__main__":
    main()
