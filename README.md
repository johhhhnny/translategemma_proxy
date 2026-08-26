# TranslateGemma Proxy

一个自用的本地翻译代理，让“沉浸式翻译”或其他 OpenAI 兼容客户端通过本地 TranslateGemma 模型完成翻译。

## 为什么需要这个项目

“沉浸式翻译”适合在阅读网页时自动提取文本并逐段翻译，但使用在线翻译服务依赖网络并可能产生 API 费用。使用本地模型，可以让翻译数据尽量留在本地，并降低对外部服务和网络的依赖。

## 为什么选择 TranslateGemma

TranslateGemma 专门针对翻译任务训练，并提供适合 Apple Silicon 和 MLX 生态的本地模型版本。相比通用聊天模型，它更适合作为翻译代理的推理核心。

不过，TranslateGemma 的输入格式与 OpenAI Chat Completions 以及“沉浸式翻译”发送的数据格式并不完全相同，主要差异包括：

- TranslateGemma 需要带有 `source_lang_code`、`target_lang_code` 和 `text` 的结构化文本项
- OpenAI 兼容请求的 `content` 可能是字符串、对象或数组
- 客户端需要普通 JSON 或 SSE 流式响应，而模型本身产生的是本地生成器输出
- 网页翻译通常是大量短文本请求，需要代理统一转换格式并处理结束标记

本项目负责在客户端协议和 TranslateGemma 输入格式之间做适配，并将模型输出包装为客户端可以使用的响应格式。

## 隐私与适用范围

代理默认只监听 `127.0.0.1`，翻译内容发送给本机模型，不会由本项目主动上传到第三方服务。但本地系统、模型运行环境和客户端本身仍可能记录数据，请根据自己的隐私要求检查日志、模型服务和操作系统配置。

这是一个个人自用项目，不保证适用于所有网页、语言组合或 OpenAI 兼容客户端。翻译结果可能存在错译、漏译或术语不一致，重要内容请人工核对。

## 功能

- 使用 MLX 加载本地 TranslateGemma 模型
- 兼容 `/v1/chat/completions` 等 POST 请求路径
- 支持普通响应和 SSE 流式响应
- 支持字符串、对象和数组形式的 `content`
- 支持从请求体、消息或 content 项读取源语言和目标语言
- 记录请求 ID、翻译方向、推理耗时和输出片段数
- 通过 `.env` 外置模型路径、监听地址、端口和默认语言

## 环境要求

- macOS Apple Silicon
- 可访问 Metal GPU 的本机终端环境
- Python 3.10 或更高版本
- 已下载 TranslateGemma 的 MLX 模型

当前项目默认模型路径为：

```text
~/.lmstudio/models/mlx-community/translategemma-4b-it-8bit
```

## 安装

安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## 配置

复制配置模板：

```bash
cp .env.example .env
```

然后按需修改 `.env`：

```dotenv
TRANSLATEGEMMA_MODEL_PATH=~/.lmstudio/models/mlx-community/translategemma-4b-it-8bit
TRANSLATEGEMMA_HOST=127.0.0.1
TRANSLATEGEMMA_PORT=8001
TRANSLATEGEMMA_SOURCE_LANG=en
TRANSLATEGEMMA_TARGET_LANG=zh
TRANSLATEGEMMA_MAX_CHUNKS=1200
```

## 启动

```bash
python3 -m venv venv
source .venv/bin/activate                 
python3 translategemma_proxy.py
```

服务默认监听：

```text
http://127.0.0.1:8001
```

启动成功后应看到类似输出：

```text
🎉 模型加载成功！智能双轨通道已建立。
Uvicorn running on http://127.0.0.1:8001
```

## 测试普通响应

```bash
curl http://127.0.0.1:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"translategemma","messages":[{"role":"user","content":"Hello, world!"}]}'
```

## 测试流式响应

```bash
curl -N http://127.0.0.1:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"translategemma","stream":true,"messages":[{"role":"user","content":"Hello, world!"}]}'
```

流式响应会返回多段 `data: {...}`，正常结束时最后会返回：

```text
data: [DONE]
```

## 语言参数示例

可以在请求体中指定语言：

```bash
curl http://127.0.0.1:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"translategemma","source_lang":"en","target_lang":"zh","messages":[{"role":"user","content":"Good morning."}]}'
```

也支持 content 数组：

```json
{
  "model": "translategemma",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "Good morning."}
      ]
    }
  ]
}
```

未提供语言参数时，使用 `.env` 中的默认值，默认是 `en -> zh`。

## 许可证

本项目代码采用 MIT License。正式发布到 GitHub 前，请在仓库根目录添加完整的 `LICENSE` 文件。

TranslateGemma、MLX、FastAPI 和沉浸式翻译分别属于其各自项目或权利人的名称与产品。TranslateGemma 模型权重及相关资源不包含在本项目中，请遵守模型发布方和相关项目的许可证。

## 版本

当前版本：`v0.0.3`

- `v0.0.1`：请求解析、语言参数和错误状态码优化
- `v0.0.2`：流式响应测试及请求耗时、语言方向日志
- `v0.0.3`：外置运行配置
