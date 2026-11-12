import asyncio
import base64
import hashlib
import json
import os
import random
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List
from urllib.parse import quote, urljoin
from curl_cffi import AsyncSession

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
IMAGE_DIR = BASE_DIR / "generated_images"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)


def load_env_file() -> None:
    """Force-loads .env from the script directory, overriding stale shell variables."""
    if not ENV_PATH.exists():
        return
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ[key.strip()] = val.strip().strip('"').strip("'")


load_env_file()


class UpstreamProviders:
    def __init__(self):
        self.session = AsyncSession(impersonate="chrome", timeout=120)
        self.env_mtime: float = 0.0
        self.gemini_init_done: bool = False
        self.gemini_snlm0e: str | None = None
        self.gemini_fsid: str | None = None
        self.gemini_bl: str = "boq_assistant-bard-web-server_20260716.08_p0"
        self.req_id: int = random.randint(100000, 999999)
        self.chatgpt_token: str = ""
        self.chatgpt_device_id: str = str(uuid.uuid4())
        self._reload_credentials_if_changed(force=True)

    def _reload_credentials_if_changed(self, force: bool = False) -> None:
        """Reloads .env automatically whenever the file is saved."""
        current_mtime = ENV_PATH.stat().st_mtime if ENV_PATH.exists() else 0.0
        if not force and current_mtime == self.env_mtime:
            return

        load_env_file()
        self.env_mtime = current_mtime
        self.gemini_init_done = False
        self.gemini_snlm0e = None

        psid = os.getenv("GEMINI_1PSID", "").strip()
        psidts = os.getenv("GEMINI_1PSIDTS", "").strip()
        psidcc = os.getenv("GEMINI_1PSIDCC", "").strip()

        if psid and not psid.startswith("your_"):
            self.session.cookies.set("__Secure-1PSID", psid, domain=".google.com")
        if psidts and not psidts.startswith("your_"):
            self.session.cookies.set("__Secure-1PSIDTS", psidts, domain=".google.com")
        if psidcc and not psidcc.startswith("your_"):
            self.session.cookies.set("__Secure-1PSIDCC", psidcc, domain=".google.com")

        self.chatgpt_token = os.getenv("CHATGPT_ACCESS_TOKEN", "").strip()
        self.chatgpt_device_id = os.getenv("CHATGPT_DEVICE_ID", self.chatgpt_device_id)

    async def close(self):
        await self.session.close()

    # ---------------------------------------------------------
    # SHARED IMAGE DOWNLOADER (Supports Gemini, ChatGPT Estuary & Redirects)
    # ---------------------------------------------------------
    async def _download_and_save_image(
        self, img_url: str, prefix: str, extra_headers: dict | None = None
    ) -> tuple[str, str]:
        """Downloads an image via curl_cffi and saves it into generated_images/."""
        default_referer = (
            "https://chatgpt.com/"
            if "chatgpt.com" in img_url or "oaiusercontent.com" in img_url
            else "https://gemini.google.com/"
        )
        headers = {
            "Referer": default_referer,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            **(extra_headers or {}),
        }

        candidate_urls = (
            [f"{img_url}=s0", f"{img_url}=s2048", img_url]
            if "lh3.googleusercontent.com" in img_url and "=" not in img_url
            else [img_url]
        )

        last_error = "Unknown download error"
        for target_url in candidate_urls:
            for attempt in range(3):
                try:
                    # Handle manual redirect hop so Authorization isn't leaked to Azure/R2 storage
                    resp = await self.session.get(
                        target_url,
                        headers=headers,
                        allow_redirects=False,
                    )
                    if resp.status_code in (301, 302, 303, 307, 308):
                        redirect_url = resp.headers.get("location") or resp.headers.get("Location")
                        if redirect_url:
                            redirect_url = urljoin(target_url, redirect_url)
                            redirect_headers = dict(headers)
                            if "chatgpt.com" not in redirect_url:
                                redirect_headers.pop("Authorization", None)
                            resp = await self.session.get(
                                redirect_url,
                                headers=redirect_headers,
                                allow_redirects=True,
                            )

                    if resp.status_code == 200 and len(resp.content) > 500:
                        img_bytes = resp.content
                        filename = f"{prefix}_{uuid.uuid4().hex[:10]}.png"
                        filepath = IMAGE_DIR / filename
                        filepath.write_bytes(img_bytes)
                        local_url = f"http://127.0.0.1:8000/images/{filename}"
                        b64_str = base64.b64encode(img_bytes).decode("utf-8")
                        return local_url, b64_str

                    last_error = f"HTTP {resp.status_code} ({len(resp.content)} bytes): {resp.text[:200]}"
                except Exception as exc:
                    last_error = str(exc)

                # Brief backoff if Estuary file is still finalizing on OpenAI's CDN
                await asyncio.sleep(1.5)

        raise RuntimeError(f"Failed to download image from {img_url}: {last_error}")

    # ---------------------------------------------------------
    # GEMINI WEB ENGINE (Text & Imagen / Nano Banana)
    # ---------------------------------------------------------
    async def init_gemini_tokens(self) -> None:
        """Scrapes cfb2h (bl), FdrFJe (f.sid), and optional SNlM0e from Gemini Web."""
        self._reload_credentials_if_changed()
        try:
            headers = {
                "Origin": "https://gemini.google.com",
                "Referer": "https://gemini.google.com/",
                "X-Same-Domain": "1",
            }
            resp = await self.session.get(
                "https://gemini.google.com/app",
                headers=headers,
                allow_redirects=True,
            )
            if resp.status_code == 200:
                snlm0e_match = re.search(r'"SNlM0e":"([^"]+)"', resp.text)
                if snlm0e_match:
                    self.gemini_snlm0e = snlm0e_match.group(1)

                bl_match = re.search(r'"cfb2h":"([^"]+)"', resp.text)
                if bl_match:
                    self.gemini_bl = bl_match.group(1)

                fsid_match = re.search(r'"FdrFJe":"([^"]+)"', resp.text)
                if fsid_match:
                    self.gemini_fsid = fsid_match.group(1)
        except Exception:
            pass
        finally:
            self.gemini_init_done = True

    @staticmethod
    def _extract_gemini_text(inner_json: list) -> str | None:
        try:
            if not isinstance(inner_json, list) or len(inner_json) <= 4:
                return None
            candidates = inner_json[4]
            if not candidates or not isinstance(candidates, list):
                return None
            first_candidate = candidates[0]
            if len(first_candidate) > 1 and isinstance(first_candidate[1], list):
                for part in first_candidate[1]:
                    if isinstance(part, str) and part:
                        return part
        except (IndexError, TypeError):
            pass
        return None

    @classmethod
    def _extract_gemini_image_urls_from_raw(cls, raw_stream: str) -> List[str]:
        cleaned = (
            raw_stream.replace("\\/", "/")
            .replace("\\u003d", "=")
            .replace("\\u0026", "&")
            .replace('\\"', '"')
            .replace("\\n", " ")
        )
        pattern = re.compile(
            r"https://lh3\.googleusercontent\.com/(?:rd-)?(?:gg-dl|gg|aida)/[A-Za-z0-9_\-]+"
        )
        found: List[str] = []
        for match in pattern.findall(cleaned):
            base_url = match.split("=")[0]
            if base_url not in found:
                found.append(base_url)
        return found

    async def _fallback_image_generation(
        self, prompt: str, response_format: str = "url"
    ) -> List[dict]:
        seed = random.randint(1000, 999999)
        encoded_prompt = quote(prompt)
        fallback_url = (
            f"https://image.pollinations.ai/prompt/{encoded_prompt}"
            f"?width=1024&height=1024&seed={seed}&nologo=true&model=flux"
        )
        local_url, b64_str = await self._download_and_save_image(
            fallback_url, prefix="gemini_fallback", extra_headers={"Referer": ""}
        )
        if response_format == "b64_json":
            return [{"b64_json": b64_str, "revised_prompt": prompt}]
        return [
            {
                "url": local_url,
                "upstream_url": fallback_url,
                "revised_prompt": prompt,
            }
        ]

    def _parse_wrb_chunk(self, raw_chunk: str, current_last_text: str) -> str:
        for match in re.finditer(r'\[\["wrb\.fr",null,("(?:\\.|[^"\\])*")', raw_chunk):
            try:
                inner_str = json.loads(match.group(1))
                inner = json.loads(inner_str)
                candidate_text = self._extract_gemini_text(inner)
                if candidate_text:
                    current_last_text = candidate_text
            except Exception:
                continue
        return current_last_text

    async def _run_gemini_request(self, prompt: str) -> tuple[str, List[str]]:
        self._reload_credentials_if_changed()
        if not self.gemini_init_done:
            await self.init_gemini_tokens()

        url = "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
        params = {
            "bl": self.gemini_bl,
            "hl": "en",
            "_reqid": str(self.req_id),
            "rt": "c",
        }
        if self.gemini_fsid:
            params["f.sid"] = self.gemini_fsid
        self.req_id += 100000

        inner_req = json.dumps([[prompt], None, [None, None, None]])
        form_data = {"f.req": json.dumps([None, inner_req])}
        if self.gemini_snlm0e:
            form_data["at"] = self.gemini_snlm0e

        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://gemini.google.com",
            "Referer": "https://gemini.google.com/",
            "X-Same-Domain": "1",
        }

        last_text = ""
        full_raw_buffer = ""

        async with self.session.stream(
            "POST",
            url,
            params=params,
            data=form_data,
            headers=headers,
        ) as resp:
            if resp.status_code != 200:
                return "", []

            async for chunk in resp.aiter_content():
                decoded = chunk.decode("utf-8", errors="ignore")
                full_raw_buffer += decoded
                last_text = self._parse_wrb_chunk(decoded, last_text)

        last_text = self._parse_wrb_chunk(full_raw_buffer, last_text)
        image_urls = self._extract_gemini_image_urls_from_raw(full_raw_buffer)

        if image_urls and last_text:
            last_text = re.sub(
                r"https?://googleusercontent\.com/image_generation_content/\S+",
                "",
                last_text,
            ).strip()

        return last_text, image_urls

    async def stream_gemini_deltas(self, prompt: str) -> AsyncGenerator[str, None]:
        try:
            text, image_urls = await self._run_gemini_request(prompt)

            if text:
                yield text

            for raw_url in image_urls:
                try:
                    local_url, _ = await self._download_and_save_image(
                        raw_url, prefix="gemini"
                    )
                    yield f"\n\n![Generated Image]({local_url})"
                except Exception:
                    yield f"\n\n![Generated Image]({raw_url})"

            if not text and not image_urls:
                yield "[Gemini Error]: Upstream returned no text or image candidates."

        except Exception as exc:
            yield f"[Gemini Exception]: {exc}"

    async def stream_gemini(self, prompt: str) -> AsyncGenerator[str, None]:
        async for delta in self.stream_gemini_deltas(prompt):
            yield self._format_sse_chunk(delta, model="gemini-web")
        yield "data: [DONE]\n\n"

    async def complete_gemini(self, prompt: str) -> dict:
        parts = []
        async for delta in self.stream_gemini_deltas(prompt):
            parts.append(delta)
        return self._format_full_response("".join(parts), model="gemini-web")

    async def generate_gemini_image(
        self, prompt: str, response_format: str = "url"
    ) -> dict:
        gen_prompt = f"Generate an image of: {prompt}"
        text, image_urls = await self._run_gemini_request(gen_prompt)

        data_items = []
        for raw_url in image_urls:
            try:
                local_url, b64_str = await self._download_and_save_image(
                    raw_url, prefix="gemini"
                )
                if response_format == "b64_json":
                    data_items.append({"b64_json": b64_str, "revised_prompt": prompt})
                else:
                    data_items.append(
                        {
                            "url": local_url,
                            "upstream_url": raw_url,
                            "revised_prompt": prompt,
                        }
                    )
            except Exception as exc:
                data_items.append({"url": raw_url, "download_error": str(exc)})

        if not data_items:
            try:
                data_items = await self._fallback_image_generation(
                    prompt=prompt, response_format=response_format
                )
                return {
                    "created": int(time.time()),
                    "provider_note": (
                        "Gemini returned placeholder token without lh3 URL; "
                        "fulfilled via fallback image engine."
                    ),
                    "gemini_raw_message": text,
                    "data": data_items,
                }
            except Exception as exc:
                return {
                    "created": int(time.time()),
                    "message": text or f"Image generation failed: {exc}",
                    "data": [],
                }

        return {"created": int(time.time()), "data": data_items}

    # ---------------------------------------------------------
    # CHATGPT WEB ENGINE (Sentinel + Text & Image Generation)
    # ---------------------------------------------------------
    def _build_pow_config(self) -> list:
        now_str = datetime.now(timezone.utc).strftime(
            "%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)"
        )
        return [
            1920 + 1080,
            now_str,
            4294705152,
            0,
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            None,
            None,
            "en-US",
            "en-US,en",
            0,
            "webdriver−false",
            "_reactListening",
            "location",
            8,
            time.time() * 1000,
        ]

    def _solve_chatgpt_pow(self, seed: str, difficulty: str) -> str:
        config = self._build_pow_config()
        diff_len = len(difficulty)
        for i in range(200000):
            config[3] = i
            config[9] = i / 10.0
            json_str = json.dumps(config, separators=(",", ":"))
            b64_data = base64.b64encode(json_str.encode("utf-8")).decode("utf-8")
            digest = hashlib.sha3_512((seed + b64_data).encode("utf-8")).hexdigest()
            if digest[:diff_len] <= difficulty:
                return "gAAAAAB" + b64_data
        return "gAAAAAB"

    async def _get_chatgpt_sentinel_headers(self, base_headers: dict) -> dict:
        init_config = self._build_pow_config()
        p_token = "gAAAAAC" + base64.b64encode(
            json.dumps(init_config, separators=(",", ":")).encode("utf-8")
        ).decode("utf-8")

        resp = await self.session.post(
            "https://chatgpt.com/backend-api/sentinel/chat-requirements",
            json={"p": p_token},
            headers=base_headers,
        )
        if resp.status_code != 200:
            body = resp.text[:300]
            raise RuntimeError(
                f"Sentinel chat-requirements failed ({resp.status_code}): {body}"
            )

        data = resp.json()
        sentinel_headers = {
            "openai-sentinel-chat-requirements-token": data.get("token", ""),
        }
        pow_info = data.get("proofofwork", {})
        if pow_info.get("required"):
            sentinel_headers["openai-sentinel-proof-token"] = self._solve_chatgpt_pow(
                pow_info.get("seed", ""), pow_info.get("difficulty", "0")
            )
        return sentinel_headers

    async def _resolve_chatgpt_file_url(
        self, asset_pointer: str, base_headers: dict
    ) -> str | None:
        file_id = (
            asset_pointer.replace("file-service://", "")
            .replace("sediment://", "")
            .strip()
        )
        if not file_id:
            return None
        resp = await self.session.get(
            f"https://chatgpt.com/backend-api/files/download/{file_id}",
            headers=base_headers,
        )
        if resp.status_code == 200:
            dl_url = resp.json().get("download_url")
            if dl_url and dl_url.startswith("/"):
                dl_url = f"https://chatgpt.com{dl_url}"
            return dl_url
        return None

    async def stream_chatgpt_deltas(
        self, messages: List[Dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        self._reload_credentials_if_changed()
        try:
            if not self.chatgpt_token or "…" in self.chatgpt_token:
                raise RuntimeError(
                    "CHATGPT_ACCESS_TOKEN is missing or contains a truncated '…' character. "
                    "Fetch the full token from https://chatgpt.com/api/auth/session"
                )

            base_headers = {
                "Authorization": f"Bearer {self.chatgpt_token}",
                "Content-Type": "application/json",
                "Origin": "https://chatgpt.com",
                "Referer": "https://chatgpt.com/",
                "oai-device-id": self.chatgpt_device_id,
                "oai-language": "en-US",
            }

            sentinel_headers = await self._get_chatgpt_sentinel_headers(base_headers)
            headers = {
                **base_headers,
                **sentinel_headers,
                "Accept": "text/event-stream",
            }

            formatted_messages = [
                {
                    "id": str(uuid.uuid4()),
                    "author": {"role": msg["role"]},
                    "content": {"content_type": "text", "parts": [msg["content"]]},
                }
                for msg in messages
            ]

            payload = {
                "action": "next",
                "messages": formatted_messages,
                "parent_message_id": str(uuid.uuid4()),
                "model": "auto",
                "timezone_offset_min": -330,
                "history_and_training_disabled": False,
                "conversation_mode": {"kind": "primary_assistant"},
                "websocket_request_id": str(uuid.uuid4()),
            }

            pending_pointers: List[str] = []

            async with self.session.stream(
                "POST",
                "https://chatgpt.com/backend-api/conversation",
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.atext()
                    yield f"[ChatGPT HTTP {resp.status_code} Error]: {body[:400]}"
                    return

                buffer = ""
                last_text = ""

                async for chunk in resp.aiter_content():
                    buffer += chunk.decode("utf-8", errors="ignore")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line.startswith("data: "):
                            continue
                        raw_data = line[6:].strip()
                        if raw_data == "[DONE]":
                            break
                        try:
                            event = json.loads(raw_data)
                            msg = event.get("message", {})
                            if not isinstance(msg, dict):
                                continue
                            parts = msg.get("content", {}).get("parts", [])
                            role = msg.get("author", {}).get("role", "")

                            for part in parts:
                                if role == "assistant" and isinstance(part, str):
                                    delta = part[len(last_text):]
                                    last_text = part
                                    if delta:
                                        yield delta
                                elif (
                                    isinstance(part, dict)
                                    and part.get("content_type") == "image_asset_pointer"
                                ):
                                    pointer = part.get("asset_pointer", "")
                                    if pointer and pointer not in pending_pointers:
                                        pending_pointers.append(pointer)
                        except json.JSONDecodeError:
                            continue

            # Resolve and download generated images after the stream completes
            dl_headers = {
                "Authorization": f"Bearer {self.chatgpt_token}",
                "Origin": "https://chatgpt.com",
                "Referer": "https://chatgpt.com/",
                "oai-device-id": self.chatgpt_device_id,
                "oai-language": "en-US",
            }
            for pointer in pending_pointers:
                dl_url = await self._resolve_chatgpt_file_url(pointer, base_headers)
                if dl_url:
                    local_url, _ = await self._download_and_save_image(
                        dl_url, prefix="chatgpt", extra_headers=dl_headers
                    )
                    yield f"\n\n![Generated Image]({local_url})"

        except Exception as exc:
            yield f"[ChatGPT Exception]: {exc}"

    async def stream_chatgpt(
        self, messages: List[Dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        async for delta in self.stream_chatgpt_deltas(messages):
            yield self._format_sse_chunk(delta, model="chatgpt-web")
        yield "data: [DONE]\n\n"

    async def complete_chatgpt(self, messages: List[Dict[str, str]]) -> dict:
        parts = []
        async for delta in self.stream_chatgpt_deltas(messages):
            parts.append(delta)
        return self._format_full_response("".join(parts), model="chatgpt-web")

    async def generate_chatgpt_image(
        self, prompt: str, response_format: str = "url"
    ) -> dict:
        messages = [{"role": "user", "content": f"Generate an image: {prompt}"}]
        output_chunks = []
        async for delta in self.stream_chatgpt_deltas(messages):
            output_chunks.append(delta)

        combined = "".join(output_chunks)
        urls = re.findall(
            r"http://127\.0\.0\.1:8000/images/[A-Za-z0-9_\.]+\.png", combined
        )

        data_items = []
        for local_url in urls:
            if response_format == "b64_json":
                filename = local_url.rsplit("/", 1)[-1]
                img_bytes = (IMAGE_DIR / filename).read_bytes()
                data_items.append(
                    {
                        "b64_json": base64.b64encode(img_bytes).decode("utf-8"),
                        "revised_prompt": prompt,
                    }
                )
            else:
                data_items.append({"url": local_url, "revised_prompt": prompt})

        if not data_items:
            return {"created": int(time.time()), "message": combined, "data": []}

        return {"created": int(time.time()), "data": data_items}

    # ---------------------------------------------------------
    # OPENAI FORMATTERS (STREAMING & FULL)
    # ---------------------------------------------------------
    @staticmethod
    def _format_sse_chunk(delta_content: str, model: str) -> str:
        chunk = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": delta_content},
                    "finish_reason": None,
                }
            ],
        }
        return f"data: {json.dumps(chunk)}\n\n"

    @staticmethod
    def _format_full_response(full_content: str, model: str) -> dict:
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": full_content,
                    },
                    "finish_reason": "stop",
                }
            ],
        }