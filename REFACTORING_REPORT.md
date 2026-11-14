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

## 第二轮深度重构

本轮从第一轮完成时的 refactor/engineering 提交 36d9e31 开始。原桌面仓库仍在 master 的 a6ed7a1。开发者指出上一轮缩小了文件，但 Editor 仍约 3400 行，跨工作区刷新集中，测试文件仍接近 900 行。开发者明确要求按实际职责拆分，保护既有界面、动画和人工确认机制，并在独立分支分阶段验证。本节中的组件划分和实现取舍由 AI 提出并实施。

### 从实际调用关系确定边界

- **编辑器。** 原来的 Editor 同时持有视频与音频时钟、波形和暂停帧缓存、图片估算请求、轨道片段、选区、剪贴板、撤销和 worker 回调。切页会停止播放、取消部分预览、重新挂载参数并刷新底栏。每种工作有不同取消时机，因而按任务的生命周期划分。
- **音视频编辑。** 音轨由源视频推导与显式空轨有不同含义，切剪、移动和撤销需要同时处理音视频。播放选区与另存选区需要共用同一段轨道计算。帧选区使用一基闭区间，独立于片段和撤销。
- **工作区状态。** 两套队列视图引用同一份 Job。选中文件的参数界面会在转换和预览之间重新挂载，单项 Export 由 Job 持有。批量默认参数与运行中进度分别有自己的生存周期。Agent 通过 AgentHost 读写队列与参数并申请任务，入口仍保留人工确认、路径授权及计划校验。
- **主窗口。** 原来一次 _refresh_controls 会更新两个列表、右侧摘要、批量对话框、硬件选项和转换按钮。预览底栏还直接读取编辑器页签、进度条、提示和取消按钮。拆分后每个组件渲染自己负责的状态。

```text
重构前
MainWindow → 多工作区刷新 · 批量参数 · 转换进度 · 直接读取编辑器控件
           → Editor → 片段 · 选区 · 播放 · 波形 · 导出设置 · 多个后台任务

重构后
MainWindow → QueueState / QueueAvailability → 队列行、侧栏、批量设置
           → ConversionRun → conversion_batch → media → media_runtime
           → AgentHost → 既有队列、单项 Export 和批量任务入口
           → Editor → EditSession / FrameSelection
                    → VideoTransport → playback
                    → AudioWorkspace → WaveView / editing
                    → ExportSettings → estimates
                    → FramePreview / PictureEstimate → editing / media
                    → EditorExport → EditorTasks → 既有导出接口
PreviewFooter ← Editor.export_status() 返回的 PreviewStatus 快照
```

### 模块接管的职责

| 组件 | 实际接管 | 取舍 |
| --- | --- | --- |
| [EditSession](frameflow/edit_session.py) | 音视频片段、源索引、插入静帧缓存、联动剪贴板与双轨撤销。统一播放和另存所用轨道 | 可脱离 Qt 验证。Editor 保留画面、波形和提示的协调 |
| [FrameSelection](frameflow/frame_selection.py) | 连续选区、离散帧覆盖、Ctrl 拖拽快照与归并 | Filmstrip 负责鼠标输入和渲染。Editor 同步帧号及光标 |
| [VideoTransport](frameflow/video_transport.py) | 播放会话、时钟、音量与全屏 QWidget 的挂载 | 共享 EditSession 的轨道。Editor 协调视频、音轨试听与帧预览互斥 |
| [AudioWorkspace](frameflow/audio_workspace.py) | 波形区间和缓存、缩放、音频选择、播放头、试听时钟及右键菜单 | 编辑操作走明确的回调。音轨仍由 EditSession 持有，波形和试听沿用前台任务 |
| [FramePreview / PictureEstimate](frameflow/preview_requests.py) | 暂停帧的单在途解码、缓存和过期校验，以及图片体积估算的独立请求代次和取消 | 按各自的生命周期清理。页签与参数通过输入和回调提供 |
| [ExportSettings](frameflow/editor_settings.py)、[editor_controls](frameflow/editor_controls.py) | 可重挂载的设置 QWidget、模式、预设、格式选项、估算显示、目录和文件名，共用小型控件工厂 | 保留同一个控件实例和 Job 参数快照。模式变化经由信号同步 |
| [EditorExport](frameflow/editor_export.py)、[EditorTasks](frameflow/editor_tasks.py) | 选择导出参数与目标。任务组件持有单线程池、取消作用域及完成回调 | 完成时先清除作用域再通知 UI。允许回调启动下一项，丢弃已取消的插入结果 |
| [BatchSettings](frameflow/batch_settings.py)、[ConversionInspector](frameflow/conversion_inspector.py) | 批量默认值、格式能力、加速交集、文件名和目录。侧栏持有选中文件摘要和按钮 | 保留批量默认值和单项参数的优先级，侧栏提供设置挂载位置 |
| [ConversionRun](frameflow/conversion_run.py)、[QueueState](frameflow/queue_state.py) | 批量运行生命周期、取消与结果计数。QueueState 保持成员和顺序并生成可操作性快照 | 手动和 Agent 仍共用同一批量执行入口。主窗口保留导航和提示 |
| [PreviewFooter](frameflow/preview_footer.py) | 根据 PreviewStatus 刷新进度、按钮及提示 | MainWindow 经 Editor 的导出和取消入口协调 |

