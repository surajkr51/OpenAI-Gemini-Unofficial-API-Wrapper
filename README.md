# OPENAI GEMINI Unofficial API

A lightweight, high-performance, unofficial **OpenAI-compatible API gateway** for **Google Gemini Web** (`gemini-web`) and **OpenAI ChatGPT Web** (`chatgpt-web`).

Instead of relying on slow, memory-heavy browser automation tools like Selenium, this wrapper uses **`curl_cffi`** to impersonate Chrome's TLS/JA3 and HTTP/2 fingerprints at the raw socket level—delivering sub-second response latency and negligible RAM usage.

## ✨ Key Features

* **Unified OpenAI Schema:** Drop-in replacement exposing `/v1/chat/completions` and `/v1/images/generations`.

* **Browserless TLS/JA3 Impersonation:** Powered by `curl_cffi` (`impersonate="chrome"`) to bypass WAF and bot checks without launching headless Chrome.

* **Full Response & Streaming Modes:** Returns a clean, combined JSON response by default (`"stream": false`) or real-time Server-Sent Events (`"stream": true`).

* **Native Image Generation & Local Hosting:**

  * Extracts generated images from **Gemini Web** (`lh3.googleusercontent.com/gg-dl/...` with automatic fallback support) and **ChatGPT Web** (`sediment://` & `/backend-api/estuary/content`).

  * Automatically downloads and serves images locally at `http://127.0.0.1:8000/images/<filename>.png` or encodes them as `b64_json`.

* **Built-in ChatGPT Sentinel & PoW Solver:** Automatically negotiates `/backend-api/sentinel/chat-requirements` and solves SHA3-512 Proof-of-Work challenges in pure Python.

* **Hot-Reloading `.env` Credentials:** Detects changes to `.env` on the fly so you can refresh cookies or tokens without restarting the FastAPI server.

* **Cross-Platform Ready:** Pre-configured with `WindowsSelectorEventLoopPolicy` for out-of-the-box stability on Windows, Linux, and macOS.

## 📁 Project Structure

```
OPENAI-GEMINI-Unofficial-API/
├── main.py               # FastAPI server, routes, and static image mounting
├── providers.py          # Upstream curl_cffi engines for Gemini & ChatGPT
├── .env                  # Session cookies and bearer tokens (git-ignored)
├── generated_images/     # Auto-created directory storing downloaded PNGs
└── README.md             # Project documentation

```

## 🚀 Installation & Setup

### 1. Clone the Repository

```
git clone https://github.com/yourusername/OPENAI-GEMINI-Unofficial-API.git
cd OPENAI-GEMINI-Unofficial-API

```

### 2. Create a Virtual Environment & Install Dependencies

**Windows (PowerShell / CMD):**

```
python -m venv venv
venv\Scripts\activate
pip install "curl-cffi[asyncio]" fastapi uvicorn pydantic

```

**Linux / macOS:**

```
python3 -m venv venv
source venv/bin/activate
pip install "curl-cffi[asyncio]" fastapi uvicorn pydantic

```

## 🔑 Configuring Credentials (`.env`)

Create a `.env` file in the root directory next to `main.py`:

```
# Google Gemini Web Cookies
GEMINI_1PSID="your___Secure-1PSID_cookie"
GEMINI_1PSIDTS="your___Secure-1PSIDTS_cookie"
GEMINI_1PSIDCC="your___Secure-1PSIDCC_cookie_optional"

# OpenAI ChatGPT Web Access Token
CHATGPT_ACCESS_TOKEN="your_full_chatgpt_jwt_access_token"

```

### How to Extract Gemini Cookies:

1. Open <https://gemini.google.com/app> in Chrome (Incognito window recommended so background tabs don't rotate your cookies).

2. Press **F12** to open Developer Tools $\rightarrow$ **Application** tab $\rightarrow$ **Cookies** $\rightarrow$ `https://gemini.google.com`.

3. Copy the values of **`__Secure-1PSID`**, **`__Secure-1PSIDTS`**, and (optionally) **`__Secure-1PSIDCC`** into `.env`.

4. Close the Incognito tab immediately after copying to prevent Google from rotating `__Secure-1PSIDTS`.

### How to Extract Your ChatGPT Access Token:

1. Log into <https://chatgpt.com> in your browser.

2. Open <https://chatgpt.com/api/auth/session> in a new tab.

3. Copy the full, untruncated **`accessToken`** string from the JSON response and paste it into `CHATGPT_ACCESS_TOKEN` in `.env`.
   *(⚠️ Do not copy the token from the DevTools Network header preview if it contains a `…` truncation ellipsis).*

## 🖥️ Running the Server

Start the Uvicorn server:

```
python main.py

```

* **API Base URL:** `http://127.0.0.1:8000/v1`

* **Interactive Swagger UI:** `http://127.0.0.1:8000/docs`

