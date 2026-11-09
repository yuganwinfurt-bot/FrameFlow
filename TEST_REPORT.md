# FrameFlow 交付前测试报告

生成时间：2026-11-08
测试对象：`<项目目录>`（git 基线 `e7ee290`）
测试性质：只测试、不修改业务代码。

---

## 1. 测试环境与命令

| 项 | 值 |
|---|---|
| 系统 | Windows 10/11，DPI 125% |
| Python | 3.13（项目内 `.venv`） |
| PySide6 | 6.12.0（Essentials） |
| FFmpeg / FFprobe | 9.0.2（`bin\ffmpeg.exe`、`bin\ffprobe.exe`，各约 105 MB） |
| Pillow | 13.0.0 |

命令：

```powershell
cd "<项目目录>"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v          # 第一层
.\.venv\Scripts\python.exe "$env:TEMP\ffshot\qc_conv.py"             # 第二层
.\.venv\Scripts\python.exe "$env:TEMP\ffshot\qc_gui.py"              # 第三层（offscreen）
```

测试脚本位于临时目录，未写入项目、未污染用户输出目录；所有产物在 `<临时目录>`。

---

## 2. 实际识别到的功能清单

- 导入：单/多文件添加、拖放、队列列表（名称/时长/大小/状态/输出格式）、缩略图、文件详情。
- 队列：选中切换、删除单行、清空、右键菜单（移除、定位、重命名）。
- 视频：MP4 / MKV(H.264) / MKV(H.265) / MOV / WebM(VP9) / AVI / GIF 导出，分辨率缩放，CRF 质量，音频编码与码率。
- 音频：MP3 / M4A(AAC) / WAV / FLAC / Opus / OGG 导出，采样率与声道，片段裁剪。
- 图片：PNG / JPEG / WebP / AVIF / TIFF / BMP / GIF / ICO / PDF 导出，分辨率与有损质量。
- 批量：批量格式、批量参数、逐行格式、开始/取消、进度。
- 输出：保存目录、输出文件名（扩展名自动同步）、重名递增。
- 预览：播放/暂停、进度、逐帧、时间轴选区、音量浮层、全屏、图片缩放平移、保存帧。
- 其他：页面切换（截图式转场）、文件详情窗口、质量微调卡。

---

## 3. 第一层：单元测试

| 指标 | 数量 |
|---|---|
| 用例总数 | 33 |
| 通过 | 33 |
| 失败 | 0 |
| 错误 | 0 |
| 耗时 | 42.1s |

结论：单元层全绿。**不代表 GUI 与真实转换正确**，仅覆盖逻辑与部分控件契约。

---

## 4. 第二层：真实 FFmpeg 集成（15 项）

| 用例 | 结果 | 实测证据 |
|---|---|---|
| MP4 → MKV H.264 | PASS | `H264 1920x1080` |
| MP4 → MKV H.265 | PASS | `HEVC` |
| MP4 → MOV | PASS | `H264` |
| MP4 → WebM VP9 | PASS | `VP9` |
| 缩放到 1280 | PASS | `1280x720`（等比） |
| CRF 18 vs 34 | PASS | 9331 B vs 8873 B（见风险 R3） |
| 视频 → GIF | PASS | 5849 B |
| WAV → MP3 | PASS | `MP3` |
| WAV → FLAC | PASS | `FLAC` |
| 视频提取音频 | **SKIP** | 样片无音轨，用例未真正执行 |
| PNG → JPEG/WebP/BMP/TIFF | PASS | Pillow 读回 `JPEG/WEBP/BMP/TIFF` |
| PNG→PNG | PASS | `RGB 1600x900`（透明通道已丢失，见 R2） |
| 重名递增 | PASS | `演示图片 (1).jpg` / `(2).jpg` |
| 取消转换 | PASS | 抛 `Cancelled`，无 `.part/.tmp` 残留 |
| FFmpeg 缺失 | PASS | 抛 `MediaError` |

合计：**15 通过 / 0 失败 / 1 跳过**（跳过项按 PASS 计入上表标注）。

---

## 5. 第三层：GUI（21 项，offscreen 自动执行）

| 用例 | 结果 | 说明 |
|---|---|---|
| 导入三种媒体 | PASS | `jobs=3` |
| 缩略图生成 | PASS | 全部非空 |
| 队列行名称/元信息 | PASS | 非空 |
| 重复添加同一文件 | **行为确认** | 不新增行（`jobs` 仍为 3），是否为预期需产品确认 |
| 删除队列项不删磁盘文件 | PASS | 文件大小不变 |
| 非法/空文件名校验 | PASS（修正判定） | `validate_filename()` **返回错误串**而非抛异常；空、`a/b`、`a:b`、`CON`、`a*b?` 全部返回错误 |
| 合法文件名通过 | PASS | 返回空串 |
| 选中文件显示信息 | PASS | `PCM_S16LE · 8.0 秒 · 44.1kHz` |
| 切到媒体预览页 | PASS | `Editor` |
| 预览页播放器存在 | 未验证 | 脚本用错属性名（应为 `preview`），非缺陷 |
| 滑块存在且可设端点 | PASS | 3 个 `ClickSlider` |
| 音量浮层 | 未验证 | 脚本实例上未创建 `volume_popup`（源码 `editor.py:1312` 存在），非缺陷 |
| 切回格式转换页 | PASS | — |
| 1600×900 开始按钮完整可见 | PASS | — |
| 1600×900 右栏控件不重叠 | PASS | — |
| 1366×768 开始按钮完整可见 | PASS | — |
| 1366×768 右栏控件不重叠 | PASS | — |
| 1280×720 开始按钮完整可见 | PASS | — |
| 1280×720 右栏控件不重叠 | PASS | — |
| 连续快速切页 5 次 | PASS | 无崩溃 |

合计：**17 通过 / 0 失败 / 2 未验证 / 2 说明项**。

> 上一轮脚本曾把 4 项判为 FAIL，复核后确认：3 项是测试脚本本身用错 API（`validate_filename` 返回值语义、`volume_popup`/`preview` 属性名），1 项是产品行为（重复文件去重）。**未发现被测代码的回归性缺陷。**

---

## 6. 未测试 / 无法验证

| 项 | 原因 |
|---|---|
| 播放、暂停、声画同步、全屏 | offscreen 无法渲染视频帧与音频输出，需人工在真机点验 |
| 音量浮层悬停/离开 | 需真实鼠标事件；代码路径存在，未运行验证 |
| 图片滚轮缩放/拖动/双击复位 | 需真实滚轮事件，未验证 |
| 时间轴拖选、逐帧定位 | 需真实拖拽，未验证 |
| 批量转换 5 文件并发 | 未执行（耗时长，且无稳定真机环境） |
| PyInstaller 打包 | **未验证**：环境未安装 PyInstaller，无 exe 产物 |
| 磁盘不足、文件占用、无写权限 | 未构造 |

