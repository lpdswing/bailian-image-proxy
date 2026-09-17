# bailian-image-proxy

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.12-3776AB)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED)

**English** · [中文](README.md)

An **OpenAI-compatible** `POST /v1/images/generations` proxy for **Alibaba Cloud Bailian
(DashScope / Model Studio)** text-to-image models. Point any OpenAI-format client
(LangChain, Presenton, your own app) at this service and use Bailian's image models
without touching your code.

## Features

- **Drop-in OpenAI compatibility** — `client.images.generate()` just works, including
  `n`, `size`, and `response_format=b64_json`
- **Broad model coverage** — `qwen-image-2.0`, `qwen-image-2.0-pro`, `wan2.7-image`,
  `wan2.7-image-pro`, and the Wanxiang series
- **Automatic sync/async routing** — picks the channel per model, falls back to sync
  when a model rejects async calls
- **Live model list** — `GET /v1/models` is fetched from the upstream API and filtered to
  image models, so it never advertises models you cannot actually use
- **Size translation** — OpenAI's `1024x1024` becomes Bailian's `1024*1024`; legacy models
  snap to the nearest officially supported resolution by aspect ratio
- **Parameter passthrough** — `negative_prompt`, `prompt_extend`, `watermark`, `seed`
  via `extra_body`
- **Observability** — structured logs with model, size, channel, duration, and the raw
  upstream error for every request
- **Identifiable content moderation** — moderation blocks return
  `content_policy_violation`, distinguishable from technical failures
- **Single container** — FastAPI + uvicorn, ~60 MB image

## Quick start

```bash
git clone https://github.com/lpdswing/bailian-image-proxy.git
cd bailian-image-proxy
cp .env.example .env
# edit .env and set your Bailian API key (Bailian console → API-KEY)

docker compose up -d --build
```

Serves on `http://localhost:8000`:

```bash
curl http://localhost:8000/health
# {"status":"ok","upstream":"https://dashscope.aliyuncs.com"}
```

## Usage

### OpenAI SDK (Python)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="anything",          # if DASHSCOPE_API_KEY is set server-side
)                                # otherwise your Bailian API key

result = client.images.generate(
    model="qwen-image-2.0",
    prompt="a shiba inu playing guitar on the moon, cyberpunk style",
    size="1024x1024",            # converted to Bailian's 1024*1024
    n=1,
)
print(result.data[0].url)        # valid for 24 hours — download it promptly
```

### curl

```bash
curl http://localhost:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-key" \
  -d '{"model":"qwen-image-2.0","prompt":"a cat","size":"1024x1024"}'
