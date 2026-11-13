# FrameFlow 工程审计与重构记录

## A. 原始审计与基线

基线为 v1.2.4（`a6ed7a1e1373e2802fe24b026a77ef21db7c432d`）。桌面项目 `master` 工作区干净，本次使用独立检出的 `refactor/engineering`。三项只读审查覆盖窗口与公共控件、媒体编辑与转换、Agent 与安全测试。启动入口、依赖、构建脚本和项目文档一并核对。

物理行含注释与空行，AST 统计包含方法和局部函数。单独计入 Python 语法的 .pyw 与 .spec。

| 文件 | 行 | 类 | 函数 | 主要职责 |
| --- | ---: | ---: | ---: | --- |
| `frameflow/__init__.py` | 1 | 0 | 0 | 包声明 |
| `frameflow/agent.py` | 221 | 1 | 14 | 会话与工具调用循环 |
| `frameflow/agent_api.py` | 269 | 4 | 15 | 模型配置凭据与 HTTP |
| `frameflow/agent_settings_ui.py` | 295 | 1 | 17 | 模型服务弹窗 |
| `frameflow/agent_tools.py` | 450 | 4 | 32 | 工具注册、授权与计划 |
| `frameflow/agent_ui.py` | 822 | 10 | 57 | 聊天、附件和确认卡 |
| `frameflow/details.py` | 498 | 4 | 30 | 媒体详情与弹窗 |
| `frameflow/drag_wheel.py` | 61 | 1 | 5 | Windows 拖动滚轮 |
| `frameflow/editing.py` | 396 | 4 | 20 | 片段操作与渲染 |
| `frameflow/editor.py` | 4480 | 15 | 295 | 编辑界面、播放、状态和导出 |
| `frameflow/filmstrip.py` | 752 | 1 | 54 | 时间轴手势、绘制和缓存 |
| `frameflow/icons.py` | 115 | 0 | 5 | 图标 |
| `frameflow/media.py` | 836 | 8 | 36 | 进程、探测、参数和转换 |
| `frameflow/model_providers.py` | 67 | 1 | 3 | 模型服务预设 |
| `frameflow/motion.py` | 1089 | 15 | 93 | 通用 Qt 控件和转场 |
| `frameflow/playback.py` | 198 | 4 | 12 | 预览解码与音频输出 |
| `frameflow/queue_drag.py` | 187 | 1 | 11 | 队列拖拽动画 |
| `frameflow/spring.py` | 109 | 3 | 12 | 弹簧和插入索引 |
| `frameflow/theme.py` | 690 | 1 | 6 | QSS 和主题 |
| `frameflow/window.py` | 2589 | 11 | 164 | 窗口、队列、设置和后台任务 |
| `FrameFlow.spec` | 67 | 0 | 0 | PyInstaller 冻结配置 |
| `packaging/package_windows.py` | 87 | 0 | 3 | 双包构建 |
| `run.pyw` | 42 | 0 | 1 | 应用入口 |
| `tests/test_agent.py` | 817 | 1 | 31 | Agent 桌面及工具集成 |
| `tests/test_agent_api.py` | 137 | 2 | 12 | API 及凭据测试 |
| `tests/test_agent_ux.py` | 228 | 1 | 13 | 设置及聊天布局 |
| `tests/test_spring.py` | 35 | 1 | 3 | 弹簧计算 |
| `tests/test_workflow.py` | 2780 | 1 | 92 | 转换、编辑、预览和转场综合测试 |

### 风险排序