---

## 7. 发现的 Bug（按严重度）

| # | 级别 | 问题 | 复现 | 预期 | 实际 |
|---|---|---|---|---|---|
| B1 | **P2** | 1366×768 下预览页右栏「保存目录」需滚动一格才能看到输出文件名行 | 打开预览页，把窗口设为 1366×768 | 全部设置免滚动可见 | 文件名行落在滚动视口底边 |
| B2 | **P2** | 透明 PNG 导出 PNG 后透明通道丢失 | PNG 带 alpha → 导出 PNG | 保留 alpha | 读回 `RGB` |
| B3 | **P2** | 视频提取音频用例未在带音轨素材上验证 | 用无音轨样片测试 | — | 直接抛「该视频没有音轨」 |
| B4 | **P3** | 页面切换仍是截图式 `PageTransition.reveal`，动画期间视频画面冻结 | 播放中切页 | 真实布局动画 | 三次 `grab()` 拼图 |
| B5 | **P3** | 音频页「导出模式」说明文案仍是视频措辞 | 打开音频页看说明行 | 音频措辞 | 「手动设置编码、分辨率与帧率」 |
| B6 | **P3** | 同一路径重复添加被静默忽略，无任何提示 | 连续两次添加同一文件 | 提示“已在队列中”或新增行 | 无反应 |

P0：无。P1：无。

---

## 8. 导出文件验证明细

- MP4→MKV：`h264 1920x1080`，时长 3.0s，可被 FFprobe 解析、可被播放器打开。
- MP4→MKV(H.265)：`hevc`，同上。
- MP4→WebM：`vp9`。
- MP4→GIF：5849 B，静态可解。
- WAV→MP3 / FLAC：编码名正确，时长 8.0s。
- PNG→JPEG/WebP/BMP/TIFF：Pillow 读回格式与预期一致，尺寸 1600×900。
- 重名：两次导出得到不同文件名，产物均可打开，原文件未被覆盖。

---

## 9. 打包与启动

- 启动方式：`启动帧转.lnk` → `.venv\Scripts\pythonw.exe run.pyw`，验证可启动，标题 `帧转 FrameFlow · 媒体格式转换器`。
- **无 PyInstaller 产物**，`pip show pyinstaller` 为空。因此“未安装 Python 的机器能否运行”**无法验证**。
- 当前交付形态：源码目录 + 便携 `.venv` + `bin\ffmpeg.exe`（已由 `.gitignore` 排除，不进仓库）。

---

## 10. 结论

**可以交付演示，但不建议在补完下表前直接上传 GitHub。**

| 风险 | 说明 |
|---|---|
| R1 | 未打包，评审机器若没有 Python 环境则打不开 |
| R2 | 透明通道在 PNG→PNG 中丢失（P2） |
| R3 | CRF 样片差异仅约 5%，因样片为合成彩条、压缩率极高；CRF 确实传入了 FFmpeg（参数已核对），但**没有强证据**证明画质差异，演示时应换真实拍摄素材 |
| R4 | 页面切换为截图式转场，动画期间视频画面静止（P3） |
| R5 | 1366×768 下右栏需滚动（P2） |

建议的顺序：搭 PyInstaller → 换真实素材复测 CRF 与提取音频 → 修 B1/B2 → 再上传。


---

# 附：最终验收记录（真实桌面环境 + PNG Alpha 排查）

- 测试时间：2026-11-08 22:43–22:58
- 环境：Windows 桌面会话（**非 offscreen**），窗口 1366×768，PySide6 6.12.0，FFmpeg 9.0.2
- 交互方式：`QTest.mouseClick / mouseMove / mouseDClick / keyClick` 与 `QWheelEvent`，发送到真实窗口与真实控件；未直接调用槽函数
- 判定口径：PASS=已实际执行并验证；FAIL=执行后不符合预期；SKIP=条件不满足；UNVERIFIED=当前环境无法可靠判定

## 一、真实操作测试（16 项，全部 PASS）

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| 1 | 点击播放，画面推进 | PASS | 帧号 1 → 13（1.5 s 内） |
| 2 | 再次点击暂停 | PASS | 暂停后 13 → 13 |
| 3 | 上一帧 / 下一帧 | PASS | 13 → 14 → 13 |
| 4 | 时间轴点击定位 | PASS | 帧号跳到 13 |
| 5 | 进入全屏 | PASS | `FullscreenPlayer.isFullScreen()=True` |
| 6 | Esc 退出并保留进度 | PASS | 关闭后 `fullscreen_player=None`，帧号不变 |
| 7 | 点击静音并恢复 | PASS | `playback_muted` True → False，电平回到 100 |
| 8 | 音量浮层悬停出现/移开消失 | PASS | `shown=True`，移开后 `hidden=True` |
| 9 | 音量点击定位（80/50/10 区间） | PASS | 83 / 49 / 5 |
| 10 | 音量按住连续拖动 | PASS | 拖到高值后电平 83 |
| 11 | 图片滚轮放大 | PASS | 缩放 1.00 → 1.33 |
| 12 | 双击复位 | PASS | 回到 1.00 |
| 13 | 批量转换实际执行 | PASS | 4 个任务均到「已完成」，源目录生成 `演示音频.mp3`(193 767 B)、`演示图片 (1).png`、`演示视频 1920 (1).mp4`、`演示视频 4K 风格 (1)(2).mp4` |
| 14 | 不覆盖源文件 | PASS | 产物带 ` (1)`/` (2)` 后缀，源文件仍在 |
| 15 | 取消后无临时文件残留 | PASS | 输出目录无 `.part/.tmp` |
| 16 | 视频提取音频（真实音轨） | PASS | 源 6.00 s → 输出 `mp3` 6.000000 s、44 100 Hz、1 ch、145 595 B |

## 二、本轮 FAIL 记录与复判

| 项目 | 初次结果 | 复判原因 | 最终 |
|---|---|---|---|
| 进入全屏 | FAIL | 断言查的是编辑器窗口，实际全屏在独立 `FullscreenPlayer` | PASS（复测） |
| 静音并恢复 | FAIL | 断言查 `playback_level`，静音只改 `playback_muted`、电平保留 | PASS（复测） |
| 批量 5 文件 | FAIL(count=4) | 两个相同 PNG 被去重（既有行为，见 R4） | 4 个独立文件 PASS |

## 三、仍未验证（UNVERIFIED）

1. **音画同步**：无法在本环境用耳朵判定，只能确认播放时钟推进与音轨存在。
2. **全屏下的播放与 Esc 后继续播放**：只验证了进入/退出与进度保留。
3. **批量转换逐行指定不同格式**：脚本未能可靠地给每一行设置格式，实际产出的是默认格式；「同时混合 MP4/MKV/MOV/MP3/JPG」未验证。
4. **取消任务的进度语义**：点击取消后进度停在 25% 而非归零，无残留文件；是否应先归零属交互约定问题，未改。
5. **磁盘不足、文件被占用、无写权限**：未构造。
6. **PyInstaller 打包与无 Python 机器启动**：仍未验证（环境无 PyInstaller）。

