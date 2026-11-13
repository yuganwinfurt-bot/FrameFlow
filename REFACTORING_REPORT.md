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
