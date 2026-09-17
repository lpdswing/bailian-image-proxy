"""OpenAI SDK 兼容的阿里百炼文生图代理服务。

暴露接口：
- POST /v1/images/generations  （OpenAI images.generate 兼容）
- GET  /v1/models
- GET  /health
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from .config import settings
from .dashscope import DashScopeClient, UpstreamError
from .schemas import (
    FALLBACK_MODELS,
    ImageItem,
    ImageRequest,
    ImageResponse,
    ModelCard,
    ModelList,
    is_image_model,
    is_sync_model,
    map_size,
)

logger = logging.getLogger("bailian-image-proxy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(
        headers={"User-Agent": "bailian-image-proxy/1.0"}
    )
    logger.info("bailian-image-proxy 启动，上游: %s", settings.DASHSCOPE_BASE_URL)
    yield
    await app.state.http.aclose()


app = FastAPI(
    title="Bailian Image Proxy",
    description="把阿里百炼文生图模型封装为 OpenAI SDK 兼容接口",
    version="1.0.0",
    lifespan=lifespan,
)


def _error_response(status: int, message: str, err_type: str, code: Optional[str] = None):
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": err_type, "code": code}},
    )


def _resolve_api_keys(authorization: Optional[str]) -> tuple[bool, Optional[str]]:
    """返回 (本服务鉴权是否通过, 调用百炼用的 key)。

    - SERVER_API_KEY 已配置：客户端 Authorization 必须与之匹配。
    - 百炼 key：优先用服务端 DASHSCOPE_API_KEY；未配置时回退用客户端传入的 Bearer key
      （这样也可以直接把百炼 API Key 填在 OpenAI SDK 的 api_key 里）。
    """
    client_key = None
    if authorization and authorization.lower().startswith("bearer "):
        client_key = authorization[7:].strip()

    if settings.SERVER_API_KEY and client_key != settings.SERVER_API_KEY:
        return False, None

    return True, (settings.DASHSCOPE_API_KEY or client_key)


@app.get("/health")
async def health():
    return {"status": "ok", "upstream": settings.DASHSCOPE_BASE_URL}


# 上游模型列表缓存: {"ts": 时间戳, "models": [...]}
_models_cache: dict[str, Any] = {"ts": 0.0, "models": []}


async def _list_upstream_image_models(http: httpx.AsyncClient, api_key: str) -> list[str]:
    """从上游官方 models 接口拉取并过滤出生图模型（带缓存）。"""
    now = time.time()
    if _models_cache["models"] and now - _models_cache["ts"] < settings.MODELS_CACHE_TTL:
        return _models_cache["models"]

    resp = await http.get(
        f"{settings.DASHSCOPE_BASE_URL}/compatible-mode/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json().get("data") or []
    models = [m["id"] for m in data if isinstance(m, dict) and m.get("id")]
    image_models = [m for m in models if is_image_model(m)]

    _models_cache.update(ts=now, models=image_models)
    return image_models


@app.get("/v1/models")
async def list_models(authorization: Optional[str] = Header(default=None)):
    """返回生图模型列表。

    默认从上游官方 models 接口实时拉取并过滤（缓存 10 分钟）；
    也可以用 IMAGE_MODELS 环境变量手动指定。上游不可用时回退到内置列表。
    """
    if settings.IMAGE_MODELS:
        models = [m.strip() for m in settings.IMAGE_MODELS.split(",") if m.strip()]
        return ModelList(data=[ModelCard(id=m) for m in models])

    authed, upstream_key = _resolve_api_keys(authorization)
    if not authed:
        return _error_response(401, "无效的访问密钥", "authentication_error")
    if upstream_key:
        try:
            models = await _list_upstream_image_models(
                app.state.http, upstream_key
            )
            if models:
                return ModelList(data=[ModelCard(id=m) for m in models])
            logger.warning("上游 models 接口未返回生图模型，使用内置兜底列表")
        except Exception as e:
            logger.warning("拉取上游模型列表失败（%s），使用内置兜底列表", e)

    return ModelList(data=[ModelCard(id=m) for m in FALLBACK_MODELS])


@app.post("/v1/images/generations")
async def images_generations(
    req: ImageRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    authed, upstream_key = _resolve_api_keys(authorization)
    if not authed:
        return _error_response(401, "无效的访问密钥", "authentication_error")
    if not upstream_key:
        return _error_response(
            401,
            "缺少百炼 API Key：请配置 DASHSCOPE_API_KEY 环境变量，"
            "或在请求头 Authorization: Bearer <你的百炼Key> 中传入",
            "authentication_error",
        )

    n = min(req.n, settings.MAX_IMAGES)
    if req.n > settings.MAX_IMAGES:
        logger.warning("n=%s 超过上限，已截断为 %s", req.n, settings.MAX_IMAGES)

    size = map_size(req.model, req.size)
    extra = req.extra_params()
    client = DashScopeClient(request.app.state.http)

    use_async = settings.PREFER_ASYNC or not is_sync_model(req.model)
    channel = {"async": use_async}
    started = time.perf_counter()
    logger.info(
        "生图请求: model=%s n=%s size=%s 通道=%s prompt=%.80s",
        req.model,
        n,
        size,
        "异步" if use_async else "同步",
        req.prompt,
    )

    try:
        if use_async:
            urls = await client.async_generate(
                upstream_key, req.model, req.prompt, n, size, extra, channel
            )
        else:
            urls = await client.sync_generate_n(
                upstream_key, req.model, req.prompt, n, size, extra
            )

        data: list[ImageItem] = []
        if req.response_format == "b64_json":
            b64s = await asyncio.gather(*(client.download_as_b64(u) for u in urls))
            data = [ImageItem(b64_json=b) for b in b64s]
        else:
            data = [ImageItem(url=u) for u in urls]

        elapsed = time.perf_counter() - started
        logger.info(
            "生图完成: model=%s n=%s 通道=%s 耗时=%.2fs (单张约 %.2fs)",
            req.model,
            len(urls),
            "异步" if channel["async"] else "同步",
            elapsed,
            elapsed / max(len(urls), 1),
        )
        return ImageResponse(data=data)

    except UpstreamError as e:
        elapsed = time.perf_counter() - started
        logger.error("上游错误 (耗时=%.2fs) [%s] %s", elapsed, e.code, e.message)
        status = e.status
        code = e.code
        lowered = e.message.lower()

        # 内容安全审核拦截（百炼绿网）：给客户端明确、可识别的错误
        if (
            code == "DataInspectionFailed"
            or "green net" in lowered
            or "inappropriate content" in lowered
            or "data inspection" in lowered.replace("_", " ")
        ):
            return _error_response(
                400,
                "内容审核未通过：百炼的内容安全策略拦截了本次生成（通常由提示词涉及敏感主题"
                f"或生成结果被判定不适宜触发）。请调整提示词后重试。原始信息：{e.message}",
                "content_policy_violation",
                code,
            )

        # 401/403 透传为鉴权错误；429 透传限流；其余 4xx 归为请求错误
        err_type = (
            "authentication_error" if status in (401, 403)
            else "rate_limit_error" if status == 429
            else "invalid_request_error" if 400 <= status < 500
            else "upstream_error"
        )
        return _error_response(status, f"百炼错误: {e.message}", err_type, e.code)
    except httpx.TimeoutException:
        logger.error("上游请求超时 (耗时=%.2fs)", time.perf_counter() - started)
        return _error_response(504, "百炼请求超时，请重试", "upstream_error")
    except Exception:
        logger.exception("未预期的错误 (耗时=%.2fs)", time.perf_counter() - started)
        return _error_response(500, "服务内部错误", "internal_error")
