import asyncio
import json
import os
import re
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from mlx_lm import load, stream_generate
import uvicorn


def _load_dotenv() -> None:
    config_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.isfile(config_path):
        return

    with open(config_path, encoding="utf-8") as config_file:
        for line in config_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip("\"'")
            if name:
                os.environ.setdefault(name, value)


_load_dotenv()

MODEL_PATH = os.getenv(
    "TRANSLATEGEMMA_MODEL_PATH",
    "~/.lmstudio/models/mlx-community/translategemma-4b-it-8bit",
)
MODEL_PATH = os.path.expanduser(MODEL_PATH)
HOST = os.getenv("TRANSLATEGEMMA_HOST", "127.0.0.1")
PORT = int(os.getenv("TRANSLATEGEMMA_PORT", "8001"))
DEFAULT_SOURCE_LANG = os.getenv("TRANSLATEGEMMA_SOURCE_LANG", "en")
DEFAULT_TARGET_LANG = os.getenv("TRANSLATEGEMMA_TARGET_LANG", "zh")
MAX_CHUNKS = int(os.getenv("TRANSLATEGEMMA_MAX_CHUNKS", "1200"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

print("正在将 TranslateGemma 动力核心直接加载至 Mac M1 Pro 内存中...")
model = None
tokenizer = None
model_load_error = None
try:
    model, tokenizer = load(MODEL_PATH)
    print("🎉 模型加载成功！智能双轨通道已建立。")
except Exception as e:
    model_load_error = e
    print(f"❌ 加载模型失败，请检查路径。错误: {str(e)}")

def _language_value(source: dict, names: tuple[str, ...]) -> str | None:
    for name in names:
        value = source.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_request_data(body: dict) -> tuple[str, str, str]:
    if not isinstance(body, dict):
        raise ValueError("request body must be an object")

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list")

    message = messages[-1]
    if not isinstance(message, dict):
        raise ValueError("the last message must be an object")

    language_names = {
        "source": ("source_lang_code", "source_lang", "source_language"),
        "target": ("target_lang_code", "target_lang", "target_language"),
    }
    source_lang = _language_value(body, language_names["source"])
    target_lang = _language_value(body, language_names["target"])
    source_lang = source_lang or _language_value(message, language_names["source"])
    target_lang = target_lang or _language_value(message, language_names["target"])

    content = message.get("content")
    text_parts = []
    if isinstance(content, str):
        text_parts.append(content)
    elif isinstance(content, dict):
        content_text = content.get("text")
        if isinstance(content_text, str):
            text_parts.append(content_text)
        source_lang = source_lang or _language_value(content, language_names["source"])
        target_lang = target_lang or _language_value(content, language_names["target"])
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                part_text = part.get("text")
                if isinstance(part_text, str):
                    text_parts.append(part_text)
                source_lang = source_lang or _language_value(part, language_names["source"])
                target_lang = target_lang or _language_value(part, language_names["target"])
    else:
        raise ValueError("message content must be a string, object, or list")

    raw_text = "".join(text_parts).strip()
    if not raw_text:
        raise ValueError("message content must contain text")

    return raw_text, source_lang or DEFAULT_SOURCE_LANG, target_lang or DEFAULT_TARGET_LANG


@app.post("/{path:path}")
async def catch_all(path: str, request: Request):
    if model is None or tokenizer is None:
        detail = "TranslateGemma model is unavailable"
        if model_load_error:
            detail = f"{detail}: {model_load_error}"
        raise HTTPException(status_code=503, detail=detail)

    try:
        body = await request.json()
        raw_text, source_lang, target_lang = _extract_request_data(body)

        # 检查插件是否显式要求流式
        is_stream_requested = body.get("stream", False)
        request_id = uuid.uuid4().hex[:12]
        print(f"\n⚡ [{request_id}] [网关捕获] 客户端期望模式: {'【流式 Stream】' if is_stream_requested else '【普通 JSON】'}")
        print(f"🌐 [{request_id}] 翻译方向: {source_lang} -> {target_lang}")
        
        # 1. 过滤前缀
        clean_text = re.sub(r'^.*?(：|:)\s*', '', raw_text, flags=re.DOTALL).strip()
        if not clean_text:
            clean_text = raw_text
            
        # 2. 抹除 %% 和压缩换行
        clean_text = clean_text.replace("%%", "").strip()
        clean_text = re.sub(r'\n\s*\n', '\n', clean_text)
        
        print(f"🧼 脱水过滤完成: {repr(clean_text[:50])}...")
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid OpenAI request format: {e}") from e

    # 组装 Prompt 模板
    messages = [{
        "role": "user",
        "content": [{
            "type": "text", "source_lang_code": source_lang, "target_lang_code": target_lang, "text": clean_text
        }]
    }]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    stop_tokens = ["<end_of_turn>", "<eos>", "<|im_end|>"]

    # --- 轨条一：如果插件要流式（Stream） ---
    if is_stream_requested:
        async def event_generator():
            started_at = time.perf_counter()
            print(f"🧠 [{request_id}] M1 Pro 正在进行【流式同步】分发...")
            count = 0
            try:
                for chunk in stream_generate(model, tokenizer, prompt=prompt):
                    chunk_text = chunk.text if hasattr(chunk, "text") else (chunk["text"] if isinstance(chunk, dict) and "text" in chunk else str(chunk))
                    if any(sw in chunk_text for sw in stop_tokens):
                        print(f"🛑 [{request_id}] 拦截到结束符，流式刹车成功！")
                        break

                    chunk_data = {
                        "id": "chatcmpl-localproxy", "object": "chat.completion.chunk", "created": 1677652288, "model": "translategemma",
                        "choices": [{"index": 0, "delta": {"content": chunk_text}, "finish_reason": None}]
                    }
                    yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0.001)
                    count += 1
                    if count > MAX_CHUNKS: break
            finally:
                elapsed = time.perf_counter() - started_at
                print(f"⏱️ [{request_id}] 流式推理耗时: {elapsed:.2f}s，输出片段: {count}")
                    
            end_data = {
                "id": "chatcmpl-localproxy", "object": "chat.completion.chunk", "created": 1677652288, "model": "translategemma",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            }
            yield f"data: {json.dumps(end_data, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            print(f"🌸 [{request_id}] 流式响应圆满结束。")

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # --- 轨条二：如果插件要普通 JSON ---
    else:
        started_at = time.perf_counter()
        print(f"🧠 [{request_id}] M1 Pro 正在进行【非流式打包】推理...")
        translated_chunks = []
        count = 0
        try:
            for chunk in stream_generate(model, tokenizer, prompt=prompt):
                chunk_text = chunk.text if hasattr(chunk, "text") else (chunk["text"] if isinstance(chunk, dict) and "text" in chunk else str(chunk))
                if any(sw in chunk_text for sw in stop_tokens):
                    print(f"🛑 [{request_id}] 拦截到结束符，打包刹车成功！")
                    break
                translated_chunks.append(chunk_text)
                count += 1
                if count > MAX_CHUNKS: break
        finally:
            elapsed = time.perf_counter() - started_at
            print(f"⏱️ [{request_id}] 非流式推理耗时: {elapsed:.2f}s，输出片段: {count}")

        final_result = "".join(translated_chunks).strip()
        final_result = final_result.split("<end_of_turn>")[0].strip()
        print(f"🌸 [{request_id}] 打包组装完成，成功获得译文: {final_result[:30]}...")

        # 返回最标准的、绝对不会报 Unexpected token 'd' 错误的纯正 JSON 字典
        return {
            "id": "chatcmpl-localproxy",
            "object": "chat.completion",
            "created": 1677652288,
            "model": "translategemma",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": final_result},
                "finish_reason": "stop"
            }]
        }

if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
