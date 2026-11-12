import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from providers import UpstreamProviders, IMAGE_DIR

# Ensure Windows compatibility for curl_cffi's libcurl event loop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

providers = UpstreamProviders()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await providers.close()


app = FastAPI(title="Unified curl_cffi LLM Wrapper", lifespan=lifespan)

# Mount local folder to serve downloaded images at http://127.0.0.1:8000/images/<file>
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=str(IMAGE_DIR)), name="images")


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "gemini-web"  # Options: "gemini-web" or "chatgpt-web"
    messages: List[ChatMessage]
    stream: Optional[bool] = False


class ImageGenerationRequest(BaseModel):
    model: str = "gemini-web"  # Options: "gemini-web" or "chatgpt-web"
    prompt: str
    n: Optional[int] = 1
    size: Optional[str] = "1024x1024"
    response_format: Optional[str] = "url"  # Options: "url" or "b64_json"


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    if not request.messages:
        raise HTTPException(status_code=400, detail="Messages list cannot be empty.")

    if request.model.startswith("gemini"):
        transcript = "\n".join(
            [f"{m.role.upper()}: {m.content}" for m in request.messages]
        )
        if request.stream:
            return StreamingResponse(
                providers.stream_gemini(transcript),
                media_type="text/event-stream",
            )
        full_response = await providers.complete_gemini(transcript)
        return JSONResponse(content=full_response)

    elif request.model.startswith("chatgpt"):
        msg_dicts = [{"role": m.role, "content": m.content} for m in request.messages]
        if request.stream:
            return StreamingResponse(
                providers.stream_chatgpt(msg_dicts),
                media_type="text/event-stream",
            )
        full_response = await providers.complete_chatgpt(msg_dicts)
        return JSONResponse(content=full_response)

    raise HTTPException(
        status_code=400,
        detail="Unsupported model. Use 'gemini-web' or 'chatgpt-web'.",
    )


@app.post("/v1/images/generations")
async def image_generations(request: ImageGenerationRequest):
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    if request.model.startswith("gemini"):
        result = await providers.generate_gemini_image(
            prompt=request.prompt,
            response_format=request.response_format or "url",
        )
        return JSONResponse(content=result)

    elif request.model.startswith("chatgpt"):
        result = await providers.generate_chatgpt_image(
            prompt=request.prompt,
            response_format=request.response_format or "url",
        )
        return JSONResponse(content=result)

    raise HTTPException(
        status_code=400,
        detail="Unsupported model. Use 'gemini-web' or 'chatgpt-web'.",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)