* **Local Image Server:** `http://127.0.0.1:8000/images/`

## 📡 API Usage Examples

### 1. Text Generation (`POST /v1/chat/completions`)

#### Windows PowerShell (`Invoke-RestMethod`)

```
# Gemini Web (Full combined response)
(Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/chat/completions" `
  -Method Post -ContentType "application/json" `
  -Body '{"model": "gemini-web", "messages": [{"role": "user", "content": "What is Google in 100 words?"}]}').choices[0].message.content

# ChatGPT Web (Full combined response)
(Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/chat/completions" `
  -Method Post -ContentType "application/json" `
  -Body '{"model": "chatgpt-web", "messages": [{"role": "user", "content": "Explain API wrappers in 50 words."}]}').choices[0].message.content

```

#### Windows `curl.exe`

```
curl.exe -g -X POST http://127.0.0.1:8000/v1/chat/completions -H "Content-Type: application/json" -d '{\"model\": \"gemini-web\", \"messages\": [{\"role\": \"user\", \"content\": \"Hello from curl_cffi!\"}]}'

```

#### Real-Time Streaming (`"stream": true`)

```
curl.exe -g -N -X POST http://127.0.0.1:8000/v1/chat/completions -H "Content-Type: application/json" -d '{\"model\": \"chatgpt-web\", \"stream\": true, \"messages\": [{\"role\": \"user\", \"content\": \"Write a haiku about servers.\"}]}'

```

#### Example Response (`"stream": false`)

```
{
  "id": "chatcmpl-8f4d2a19c0b1",
  "object": "chat.completion",
  "created": 1794425000,
  "model": "gemini-web",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Google is a global technology leader..."
      },
      "finish_reason": "stop"
    }
  ]
}

```

### 2. Image Generation (`POST /v1/images/generations`)

Generates images using **Gemini Web** or **ChatGPT Web**, downloads the resulting `.png` into `./generated_images/`, and returns a local URL (or `b64_json`).

#### Generate & Auto-Open Image in Windows PowerShell

```
$res = Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/images/generations" `
  -Method Post -ContentType "application/json" `
  -Body '{"model": "chatgpt-web", "prompt": "a futuristic neon motorcycle parked on a rainy street at day desert"}'
$res | ConvertTo-Json -Depth 5
if ($res.data.Count -gt 0) { Start-Process $res.data[0].url }

```

#### Generate via `curl.exe` (`gemini-web` or `chatgpt-web`)

```
curl.exe -g -X POST http://127.0.0.1:8000/v1/images/generations -H "Content-Type: application/json" -d '{\"model\": \"gemini-web\", \"prompt\": \"a futuristic neon motorcycle parked on a rainy street at day desert\"}'

```

#### Example Image Response

```
{
  "created": 1794426120,
  "data": [
    {
      "url": "http://127.0.0.1:8000/images/chatgpt_4a91bc08e2.png",
      "revised_prompt": "a futuristic neon motorcycle parked on a rainy street at day desert"
    }
  ]
}

```

### 3. Using with the Official Python `openai` SDK

Because the endpoints follow the OpenAI specification, you can point the official `openai` Python library directly at your local server:

```
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="not-needed",
)

# Chat Completion
chat_resp = client.chat.completions.create(
    model="gemini-web",  # or "chatgpt-web"
    messages=[{"role": "user", "content": "Give me 3 Python performance tips."}],
    stream=False,
)
print(chat_resp.choices[0].message.content)

# Image Generation
img_resp = client.images.generate(
    model="chatgpt-web",  # or "gemini-web"
    prompt="A cyberpunk library floating in the clouds",
)
print("Saved Image URL:", img_resp.data[0].url)

```

## 🛠️ Troubleshooting

| Issue / Message | Cause & Fix | 
| ----- | ----- | 
| **`CHATGPT_ACCESS_TOKEN is missing or contains a truncated '…' character`** | Chrome DevTools truncates long headers with a literal `…` character. Fetch the full token from `https://chatgpt.com/api/auth/session`. | 
| **Gemini returns placeholder token without `lh3` URL** | Google rotated your `__Secure-1PSIDTS` cookie while a browser tab was open. Log in via an Incognito window, copy fresh cookies into `.env`, and close the Incognito window. | 
| **Windows `curl: (3) URL rejected: Bad hostname`** | Windows `cmd.exe` and PowerShell do not support multiline `\` backslashes or single-quoted JSON without escaping. Use `curl.exe -g` on a single line as shown above, or use `Invoke-RestMethod`. | 

## ⚠️ Disclaimer

This project is an **unofficial** reverse-engineered wrapper created strictly for educational, testing, and personal research purposes. It is not affiliated with, endorsed by, or supported by OpenAI or Google. Automating web interfaces may violate the respective platforms' Terms of Service—use responsibly and at your own risk.