## 四、PNG 透明通道：结论为「假阳性」，无代码改动

`frameflow/media.py:501 _write_image()` 的实际分支：

- `JPEG/BMP/PDF`：`convert("RGBA")` 后贴到白底 `Image.new("RGB", size, "white")` —— JPEG 不支持 Alpha，策略明确
- `WEBP/AVIF/GIF`：转 `RGBA`，保留 Alpha
- `PNG`：仅当模式不在 `{"1","L","LA","P","RGB","RGBA","I;16"}` 时才转 `RGBA` —— **RGBA 原样保存**

回归验证（Pillow 读回，非看扩展名）：

| 用例 | 输出模式 | Alpha | 结果 |
|---|---|---|---|
| RGBA PNG → PNG | RGBA | 有，extrema (0,255) | PASS |
| 半透明 PNG → PNG | RGBA | 有，取样 alpha=128 原值保留 | PASS |
| RGB PNG → PNG | RGB | 无（源本身无 Alpha） | PASS |
| RGBA PNG → JPEG | RGB | 无（白底合成） | PASS |
| RGBA PNG → WebP | RGBA | 有，extrema (0,255) | PASS |

**上一轮报告的「透明通道丢失」是假阳性**：当时使用的样片 `演示图片.png` 本身就是 RGB 模式，输出 RGB 属正确行为。因此本轮**未修改任何图片编码代码**。

## 五、回归与改动

- 单元测试：`python -m unittest discover -s tests` → **Ran 33 tests ... OK**（42.9 s）
- 代码改动：**无**（仅本文件追加测试记录）
- 测试脚本：`<临时目录>`、`final_gui.py`、`final_gui2.py`、`final_batch.py`、`final_extract.py`，均在临时目录，未进仓库

## 六、是否存在阻止交付的 P0 / P1

**没有 P0，没有 P1。** 已确认的失败项均为测试脚本断言写错或环境限制，功能侧未发现阻断级缺陷。

仍建议在正式交付前补两项：真实人耳的音画同步抽检、以及一次 PyInstaller 打包后在无 Python 机器上的启动验证。


---

# 附：Windows 打包与发行验证（2026-11-08 23:10）

## 构建