| 优先级 | 问题及原始位置 | 维护影响 |
| --- | --- | --- |
| 高 | `window.py:870–2589` 的 MainWindow 管理队列、预览、共享表单、文件操作和转换，876–879 同时保存四个映射，1520–1540 同步两种 Qt 视图的排序 | Qt 几何、媒体状态和任务拥有者不同，变更容易波及多个页面。 |
| 高 | `editor.py:1078–4480` 集中控件组装、选区、预览调度和导出，1186–2116 的 `_build` 为 931 行，2437–2501 在对话框内部执行估算公式 | 独立控件、状态与数值算法目前难以单独定位。 |
| 高 | `agent_tools.py:92–107,146–213,285–435` 访问窗口字典、列表、导出快照、进度控件和取消令牌 | 工具安全依赖实际文件身份、快照一致性和作用域，不能通过随意替换 GUI 状态保留行为。 |
| 高 | `tests/test_workflow.py:36–75` 每项都生成样片，`tests/test_agent_ux.py:28–33` 改全局 Qt 样式 | 控件用例付出媒体准备成本，单独运行与发现顺序可能给出不同结果。 |
| 中 | `media.py:700–836` 与 `editing.py:287–371` 均处理编码重试与发布，分支指标分别为 47 与 44 | 普通转换与编辑渲染的码率限制、失败和进度时机有实际差异，不能机械合并。 |
| 中 | `editor.py:33` 读取私有编码表，`playback.py:11` 读取编辑滤镜图。 `motion.py` 同时放了转场、滚轮、公共控件和拖拽数据 | 依赖是单向但契约不够清楚，本轮集中处理影响更大的边界。 |
| 中 | `test_agent.py:74–90` 用布局下标找按钮，`test_workflow.py:1427–1482` 混合控件结构与真实导出，1807 模拟 Qt 拖拽 | 用户动作与实现结构需分别验证。窗口裁切、过渡中间帧和坐标是真实契约，保留几何检查。 |
| 低 | `theme.py` 多数为 QSS，`filmstrip.py` 主体为连贯的手势与绘制，时长格式化存在亚秒语义差异 | 长度本身不是缺陷。保留样式、时间线绘制及有语义差异的重复。 |

静态导入图无模块环。主要依赖为 `run → window → editor / agent / agent_tools`、`editor → editing / playback / filmstrip / media`、`agent → agent_settings_ui → agent_ui`。Agent 访问窗口状态是运行时耦合，静态无环不能消除这个问题。

## 方案与实施顺序

```text
run.pyw
└─ window.py                页面、导航、信号连接
   ├─ queue_view.py         两种队列、几何和拖拽
   ├─ queue_state.py        媒体记录与顺序
   ├─ conversion_batch.py   后台探测及批量转换
   ├─ editor.py             编辑编排和控件绑定
   │  ├─ editor_widgets.py  原生预览、波形和播放器组件
   │  ├─ estimates.py       音视频体积估算
   │  └─ editing / playback / filmstrip
   └─ agent / agent_tools   会话、校验、授权及确认
      ├─ 明确的宿主操作接口
      └─ agent_ui / agent_attachments / agent_settings_ui
media.py                    媒体入口与编码语义
tests/                      分主题回归及共享夹具
```

考虑过将队列改为全新的 Qt model，或用多个混入类分割 Editor。前者会同时影响宽窄视图、拖拽及选中绑定，后者容易隐藏共享可变状态。AI 选择独立组件与显式操作接口，按 B 控件、C 业务与状态、D 测试、E 清理与文档提交。

**开发者明确要求** 审计先行、独立分支、逐阶段测试与提交，保持现有 UI、格式、媒体语义和 Agent 权限，待明确同意后再考虑合并。目录名称、代码边界和测试实现属于 AI 实施方案。


阶段 A 基线：Windows 11 原生 Qt，125% DPI，81/81 通过，169.830 秒，0 跳过。使用现有 FFmpeg / FFprobe。

## 分阶段实施

### B · 拆出独立界面职责

- **队列** 原 `window.py` 同时包含主窗口、宽队列行、窄队列绘制、列宽、菜单和拖拽。AI 把两种队列的绘制、真实视口列宽和拖拽放到 [queue_view.py](frameflow/queue_view.py)，共享标签与缩略图缩放放到 [widgets.py](frameflow/widgets.py)。保留原 QListWidget 两种视图和尺寸校验，避免同时迁移 Qt model、拖拽和动画；对照了搬移定义的 AST，复测重复缩放、批量导出和跨组件拖拽。
- **编辑器** 原 `Editor._build` 连续构建播放器、波形、输出设置和时间轴。AI 把预览、传输按钮和音量组件放到 [editor_widgets.py](frameflow/editor_widgets.py)，波形绘制及手势放到 [wave_view.py](frameflow/wave_view.py)，把编辑器初始化拆成按页面和区域组织的方法。全屏播放器继续由 Editor 管理，因为它会重新挂载同一个视频控件。这个取舍保留了播放、全屏和时间线的状态生命周期。
- **Agent 附件** 原 `agent_ui.py` 将待发送附件、历史附件、气泡和聊天输入混在一起。AI 把附件卡片和换行布局放到 [agent_attachments.py](frameflow/agent_attachments.py)，保留拖放数据和控件对象名称。附件拖入、删除后输入区布局、已发送附件以及短中文消息回归均纳入原生测试。

