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
sources/bodies.json 155 个索尼机型与 A7M5 的色空间距离（ΔE / max|M−I| / 建议）
tools/pack_sources.py    把 xmp 源表打包成 CL.data（维护用，平时不用跑）
tools/make_body_report.py 重新生成 bodies.json（维护用，需要 rawpy 和一张样片）
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

## 跨机型色彩换算（可选，默认关闭）

12 张源表是在 **A7M5 的白平衡后线性相机 RGB** 上标定的。别的机身记录同一片颜色时会得到不同的三元组，
所以不换算时，表读到的其实是"另一套坐标"——这也是为什么老机身套上去会偏。两个空间的关系就是
ForwardMatrix 的比值（两者都映射到 XYZ D50，可以相消）：

```
M = inv(FM_A7M5) @ FM_this_body          按行归一（行和 1，保证中性仍是中性）
```

A7R3 → A7M5 实测：

```
[[ 1.1360 -0.2233  0.0873]
 [ 0.0704  0.9310 -0.0014]
 [ 0.0575  0.0122  0.9303]]        条件数 1.34，M·(1,1,1) = (1,1,1)
```

意思是：A7M5 记录一个纯红时会带上 7% 绿、6% 蓝，A7R3 不会；M 把这点差异补回来。

### 放哪儿：底座，还是表里

| 方式 | 做法 | 优点 | 缺点 |
| --- | --- | --- | --- |
| **底座（默认）** | `ForwardMatrix = P @ M` | 零重烘；D65 与 A 光源各自折一次，ACR 按实拍白平衡插值，两个光源都对 | 需 LR 接受非标准的 ForwardMatrix（数学已验证，LR 行为未实测） |
| 外观表 | `T_new(v) = T_A(M·decode(v))` | 只用表，路径与影调烘表一致，行为已知 | 只能用一组 M（M1），色温偏离大时残差变大（M1 与 M2 差 0.113） |

原理上是同一件事：ACR 的工作 RGB = `inv(P) @ FM @ camera`，把 FM 换成 `P@M`，工作 RGB 就等于
`M·camera`——表就"以为"自己看到的是 A7M5 的读数。两条路线实测成品差：均值 **0.09/255**，p99 0.47。

### 该不该开

这张表**已经算好放进 `sources/bodies.json`**（155 个机型），工具每次生成都会读它：界面在机型下方
显示一行蓝色提示，命令行在日志第二行打印同样内容，取值是 `ΔE 均`、`p95`、`max|M−I|` 和一句建议。

数值是怎么来的：拿一张实拍图（A7R3，`_DSC6885.ARW`）采样 4 万个像素，把 ST 表在"本机坐标"和
"M 换算后坐标"各求一次，再比 Lab 的 ΔE76。推荐阈值：**ΔE 均 < 1 不必开；1–2.5 可选；> 2.5 建议开**；
`max|M−I| > 0.5` 时提示降到 0.6–0.8 强度（色域外裁切明显）。

重算（换了样片或想改判定阈值时）：

```bash
python tools/make_body_report.py --raw YOUR.ARW        # 需要 rawpy
```

摘几行看规模：

| 机型 | max\|M−I\| | ΔE 均 | ΔE p95 |
| --- | --- | --- | --- |
| A7R5 / A7C2 / A7M4 / A1 II | 0.06–0.09 | **0.5–1.0** | 1.1–2.6 |
| A7R4 / A7R3 / FX3 / A6700 / ZV-E10 | 0.09–0.22 | 1.3–1.7 | 3.0–4.2 |
| A7M3 / A6400 | 0.09–0.12 | 1.9–2.0 | 4.8–5.2 |
| A9 | 0.20 | **4.0** | 10.4 |
| A6000 / NEX-7 | 0.95 | **11–12** | 24–26 |

JSON 里每条长这样，`advice` 是 `skip` / `optional` / `yes` / `yes-soft` 之一：

```json
"Sony ILCE-7RM3": { "max_mi": 0.223, "de_mean": 1.46, "de_p95": 3.71, "advice": "optional" }
```

文件只有 18 KB，`sources/` 里和 `CL.data` 并列；缺这个文件时工具照常工作，只是不显示那行提示。

