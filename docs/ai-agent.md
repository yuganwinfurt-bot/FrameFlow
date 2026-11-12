# FrameFlow AI Agent

AI Agent 是第三个 Windows 桌面工作区。左侧沿用媒体队列，右侧用于聊天、附件和转换计划。聊天和工具调用向配置的模型服务发送消息、附件名称和所查询的媒体元数据。FFprobe、缩略图和 FFmpeg 转换都在本机运行，工具收到的是队列 ID。

## 开始使用

在 Windows 安装版或便携版启动 FrameFlow，进入顶部「AI Agent」，点击「API 设置」。从源码运行时，在项目目录执行 `.\.venv\Scripts\python.exe run.pyw`。AI Agent 需要联网及自己的 API Key，媒体转换和编辑可离线使用。 [下载 v1.2.4](https://github.com/yuganwinfurt-bot/FrameFlow/releases/tag/v1.2.4)

1. 在「模型服务配置」里选择厂商，从下拉选模型或输入完整模型 ID，再填写 API Key。选择「自定义」或打开「高级设置」可填写 API Base URL，支持填写完整的 `/chat/completions` 地址。高级设置也可调整请求超时时间。
2. 点击「测试连接」。FrameFlow 用一个小型函数探测请求检查鉴权、模型与工具调用，探测阶段只发送测试文本。点「保存」后保留当前厂商的配置。使用未探测的配置首次发消息时也会先检查工具调用。
3. 从左侧队列拖媒体到聊天区域，或使用「添加附件」。在 Agent 页可按 Ctrl 多选后拖动。Enter 发送，Shift+Enter 换行。发送后，附件与文字一起保留在该条消息中。
4. 试试「把附件转成 MP4 H.265，设置 1080P，输出名叫项目预览，先给我转换方案」。1080P 沿用 FrameFlow 的最长边 1920 像素预设，并保持源宽高比。
5. 若要用新目录，先点输入区左下角的文件夹按钮，再告诉助手使用已授权目录。核对计划中的文件、格式、质量和位置，点击「确认执行」。
6. 通过同一计划卡查看逐项进度、失败原因和成品。生成中的「停止生成」控制模型请求，任务卡上的「取消转换」控制已经确认的 FFmpeg 任务。

各厂商分别保存配置，Windows 使用当前用户的 DPAPI 加密 API Key。更改端点时，需要为新地址重新填写 Key。当前账户无法加密时，配置仅在本次运行可用。公网连接使用 HTTPS，HTTP 限于本机环回地址。聊天历史与待发附件保留在当前程序会话，切页和选择媒体不会清空它们。

## 模型厂商与协议

预设对应 Chat Completions 地址。厂商的模型权限与可用地区以账号为准，实际可用性由工具探测和后续请求确认。

| 厂商 | 默认 API Base URL | 官方文档 |
| --- | --- | --- |
| DeepSeek | `https://api.deepseek.com` | [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) |
| OpenAI | `https://api.openai.com/v1` | [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)、[Chat Completions](https://developers.openai.com/api/reference/chat-completions/overview) |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | [工具调用](https://docs.bigmodel.cn/cn/guide/capabilities/function-calling)、[GLM-5.2](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2) |
| 阿里云百炼（通义千问） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | [OpenAI 兼容 Chat](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)、[Qwen3-Max](https://help.aliyun.com/zh/model-studio/model-qwen3-max) |
| Kimi（Moonshot） | `https://api.moonshot.cn/v1` | [Kimi K3](https://platform.kimi.com/docs/guide/kimi-k3-quickstart)、[工具调用](https://platform.kimi.com/docs/guide/use-kimi-api-to-complete-tool-calls) |
| 硅基流动 | `https://api.siliconflow.cn/v1` | [Chat Completions](https://docs.siliconflow.cn/docs/api/chat-completions-post)、[Function Calling](https://docs.siliconflow.cn/docs/userguide/guides/function-calling) |
| 火山方舟 | `https://ark.cn-beijing.volces.com/api/v3` | [对话 API](https://docs.volcengine.com/docs/ark/chat-api)、[模型列表](https://docs.volcengine.com/docs/ark/model-list) |
| 自定义 OpenAI 兼容接口 | 手动填写 | 服务需支持 Chat Completions 和工具调用。 |

百炼和 Kimi 使用中国内地默认地址，跨区使用时填写匹配账号的地址。火山方舟可手动填写已开通模型的 ID 或 `ep-` 接入点 ID。仅开放 Responses API、仅支持文本对话，或依赖专用接口的模型会在探测阶段提示限制。通过探测表示服务能返回工具调用，实际工具权限和多轮行为仍取决于模型。

## 工具

| 用途 | Tool Calling |
| --- | --- |
| 读取队列、媒体信息、当前选中项和任务结果 | `list_media`、`get_media_info`、`get_current_selection`、`get_task_status` |
| 修改共享的队列和输出状态 | `select_media`、`set_output_format`、`set_resolution`、`set_quality`、`set_output_directory`、`set_output_filename` |
| 提交待确认方案、检查确认状态和取消 Agent 转换 | `prepare_conversion`、`start_conversion`、`cancel_conversion` |

工具的文件范围是当前队列。保存目录限于源目录、已有输出设置或本次由用户选择的目录。`set_quality` 可设置常规视频格式的目标体积，计划卡会显示该目标，实际体积由原转换流程按媒体内容尽量逼近。确认时核对方案中的文件、目录和参数是否变化，然后把同一份快照交给 FrameFlow 原来的转换队列。模型调用 `start_conversion` 得到确认状态，真正开始转换由桌面计划卡的确认按钮触发。取消工具仅作用于同一会话中匹配的 Agent 任务。每次消息最多 6 轮、16 次工具调用。

## 验收

界面与厂商配置更新后，Windows 原生窗口在系统 125% 缩放下通过 61/61 项测试，在同一台机器的 Qt 等效 100% 缩放下通过 17/17 项 Agent 测试。系统鼠标复测了单文件及 Ctrl 多选后的双文件拖放，也检查了 8 家厂商的下拉选项。用独立 Windows 进程验证了厂商、模型和两组 DPAPI 凭据能够重新读取。

本机 HTTP 测试服务覆盖连接、鉴权错误、模型不存在、地址错误、不返回工具调用、超时、停止及工具调用往返。v1.2.0 发行前的 Windows 原生全量回归通过 75/75。另用已配置的真实 DeepSeek 账号执行合成素材转换流程，确认前无输出，确认后 FFprobe 读到 HEVC / AAC、640×360、6 秒。其他厂商的真实服务未复测。 [验收记录](../TEST_REPORT.md#v120-release-acceptance)

转换测试使用合成媒体。模型提出 MP4 / H.265 计划后，输出目录为空。点击确认后经 FFprobe 回读，成品为 MP4、HEVC（`hvc1`）、160×90、2 秒。另测失败反馈、取消清理及参数改动使旧方案失效。

Agent 以完整消息显示模型回复。切页时保留本次程序运行的聊天内容，退出程序后可再次打开已保存的模型配置。
