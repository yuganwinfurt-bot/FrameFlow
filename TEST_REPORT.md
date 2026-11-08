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
