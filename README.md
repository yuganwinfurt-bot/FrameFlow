# 帧转 FrameFlow

**在 Windows 上预览、剪辑和批量转换视频、音频与图片。** 拖入素材后即可查看缩略图、分辨率、时长和编码。所有媒体处理在本机完成。

这是一个三天限时的 AI 桌面端开发笔试项目。客户端使用 PySide6 Widgets，媒体处理通过 FFmpeg、FFprobe 和 Pillow 完成。

[下载安装包（Windows x64）](https://github.com/yuganwinfurt-bot/FrameFlow/releases/download/v1.0.1/FrameFlow-Setup-v1.0.1-Windows-x64.exe) · [AI 开发会话总结](FrameFlow_开发会话总结_2026-11-08.md) · [测试报告](TEST_REPORT.md)

## 下载和试用

| 版本 | 下载 | 启动方式 |
| --- | --- | --- |
| **EXE 安装版（推荐）** | [v1.0.1 安装包](https://github.com/yuganwinfurt-bot/FrameFlow/releases/download/v1.0.1/FrameFlow-Setup-v1.0.1-Windows-x64.exe) | 双击进入中文安装向导，选择安装目录。安装完成后可从开始菜单启动 |
| 便携 ZIP | [v1.0.1 便携包](https://github.com/yuganwinfurt-bot/FrameFlow/releases/download/v1.0.1/FrameFlow-Windows-x64.zip) | 完整解压后双击 `FrameFlow.exe`，保留同目录的 `_internal` 文件夹 |

两种发行包均内置 Python 运行环境、PySide6 和 FFmpeg/FFprobe，可离线使用。**v1.0.1 修复了旧安装包的 QtCore DLL 加载错误**，可以覆盖升级。 [查看发行说明](https://github.com/yuganwinfurt-bot/FrameFlow/releases/tag/v1.0.1) · [全部发行版本](https://github.com/yuganwinfurt-bot/FrameFlow/releases)

试用时，先拖入一个 MP4 文件，选中队列里的素材，在右侧设置输出格式和目录。点「开始转换」，完成后点「打开成品」。切到「媒体预览」可以逐帧查看并保存当前帧。

## 界面截图

<table>
<tr>
<td><img src="docs/desktop-preview.png" alt="格式转换页，左侧为素材队列，右侧为输出设置" width="420"></td>
<td><img src="docs/editor-panel.png" alt="媒体预览页，包含播放器、时间线和输出设置" width="420"></td>
</tr>
<tr>
<td align="center">格式转换</td>
<td align="center">媒体预览与逐帧编辑</td>
</tr>
</table>

<details>
<summary>查看文件详情界面</summary>

<img src="docs/details-preview.png" alt="文件详情页，显示媒体参数和音轨信息" width="520">

</details>

## 主要功能

| 场景 | 支持的操作 |
| --- | --- |
| 导入与管理 | 拖放或选择本地文件，后台读取缩略图和媒体信息。队列按文件保存输出设置并显示进度和失败原因 |
| 视频转换 | 输出 MP4、MKV、MOV、WebM、AVI 或 GIF。可调整分辨率、帧率、质量、音频码率和目标体积。部分编码器可用硬件加速，失败时回退软件编码 |
| 预览与编辑 | 播放、全屏、音量、逐帧浏览、时间线选段。支持音视频联动裁剪、波形选段和单帧导出 |
| 音频与图片 | 提取视频音轨，转换 MP3、M4A、WAV、FLAC、Opus、OGG。图片支持 PNG、JPEG、WebP、AVIF、TIFF、BMP、GIF、ICO、PDF |
| 成品管理 | 选择保存目录、取消转换、打开成品。同名输出自动加序号，保留源文件和已有成品 |

实际可用的硬件编码选项取决于 FFmpeg 构建、显卡与驱动。目标体积是编码目标，成品大小取决于素材与编码器。

## 客户端实现

| 模块 | 职责 |
| --- | --- |
| [启动入口](run.pyw) | 创建 Qt 应用，定位随包的媒体工具，启动主窗口 |
| [窗口与队列](frameflow/window.py) | Qt 主线程更新界面，后台线程探测媒体和处理批量任务。每个素材保存独立的导出参数 |
| [媒体处理](frameflow/media.py) | FFprobe 读取元数据，FFmpeg 生成缩略图、转码并回传进度。`Processes` 管理子进程取消，成品从临时文件发布 |
| [编辑与播放](frameflow/editor.py) · [片段渲染](frameflow/editing.py) · [播放调度](frameflow/playback.py) | 逐帧预览按需解码。剪切与粘贴更新片段引用，预览和导出按同一时间线渲染 |
| [工作流测试](tests/test_workflow.py) | 覆盖真实导出、参数隔离、异步定位、音视频联动、取消与同名保护 |

一个实际问题是，实拍视频有 **6.76 秒**，音轨约 **6.18 秒**就结束，选择多段帧时预览会卡在准备阶段。修复后，渲染按每段时长建立静音底轨，再叠加实际音频。用原来的四段选区复测，共 **34 帧**，成功播放到最后一帧。 [复现与回归记录](SESSION_SUMMARY.md) · [音轨渲染代码](frameflow/editing.py)

## 我怎样用 AI 开发和验收

我确定客户端选型，用截图和实际操作给 AI 划定修改范围，再根据运行结果决定接受、继续修改或停止。比如，我要求同一文件设为 **H.265、720P、CRF 23** 后，切换转换页和预览页仍保留参数。实现复用了导出参数模型，回归用例同时检查了两页同步和不同文件之间的参数隔离。 [AI 开发会话总结](FrameFlow_开发会话总结_2026-11-08.md) · [参数回归用例](tests/test_workflow.py#L664)

- [功能演进记录](SESSION_SUMMARY.md)｜需求变更、技术方案与阶段验证。
- [界面改版记录](docs/session-2026-11-07.md)｜参考图、用户反馈、界面取舍与回归。
- [测试报告](TEST_REPORT.md)｜交付记录含 33 项自动化回归与 16 项真实 Windows 窗口操作测试。媒体产物通过 FFprobe 或 Pillow 回读核对。

v1.0.1 在 Windows 本机另验了从旧安装包覆盖升级、主窗口启动和卸载。独立干净机器上的启动和人耳判断声画同步仍待验证。 [v1.0.1 发行说明](https://github.com/yuganwinfurt-bot/FrameFlow/releases/tag/v1.0.1)

## 从源码运行

```powershell
git clone https://github.com/yuganwinfurt-bot/FrameFlow.git
cd FrameFlow
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

把 `ffmpeg.exe` 和 `ffprobe.exe` 放入 `bin`，或添加到 `PATH`。配置完成后运行下方命令启动应用。 [媒体工具配置说明](bin/README.md)

```powershell
.\.venv\Scripts\python.exe run.pyw
```

用下面的命令运行工作流测试。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

如需复现 v1.0.1 的便携构建，运行 `git switch --detach v1.0.1`，再运行 `.\build_windows.bat`。脚本调用 PyInstaller，生成 `dist\FrameFlow\FrameFlow.exe` 和随附运行文件。 [v1.0.1 打包配置](https://github.com/yuganwinfurt-bot/FrameFlow/blob/v1.0.1/FrameFlow.spec)

## 许可证

FrameFlow 源码目前未附开源许可证。随包分发的 FFmpeg 等第三方组件遵循各自的许可证，见发行目录中的 `ThirdParty`。