### C · 收拢数据和工作线程边界

- **队列状态** 宽窄列表继续负责选中与显示。[QueueState](frameflow/queue_state.py) 独占队列成员和排序，主窗口和其他读者通过只读映射查看同一个有序字典。每个 Job 的对象身份和可变媒体状态保留，行控件读取选定格式，默认格式由主窗口处理。相比一次性切换至 QAbstractItemModel，这一阶段可以直接复用原生拖放和宽窄视图，并减少排序写入口。
- **媒体工作线程** 原 `MainWindow._inspect`、`_run_batch` 同时使用后台 FFmpeg 和 Qt 信号。AI 提取 [conversion_batch.py](frameflow/conversion_batch.py)，窗口传入信号回调，工作函数可以脱离 Qt 测试。输出后探测失败仍保留成品，取消和结束回调顺序保持原样。[media_runtime.py](frameflow/media_runtime.py) 管理随包工具查找、进程生命周期和终止。保留 `frameflow.media` 的原导入入口，避免修改编码参数、编辑渲染和打包脚本。
- **Agent 宿主** 原工具类直接读取窗口列表、选中项、进度控件和批次字典。AI 采用 [AgentHost](frameflow/agent_host.py) 显式描述允许的桌面操作，主窗口适配现有信号和执行器。继续按真实文件身份和参数校验待确认计划，通过窗口持有的作用域身份约束取消。13 个工具规范和唯一的人工确认入口保留。相比把转换器实例直接交给工具层，这个接口让批次和窗口状态的依赖能从方法签名看出来，代价是增加一组薄适配方法。
- **预计体积** 原算法直接从编辑器读取控件和轨道并立即更新标签。AI 把音视频公式放在 [estimates.py](frameflow/estimates.py)，编辑器只决定当前导出模式、时长、输入参数和展示。图片仍按请求代号收异步结果。保留原有算法、格式化规则和同媒体类型的对比规则。

### D · 按风险重组测试

将原 `WorkflowTest` 的 52 个用例分到 7 个文件。转换和编辑模型可单独运行，媒体夹具按用例创建临时目录并按需生成样片。每个测试进程只初始化一次正式应用的样式、字体和调色板，各工作区使用临时 INI。HTTP 夹具独立于测试类，只使用本机回环服务和合成令牌。比对用例 AST，原有 81 个名称全部保留，4 个旧用例体发生变化。两个 Mock 路径跟随被提取函数，一个计划卡断言保留原文本要求并改为在实际卡片上查找标签，硬件回退用例在故障注入前准备样片。原有实际输出、控件几何和安全断言保留。

初次重组在 Agent UX 用例结束后恢复 QApplication 样式，完整运行出现长时间无响应并被终止。改为按类恢复后，Agent UX 独立通过，完整运行又在桌面控件用例结束后无响应。最终由统一入口在每个测试进程中只设置一次主题，窗口按用例清理并处理延迟销毁。受限环境离屏诊断中的回环连接失败单独记录，未计入 Windows 原生结果。

统一主题后的第一次全量运行完成，结果为 88/89。硬件回退用例的 Mock 同时捕获了延迟生成样片与探测的命令，出现多余的 `mpeg4` 和探测调用。将样片准备移到故障注入之前，保留对 `h264_amf → libx264`、取消不回退和真实解码的断言。

