"""OpenAI-compatible request/response schemas for /v1/images/generations."""
import math
import re
import time
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ImageRequest(BaseModel):
    """OpenAI images.generate request body.

    额外字段（negative_prompt / prompt_extend / watermark / seed 等）会被透传给百炼。
    """

    model: str
    prompt: str
    n: int = Field(default=1, ge=1, le=16)
    size: Optional[str] = None  # "1024x1024" / "1328*1328" / "auto" / None
    response_format: Literal["url", "b64_json"] = "url"
    user: Optional[str] = None
    quality: Optional[str] = None  # 忽略，仅为兼容 OpenAI 客户端
    style: Optional[str] = None    # 忽略，仅为兼容 OpenAI 客户端

    model_config = {"extra": "allow"}

    def extra_params(self) -> dict[str, Any]:
        """百炼专属参数透传（通过 OpenAI SDK 的 extra_body 传入）。"""
        allowed = {"negative_prompt", "prompt_extend", "watermark", "seed"}
        extra = self.model_extra or {}
        return {k: v for k, v in extra.items() if k in allowed}


class ImageItem(BaseModel):
    url: Optional[str] = None
    b64_json: Optional[str] = None
    revised_prompt: Optional[str] = None


class ImageResponse(BaseModel):
    created: int = Field(default_factory=lambda: int(time.time()))
    data: list[ImageItem]


class ModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "dashscope"


class ModelList(BaseModel):
    object: str = "list"
    data: list[ModelCard]


class ErrorDetail(BaseModel):
    message: str
    type: str = "upstream_error"
    code: Optional[str] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ---------------------------------------------------------------------------
# 模型与尺寸映射
# ---------------------------------------------------------------------------

# qwen-image 系列（token 计费）支持的分辨率
QWEN_IMAGE_SIZES = [
    (1328, 1328),  # 1:1
    (1664, 928),   # 16:9
    (1472, 1140),  # 4:3
    (1140, 1472),  # 3:4
    (928, 1664),   # 9:16
]

# 上游 model id 里出现这些词就认为是生图模型（用于过滤 /compatible-mode/v1/models）
IMAGE_MODEL_RE = re.compile(r"(image|t2i|text2image|flux|stable-diffusion|dall-e)", re.I)

# 上游拉取失败时的兜底列表
FALLBACK_MODELS = [
    # Token 套餐域名（token-plan.*.maas.aliyuncs.com）
    "qwen-image-2.0",
    "qwen-image-2.0-pro",
    "wan2.7-image",
    "wan2.7-image-pro",
    # 公共百炼域名（dashscope.aliyuncs.com）
    "qwen-image",
    "qwen-image-plus",
    "wan2.2-t2i-flash",
    "wan2.2-t2i-plus",
    "wanx2.1-t2i-turbo",
    "wanx2.1-t2i-plus",
]


def is_image_model(model_id: str) -> bool:
    return bool(IMAGE_MODEL_RE.search(model_id))


# 走同步 multimodal-generation 接口的模型（Token 套餐域名仅支持这种方式）
_SYNC_PREFIXES = ("qwen-image", "wan2.7-image")


def is_sync_model(model: str) -> bool:
    return model.startswith(_SYNC_PREFIXES)


def _parse_size(size: str) -> Optional[tuple[int, int]]:
    """解析 'WxH' / 'W*H' 格式，失败返回 None。"""
    normalized = size.strip().lower().replace("*", "x")
    parts = normalized.split("x")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def map_size(model: str, size: Optional[str]) -> Optional[str]:
    """把 OpenAI 风格的 size 映射为百炼格式。

    - None / "auto" -> None（用百炼默认值）
    - 旧版 qwen-image（1.x）-> 按宽高比匹配最接近的官方分辨率
    - 其余模型（qwen-image-2.0 / wan2.7 等）-> "1024x1024" 转 "1024*1024" 直接透传
    """
    if not size or size.strip().lower() == "auto":
        return None

    parsed = _parse_size(size)
    if parsed is None:
        return None

    w, h = parsed
    if model not in ("qwen-image", "qwen-image-plus"):
        return f"{w}*{h}"

    # 按 log 宽高比找最接近的官方支持分辨率
    target = math.log(w / h)
    best = min(QWEN_IMAGE_SIZES, key=lambda s: abs(math.log(s[0] / s[1]) - target))
    return f"{best[0]}*{best[1]}"
