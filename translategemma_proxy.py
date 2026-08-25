from fastapi import FastAPI, Request
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
try:
    model, tokenizer = load(MODEL_PATH)
    print("🎉 模型加载成功！智能双轨通道已建立。")
except Exception as e:
    print(f"❌ 加载模型失败，请检查路径。错误: {str(e)}")

@app.post("/{path:path}")
async def catch_all(path: str, request: Request):
    try:
        body = await request.json()
        raw_text = body["messages"][-1]["content"]
        
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
    except Exception as e:
        return {"error": "Invalid OpenAI request format"}

    # 组装 Prompt 模板
    messages = [{
        "role": "user",
        "content": [{
            "type": "text", "source_lang_code": "en", "target_lang_code": "zh", "text": clean_text
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

@app.post("/{path:path}")
async def catch_all(path: str, request: Request):
    return await process_any_request(request)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