| 风险 | 输入及通过条件 | 盲区 |
| --- | --- | --- |
| 批量处理一项失败后连带后续失败 | 损坏 MOV 加有效 MOV。第一项失败，第二项真实 MKV/H.264，经 FFprobe 验证容器、编码、尺寸及时间，完整解码且无临时残留 | 一个合成样片，无法覆盖所有容器异常 |
| 运行资源路径被开发 PATH 掩盖 | 模拟随包根目录中的 `bin`，让 PATH 查找失败，必须选中随包的两个文件。真实媒体用例另验证实际执行 | 未启动本轮冻结 EXE |
| 模型错误回复在拒绝前已部分生效 | 本机 HTTP 返回重复 ID，第一项请求改画质，第二项请求建计划。拒绝回复且参数、待确认计划和转换作用域均未变化 | 使用受控模型协议响应，未验证真实云模型的输出差异 |
| 用户取消后模型仍持有计划 ID | Qt 点击取消，再用同一 ID 调 `start_conversion`。必须拒绝、不得启动、目录内容不变 | 走真实按钮与工具分派，未测网络重放 |
| 时间段越界或音轨短于画面 | 空轨和越界裁剪返回空结果，剩余音频后补静音到请求长度，原轨不变。已有联动用例继续导出并回读 | 单元用例验证片段算术，实际音频由原有 FFmpeg 工作流验证 |
| 错用源时长或给不可预估格式输出精确数字 | 6.76 秒改至 17.48 秒的 192 kbps MP3、显式 WAV 采样率和声道、FLAC/VBR/目标体积及视频范围 | 单元用例验证单位、变化方向与拒绝原因，实际误差由真实媒体用例限定 |
| 队列裁切及跨页状态丢失 | 原生 Qt 验证表头和真实视口交集、转场中间几何和 20 次快速切页；另通过 Win32 标题栏操作验证窗口循环 | Windows 125% 单显示器，未覆盖多屏和跨屏 DPI 变化 |
| 取消、拒写、重名或回退误留成品 | 保留真实取消、同名保护及 CPU 回退。拒写单测注入一次 PermissionError，观察无重复尝试及无工作目录 | 拒写用例模拟文件系统错误，未创建真实 ACL；无法覆盖所有显卡驱动 |

### E · 删除已确认无调用的代码

移除 Editor 中未被引用的字段构建函数、图片页切换方法和旧 `export_grid` 属性。详情与估算使用相同的字节格式化函数，原来字节、KB、MB、GB 与空值的显示保留。README 改为指向当前职责和用例，历史文档里的旧测试链接固定到 v1.2.4 提交，保留当时的测试叙述和开发者反馈。

**开发者决策记录** 开发者明确要求分阶段提交、保护已发布版本、用实际媒体复验并保留 Agent 的人工确认与路径约束。上述拆分、保留现有 Qt model 和类级夹具为 AI 实施方案。

## 重构前后实测

| 维度 | v1.2.4 | 重构后 |
| --- | ---: | ---: |
| Python 文件数 | 26 个 .py，另有 1 个 .pyw、1 个 .spec | 49 个 .py，另有 1 个 .pyw、1 个 .spec |
| 应用包与测试目录内 .py 文件数 | 20 / 5 | 31 / 17 |
| 最大模块 | `editor.py` 4480 行 | `editor.py` 3610 行 |
| 主窗口模块 | `window.py` 2589 行，11 个类 | `window.py` 1901 行，3 个类 |
| Agent UI 模块 | `agent_ui.py` 822 行，10 个类 | `agent_ui.py` 645 行，6 个类 |
| 最大类 | `Editor` 3403 行 | `Editor` 3400 行 |
| 最长函数 | `Editor._build` 931 行 | `Editor._build_output_settings` 376 行 |
| 函数最大分支指标 | 56，`Editor._build` | 47，`convert` 和 `MainWindow._refresh_controls` |
| 最大测试模块 | `test_workflow.py` 2780 行 | `test_agent.py` 857 行 |
| 静态导入环 | 0 | 0 |
| 完全重复的 50-token 滑窗 | 全仓 34，应用包 13 | 全仓 21，应用包 0 |
| 原有核心用例 | 81/81 | 81/81，另有 8 项新增边界用例 |

