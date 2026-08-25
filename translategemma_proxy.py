from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import uvicorn
import re
import json
import asyncio
from mlx_lm import load, stream_generate

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_PATH = "/Users/zy/.lmstudio/models/mlx-community/translategemma-4b-it-8bit"

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

    return raw_text, source_lang or "en", target_lang or "zh"


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
        print(f"\n⚡ [网关捕获] 成功截获请求！客户端期望模式: {'【流式 Stream】' if is_stream_requested else '【普通 JSON】'}")
        
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
            print("🧠 M1 Pro 正在全速进行【流式同步】分发...")
            count = 0
            for chunk in stream_generate(model, tokenizer, prompt=prompt):
                chunk_text = chunk.text if hasattr(chunk, "text") else (chunk["text"] if isinstance(chunk, dict) and "text" in chunk else str(chunk))
                if any(sw in chunk_text for sw in stop_tokens):
                    print("🛑 拦截到结束符，流式刹车成功！")
                    break
                
                chunk_data = {
                    "id": "chatcmpl-localproxy", "object": "chat.completion.chunk", "created": 1677652288, "model": "translategemma",
                    "choices": [{"index": 0, "delta": {"content": chunk_text}, "finish_reason": None}]
                }
                yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.001)
                count += 1
                if count > 1200: break
                    
            end_data = {
                "id": "chatcmpl-localproxy", "object": "chat.completion.chunk", "created": 1677652288, "model": "translategemma",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            }
            yield f"data: {json.dumps(end_data, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            print("🌸 流式响应圆满结束。")

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # --- 轨条二：如果插件要普通 JSON ---
    else:
        print("🧠 M1 Pro 正在全速进行【非流式打包】推理...")
        translated_chunks = []
        count = 0
        for chunk in stream_generate(model, tokenizer, prompt=prompt):
            chunk_text = chunk.text if hasattr(chunk, "text") else (chunk["text"] if isinstance(chunk, dict) and "text" in chunk else str(chunk))
            if any(sw in chunk_text for sw in stop_tokens):
                print("🛑 拦截到结束符，打包刹车成功！")
                break
            translated_chunks.append(chunk_text)
            count += 1
            if count > 1200: break

        final_result = "".join(translated_chunks).strip()
        final_result = final_result.split("<end_of_turn>")[0].strip()
        print(f"🌸 打包组装完成，成功获得译文: {final_result[:30]}...")

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
    uvicorn.run(app, host="127.0.0.1", port=8001)
