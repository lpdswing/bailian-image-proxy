# bailian-image-proxy

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/lpdswing/bailian-image-proxy/actions/workflows/ci.yml/badge.svg)](https://github.com/lpdswing/bailian-image-proxy/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-3776AB)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED)

把**阿里云百炼（DashScope / Model Studio）的文生图模型**封装成 **OpenAI SDK 兼容**的
`POST /v1/images/generations`，让任何支持 OpenAI 格式的代码或工具（LangChain、n8n、
各类 AI 应用）无需改造就能直接用上百炼的生图能力。

## 特性

- **OpenAI 兼容**：`client.images.generate()` 直接可用，支持 `n`、`size`、`response_format=b64_json`
- **模型全覆盖**：`qwen-image-2.0` / `qwen-image-2.0-pro` / `wan2.7-image` / `wan2.7-image-pro` / 万相系列
- **同步异步自动选路**：按模型能力自动选择，不支持异步时自动回退同步
- **模型列表实时同步**：`GET /v1/models` 从上游官方接口拉取并过滤，不会给出用不了的模型
- **尺寸自动换算**：OpenAI 的 `1024x1024` 自动转百炼的 `1024*1024`，旧模型按宽高比匹配官方分辨率
- **参数透传**：`negative_prompt` / `prompt_extend` / `watermark` / `seed` 经 `extra_body` 直达百炼
- **可观测**：每次请求都有模型、尺寸、通道、耗时、上游原始错误的结构化日志
- **内容审核可识别**：审核拦截返回 `content_policy_violation`，与技术故障区分得开
- **单容器部署**：FastAPI + uvicorn，镜像约 60MB

## 快速开始

### 方式 1：用预构建镜像（不用克隆）

```bash
docker run -d --name bailian-image-proxy -p 8000:8000 \
  -e DASHSCOPE_API_KEY=sk-你的百炼Key \
  -e SERVER_API_KEY=自己设一个访问密钥 \
  ghcr.io/lpdswing/bailian-image-proxy:latest
```

镜像标签：`latest`（最新正式版）、`1.0.0` / `1.0`（版本号）、`edge`（main 分支最新）、
`sha-xxxxxxx`（对应提交）。支持 `linux/amd64` 与 `linux/arm64`。

### 方式 2：从源码构建

```bash
git clone https://github.com/lpdswing/bailian-image-proxy.git
cd bailian-image-proxy
cp .env.example .env
# 编辑 .env，填入百炼 API Key（百炼控制台 → API-KEY）

docker compose up -d --build
```

服务监听 `http://localhost:8000`，健康检查：

```bash
curl http://localhost:8000/health
# {"status":"ok","upstream":"https://dashscope.aliyuncs.com"}
```

## 用法

### OpenAI SDK（Python）

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="任意值",            # 服务端已配 DASHSCOPE_API_KEY 时随便填
)                               # 未配则填你的百炼 API Key

result = client.images.generate(
    model="qwen-image-2.0",
    prompt="一只在月球上弹吉他的柴犬，赛博朋克风格",
    size="1024x1024",           # 自动转换为百炼格式 1024*1024
    n=1,
)
print(result.data[0].url)       # 图片 URL，24 小时内有效，请及时转存
```

### curl

```bash
curl http://localhost:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 你的密钥" \
  -d '{"model":"qwen-image-2.0","prompt":"一只猫","size":"1024x1024"}'
```

### 百炼专属参数（`extra_body` 透传）

```python
result = client.images.generate(
    model="qwen-image-2.0",
    prompt="一只猫",
    extra_body={
        "negative_prompt": "模糊, 低质量",
        "prompt_extend": True,   # 智能改写提示词
        "watermark": False,      # 是否加水印
        "seed": 42,
    },
)
```

### 返回 base64

```python
result = client.images.generate(..., response_format="b64_json")
```

## 支持的模型

| 模型 | 同步 | 异步 | 默认通道 | 上游接口 |
|---|:---:|:---:|---|---|
| `qwen-image-2.0` / `qwen-image-2.0-pro` | ✅ | ❌ | 同步 | multimodal-generation/generation |
| `wan2.7-image` / `wan2.7-image-pro` | ✅ | ✅ | 同步 | 同步: multimodal-generation/generation<br>异步: image-generation/generation |
| `qwen-image-3.0` 系列 | ✅ | ✅ | 同步 | 同上 |
| `qwen-image` / `qwen-image-plus` | — | ✅ | 异步 | 同上 |
| `wan2.2-t2i-*` / `wanx2.1-t2i-*` | — | ✅ | 异步 | text2image/image-synthesis（旧接口） |

依据[阿里云官方文生图文档](https://help.aliyun.com/zh/model-studio/text-to-image)并逐条实测：

- **并非所有模型都支持异步**。例如 `qwen-image-2.0` 走异步会返回
  `AccessDenied: current user api does not support asynchronous calls`
  （该报错是**模型级**的，不是账号级 —— 同一个 Key 调 `wan2.7-image` 异步完全正常）
- 服务默认走**同步通道**（实测更快：`qwen-image-2.0` 约 3.5s，`wan2.7-image` 约 15s；
  异步因轮询开销约 19s）
- `PREFER_ASYNC=true` 可让支持异步的模型优先走异步任务接口，不支持时自动回退同步，
  并在日志中如实标注实际通道（`通道=同步`）
- 异步任务接口按模型自动选择：新模型用 `image-generation/generation`，老模型用
  `text2image/image-synthesis`，两种任务结果结构都能解析

### Token 套餐（专享域名）

百炼 Token 套餐使用**专享域名**，模型命名与公共域名不同：

```bash
# .env
DASHSCOPE_BASE_URL=https://token-plan.cn-beijing.maas.aliyuncs.com
```

对应可用生图模型为 `qwen-image-2.0` / `qwen-image-2.0-pro` / `wan2.7-image` /
`wan2.7-image-pro`（公共域名的 `qwen-image`、`wanx2.1-t2i-*` 在此域名会报 `Model not exist`）。
`GET /v1/models` 会自动反映当前域名的真实可用列表。

## 鉴权

两种模式任选：

1. **服务端统一 Key**：`.env` 配 `DASHSCOPE_API_KEY`，客户端 `api_key` 随便填
2. **客户端自带 Key**：服务端不配 `DASHSCOPE_API_KEY`，客户端 `api_key` 填自己的百炼 Key

可选 `SERVER_API_KEY`：设置后本服务自身需鉴权，客户端必须带匹配的 Bearer Token
（此时百炼 Key 只能来自服务端）。**公网部署务必设置。**

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DASHSCOPE_API_KEY` | 空 | 百炼 API Key |
| `SERVER_API_KEY` | 空 | 本服务访问密钥（可选，公网部署必设） |
| `DASHSCOPE_BASE_URL` | `https://dashscope.aliyuncs.com` | 国际站用 `dashscope-intl`，Token 套餐用专享域名 |
| `PREFER_ASYNC` | false | true = 支持异步的模型优先走异步任务接口 |
| `UPSTREAM_TIMEOUT` | 180 | 同步请求超时（秒） |
| `TASK_TIMEOUT` | 300 | 异步任务轮询总超时（秒） |
| `TASK_POLL_INTERVAL` | 2 | 异步任务轮询间隔（秒） |
| `MAX_IMAGES` | 4 | 单次请求最大 `n` |
| `IMAGE_MODELS` | 空 | `/v1/models` 返回的模型（逗号分隔）；空 = 从上游自动拉取过滤 |
| `MODELS_CACHE_TTL` | 600 | 上游模型列表缓存时长（秒） |