物理行包含空行和注释。分支指标按 AST 计数，基础分 1，加条件、循环、异常分支、推导式过滤、布尔运算分支和匹配分支。重复量忽略注释和空白，要求 50 个 token 完全一致且覆盖至少 6 行，记录重复出现的次数。该指标可识别长段逐字重复，近似重复和语义重复仍需代码审查。最大类的体量基本持平，因为大段迁出的是独立控件，Editor 内部构造拆分后仍管理同一批相互关联的生命周期。

```text
frameflow/
├─ window.py · queue_state.py · queue_view.py · widgets.py
├─ media.py · media_runtime.py · conversion_batch.py · media_text.py
├─ editor.py · editor_widgets.py · wave_view.py · estimates.py
├─ editing.py · playback.py · filmstrip.py · motion.py
└─ agent.py · agent_api.py · agent_tools.py · agent_host.py
   └─ agent_ui.py · agent_attachments.py · agent_settings_ui.py
tests/
├─ test_media_conversion.py · test_export_estimates.py
├─ test_desktop_queue.py · test_desktop_controls.py · test_desktop_transitions.py
├─ test_editing_model.py · test_editor_timeline.py · test_editor_playback.py · test_editor_export.py
└─ test_agent.py · test_agent_api.py · test_agent_ux.py · test_spring.py
```

## 验证记录

Windows 11 构建 26200、125% 缩放（Qt DPR 1.25）、Python 3.13.0、PySide6 6.12.0、FFmpeg 9.0.2。用 `QT_QPA_PLATFORM=windows` 运行 `python -m unittest discover -s tests -v`。转换使用合成媒体，Agent 使用本机 HTTP 服务和合成密钥。

| 阶段 | Git 提交 | Windows 原生结果 |
| --- | --- | --- |
| A 基线 | `6e26c66` | 81/81，169.830 秒 |
| B 界面组件 | `2026350` | 81/81，142.751 秒 |
| C 状态、后台任务和宿主接口 | `23c82ed` | 81/81，143.692 秒 |
| D 测试重组 | `355fbd5` | 89/89，122.907 秒 |
| E 清理与最终回归 | 本报告所在提交 | 89/89，122.122 秒 |

最终运行 **89 通过、0 失败、0 跳过**。另外使用 4 个合成视频和 1 个 MP3，通过 Win32 标题栏点击做 20 次还原和最大化，共记录 41 个端点。全部 205 个行快照中，视口与行几何一致，表头对齐，格式框和删除按钮完整可见。末轮最大化视口 1106 px，首行两个控件的横坐标范围分别为 [930,1062) 和 [1070,1098)。还原至 940×720 时视口 596 px，对应范围为 [420,552) 和 [560,588)。确认可选择 MP3、选择 MKV 选项、删除队列项且保留源文件。测试数据与截图留在本地工作区。

额外对比确认 13 个工具定义和 6 个编码及成品发布函数的 AST 与基线一致。窗口测试覆盖真实视口和 Qt 事件，部分拖放、硬件故障、拒写及音频设备通过测试夹具模拟。真实云端模型、其他 DPI 与跨屏、真实 Windows ACL 和冻结 EXE 安装运行均未验证。随包工具路径通过模拟目录与真实工具调用分别验证。

## 仍有的技术债务

- Editor 仍持有播放器、挂载的输出设置、撤销与异步任务的相互关联状态，`_build_output_settings` 还有 376 行。进一步拆分应先确定编辑会话的所有权及回调生命周期，并增加挂载、异步取消和恢复的特征测试。
- 主窗口的 `_refresh_controls` 仍集中决定多个工作区的可操作状态，分支指标为 47。Qt 控件和可变 Job 状态仍通过窗口编排，`AgentHost` 也继续返回 Job 对象。未来可以在明确快照和状态同步语义后进一步收窄。
- 普通转码与编辑渲染各有尺寸预算和回退策略，暂保留现有参数语义。部分测试仍以几何或内部状态观察行为，涉及窗口裁切、真实端点和播放状态时，这些断言提供了外部成品无法表达的证据。测试依赖部分已弃用的 Qt 事件构造器。