考虑过继续拆大函数、使用转发混入类，以及给三页统一一个全局状态对象。实际采用组合与窄回调，因为队列成员、单项参数、运行中批次、暂态选区和剪辑结果的写入时机不同。代价是初始化多出组件连接。对比确认 11 个基线模块的 Git 内容相同，包括 Agent 请求、宿主协议和工具、FFmpeg 核心、播放解码、编辑渲染和原生转场。

### 规模与复杂度

沿用第一轮 AST 和物理行统计。函数分支指标包含条件、循环、异常、推导式和布尔分支。重复量按相同的 50-token 滑窗计算，只识别长段逐字重复。

| 维度 | 第二轮前 | 第二轮后 |
| --- | ---: | ---: |
| Python 文件（.py） | 49 | 67 |
| 应用 / 测试 .py 文件 | 31 / 17 | 44 / 22 |
| Editor 类 | 3400 行 | 1161 行 |
| MainWindow 类 | 1718 行 | 1273 行 |
| 超过 1500 行的生产类 | 2 | 0 |
| 最大应用模块 | editor.py，3610 行 | window.py，1392 行 |
| 最长生产函数 | Editor._build_output_settings，376 行 | AgentPage.__init__，164 行 |
| MainWindow._refresh_controls | 79 行，分支指标 47 | 12 行，分支指标 7 |
| 最大生产函数分支指标 | 47 | 47，media.convert |
| 最大测试文件 | test_agent.py，857 行 | test_desktop_queue.py，635 行 |
| 静态导入环 | 0 | 0 |
| 完全重复的 50-token 滑窗（全仓 / 应用） | 21 / 0 | 20 / 0 |

### 验证

Windows 11 构建 26200，Python 3.13.0，PySide6 6.12.0。使用 Windows Qt 插件、系统缩放 125%、真实 FFmpeg / FFprobe 和合成媒体。基线 89/89，122.828 秒。最终保留原有 89 个测试方法，另外增加两个领域边界用例，强化五文件批量和 Agent 工具分派两项覆盖。

最终 Windows 全量 **91 通过，0 失败，0 跳过**，耗时 113.948 秒。此前同一批生产代码也完成一轮 91/91（115.419 秒），随后补齐 Agent 分派断言并重跑。

