"""Service configuration via environment variables."""
import os


class Settings:
    # 百炼 API Key（服务端统一配置；未配置时回退使用请求头里的 Bearer key）
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")

    # 本服务自身的访问密钥（可选；设置后客户端必须带 Authorization: Bearer <key>）
    SERVER_API_KEY: str = os.getenv("SERVER_API_KEY", "")

    # 百炼接口地址（国际站可改为 https://dashscope-intl.aliyuncs.com）
    DASHSCOPE_BASE_URL: str = os.getenv(
        "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com"
    )

    # 单次上游请求超时（秒）—— qwen-image 同步生成可能需要 30~90s
    UPSTREAM_TIMEOUT: float = float(os.getenv("UPSTREAM_TIMEOUT", "180"))

    # wan 系列异步任务轮询总超时（秒）与轮询间隔（秒）
    TASK_TIMEOUT: float = float(os.getenv("TASK_TIMEOUT", "300"))
    TASK_POLL_INTERVAL: float = float(os.getenv("TASK_POLL_INTERVAL", "2"))

    # 单次请求最多生成几张图
    MAX_IMAGES: int = int(os.getenv("MAX_IMAGES", "4"))

    # /v1/models 返回的模型列表（逗号分隔）。
    # 留空 = 自动从上游官方 models 接口拉取并过滤出生图模型（推荐）
    IMAGE_MODELS: str = os.getenv("IMAGE_MODELS", "")

    # 上游模型列表的缓存时长（秒）
    MODELS_CACHE_TTL: float = float(os.getenv("MODELS_CACHE_TTL", "600"))

    # 优先走异步任务接口（对支持异步的模型）。默认 false：同步接口更快；
    # 设为 true 时若模型不支持异步会自动回退同步。
    PREFER_ASYNC: bool = os.getenv("PREFER_ASYNC", "false").lower() in ("1", "true", "yes")


settings = Settings()
