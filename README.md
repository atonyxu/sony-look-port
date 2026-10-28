# sony-look-port

把索尼的 12 种 **Creative Look**（创意外观）移植到任意索尼机型，生成 Lightroom / Camera Raw 用的配置文件。

## 快速开始

```bash
python look_port_gui.py                                   # 图形界面
python look_port.py --raw IMG.ARW --out ./out             # 机型从 RAW 自动识别
python look_port.py --model "Sony ILCE-7RM5" --out ./out  # 直接指定机型
python look_port.py --raw IMG.ARW --install               # 装进 Lightroom
```

界面流程：**选 RAW**（自动读机型）→ 确认机型（可手改，支持 `ILCE-7RM3A`、`ILCE-1M2` 这类）→
选输出目录 → 「生成」或「生成并安装到 Lightroom」。

环境：Python 3.9+，依赖只有 `numpy`：

```bash
pip install -r requirements.txt
```

**需要带 tkinter 的解释器**。

## 目录

```
look_port.py        核心库 + 命令行
look_port_gui.py    tkinter 界面
lib/                crs_table.py（RGBTable 编解码）、acr_profiles.py（表写出）、dcp.py（DCP 读写）、
                    acr_encoding.py/.json（ACR 线性↔表输入刻度，影调烘表时要解码亮度）
sources/CL.data     12 张源表打包成的单个素材文件（移植的唯一基准）
tools/pack_sources.py  把 xmp 源表打包成 CL.data（维护用，平时不用跑）
```

## 输出什么

每个机型 13 个文件：

| 文件 | 说明 |
| --- | --- |
| `<前缀> ST…FL3.xmp` | 12 种外观，装进 LR 的 `Settings`；显示名带中文（`SonyA7R3 ST 标准`） |
| `<前缀> Linear Base (Identity).dcp` | 配套底座，装进 `CameraProfiles` |

中文名沿用机内菜单：ST 标准、PT 人像、NT 中性、VV 鲜艳、VV2 鲜艳 2、FL 胶片、FL2 胶片 2、FL3 胶片 3、
IN 即时、SH 柔和高调、BW 黑白、SE 深褐色。文件名保持 ASCII，只有显示名和描述带中文。

只生成 12 个 xmp + 1 个 dcp，**不再附带底座 xmp**。

前缀默认按机型推导：`ILCE-7RM3 → SonyA7R3`、`ILCE-1M2 → SonyA1M2`、未知机型退化为去掉 `ILCE-`/`-`
的写法（`ILCE-ZV-E10 → SonyZVE10`）。**界面上可以直接改**（换机型时会重新推导），命令行用 `--prefix`。

**底座必须是 Identity 那个**。它把 `ForwardMatrix` 设成线性 ProPhoto → XYZ D50，于是 ACR 的工作
RGB 就是白平衡后的相机 RGB —— 正是外观表被标定时看到的信号。换成 `Adobe Standard` 会让外观叠两遍。

## 原理

外观链**不重算**。`sources/CL.data` 里的 12 张 64³ 表已经包含完整链路（LM16 逐色相矩阵 → 主伽马 →
RGB2YCC → Y 伽马 → YCC2RGB → SSCS），并且和索尼成片对过。换机型时真正变的只有表头：

| 字段 | 生成值 |
| --- | --- |
| `crs:CameraModelRestriction` | 你填的机型 |
| `crs:CameraProfile` | `<前缀> Linear Base (Identity)` |
| `crs:UUID` | `md5("<前缀>/ST")`，同机型可复现 |
| Name / Group | `<前缀> ST 标准` / `Sony A7R3` |
| `crs:Table_*` | 64³ 源表三线性降采样到 32³ |

底座里的 `ColorMatrix` 从本机 Adobe 的 `<机型> Adobe Standard.dcp` 抄（渲染不走它，只有
`ForwardMatrix` 起作用）；找不到就借用 A7M5 的，并在日志里写明。Adobe 的文件名不统一
（`Sony ZV-E10 …` 而不是 `Sony ILCE-ZV-E10 …`），所以精确匹配失败会按 `UniqueCameraModel`
建索引全目录扫一遍。

## 影调模仿（可选，默认关闭）

Edit 默认开 DRO，所以同一张 RAW 在 Sony 软件里显得更亮、阴影更干净。**DRO 是局部处理**
（`ZcTaskVatr`：整幅统计出低分辨率增益网格，再按像素取增益），空间那半截复现不了；能复现的是它的
整体性格——抬暗部、护高光：

```
gain = 1 + s·(1 − min(u/0.30, 1))²      s = 0.30 … 1.08（Lv1 .. Lv5）
```

### 为什么烘进外观表，而不是写进底座的影调曲线

一开始是把这条曲线写进 `<前缀> Linear Base (Identity).dcp` 的 `ProfileToneCurve`，结果颜色变淡：
`ProfileToneCurve` 对 **R、G、B 分别**执行，而这条曲线的增益随亮度变化（0.05 处 ×1.385、0.18 处
×1.095、0.3 以上 ×0.99）。一个像素的三个通道各拿一个倍数，通道比值被压向 1，颜色就灰了。

