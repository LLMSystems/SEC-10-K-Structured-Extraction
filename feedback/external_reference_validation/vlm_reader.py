from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DEFAULT_MODEL = "google/gemma-4-31b-it"


class VLMImageReader:
    def __init__(
        self,
        model: str | None = None,
        cache_dir: str | Path = "vlm_cache",
        base_url: str | None = None,
    ) -> None:
        self.model = model or os.getenv("OPENROUTER_MODEL") or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
        self.cache_root = Path(cache_dir)
        self.cache_dir = self.cache_root / self.safe_model_name(self.model)
        self._client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")
            if not api_key:
                raise RuntimeError("Missing OPENAI_API_KEY or OPENROUTER_API_KEY in .env")

            headers = {}
            site_url = os.getenv("OPENROUTER_SITE_URL")
            app_name = os.getenv("OPENROUTER_APP_NAME")
            if site_url:
                headers["HTTP-Referer"] = site_url
            if app_name:
                headers["X-Title"] = app_name

            self._client = OpenAI(
                api_key=api_key,
                base_url=self.base_url,
                default_headers=headers,
            )

        return self._client

    def read_image(
        self,
        image_path: str | Path,
        prompt: str = None,
        use_cache: bool = True,
        force: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        image_sha = self.image_sha256(image_path)
        cache_key = self.cache_key(image_sha=image_sha, prompt=prompt)

        if use_cache and not force:
            cached = self._load_cache_entry(cache_key)
            if cached:
                print(f"Cache hit: {self._cache_entry_path(cache_key)}")
                return str(cached["text"])

        text = self._call_vlm(image_path=image_path, prompt=prompt, max_tokens=max_tokens)

        if use_cache:
            self._save_cache_entry(
                cache_key,
                {
                    "text": text,
                    "image_sha256": image_sha,
                    "prompt": prompt,
                    "model": self.model,
                    "base_url": self.base_url,
                    "source_path": str(image_path),
                    "created_at": int(time.time()),
                },
            )
            print(f"Cache saved: {self._cache_entry_path(cache_key)}")

        return text

    def image_sha256(self, image_path: str | Path) -> str:
        digest = hashlib.sha256()
        with Path(image_path).open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def cache_key(self, image_sha: str, prompt: str) -> str:
        payload = {
            "image_sha256": image_sha,
            "prompt": prompt,
            "model": self.model,
            "base_url": self.base_url,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def safe_model_name(self, model: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "__", model).strip("._-")
        return safe or "unknown_model"

    def requires_reasoning(self) -> bool:
        """Some OpenRouter endpoints reject reasoning=false and require the default behavior."""
        return self.model.lower() == "google/gemini-2.5-pro"

    def _call_vlm(self, image_path: Path, prompt: str, max_tokens: int | None = None) -> str:
        kwargs: dict = {}
        # 轉錄任務不需要推理。推理模型（如 qwen3.6）會花上千 token 思考、把 max_tokens 用光、
        # content 變空。OpenRouter 的 reasoning.enabled=False 可關掉（非推理模型為 no-op）。
        if "openrouter" in self.base_url and not self.requires_reasoning():
            kwargs["extra_body"] = {"reasoning": {"enabled": False}}

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": self._image_to_data_url(image_path),
                            },
                        },
                    ],
                }
            ],
            temperature=0,
            max_tokens=max_tokens,
            **kwargs,
        )

        message = response.choices[0].message.content
        if not message:
            raise RuntimeError("VLM returned an empty response")

        return message

    def read_images(self, image_paths, prompt: str = None, use_cache: bool = True,
                    force: bool = False, max_tokens: int | None = None) -> str:
        """多圖版 read_image（同一訊息給多張圖，如 tail 的 [ep-1, ep] 跨頁窗口）。"""
        paths = [Path(p) for p in image_paths]
        for p in paths:
            if not p.exists():
                raise FileNotFoundError(f"Image not found: {p}")
        joined_sha = "+".join(self.image_sha256(p) for p in paths)
        cache_key = self.cache_key(image_sha=joined_sha, prompt=prompt)
        if use_cache and not force:
            cached = self._load_cache_entry(cache_key)
            if cached:
                print(f"Cache hit: {self._cache_entry_path(cache_key)}")
                return str(cached["text"])
        text = self._call_vlm_multi(paths, prompt, max_tokens)
        if use_cache:
            self._save_cache_entry(cache_key, {
                "text": text, "image_sha256": joined_sha, "prompt": prompt,
                "model": self.model, "base_url": self.base_url,
                "source_path": ";".join(str(p) for p in paths), "created_at": int(time.time()),
            })
        return text

    def _call_vlm_multi(self, image_paths, prompt: str, max_tokens: int | None = None) -> str:
        kwargs: dict = {}
        if "openrouter" in self.base_url and not self.requires_reasoning():
            kwargs["extra_body"] = {"reasoning": {"enabled": False}}
        content = [{"type": "text", "text": prompt}]
        for p in image_paths:
            content.append({"type": "image_url",
                            "image_url": {"url": self._image_to_data_url(p)}})
        response = self.client.chat.completions.create(
            model=self.model, messages=[{"role": "user", "content": content}],
            temperature=0, max_tokens=max_tokens, **kwargs,
        )
        message = response.choices[0].message.content
        if not message:
            raise RuntimeError("VLM returned an empty response")
        return message

    def _image_to_data_url(self, image_path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(image_path)
        if mime_type is None:
            mime_type = "image/png"

        encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"

    def _cache_entry_path(self, cache_key: str) -> Path:
        return self.cache_dir / f"{cache_key}.json"

    def _load_cache_entry(self, cache_key: str) -> dict[str, Any] | None:
        cache_path = self._cache_entry_path(cache_key)
        if not cache_path.exists():
            return None

        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Cache file is not valid JSON: {cache_path}") from exc

    def _save_cache_entry(self, cache_key: str, entry: dict[str, Any]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self._cache_entry_path(cache_key)
        temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(cache_path)