```

### Bailian-specific parameters

```python
result = client.images.generate(
    model="qwen-image-2.0",
    prompt="a cat",
    extra_body={
        "negative_prompt": "blurry, low quality",
        "prompt_extend": True,   # intelligent prompt rewriting
        "watermark": False,
        "seed": 42,
    },
)
```

## Supported models

| Model | Sync | Async | Default | Upstream endpoint |
|---|:---:|:---:|---|---|
| `qwen-image-2.0` / `qwen-image-2.0-pro` | ✅ | ❌ | sync | multimodal-generation/generation |
| `wan2.7-image` / `wan2.7-image-pro` | ✅ | ✅ | sync | sync: multimodal-generation/generation<br>async: image-generation/generation |
| `qwen-image-3.0` series | ✅ | ✅ | sync | same as above |
| `qwen-image` / `qwen-image-plus` | — | ✅ | async | same as above |
| `wan2.2-t2i-*` / `wanx2.1-t2i-*` | — | ✅ | async | text2image/image-synthesis (legacy) |

Based on the [official text-to-image docs](https://help.aliyun.com/zh/model-studio/text-to-image)
and verified empirically:

- **Not every model supports async.** `qwen-image-2.0` returns
  `AccessDenied: current user api does not support asynchronous calls`. That error is
  **per-model, not per-account** — the same key runs `wan2.7-image` asynchronously fine.
- The service defaults to the **sync channel** because it is measurably faster
  (`qwen-image-2.0` ≈ 3.5s, `wan2.7-image` ≈ 15s; async adds polling overhead, ≈ 19s).
- Set `PREFER_ASYNC=true` to prefer the async task API for models that support it;
  unsupported models fall back automatically and the log records the channel actually
  used (`通道=同步`).
- The async endpoint is selected per model: `image-generation/generation` for newer
  models, `text2image/image-synthesis` for legacy ones. Both result shapes are parsed.

### Bailian Token plans (dedicated endpoint)

Token plans use a **dedicated domain** with different model names:

```bash
# .env
DASHSCOPE_BASE_URL=https://token-plan.cn-beijing.maas.aliyuncs.com
```

Available image models there are `qwen-image-2.0`, `qwen-image-2.0-pro`, `wan2.7-image`,
and `wan2.7-image-pro` (the public-domain names `qwen-image` and `wanx2.1-t2i-*` return
`Model not exist`). `GET /v1/models` reflects the real list for whichever domain you use.

## Authentication

Two modes:

1. **Server-side key** — set `DASHSCOPE_API_KEY`; clients may pass any `api_key`
2. **Client-side key** — leave `DASHSCOPE_API_KEY` empty; clients pass their own Bailian key

Optional `SERVER_API_KEY` puts this service behind its own bearer token
(the Bailian key must then come from the server). **Always set it on a public deployment.**

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DASHSCOPE_API_KEY` | empty | Bailian API key |
| `SERVER_API_KEY` | empty | Access key for this service (set it if public) |
| `DASHSCOPE_BASE_URL` | `https://dashscope.aliyuncs.com` | Use `dashscope-intl` for the international site |
| `PREFER_ASYNC` | false | Prefer the async task API where supported |
| `UPSTREAM_TIMEOUT` | 180 | Sync request timeout (seconds) |
| `TASK_TIMEOUT` | 300 | Total async polling timeout (seconds) |
| `TASK_POLL_INTERVAL` | 2 | Async polling interval (seconds) |
| `MAX_IMAGES` | 4 | Max `n` per request |
| `IMAGE_MODELS` | empty | Models to advertise on `/v1/models` (comma-separated); empty = fetched upstream |
| `MODELS_CACHE_TTL` | 600 | Upstream model list cache TTL (seconds) |

## Calling from another Docker service

Inside a container, `localhost` refers to that container. Use one of:

1. **Host gateway IP** — `docker inspect <container> --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}'`,
   then use `http://172.18.0.1:8000/v1`
2. **`host.docker.internal`** — works out of the box on Docker Desktop; on Linux add
   `extra_hosts: ["host.docker.internal:host-gateway"]`
3. **Shared network (most robust)** — `docker network connect bailian-image-proxy_default <container>`,
   then use `http://bailian-image-proxy:8000/v1`

The API key is the value of `SERVER_API_KEY`, not your Bailian key.

## Troubleshooting

```bash
docker compose logs -f bailian-image-proxy
```

```
生图请求: model=qwen-image-2.0 n=2 size=1024*1024 通道=同步 prompt=...
生图完成: model=qwen-image-2.0 n=2 通道=同步 耗时=3.58s (单张约 1.79s)
```

| HTTP | type / code | Meaning |
|---|---|---|
| 401 | `authentication_error` | Wrong access key — use `SERVER_API_KEY`, not the Bailian key |
| 400 | `content_policy_violation` / `DataInspectionFailed` | Blocked by Bailian's content moderation; retrying the same prompt rarely helps |
| 400 | `invalid_request_error` / `Model not exist` | Model not available on this domain — check `GET /v1/models` |
| 429 | `rate_limit_error` | Rate limited upstream, retry later |
| 504 | `upstream_error` | Upstream timeout — raise `UPSTREAM_TIMEOUT` / `TASK_TIMEOUT` |

## Development

```bash
pip install -r requirements.txt
export DASHSCOPE_API_KEY=sk-xxx
uvicorn app.main:app --reload --port 8000
```

Interactive docs at `http://localhost:8000/docs`.

```
app/
├── main.py       # FastAPI routes, auth, error mapping
├── dashscope.py  # upstream calls (sync/async channels + polling)
├── schemas.py    # OpenAI schemas, size mapping, model filtering
└── config.py     # environment configuration
```

## Limitations

- Image URLs are served from Bailian OSS and **expire after 24 hours**;
  `response_format=b64_json` makes the service download and encode them for you
- The sync channel returns one image per call; `n>1` issues n concurrent calls.
  The async channel accepts `n` directly
- Text-to-image only — image editing / variations (`/v1/images/edits`) are not implemented

## License

[MIT](LICENSE). Not affiliated with Alibaba Cloud. "Tongyi", "Bailian", "Qwen" and
"DashScope" are trademarks of their respective owners.