"""Official Chat Completions presets for the model settings dialog."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    name: str
    url: str
    models: tuple[str, ...]
    icon: str
    hint: str = ""


CUSTOM = "custom"
DEFAULT_TIMEOUT = 45
MIN_TIMEOUT, MAX_TIMEOUT = 5, 300
PROVIDERS = {
    "deepseek": Provider("DeepSeek", "https://api.deepseek.com",
                         ("deepseek-flash", "deepseek-v4-pro"), "wave"),
    "openai": Provider("OpenAI", "https://api.openai.com/v1",
                       ("gpt-5.6-luna", "gpt-5.6-terra"), "robot",
                       "仅支持 Chat Completions 工具调用。仅支持 Responses API 的模型无法用于本助手。"),
    "glm": Provider("智谱 GLM", "https://open.bigmodel.cn/api/paas/v4",
                    ("glm-5.3", "glm-5.2"), "layers",
                    "使用标准 API Key。Coding Plan 专用端点需要在高级设置中单独填写。"),
    "qwen": Provider("阿里云百炼（通义千问）", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                     ("qwen-plus", "qwen3-max"), "cloud",
                     "默认使用中国内地端点。其他地域请填写对应端点，并使用同地域的 API Key。"),
    "kimi": Provider("Kimi（Moonshot）", "https://api.moonshot.cn/v1",
                     ("kimi-k3", "kimi-k3.1-pro"), "moon",
                     "默认使用中国内地端点。K3 系列始终开启思考，响应时间可能较长。"),
    "siliconflow": Provider("硅基流动", "https://api.siliconflow.cn/v1",
                            ("deepseek-ai/DeepSeek-V4-Flash", "Pro/zai-org/GLM-5.2"), "chip",
                            "使用平台提供的完整模型 ID。模型是否支持工具调用，以探测结果为准。"),
    "ark": Provider("火山方舟", "https://ark.cn-beijing.volces.com/api/v3",
                    ("doubao-seed-2-0-lite-260428",), "spark",
                    "默认使用北京端点。请先开通模型，也可手动填入控制台提供的 ep- 接入点 ID。"),
    CUSTOM: Provider("自定义 OpenAI 兼容接口", "", (), "gear",
                     "请填写兼容 Chat Completions 和函数工具调用的服务地址。"),
}


def provider_for_url(url):
    base = url.rstrip("/").removesuffix("/chat/completions").rstrip("/").lower()
    if base == "https://api.deepseek.com/v1":
        return "deepseek"
    if base == "https://api.moonshot.ai/v1":
        return "kimi"
    return next((name for name, provider in PROVIDERS.items() if provider.url.lower() == base), CUSTOM)


def uses_kimi_reasoning(url, model):
    return provider_for_url(url) == "kimi" and model.startswith("kimi-k3")


def tool_parameters(provider, model):
    """Use non-thinking Chat Completions where the provider requires reasoning replay."""
    if provider in {"deepseek", "glm", "ark"} or provider == "kimi" and model == "kimi-k2.6":
        return {"thinking": {"type": "disabled"}}
    if provider in {"qwen", "siliconflow"}:
        return {"enable_thinking": False}
    if provider == "kimi" and model.startswith("kimi-k3"):
        return {"reasoning_effort": "low"}
    if provider == "openai" and model.startswith(("gpt-5.4", "gpt-5.5", "gpt-5.6", "gpt-6-luna")):
        return {"reasoning_effort": "none"}
    return {}