实测（A7R3 实拍，ST 外观，Lv3）：

| 做法 | ΔL\* | ΔC\*（饱和度） |
| --- | --- | --- |
| 写进底座曲线（逐通道） | +3.29 | **−1.81（−10.8%）** |
| 烘进外观表（统一增益） | +3.31 | +0.39（+2.3%） |

掉得最狠的是 L\* 65–85 那一档（主体所在），旧做法 −25.1%，新做法 −1.3%。Sony 自己的 DRO 是
**增益图**：对三个通道乘同一个倍数，所以色相和饱和度都不动。外观表是完整的 RGB→RGB 映射，可以照做：

```
g = curve(Y) / Y            T_new(v) = T_old( encode(g · decode(v)) )
```

这样影调整个活在 12 张 xmp 里，底座回到恒等曲线（2 个点），不会叠两次。

### 参数

界面第 3 区有个「启用影调模仿」勾选框，**默认不勾**——不勾时表就是原样，输出最"中性"。勾上后三个
参数生效（已预填推荐值）：

| 参数 | 作用 | 建议 |
| --- | --- | --- |
| DRO 模拟 | 暗部提亮强度，0 = 关 | 想贴近 Edit 用 Lv3–Lv5 |
| 曝光微调 EV | 整体 ±EV，高光软压不切白 | 一般留 0 |
| 黑位 | 裁掉线性最底端（清阴影噪点） | 0.003–0.008 |

命令行等价：`--dro 3 --toe 0.003 --ev 0`（不写则关闭）。常数与仓库里的 `a7r3_base_tune.py` 同源，
曲线 96 点、单调、`(0,0)`→`(1,1)`，节点按 `x=t^2.2` 向暗部加密。

**做不到的部分**：Edit 按局部亮度决定增益，所以"亮墙旁边的一小块暗部"会被它抬得更多，边缘还可能有
轻微光晕；这里只看像素本身的亮度。

## 安装时的一个坑

Lightroom 会**同时**扫描两个目录：

```
C:\ProgramData\Adobe\CameraRaw\Settings\       ← 共享
%APPDATA%\Adobe\CameraRaw\Settings\            ← 当前用户
```

共享目录里如果有**同名**文件，会静默顶掉用户目录那份。装完必须**完全退出 Lightroom 再打开** —— 它只在启动时扫描这两个目录。

## 参数

| 参数 | 说明 |
| --- | --- |
| `--raw` | 该机型的 RAW，仅用于读机型，不修改 |
| `--model` | 机型字符串，写进 `CameraModelRestriction`，要和 ARW 的 Model 一致 |
| `--out` | 输出目录，默认 `look_port_out` |
| `--src` | 源表目录，默认 `sources/` |
| `--prefix` | 文件前缀，默认按机型推导 |
| `--dro` | 影调模仿的 DRO 强度 0–5，默认 **0（关）**，推荐 3 |
| `--ev` / `--toe` | 曝光微调（EV）/ 黑位裁切（线性 0..1），默认均 0；推荐 0.003 |
| `--install` / `--clean` | 装进 Lightroom / 顺手清理共享目录同名文件 |

## 验证

- **表内容**：不启用影调时，生成的 12 张表与拆分前的发布版本逐点一致（max diff 0.000000），底座
  DCP 除 `ProfileCopyright` 文案外标签相同。
- **影调模仿**：启用后底座恒等（2 点曲线）、12 张表带增益。实测亮度与旧做法一致（ΔL\* +3.31 vs
  +3.29），饱和度不掉（ΔC\* +0.39 vs −1.81）。BW 中灰仍严格中性（三通道差 0.0000），SE 的棕褐比值
  （0.818/0.756/0.707）保持。
- **机型识别**：4 个真实 ARW 测过（含合成色卡），A7R5 / ZV-E10 / 未知机型三条路径都通。
- **耗时**：不启用影调 1 秒；启用（12 个表各做一次 32k 点预合成）约 4 秒。

## 已知限制

- 表值源自 A7M5，**没做跨机型色彩换算**。CFA 接近的机型（A7 系全画幅）中性完全一致、饱和色差
  1–2 个 a\*；APS-C（A6xxx）会偏一些。严谨做法是按 `M = inv(FM_A7M5) @ FM_A7R3` 换算，还没做进工具。
- 亮度基准沿用 A7M5 的 ACR 编码（中灰 6422）。若整组偏亮偏暗，先用曝光补偿。
- BW / SE 的棕褐在 32³ 降采样后仍在（SE 中灰 `0.813 / 0.751 / 0.702`）。
- 原生支持 Creative Look 的机型（A7M5、A7R5、A7C2 等）这套只是模拟；不支持的老机型（A7R3 及更早）
  才真正需要它。

## 来源

从 `sony-a7m5-looks` 项目拆出来的独立工具。`sources/` 的 12 张表源自 A7M5 的 ARW
