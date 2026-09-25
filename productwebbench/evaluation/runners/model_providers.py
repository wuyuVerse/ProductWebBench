from __future__ import annotations

import io
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChatRequest:
    model: str
    prompt: str
    max_tokens: int
    temperature: float = 0.0
    # Modality C (design-fidelity): optional base64-encoded PNG design screenshots.
    # When present, content becomes an OpenAI-style multimodal array so a VLM
    # solver can *see* the target design. Empty -> plain text (modality A), unchanged.
    images_b64: tuple = ()


@dataclass
class ChatResult:
    ok: bool
    content: str = ""
    error: str = ""
    latency_sec: float = 0.0
    usage: dict = field(default_factory=dict)


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, api_key: str, timeout_sec: int = 300, max_retries: int = 2, retry_backoff_sec: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.retry_backoff_sec = retry_backoff_sec

    def complete(self, request: ChatRequest) -> ChatResult:
        if request.images_b64:
            content = [{"type": "text", "text": request.prompt}]
            for b64 in request.images_b64:
                content.append({"type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64}"}})
        else:
            content = request.prompt
        payload = {
            "model": request.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        http_request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.time()
        errors = []
        for attempt in range(self.max_retries + 1):
            try:
                data = self._post_json(http_request, payload)
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return ChatResult(
                    ok=True,
                    content=content,
                    latency_sec=round(time.time() - started, 2),
                    usage=data.get("usage", {}),
                )
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                error = f"HTTP {exc.code}: {body[:2000]}"
                errors.append(error)
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= self.max_retries:
                    return ChatResult(ok=False, error=error, latency_sec=round(time.time() - started, 2))
            except Exception as exc:
                error = repr(exc)
                errors.append(error)
                if attempt >= self.max_retries:
                    return ChatResult(ok=False, error="; ".join(errors[-3:]), latency_sec=round(time.time() - started, 2))
            time.sleep(self.retry_backoff_sec * (attempt + 1))
        return ChatResult(ok=False, error="; ".join(errors[-3:]), latency_sec=round(time.time() - started, 2))

    def _post_json(self, http_request: urllib.request.Request, payload: dict) -> dict:
        if shutil.which("curl"):
            return self._post_json_with_curl(payload)
        with urllib.request.urlopen(http_request, timeout=self.timeout_sec) as response:
            return json.loads(response.read().decode("utf-8", "replace"))

    def _post_json_with_curl(self, payload: dict) -> dict:
        marker = "\n__PRODUCTWEBBENCH_HTTP_STATUS__:"
        body = json.dumps(payload)
        command = [
            "curl",
            "-sS",
            "--max-time",
            str(self.timeout_sec),
            "-X",
            "POST",
            self.base_url + "/chat/completions",
            "-H",
            "Authorization: Bearer " + self.api_key,
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            "@-",
            "-w",
            marker + "%{http_code}",
        ]
        proc = subprocess.run(
            command,
            input=body,
            text=True,
            capture_output=True,
            timeout=self.timeout_sec + 15,
        )
        output = proc.stdout
        if marker not in output:
            message = (proc.stderr or output or f"curl exit {proc.returncode}")[:2000]
            raise TimeoutError(message) if proc.returncode == 28 else RuntimeError(message)
        response_body, status_text = output.rsplit(marker, 1)
        status = int(status_text.strip() or "0")
        if status >= 400:
            raise urllib.error.HTTPError(
                self.base_url + "/chat/completions",
                status,
                response_body[:2000],
                hdrs=None,
                fp=io.BytesIO(response_body.encode("utf-8", errors="replace")),
            )
        if proc.returncode != 0:
            message = (proc.stderr or response_body or f"curl exit {proc.returncode}")[:2000]
            raise RuntimeError(message)
        return json.loads(response_body)