界面里是第 2 区那个勾选框（强度默认 1.0，方式默认「底座」）。命令行：

```bash
look_port.py --model "Sony ILCE-7RM3" --transfer 1.0                  # 折进底座
look_port.py --model "Sony ILCE-9"    --transfer 1.0 --transfer-rebake # 烘进表
look_port.py --model "Sony ILCE-7RM3" --transfer 0.6                  # 六成强度，防饱和色过冲
```

### 三个前提

1. **3×3 只是线性近似**。光谱差异不是线性的，饱和色仍会偏；A7R3 上试过，红墙会被推得更粉，所以留了
   强度参数。真要严谨，得双机同光源拍同一张色卡，拟合 3×3 + 残差表。
2. **线性值必须按各机 sensor clip 归一**（`0.515625 × (white − black)`）。A7R3 / A7R5 / A7M5 都是
   14 位、白电平 16383，直接可用；12 位机身（A6000 等）要先做 4× 缩放。
3. **目标机 = A7M5 时自动跳过**（M 就是单位矩阵）。

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
| `--transfer` | 跨机型色彩换算强度 0–1，默认 **0（关）**，推荐 1.0 |
| `--transfer-rebake` | 换算写进 12 张表（默认写进底座 ForwardMatrix） |
| `--dro` | 影调模仿的 DRO 强度 0–5，默认 **0（关）**，推荐 3 |
| `--ev` / `--toe` | 曝光微调（EV）/ 黑位裁切（线性 0..1），默认均 0；推荐 0.003 |
| `--install` / `--clean` | 装进 Lightroom / 顺手清理共享目录同名文件 |

## 验证

- **表内容**：不启用影调时，生成的 12 张表与拆分前的发布版本逐点一致（max diff 0.000000），底座
  DCP 除 `ProfileCopyright` 文案外标签相同。
- **影调模仿**：启用后底座恒等（2 点曲线）、12 张表带增益。实测亮度与旧做法一致（ΔL\* +3.31 vs
  +3.29），饱和度不掉（ΔC\* +0.39 vs −1.81）。BW 中灰仍严格中性（三通道差 0.0000），SE 的棕褐比值
  （0.818/0.756/0.707）保持。
- **机型差异数据**：`sources/bodies.json` 覆盖 155 个 Sony 机型；界面切换机型时提示实时更新
  （A7R3 → 可选、A9 → 建议开、A7R5 → 不必开、A6000 → 建议 0.6–0.8）。
- **跨机型换算**：`ForwardMatrix` 与 `P@M` 的差为 4.9e-7，模拟 ACR 得到的工作 RGB 与 `M·camera`
  的差为 6.2e-7；「底座」与「表」两条路线的成品差均值 0.09/255、最大 1.4/255。
- **机型识别**：4 个真实 ARW 测过（含合成色卡），A7R5 / ZV-E10 / 未知机型三条路径都通。
- **耗时**：不启用影调 1 秒；启用（12 个表各做一次 32k 点预合成）约 4 秒。

## 已知限制

- 跨机型换算默认**关闭**，且 3×3 只是线性近似；ΔE 均 < 1 的机型（A7R5/A7C2/A7M4/A1 II）不值得开。
- 换算写进底座这条路依赖 Lightroom 接受非标准 `ForwardMatrix`，尚未在 LR 里实测；若无效，
  改用 `--transfer-rebake`（行为已知）。
- 12 位机身（A6000 等）需要先把线性值按 sensor clip 重新归一，工具目前按 14 位满幅假设。
- 亮度基准沿用 A7M5 的 ACR 编码（中灰 6422）。若整组偏亮偏暗，先用曝光补偿。
- BW / SE 的棕褐在 32³ 降采样后仍在（SE 中灰 `0.813 / 0.751 / 0.702`）。
- 原生支持 Creative Look 的机型（A7M5、A7R5、A7C2 等）这套只是模拟；不支持的老机型（A7R3 及更早）
  才真正需要它。

## 来源

从 `sony-a7m5-looks` 项目拆出来的独立工具。`sources/` 的 12 张表源自 A7M5 的 ARW
