"""百炼 DashScope 上游调用封装。

- 同步接口: POST /api/v1/services/aigc/multimodal-generation/generation
  （qwen-image / wan2.7-image 系列；Token 套餐域名仅支持这种方式）
- 异步接口: POST /api/v1/services/aigc/text2image/image-synthesis + 任务轮询
  （公共百炼域名上的 wan / wanx 系列；若上游拒绝异步则自动回退同步接口）
"""
import asyncio
import base64
import logging
from typing import Any, Optional

import httpx

from .config import settings

logger = logging.getLogger("bailian-image-proxy")


class UpstreamError(Exception):
    """百炼返回的错误，携带 HTTP 状态码、错误码与消息。"""

    def __init__(self, message: str, code: Optional[str] = None, status: int = 502):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


class DashScopeClient:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http

    def _headers(self, api_key: str, async_task: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if async_task:
            headers["X-DashScope-Async"] = "enable"
        return headers

    @staticmethod
    def _raise_for_error(resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError:
            raise UpstreamError(
                f"百炼返回非 JSON 响应 (HTTP {resp.status_code}): {resp.text[:300]}",
                status=502,
            )
        if resp.status_code != 200 or body.get("code"):
            raise UpstreamError(
                message=body.get("message") or f"HTTP {resp.status_code}",
                code=body.get("code"),
                status=resp.status_code if resp.status_code != 200 else 400,
            )
        return body

    # ------------------------------------------------------------------
    # 同步接口（multimodal-generation）
    # 适用于：qwen-image 系列、wan2.7-image 系列（Token 套餐域名仅支持同步）
    # ------------------------------------------------------------------
    async def sync_generate(
        self,
        api_key: str,
        model: str,
        prompt: str,
        size: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> str:
        """生成一张图，返回图片 URL。"""
        parameters: dict[str, Any] = dict(extra or {})
        if size:
            parameters["size"] = size

        payload = {
            "model": model,
            "input": {
                "messages": [{"role": "user", "content": [{"text": prompt}]}]
            },
        }
        if parameters:
            payload["parameters"] = parameters

        resp = await self.http.post(
            f"{settings.DASHSCOPE_BASE_URL}/api/v1/services/aigc/multimodal-generation/generation",
            headers=self._headers(api_key),
            json=payload,
            timeout=settings.UPSTREAM_TIMEOUT,
        )
        body = self._raise_for_error(resp)

        # 图片 URL 位于 output.choices[0].message.content[*].image
        try:
            content = body["output"]["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise UpstreamError(f"无法解析百炼响应: {str(body)[:300]}", status=502)

        for item in content if isinstance(content, list) else [content]:
            if isinstance(item, dict) and item.get("image"):
                return item["image"]
        raise UpstreamError(f"百炼响应中未找到图片: {str(body)[:300]}", status=502)

    async def sync_generate_n(
        self,
        api_key: str,
        model: str,
        prompt: str,
        n: int,
        size: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> list[str]:
        """同步接口单次只出一张图，n 张并发调用。"""
        tasks = [
            self.sync_generate(api_key, model, prompt, size, extra)
            for _ in range(n)
        ]
        return list(await asyncio.gather(*tasks))

    # ------------------------------------------------------------------
    # 异步接口（提交任务 + 轮询）
    # 新接口 image-generation/generation 用于 wan2.6/2.7 等新模型，
    # 旧接口 text2image/image-synthesis 用于 wanx2.1 / wan2.2 等老模型（按顺序自动尝试）。
    # 注意：并非所有模型都支持异步（如 qwen-image-2.0 会返回 AccessDenied），
    # 遇到这种情况自动回退同步接口。
    # ------------------------------------------------------------------
    ASYNC_ENDPOINT = "/api/v1/services/aigc/image-generation/generation"
    LEGACY_ASYNC_ENDPOINT = "/api/v1/services/aigc/text2image/image-synthesis"

    @staticmethod
    def _is_no_async_support(e: UpstreamError) -> bool:
        return "asynchronous" in e.message.lower() or e.code == "AccessDenied"

    @staticmethod
    def _extract_urls(output: dict[str, Any]) -> list[str]:
        """兼容两种任务结果结构，提取图片 URL。"""
        urls: list[str] = []
        # 新接口：output.choices[].message.content[].image
        for choice in output.get("choices") or []:
            content = (choice.get("message") or {}).get("content") or []
            for item in content if isinstance(content, list) else [content]:
                if isinstance(item, dict) and item.get("image"):
                    urls.append(item["image"])
        if urls:
            return urls
        # 旧接口：output.results[].url
        for item in output.get("results") or []:
            if isinstance(item, dict) and item.get("url"):
                urls.append(item["url"])
        return urls

    async def async_generate(
        self,
        api_key: str,
        model: str,
        prompt: str,
        n: int = 1,
        size: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
        channel: Optional[dict[str, Any]] = None,
    ) -> list[str]:
        """提交异步任务并轮询直到完成，返回图片 URL 列表。

        channel: 调用方传入的字典，若发生同步回退会被置为 {"async": False}，
                 便于调用方记录实际使用的通道。
        """
        parameters: dict[str, Any] = {"n": n, **(extra or {})}
        if size:
            parameters["size"] = size

        candidates = [
            (
                self.ASYNC_ENDPOINT,
                {
                    "model": model,
                    "input": {
                        "messages": [{"role": "user", "content": [{"text": prompt}]}]
                    },
                    "parameters": parameters,
                },
            ),
            (
                self.LEGACY_ASYNC_ENDPOINT,
                {
                    "model": model,
                    "input": {"prompt": prompt},
                    "parameters": parameters,
                },
            ),
        ]

        last_error: Optional[UpstreamError] = None
        for endpoint, payload in candidates:
            try:
                resp = await self.http.post(
                    f"{settings.DASHSCOPE_BASE_URL}{endpoint}",
                    headers=self._headers(api_key, async_task=True),
                    json=payload,
                    timeout=30,
                )
                body = self._raise_for_error(resp)
            except UpstreamError as e:
                if self._is_no_async_support(e):
                    logger.warning(
                        "%s 不支持异步调用，回退同步接口: %s", model, e.message
                    )
                    if channel is not None:
                        channel["async"] = False
                    return await self.sync_generate_n(
                        api_key, model, prompt, n, size, extra
                    )
                last_error = e
                logger.warning("异步接口 %s 调用失败: %s", endpoint, e.message)
                continue

            task_id = body.get("output", {}).get("task_id")
            if not task_id:
                last_error = UpstreamError(
                    f"百炼未返回 task_id: {str(body)[:300]}", status=502
                )
                continue
            return await self._poll_task(api_key, task_id)

        raise last_error or UpstreamError("异步任务提交失败", status=502)

    async def _poll_task(self, api_key: str, task_id: str) -> list[str]:
        url = f"{settings.DASHSCOPE_BASE_URL}/api/v1/tasks/{task_id}"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.TASK_TIMEOUT

        while loop.time() < deadline:
            await asyncio.sleep(settings.TASK_POLL_INTERVAL)
            resp = await self.http.get(
                url, headers=self._headers(api_key), timeout=30
            )
            body = self._raise_for_error(resp)
            output = body.get("output", {})
            status = output.get("task_status")

            if status == "SUCCEEDED":
                urls = self._extract_urls(output)
                if not urls:
                    raise UpstreamError(
                        f"任务成功但未返回图片 URL: {str(body)[:300]}", status=502
                    )
                return urls
            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                raise UpstreamError(
                    message=output.get("message") or f"任务状态: {status}",
                    code=output.get("code"),
                    status=400,
                )
            # PENDING / RUNNING -> 继续轮询

        raise UpstreamError(f"任务 {task_id} 超时（{settings.TASK_TIMEOUT}s）", status=504)

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    async def download_as_b64(self, image_url: str) -> str:
        """下载图片并转为 base64（response_format=b64_json 时使用）。"""
        resp = await self.http.get(image_url, timeout=60)
        if resp.status_code != 200:
            raise UpstreamError(
                f"下载图片失败: HTTP {resp.status_code}", status=502
            )
        return base64.b64encode(resp.content).decode()