## 接入其他 Docker 服务

另一个容器访问本服务时，`localhost` 指向它自己，必须换地址。三种方式：

**方式 1：宿主机网关 IP（最简单）**

```bash
docker inspect <你的容器名> --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}'
# 例如 172.18.0.1，则 base_url 填 http://172.18.0.1:8000/v1
```

**方式 2：`host.docker.internal`**（Docker Desktop 开箱可用；Linux 需在 compose 加
`extra_hosts: ["host.docker.internal:host-gateway"]`）

**方式 3：两容器同网络（最稳，不依赖宿主机 IP）**

```bash
docker network connect bailian-image-proxy_default <你的容器名>
# base_url 用容器名：http://bailian-image-proxy:8000/v1
```

**API Key** 填 `SERVER_API_KEY` 的值（本服务的访问密钥，不是百炼 Key）。

**Model** 填 `GET /v1/models` 返回的任意模型名，例如 `qwen-image-2.0`。

## 排查

每次请求都有结构化日志，含模型、尺寸、通道、耗时与上游原始错误：

```bash
docker compose logs -f bailian-image-proxy
docker compose logs -f bailian-image-proxy | grep -E "生图请求|生图完成"
```

```
生图请求: model=qwen-image-2.0 n=2 size=1024*1024 通道=同步 prompt=一枚精致的陶瓷茶杯...
生图完成: model=qwen-image-2.0 n=2 通道=同步 耗时=3.58s (单张约 1.79s)
```

耗时为整体墙钟时间；`n>1` 时服务并发调用上游，单张时间约为总时间除以张数。
`b64_json` 模式还包含图片下载与编码。失败的请求同样记录耗时。

### 常见错误

| HTTP | type / code | 含义与处理 |
|---|---|---|
| 401 | `authentication_error` | 访问密钥不对。填 `SERVER_API_KEY` 的值（不是百炼 Key） |
| 400 | `content_policy_violation` / `DataInspectionFailed` | **百炼内容审核（绿网）拦截**。提示词涉及敏感主题或生成结果被判定不适宜；换提示词或改用图库素材，同一提示词重试通常无效 |
| 400 | `invalid_request_error` / `Model not exist` | 模型名不被当前域名支持，用 `GET /v1/models` 查真实可用模型 |
| 429 | `rate_limit_error` | 百炼侧限流，稍后重试 |
| 504 | `upstream_error` | 上游超时，调大 `UPSTREAM_TIMEOUT` / `TASK_TIMEOUT` |

## 本地开发

```bash
pip install -r requirements.txt
export DASHSCOPE_API_KEY=sk-xxx
uvicorn app.main:app --reload --port 8000
```

交互式 API 文档：`http://localhost:8000/docs`

```
app/
├── main.py       # FastAPI 路由、鉴权、错误映射
├── dashscope.py  # 上游调用（同步/异步双通道 + 轮询）
├── schemas.py    # OpenAI 格式模型、尺寸映射、模型过滤
└── config.py     # 环境变量
```

## 已知限制

- 返回的图片 URL 由百炼 OSS 提供，**24 小时后失效**；`response_format=b64_json`
  时服务代为下载并转 base64
- 同步通道上一次只出一张图，`n>1` 时服务并发调用 n 次；异步通道可直接指定 `n`
- 仅实现文生图（generations）；图生图 / 图片编辑（edits、variations）未实现

## 许可

[MIT](LICENSE)。本项目与阿里云无隶属关系，"通义"、"百炼"、"Qwen"、"DashScope"
等商标归其各自所有者。