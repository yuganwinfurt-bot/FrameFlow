# FrameFlow AI Agent

AI Agent 是第三个 Windows 桌面工作区。左侧沿用媒体队列，右侧用于聊天、附件和转换计划。聊天和工具调用向配置的模型服务发送消息、附件名称和所查询的媒体元数据。FFprobe、缩略图和 FFmpeg 转换都在本机运行，工具收到的是队列 ID。

## 从源码试用

在项目目录运行 `.\.venv\Scripts\python.exe run.pyw`。进入顶部「AI Agent」，点击「API 设置」。

1. 填入服务商的 API Base URL、API Key 和模型 ID。Base URL 通常以 `/v1` 结尾，也可填写完整的 `/chat/completions` 地址。
2. 点击「测试连接」。测试只发送一个小型工具探测请求。模型要支持 OpenAI 兼容 Chat Completions 和指定函数的 Tool Calling。保存后，首次发送也会检查此能力。
3. 从左侧队列拖媒体到聊天区域，或使用「添加附件」。在 Agent 页可按 Ctrl 多选后拖动。Enter 发送，Shift+Enter 换行。发送后，附件与文字一起保留在该条消息中。
4. 试试「把附件转成 MP4 H.265，设置 1080P，输出名叫项目预览，先给我转换方案」。1080P 沿用 FrameFlow 的最长边 1920 像素预设，并保持源宽高比。
5. 若要用新目录，先点 Agent 页的「选择输出目录」，再告诉助手使用已授权目录。核对计划中的文件、格式、质量和位置，点击「确认执行」。
6. 通过同一计划卡查看逐项进度、失败原因和成品。生成中的「停止生成」控制模型请求，任务卡上的「取消转换」控制已经确认的 FFmpeg 任务。

Windows API Key 使用当前用户的 DPAPI 加密保存。当前账户无法加密时，仅本次运行可用。公网连接使用 HTTPS，HTTP 限于本机环回地址。聊天历史与待发附件保留在当前程序会话，切页和选择媒体不会清空它们。

## 工具

| 用途 | Tool Calling |
| --- | --- |
| 读取队列、媒体信息、当前选中项和任务结果 | `list_media`、`get_media_info`、`get_current_selection`、`get_task_status` |
| 修改共享的队列和输出状态 | `select_media`、`set_output_format`、`set_resolution`、`set_quality`、`set_output_directory`、`set_output_filename` |
| 提交待确认方案、检查确认状态和取消 Agent 转换 | `prepare_conversion`、`start_conversion`、`cancel_conversion` |

工具的文件范围是当前队列。保存目录限于源目录、已有输出设置或本次由用户选择的目录。确认时核对方案中的文件、目录和参数是否变化，然后把同一份快照交给 FrameFlow 原来的转换队列。模型调用 `start_conversion` 得到确认状态，真正开始转换由桌面计划卡的确认按钮触发。取消工具仅作用于同一会话中匹配的 Agent 任务。每次消息最多 6 轮、16 次工具调用。

## 验收

离屏和 Windows 原生窗口（125% 缩放）的全量回归各通过 55/55 项。原生窗口还实际操作了单文件及 Ctrl 多选后的双文件拖放。模型协议由本机 HTTP 测试服务提供，覆盖连接、鉴权错误、超时、停止以及工具调用往返。

转换测试使用合成媒体。模型提出 MP4 / H.265 计划后，输出目录为空。点击确认后经 FFprobe 回读，成品为 MP4、HEVC（`hvc1`）、160×90、2 秒。另测失败反馈、取消清理及参数改动使旧方案失效。

可配置的云端服务需要开发者提供有效配置后另测。第一版以完整消息回传显示回复，当前安装版与 ZIP 对应 v1.1.0，Agent 在源码开发分支试用。