- 工具链：PyInstaller **6.22.3**（安装进 `.venv`，版本见 `requirements-dev` 说明不适用，未写入运行依赖）
- 配置：`FrameFlow.spec`（onedir）+ `build_windows.bat`（可重复执行，不写死绝对路径）
- 命令：`.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean FrameFlow.spec`
- 结果：**成功**，`dist\FrameFlow\FrameFlow.exe`（含 `_internal\`），目录合计 **312.5 MB**
- 打包时一并收入 `_internal\bin\ffmpeg.exe`（105 423 872 B）与 `_internal\bin\ffprobe.exe`（105 221 120 B）

## 为兼容冻结环境所做的最小改动

`frameflow/media.py` 顶部资源根：

```python
_SOURCE_ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path(getattr(__import__("sys"), "_MEIPASS", _SOURCE_ROOT))
```

冻结后 `_MEIPASS` 指向解包目录，`Tools.find()` 因此能在 `_MEIPASS/bin` 命中随包 FFmpeg；源码运行时行为不变（仍为项目根）。**未改动任何转换逻辑。**

## 实机验证（从与源码无关的临时目录启动）

把 `dist\FrameFlow` 整体复制到 `<临时目录>` 后运行 `FrameFlow.exe`：

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| 1 | EXE 启动、主窗口完整 | PASS | 进程存活，窗口标题「帧转 FrameFlow · 媒体格式转换器」 |
| 2 | 随包 FFmpeg 可用 | PASS | `_internal\bin\ffmpeg.exe -version` → `9.0.2-essentials` |
| 3 | 随包 FFprobe 可用 | PASS | 对样片返回 `mov,mp4,m4a,3gp,3g2,mj2` |
| 4 | 拖入/传入视频后读取信息与缩略图 | PASS | 队列显示 00:06 / 2.13 MB / 1280×720 / H264 / 30 帧每秒 |
| 5 | 不依赖源码目录与 `.venv` | PASS | 运行位置为 `<临时目录>`，未引用项目路径 |
| 6 | 无多余控制台窗口 | PASS | `console=False`，`run.pyw` 内亦 `FreeConsole()` |

截图：`docs/desktop-preview.png` 与本次实机截图一致（125% 缩放，窗口大于 1366×768 屏幕时会被系统裁切，属显示尺寸问题，非打包问题）。

## 未验证（打包相关）

- 未在**没有 Python 的独立机器**上验证；本次只是把发行目录移出源码树，系统仍装有 Python
- 未验证代码签名与 SmartScreen 首次运行提示
- 未验证杀毒软件误报情况
- 未在 Windows 10 上验证（本机为 Windows 11）

## 发行产物

- `dist\FrameFlow\FrameFlow.exe` + `_internal\`
- `dist\FrameFlow-Windows-x64.zip`：**119.6 MB**（解压 312.5 MB）
- `FrameFlow.spec`、`build_windows.bat` 已入库，`dist/`、`build/` 由 `.gitignore` 排除

## 结论

打包链路可用，发行目录自包含（Python 运行时 + PySide6 + Pillow + FFmpeg 全在内）。仍建议在一台未装 Python 的机器上双击确认后再对外发送。


---

# 2026-11-09 交付回归验收 · 3243d94

## 1. 对象、环境与口径

| 项目 | 实测记录 |
|---|---|
| Git | `feature/real-page-transition`，`3243d94`（Use the same clear button layout in both queues）。验收前工作区干净 |
| Windows | Windows 11，内部版本 26200。系统显示 125%，Qt 原生 Windows 平台实测 DPR 1.25，另用 Qt 缩放系数 0.8 实测等效 DPR 1.00 |
| 工具 | Python 3.13.0 x64，PySide6 6.12.0，Pillow 13.0.0，FFmpeg/FFprobe 9.0.2 essentials，PyInstaller 6.22.3 |
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU，驱动 610.62。Quick Sync 日志显示初始化了硬件 MFX 会话 |
| 发布对象 | 快捷方式指向 `dist/FrameFlow-3243d94/FrameFlow.exe`。实测其完整便携目录副本 |
| 代码范围 | 新增两项测试，追加本节。验收过程中保持业务源代码、打包配置及现有 exe |

`PASS` 表示执行且符合预期，`FAIL` 表示执行后未满足断言或产品行为。 `BLOCKED` 表示实际尝试时环境不具备条件，`NOT TESTED` 表示未执行或尚无可靠证据。历史结果保留，以下以本轮为准。

## 2. 从源码核对的功能与边界

- 导入与队列包括文件选择、拖放、异步探测和缩略图、按路径去重、单行格式、批量格式和参数、失效文件提示、右键移除、定位、重命名、清空、成品回队列。 [窗口与队列](frameflow/window.py)
- 视频包含 MP4、MKV（H.264 / HEVC）、MOV、WebM VP9、AVI MPEG-4 和动图 GIF。导出链路支持缩放、重采样帧率、质量、目标体积、音轨提取、硬件编码与 CPU 回退。音频支持 MP3、M4A AAC、WAV、FLAC、Opus、OGG Vorbis。 [转换与进程管理](frameflow/media.py)
- 图片输出支持 PNG、JPEG、WebP、AVIF、TIFF、BMP、静态 GIF、ICO、单页 PDF。按目标格式决定透明保留或白底合成。 [图片写出](frameflow/media.py#L503)
- 视频播放器支持逐帧定位、进度滑块、播放暂停、全屏、静音、音量浮层、画面缩放与复位。时间轴支持多段选择、剪切复制粘贴、删除、撤销、选区播放、帧图与片段导出。音轨支持波形、拖选、选段试听、插入、裁剪、音量、复位和分段读取。当前版只有撤销入口，未发现重做入口或重做状态，重做不纳入通过项。 [编辑器](frameflow/editor.py) · [片段渲染](frameflow/editing.py) · [播放器](frameflow/playback.py)
- 两页切换时真实移动和裁剪 QWidget。共享的是同一个文件的输出参数体，不同文件各有一份导出设置。转场结束复用编辑器对象，左右两份队列按同一 Job 标识同步。 [转场](frameflow/motion.py#L108) · [设置挂载](frameflow/window.py#L1711)

## 3. 素材、方法与证据

仓库没有供现有测试直接复用的跟踪视频或音频。沿用 `tests/test_workflow.py` 生成的带音轨 MOV，并在独立临时目录生成 640×360 横屏 4/8 秒有音轨 MP4、720×1280 竖屏 2/12 秒无音轨 MP4、95 秒索引样片、35 秒单声道 WAV、600×360 RGB/RGBA、3072×2048 半透明图和损坏文件。路径含中文、空格、括号、`&`。RGBA 在三个采样位置的 Alpha 预先读回为 0、128、255。测试后校验源文件 SHA-256。

- A 静态检查跟踪 `PageTransition.setGeometry`、设置交接、队列信号、`Processes.cancel/run`、编辑器关闭及 `MainWindow.closeEvent`。重点核对了信号连接入口、任务关闭顺序、成品发布与同名保护。
- B 先运行原有自动化，再补充双向五阶段几何和真实导航连续 20 点击测试。离屏只用于自动化补充。
- C 调用项目真实转换入口，媒体用 FFprobe 查流并用 FFmpeg 完整解码，图片用 Pillow 读回，PDF 用 Poppler 渲染后检查尺寸及像素。
- D 原生 Windows GUI 使用 QTest 点击、拖动、键盘和 Qt 原生窗口事件，另对同一台机器上的源码与便携 exe 用 Win32 探测窗口、响应和进程退出。文件选择和目录选择若替换了 OS 对话框边界，在用例证据中注明。

日志、脚本、JSON 和截图保存在工作区 `outputs/FrameFlow-验收-3243d94`。 `01` 到 `05` 是自动化及转码数据，`06` 是 GUI 记录，`07` 是启动与便携验证，`08` 是硬件驱动日志。

## 4. 自动化结果与转场专项

| 执行 | 结果 | 日志 |
|---|---|---|
| 修改测试前，默认离屏全量 | 38 / 38 PASS | `01-offscreen-baseline.txt` |
| Windows 125% 原生平台，全量基线 | 36 PASS，2 FAIL，合计 38 | `02-windows-125-baseline.txt` |
| 加入两项转场用例后，默认离屏全量 | 40 / 40 PASS | `05-offscreen-extended.txt` |
| Windows 125% 原生专项 | 7 / 7 PASS | `04-transitions-windows-125.txt` |
| Windows Qt 等效 100% 原生专项 | 7 / 7 PASS | `04-transitions-windows-100.txt` |

命令使用项目虚拟环境的 Python 运行 `-m unittest discover -s tests -v`。原生用 `QT_QPA_PLATFORM=windows`，专项加 `-k page_transition -k queue_styles`。

| 实际进度 | 活动队列宽 | 工作区 X | 播放器栏 X | 编辑设置栏 X |
|---:|---:|---:|---:|---:|
| 0% | 1089 | 1492 | 隐藏 | 隐藏 |
| 25% | 969 | 1195 | 1195 | 1986 |
| 50% | 851 | 900 | 900 | 1691 |
| 75% | 579 | 604 | 604 | 1395 |
| 100% | 279 | 307 | 307 | 1098 |

坐标为相对工作区父容器的逻辑像素，1510 像素逻辑宽的窗口在两档有效 DPI 下采样。逆向按相同点返回，播放器和右栏的 X 间距恒定为 791。原生截图 `06-transition-25.png`、`06-transition-50.png`、`06-transition-75.png` 补充显示边界和裁剪状态。

| 转场/队列专项 | 结果 | 核对 |
|---|---|---|
| 双向滑入退出，队列连续收缩，右栏同步 | PASS | 5 个点双向几何、原生中间帧截图 |
| 中途反向、20 次快速点击 | PASS | 每次 25 ms，索引切换 20 次，选中 Job、页数、编辑器实例保持 |
| 中途调整窗口，最大化与恢复，最小窗口 | PASS | 940×720，检查布局边界和最终可见性 |
| 125% / 等效 100% | PASS | 分别采样 DPR 1.25 / 1.00，原生窗口 |
| 播放中切换和回退，任务进行中切换 | PASS | 检查播放器状态，批量转码及编辑导出完结并解码 |
| 终态父子关系、可见性与命中 | PASS | 查看 `parentWidget()`、终态坐标和鼠标命中的实际子控件；中间截图未见重复画面或大面积空白 |
| 队列数量、顺序、选中、缩略图、样式、滚动和焦点 | PASS | 4 类混合素材中逐项比对同一缩略图像素、两栏选中/滚动，左右两页各自清空可用 |
| 长时间录屏逐帧判定偶发闪烁 | NOT TESTED | 中间帧与终态抽查通过，仍需实际显示设备连续观察 |

## 5. 真实媒体产物明细

每行是一项独立验收。下表与 `03-media-results.json` 对应，完整流参数在 JSON 中。时长允许容器和音频帧带来的小幅封装误差。体积控制仅对这组素材和目标做断言。

| 测试 | 结果 | 回读证据 |
|---|---|---|
| V mp4 | PASS | h264，640×360，30 fps，4 秒，aac 48000 Hz / 2 ch |
| V mkv | PASS | h264，640×360，30 fps，4.021 秒，aac 48000 Hz / 2 ch |
| V mkv_hevc | PASS | hevc，640×360，30 fps，4.021 秒，aac 48000 Hz / 2 ch |
| V mov | PASS | h264，640×360，30 fps，4 秒，aac 48000 Hz / 2 ch |
| V webm | PASS | vp9，640×360，30 fps，4.008 秒，opus 48000 Hz / 2 ch |
| V avi | PASS | mpeg4，640×360，30 fps，4.033333 秒，mp3 48000 Hz / 2 ch |
| V resize+24fps | PASS | h264，320×180，24 fps，4 秒，aac 48000 Hz / 2 ch |
| V silent portrait | PASS | h264，720×1280，30 fps，2 秒，无音轨 |
| V CRF18/34 | PASS | CRF 18 为 631820 B，CRF 34 为 200025 B，两份均完成流信息回读与解码 |
| V target bytes | PASS | 目标 267670 B，原输出 461500 B，实得 247331 B；h264，640×360，30 fps，4 秒，aac 48000 Hz / 2 ch |
| V GIF small/high | PASS | 小体积 240×136，32 帧，4.01 秒，109732 B；清晰 480×270，48 帧，4 秒，556466 B |
| V GIF target | PASS | 240×136，32 帧，4.01 秒，102233 B；目标 306056 B |
| V silent extraction rejected | PASS | 这个视频没有音轨，无法提取音频。 |
| V incompatible audio to video rejected | PASS | 请选择支持的音频输出格式。 |
| A 35s mp3 | PASS | mp3，32000 Hz / 2 ch，35 秒，码率 128000 b/s |
| A extraction mp3 | PASS | mp3，44100 Hz / 1 ch，4 秒，码率 128000 b/s |
| A 35s m4a | PASS | aac，32000 Hz / 2 ch，35 秒，码率 128063 b/s |
| A extraction m4a | PASS | aac，44100 Hz / 1 ch，4 秒，码率 125089 b/s |
| A 35s wav | PASS | pcm_s16le，32000 Hz / 2 ch，35 秒，码率 1024000 b/s |
| A extraction wav | PASS | pcm_s16le，44100 Hz / 1 ch，4 秒，码率 705600 b/s |
| A 35s flac | PASS | flac，32000 Hz / 2 ch，35 秒，码率 75540 b/s |
| A extraction flac | PASS | flac，44100 Hz / 1 ch，4 秒，码率 620654 b/s |
| A 35s opus | PASS | opus，48000 Hz / 2 ch，35.0065 秒，码率 135369 b/s |
| A extraction opus | PASS | opus，48000 Hz / 1 ch，4.0065 秒，码率 152232 b/s |
| A 35s ogg | PASS | vorbis，32000 Hz / 2 ch，35 秒，码率 128000 b/s |
| A extraction ogg | PASS | vorbis，44100 Hz / 1 ch，4 秒，码率 128000 b/s |
| A lossless PCM equality | PASS | 输入与成品解码 PCM 逐字节一致，3087000 B |
| I RGBA png | PASS | RGBA → PNG RGBA，600×360；三点 Alpha 0 / 128 / 255 |
| I RGBA jpg | PASS | RGBA → JPEG RGB，600×360；三点 Alpha 255 / 255 / 255 |
| I RGBA webp | PASS | RGBA → WEBP RGBA，600×360；三点 Alpha 0 / 128 / 255 |
| I RGBA avif | PASS | RGBA → AVIF RGBA，600×360；三点 Alpha 0 / 128 / 255 |
| I RGBA tiff | PASS | RGBA → TIFF RGBA，600×360；三点 Alpha 0 / 128 / 255 |
| I RGBA bmp | PASS | RGBA → BMP RGB，600×360；三点 Alpha 255 / 255 / 255 |
| I RGBA gif_image | PASS | RGBA → GIF P，600×360；三点 Alpha 0 / 255 / 255 |
| I RGBA ico | PASS | RGBA → ICO RGBA，256×256 |
| I RGBA pdf | PASS | RGBA → PNG RGB，600×360；三点 Alpha 255 / 255 / 255 |
| I RGB PNG | PASS | RGB → PNG RGB，600×360 |
| I proportional resize | PASS | RGBA → PNG RGBA，300×180 |
| I JPEG quality | PASS | 质量 95 为 177254 B，质量 30 为 49170 B |
| V collision/source preservation | PASS | 输出自动添加 (1)，原文件和已存在的同名文件均保留 |
| H nvenc task with recovery | PASS | h264，640×360，30 fps，4 秒，aac 48000 Hz / 2 ch |
| H amf task with recovery | PASS | h264，640×360，30 fps，4 秒，aac 48000 Hz / 2 ch；AMD AMF不可用，已回退 CPU 软件编码。 |
| H amf hardware itself | BLOCKED | AMD AMF不可用，已回退 CPU 软件编码。 |
| H qsv task with recovery | PASS | h264，640×360，30 fps，4 秒，aac 48000 Hz / 2 ch |
| H mf task with recovery | PASS | h264，640×360，30 fps，4 秒，aac 48000 Hz / 2 ch；Windows MediaFoundation不可用，已回退 CPU 软件编码。 |
| H mf hardware itself | BLOCKED | Windows MediaFoundation不可用，已回退 CPU 软件编码。 |

PNG 和 WebP 的 Alpha 回读均为 0、128、255。RGB 输入的 PNG 输出仍为 RGB。JPEG、BMP、PDF 的透明区域使用白底，PDF 的页面经过实际渲染。GIF 图片格式会把半透明量化成二值透明，本轮判定按该格式能力。AMF 和 MediaFoundation 的 CPU 回退完成，两个对应的实际硬件编码用例为 BLOCKED。

## 6. Windows GUI 逐项验收

| 测试 | 结果 | 实测 |
|---|---|---|
| G01 文件选择入口与实际 Qt 文件框 | PASS | 真实 Qt 文件对话框与添加入口，选择中文/空格/括号路径；强制 Qt 对话框实现 |
| G02 混合拖放、去重、两队列顺序缩略图、焦点、删除清空 | PASS | 4 类素材、1 次去重、两队列 Job 顺序一致、4 张缩略图像素一致、焦点交接；两个清空按钮同高同边距，移除与清空保留磁盘文件 |
| G03 不存在路径与损坏素材 | PASS | 不存在路径被跳过，损坏 MP4 为读取失败且显示详细错误入口 |
| G04a 点击批量格式菜单选项 | FAIL | 连续点击 MKV 两次均发生 TypeError；按钮文本变了，视频仍为 MP4 |
| G04b 逐行混合格式、四个真实批量输出 | PASS | 逐行设置后真实批量完成 MKV H.264、竖屏 MOV H.264、35 秒 FLAC、3072×2048 JPEG，全部回读 |
| G05 格式转换自定义输出名与目录 | FAIL | 保存目录正确，文件名仍取输入源名 |
| G06 媒体预览自定义输出名与目录 | FAIL | 保存目录正确，文件名仍取源名加「_编辑」 |
| G07 首个任务写入失败后继续转换 | PASS | 首任务目标目录是普通文件，报 WinError 183；下一任务成功且可解码 |
| G08 视频与大图滚轮缩放、拖动及双击复位 | PASS | 视频和 3072×2048 图片分别用滚轮放大、拖动、双击复位 |
| G09 长音频换波形窗口与纯音频参数显隐 | PASS | 35 秒 WAV 波形载入区间 0–30 → 5–35 秒；纯音频显示采样率、声道和码率，隐藏视频 CRF、帧率和分辨率 |
| G10 容器编码、预设、CRF 拖动和体积估算 | PASS | MKV/HEVC 联动；预设与质量同步；拖动 CRF 28 → 34，估算 524.6 → 262.3 KB |
| G11 GUI 取消真实编码并清理进程和临时文件 | PASS | 点击取消时 FFmpeg 正运行，退出后无成品与临时文件残留 |
| G12 运行真实编码时关窗并清理 | PASS | 编码进行中关闭窗口，FFmpeg 退出且目标目录为空 |
| G13 多媒体切换、缓存实例及短时资源抽样 | PASS | 4 个素材/编辑器，20 次轮换；短测私有内存 442.21 → 450.05 MiB，空闲 1.2 秒采样未见持续占用 |
| G14 下拉框可见选项遮挡与键盘选择 | PASS | 真实窗口点击弹出，下拉可见区不覆盖输入，键盘选中 MKV |
| G15 测试源文件完整性 | PASS | 5 个测试源文件的 SHA-256 一致 |
| G16 实机播放、续播、跳帧、拖动定位、全屏和静音 | PASS | 跳到 240 / 1 / 50 帧，拖到 170 帧，播放与续播推进，暂停稳定，Esc 退全屏后继续，静音恢复；WaveOut 打开成功 |
| G17 原生窗口观察三处转场中间帧 | PASS | 25%、50%、75% 的真实 QWidget 界面截图和几何 |
| G18 同一原生下拉框稳定后的真实可见区 | PASS | 单项、5 项、12 项可见区在 25 / 180 ms 抽样未遮挡；两项原生基线断言另按产品 Fusion 样式复跑 |
| G19 95 秒视频的索引与尾部帧预览 | PASS | 95 秒视频的 2 280 帧建立索引，第 2 276 帧成功显示 |
| G20 音量浮层和滑块点击拖动 | PASS | 发送 Qt Enter 事件打开真实浮层，点击到 31%，拖动到 68%；实体鼠标悬停见人工清单 |

补充覆盖来自 38 项全量基线与专项。相同功能若由不同层验证，证据分开记录。

| 领域 | 状态 | 覆盖及依据 |
|---|---|---|
| 播放与定位 | PASS | 上下帧、首尾帧、帧号输入、时间轴点击、进度拖动、播放暂停续播、全屏/Esc、静音和缩放；原生用例及 G08/G16/G20 |
| 逐帧编辑 | PASS | 帧缩略图、拖选/右键、Shift 双向扩选（含 20–30 向左选 10）、Ctrl 保留原选区、剪切/复制/粘贴/删除/撤销、选区播放、单帧保存及剪辑回队列；`test_filmstrip_*`、`test_shift_*`、`test_video_editor_*`、`test_edited_timeline_*` |
| 音轨编辑 | PASS | 点击定位、拖选和右键、Ctrl+滚轮放大与滚轮横向移动、逐样本细节、低音量、静音、播放暂停及试听、插入、删除、音量、复位、长音轨、视频尾部无音频、超长音轨标记；`test_embedded_media_*`、`test_audio_*`、`test_video_audio_overflow_*`、`test_disjoint_playback_*`、G09 |
| 音画联动与产物 | PASS | 双向裁剪回读时长、缺失音频区间补静音、不连续片段播放、旧异步结果与快速切帧；`test_audio_and_video_trim_in_both_directions`、`test_disjoint_playback_*`、`test_editor_rapid_seeking_*` |
| 跨媒体拖入 | PASS | 图片、视频、音频分别送到插入边界，异步操作中撤销、缺失素材与打开原目录的调用路径；`test_drag_queued_media_*`、`test_missing_queued_media_*` |
| 参数与成品 | PASS | 同一文件两页共享设置、不同文件隔离、批量参数及目标体积传递、成品路径、重名递增、进度、失败继续、任务取消/退出清理；`test_output_settings_are_shared_per_file`、`test_desktop_import_preview_and_batch_conversion`、G04b/G07/G10–G12、媒体矩阵 |
| 批量格式菜单、输出文件名 | FAIL | G04a/G05/G06，见下文复现 |
| 成品打开和定位 | PASS | 已验证应用传给系统的路径。实际关联播放器和 Explorer 行为留在人工清单 |
| 稳定性 | PASS | 95 秒视频尾帧、35 秒波形、3072×2048 图片、20 次页面快切与 20 次文件轮换、同时观察退出后的测试子进程。短时资源抽样不外推为长时性能结论 |

## 7. 启动与便携交付

| 验收 | 状态 | 证据 |
|---|---|---|
| 源码入口启动和退出，第一次 | PASS | 实际 `pythonw.exe run.pyw`，带样片参数，标题、主窗口、缩略图与 FFprobe/FFmpeg 子进程出现，退出码 0 |
| 源码入口启动和退出，第二次 | PASS | 同一路径复跑，窗口响应且正常退出，无任务子进程留存 |
| 便携 exe 独立目录启动，第一次 | PASS | 完整目录复制到临时目录，工作目录独立，PATH 仅 System32。样片 H.264 / 320×180 / 24 fps 及缩略图可见。标题和应用图标正确，无可见控制台，退出码 0 |
| 便携 exe 独立目录启动，第二次 | PASS | 复跑窗口响应、退出码 0、无可见控制台与子进程留存 |
| 当前源码启动环境无额外控制台 | FAIL | `.venv/Scripts/pythonw.exe` 两次检测到非 cloaked 的短时控制台；空白 `.pyw` 对照复现，见 ENV-01 |

验收 exe SHA-256 为 `c1b5d261b4fd1ba6ceeb547c4c4e556355d5f5815d702657b25b71d367460c64`。源码截图 `07-source-window.png`，便携截图 `07-exe-window.png`，进程及控制台记录 `07-startup-results.json`。本机移出源码和限制 PATH 测的是便携运行，干净机器与安装程序见人工清单。

## 8. 失败、复现与归因

### FF-01 · P2 · 批量格式菜单点击后未应用（稳定复现）

1. 加入 MP4、WAV、PNG。在转换页点击「选择输出格式」，在视频组点击「MKV · H.264」。
2. 预期 MP4 行改为 MKV，音频和图片仍保留各自兼容格式。实际菜单按钮文字变为 MKV，队列仍为 `mp4/mp3/png`。点击两次同样失败。
3. GUI 回调抛 `TypeError: QComboBox.itemData(str)`。菜单传入字符串 key，处理函数已经区分字符串和索引，随后又对原始入参调用一次 `itemData(index)`。应只沿用解析后的格式 key。
4. 证据为 G04a 的两次真实点击和回调栈。逐行选格式并执行同一批次通过 G04b。该调用逻辑在动画前的 `602e48f` 已存在。 [格式菜单](frameflow/window.py#L1188) · [异常位置](frameflow/window.py#L1209)

### FF-02 · P2 · 两页的自定义输出文件名未生效（两条入口均复现）

1. 选中有音轨 MP4，设一个可写中文输出目录，在右侧输出文件名键入 `QA output & (7)`。分别点击「开始转换」与「开始导出」。
2. 预期实际名称为 `QA output & (7).mp4`。实际转换页使用源文件名，预览页使用源名加「_编辑」。两份成品都落在指定目录并通过 FFprobe 和完整解码，源文件保留。
3. 输入已写入 `Job.output_stem`，但批量与编辑导出的发布链路未使用这个字段。主窗口现有的 `_apply_output_stem` 未被调用。最小修复范围是将合法的输出名传入两条发布路径，并复用同名保护。
4. 证据为 G05/G06；同名保护单项通过，命名功能可在导出后手动重命名成品。 [输入状态](frameflow/window.py#L1764) · [批量转换](frameflow/window.py#L2222) · [编辑导出](frameflow/editor.py#L4158) · [成品发布](frameflow/media.py#L628)

### ENV-01 · P3 体验 · 当前虚拟环境启动器短暂显示控制台

从验收进程调用项目 `.venv/Scripts/pythonw.exe` 运行源码，两次在主窗口出现前采样到可见、非 cloaked 的 `ConsoleWindowClass`，进程树含 `python.exe` 与 `conhost.exe`。同一启动器运行只休眠的空白 `.pyw` 仍有该窗口，说明该现象不依赖 FrameFlow、Qt 或 FFmpeg 业务代码。便携 exe 两次没有可见控制台。证据为 `07-startup-results.json` 的源码、便携和空白对照。

### TEST-01 · 原生全量测试样式差异

默认 Windows 测试样式下，`test_combo_popup_does_not_cover_source_and_keeps_keyboard_selection` 的外框上边为 151，预期 ≥155；`test_combo_popup_matches_input_width_and_shows_every_row` 的外框宽为 224，预期 220。保留原全量执行的两项 FAIL。按 `run.pyw` 使用 Fusion、实际配色和字体后，原断言两项均通过（`09-popup-production-style.txt`）。实机的可见行、点击和键盘选择另通过 G14/G18，因此本轮未确认用户界面下拉框业务缺陷。

脚本复判也保留在探索日志中。首次文件框没有正确选中是脚本选择方式，视频拖动需按住实际滑柄，放大平移需拷贝 QPointF 再比较，内存采样需传 64 位进程句柄。混合批量首轮 12 秒 VP9 未在短等待窗结束，复测使用 55 秒上限和 MOV 获得四类成品，VP9 由独立媒体矩阵完成回读。以上均按补测结果归类。

## 9. 人工与环境补验清单

| 项目 | 状态 | 边界 |
|---|---|---|
| Windows 系统文件选择器、目录选择器及系统外部打开/定位 | NOT TESTED | Qt 文件框本身、用户入口、保存目录以及传给系统的路径已验证；原生 Shell 对话框与默认应用实点未做 |
| 人耳音画同步、音量、噪声及延迟 | NOT TESTED | WaveOut、播放时钟及音视频流已核对，未进行人工监听 |
| 实体鼠标悬停与真实显示器观察偶发闪烁 | NOT TESTED | 音量浮层的 Enter 处理和拖动通过；指针试验未可靠触发。转场中间帧与坐标通过，未做连续物理屏录判定 |
| Windows 系统设置切到 100% 和跨屏切换 | NOT TESTED | 已测原生 Qt DPR 1.00 与 1.25。系统级改动及跨显示器另验 |
| 磁盘耗尽与真实 NTFS ACL 拒写 | NOT TESTED | 测过路径是文件时批量继续和错误处理 |
| 独占锁住输入或输出文件 | NOT TESTED | 测过损坏、移走与正常同名保护 |
| 多小时素材、持续负载及大 4K 任务 | NOT TESTED | 本轮最长视频 95 秒，资源测量为短时抽样 |
| 无 Python 的干净 Windows 10/11 | NOT TESTED | 便携包从独立目录且限制 PATH 验证；仍在同一台有 Python 的机器 |
| 当前提交对应的新安装程序、升级、卸载、签名/SmartScreen | NOT TESTED | 本轮发布对象为现成便携 exe。旧发布的历史安装结果保留 |
| 4K HEVC 及不同 GPU/驱动的硬件编码矩阵 | NOT TESTED | 本轮实际跑通 H.264 NVENC/QSV；AMF/MF 硬件路径 BLOCKED，已验 CPU 回退 |

## 10. 交付结论

| 主验收统计 | PASS | FAIL | BLOCKED | NOT TESTED | 合计 |
|---|---:|---:|---:|---:|---:|
| 自动化（追加后最终离屏用例集） | 40 | 0 | 0 | 0 | 40 |
| 真实媒体矩阵 | 44 | 0 | 2 | 0 | 46 |
| Windows GUI 补充用例 | 18 | 3 | 0 | 0 | 21 |
| 启动与便携 | 4 | 1 | 0 | 0 | 5 |
| 人工与环境补验 | 0 | 0 | 0 | 10 | 10 |
| **合计** | **106** | **4** | **2** | **10** | **122** |

基线复跑、相同用例的 DPI 专项和默认原生全量对照另列在第 4 节，避免把同一自动化用例重复相加。四项 FAIL 中三条为两处业务缺陷，另一条为本机源码启动器的控制台现象。硬件回退成功和硬件本身不可用分别计数。

本轮未发现 P0 或 P1。真实布局动画和媒体队列通过回归，便携包可用于本机验收和演示。**暂缓作为下一版正式发布。** 批量格式菜单会造成用户以为改了格式、实际仍导出原格式，自定义文件名也未落盘。建议完成这两处修复后，用同一批素材复跑，并在无 Python 的干净 Windows 环境验证新版便携与安装包。

## 11. FF-01 与 FF-02 修复复测（2026-11-09）

基于 `feature/real-page-transition` 的 `3243d94` 修复。上文 G04a、G05、G06 的失败记录保留，以下为修复后实测。四项新增用例从实际菜单、文件名输入框和导出按钮进入，FFmpeg 处理真实的合成媒体，FFprobe 回读已落盘成品。

| 原缺陷 | 复测 | 可复核结果 |
|---|---|---|
| G04a / FF-01 批量格式 | PASS | 点击「MKV · H.264」后，两个原为 MP4 的视频行都改为 MKV。音频和图片保持各自的输出格式。混合队列四个成品均可完整解码，两个 `.mkv` 由 FFprobe 确认为 Matroska / H.264。 |
| G05 / FF-02 格式转换命名 | PASS | 输入 `转换 Export (校对)`，目录内已有同名 `.mp4` 时生成 `转换 Export (校对) (1).mp4`。FFprobe 确认为 H.264，原视频和占位文件内容一致。输入 `坏/名称` 会显示校验错误，目标目录无成品和临时文件。 |
| G06 / FF-02 媒体预览命名 | PASS | 输入 `剪辑 Preview (最终)` 后快速切换两页，选中的素材和名称均保留。点击「开始导出」生成 `剪辑 Preview (最终).mp4`，FFprobe 确认为 H.264，完整解码通过。输入 `CON` 会显示 Windows 保留名称错误，目录中只有上次的有效成品。 |
| FF-02 预览页直接导入图片 | PASS | 从预览页导入 `截图 Image (原始).png`，在底栏导出并以源文件同名保存时生成 `截图 Image (原始) (1).png`。FFprobe 确认为 PNG，源图片字节一致。 |

FF-01 根据调用参数类型取格式标识，再按每行已有的可选格式匹配。FF-02 在启动后台任务前把名称放入导出参数，由成品发布函数应用后缀和同名序号。名称留空时沿用对应入口的默认命名。预览页底栏在图片页调用现有的图片保存入口，片段和单帧另存对话框仍以所选路径为准。相关代码见 [批量格式](frameflow/window.py#L1186)、[转换参数](frameflow/window.py#L2108)、[预览参数](frameflow/editor.py#L2727) 和 [成品发布](frameflow/media.py#L657)。

- `python -m unittest discover -s tests -v`（离屏）通过 44/44，用时 76.6 秒。
- Windows 原生窗口按产品样式运行同一测试集，通过 44/44，用时 77.2 秒。显示缩放为 125%。Qt 测试驱动向可见窗口发送点击和文本输入，目录选择器使用隔离的临时路径。
- 20 次快速切换、两页选中项同步、转场中间位置、缩放、播放时反向切换及转换过程中切换均通过原生回归。原验收的测试和断言保留。

两项 P2 均通过复测，可以进入 PyInstaller 候选构建。正式发布前需用本提交生成的新包，在干净 Windows 环境验证安装、启动和实际导出。


## 12. v1.1.0 Windows x64 发行包验收（2026-11-09）

**构建。** 两个发行包都取自提交 `4591a059059191898ae5fb0133372f922aff89a5` 的同一次 PyInstaller 6.22.3 `onedir` 构建。它包含 `c23c792` 的功能修复。发布提交另外收录 README、测试报告和会话总结，程序与打包源码和构建提交一致。环境为 Windows 11 构建 26200、64 位 Python 3.13.0、PySide6 6.12.0、Pillow 13.0.0、FFmpeg/FFprobe 9.0.2 Essentials。安装包复用 NSIS 3.13 和 v1.0.1 的每用户注册表键。构建源码复跑离屏全量 44/44，通过时间为 71.5 秒。

| 发行文件 | 字节 | SHA-256 |
| --- | ---: | --- |
| `FrameFlow-Windows-x64.zip` | 122391376 | `730e66ba218fbdb7dc51b71e397f4096668f7aa84a3819c1d9f4df1c3b8bf572` |
| `FrameFlow-Setup-v1.1.0-Windows-x64.exe` | 87350439 | `4f4f038e7babe23819c939d08f24e33953f08e475628ab3e8ac9a06fc9da1e53` |

| 验收 | 结果 | 实测 |
| --- | --- | --- |
| 独立目录解压 ZIP | PASS | `FrameFlow.exe` 和 `_internal` 完整。解压后 179 个文件逐个 SHA-256 比对，与构建一致。EXE 为 x64 GUI 子系统，资源版本 1.1.0。 |
| 便携版启动、导入、播放和转场 | PASS | 以限制到 Windows 系统目录的 PATH 启动，显示两个 640×360 / 24 FPS 样片和真实缩略图。播放位置推进到 1.042 秒，约 122 ms 捕获右侧滑入中间帧。独立启动抽样无可见控制台，正常退出且进程清理。 |
| 中文安装向导和开始菜单 | PASS | 运行安装程序，输入独立安装目录，勾选桌面快捷方式。两个入口指向实际的 FrameFlow.exe。「已安装的应用」登记包含名称、1.1.0、图标、目录和卸载命令。实际双击语义通过 Windows Shell 打开开始菜单快捷方式。 |
| 安装版及升级版运行 | PASS | 两次均逐一核对 179 个载荷文件及 SHA-256。播放位置推进，分别在约 125 ms、112 ms 抽查转场中间画面，关闭后无应用或媒体工具进程残留。 |
| 三种运行方式真实导出 | PASS | 便携、全新安装和升级后都操作批量菜单选择「MKV · H.264」。落盘 `发布 Export (样片).mkv` 和 `Sample (2).mkv`，FFprobe 确认为 Matroska / H.264、640×360。测试源文件哈希保持一致。 |
| 旧版覆盖升级 | PASS | 先安装与 GitHub v1.0.1 哈希一致的 EXE，再直接运行 v1.1.0 安装程序，不指定新目录。沿用原目录，登记从 1.0.1 更新到 1.1.0。安装树与新版构建一致，无旧版程序文件残留。 |
| 卸载与用户数据保护 | PASS | 实际执行卸载程序。空安装目录、开始菜单和桌面快捷方式被清理。升级测试目录仅保留刻意放入的用户 MP4 和导出的 MKV，两者 SHA-256 一致。 |
| 独立无 Python 的干净 Windows | 未验证 | 双包均在当前 Windows 11 机器实际执行。便携版隔离工作目录和 PATH，尚无独立测试机结果。 |

复测使用可见的 Windows 窗口，由系统自动化实际点击菜单、输入名称和启动安装程序。最初的向导探测遗漏 NSIS 原生按钮与组件状态，另一次脚本从快捷方式启动后没有预先打开进程句柄。修正驱动后复跑通过，两次均沿用同一组发行文件。升级通过静默参数操作并实际运行安装版。现有 v1.0.1 登记与快捷方式在测试结束后恢复。