| 层级 | 实测与判定 | 局限 |
| --- | --- | --- |
| 编辑状态 | 离散帧重排后逐帧核对顺序，短音轨补静音，撤销恢复两轨。Ctrl 拖拽改向时使用起始选区快照并保留未改帧，反向区间清空结果 | 纯状态用例之外，由原生 Qt 测试左右键、Shift 和跨媒体拖入 |
| 真实媒体 | 点击批量菜单，三个视频实际导出 MKV/H.264，FFprobe 回读 320×180 与时长。音频实际解码、回读时长。PNG 用 Pillow 核对 40×30、alpha=96。五份源文件字节一致 | 合成素材和当前 FFmpeg 构建，媒体变体和设备的覆盖有限 |
| 编辑、播放、估算 | 快速逐帧定位、暂停、全屏、音量、双轨编辑与撤销、单帧保存、波形缺失恢复、取消异步插入。预览导出和跨页批处理用实际输出及进度断言 | 部分设备行为由 winsound 的测试替身观察 |
| 原生窗口及 DPI | 同一进程使用实际 Win32 标题栏点击执行 20 次还原和最大化，另做最小化、恢复、选 MP3、选择 MKV、删除队列项。系统 125% 和 Qt 有效 DPR 1.0 各一轮。每轮 42 个端点、209 次行几何检查，均无裁切。自动化另覆盖 20 次快速切页 | DPR 1.0 来自应用级缩放。未测系统 DPI 切换和跨显示器。截图及 geometry.jsonl 在本地工作目录 |
| Agent | 13 个工具定义保持一致。以本机 HTTP 模型响应和工具分派测试读队列、选择、配置、计划状态、人工确认、非法参数和路径、过期计划、重复调用与取消。确认后实际导出 MP4/HEVC 160×90 | 未访问云端模型或使用真实密钥 |
| 异常及资源 | 损坏媒体、取消、成品同名、退出清理及随包工具查找均执行 | 拒写和编码器回退使用故障注入，未实测 Windows ACL 或实际 GPU 编码及冻结版 |

实施时有几个失败帮助检验切分边界。选区另存仍调用拆分前的函数，实际导出测试触发错误，随后统一使用 EditSession.tracks_for_selection。首轮全量测试出现两个测试夹具缺失问题，补齐共享拖放和样本函数后重跑。其他测试中的旧控件归属与导入路径也改为实际组件，保留原有行为断言。标题栏脚本首次传入相对路径，选择 MP3 后拿实际绝对路径比较导致断言超时。改用绝对素材路径后两种缩放均完整通过。

### Git 与长期规范

| 提交 | 检查点 |
| --- | --- |
| 48815cd、d980317 | 导出设置、音视频编辑状态 |
| b4d3a42、22446eb | 视频播放、帧预览和图片估算 |
| cca827e、648dae3 | 转换侧栏与批量设置、音频工作区 |
| a1fa0c2、a7767b6 | 编辑导出和任务、预览底栏、批量运行状态 |
| d13535e、3c81fbb | 测试重组和真实五文件验证、独立时间轴选区 |
| 本报告所在提交 | Agent 工具分派复验、Skill 与验收依据 |

[frameflow-engineering](.agents/skills/frameflow-engineering/SKILL.md) 通过格式校验。新开的临时 Codex 会话通过 skills/list 查到仓库内启用的 Skill，显式调用完成，并返回职责归属和全量回归规则。根目录 [AGENTS.md](AGENTS.md) 指定在开发、修复、UI、Agent 工具、测试和架构调整时使用。本轮实际应用了状态归属、异步取消、真实媒体回读、阶段测试及复杂度检查。

### 剩余技术债

- MainWindow 仍为 1273 行，承担导航、双队列同步、导入、文件操作及 AgentHost 编排。AgentHost 仍返回可变 Job。继续收窄时需要先定义 Job 快照和更新规则，再验证选中、排序、导入及 Agent 并发。
- ExportSettings 的表单仍有约 1100 行，负责同一个可重挂载组件的模式、格式、预设、参数和估算。进一步拆开参数模型需要保护双向信号和预设恢复时序。AudioWorkspace 约 560 行，波形控件、定位和试听共用一个播放位置及波形区间。
- AgentPage.__init__ 和 ModelDialog.__init__ 分别有 164、150 行，主要是界面装配。media.convert 仍有 137 行和分支指标 47，保留其体积预算、回退和发布语义。一些 Qt 事件测试仍使用已弃用的构造器。
