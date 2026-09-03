"""Thin wrapper over the Anthropic SDK for the team's two call shapes:

* `ask_text`       — free-form Markdown analysis, streamed, optional web search
* `ask_structured` — Pydantic-validated JSON via `messages.parse`

Both calls put the shared market data in a cached system block *before* the
role prompt, so all nine team members in one run hit the same prompt cache.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Type, TypeVar

import anthropic
from pydantic import BaseModel

from .config import MODEL

T = TypeVar("T", bound=BaseModel)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_created: int = 0
    calls: int = 0
    web_searches: int = 0

    def add(self, usage) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_created += getattr(usage, "cache_creation_input_tokens", 0) or 0
        server = getattr(usage, "server_tool_use", None)
        if server is not None:
            self.web_searches += getattr(server, "web_search_requests", 0) or 0

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read": self.cache_read,
            "cache_created": self.cache_created,
            "web_searches": self.web_searches,
        }


class RefusalError(RuntimeError):
    pass


@dataclass
class TeamLLM:
    model: str = MODEL.model
    stream_to_stdout: bool = MODEL.stream
    usage: Usage = field(default_factory=Usage)
    _client: anthropic.Anthropic | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            # Resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login` profile.
            self._client = anthropic.Anthropic()
        return self._client

    # ------------------------------------------------------------------ #
    @staticmethod
    def _system_blocks(shared_context: str, role_prompt: str) -> list[dict]:
        return [
            {"type": "text", "text": shared_context, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": role_prompt},
        ]

    @staticmethod
    def _text_of(message) -> str:
        return "".join(b.text for b in message.content if b.type == "text")

    @staticmethod
    def _check_refusal(message, who: str) -> None:
        if message.stop_reason == "refusal":
            detail = ""
            if message.stop_details is not None:
                detail = f" ({message.stop_details.category}: {message.stop_details.explanation})"
            raise RefusalError(f"{who} 的請求被模型拒絕{detail}")

    # ------------------------------------------------------------------ #
    def ask_text(self, who: str, shared_context: str, role_prompt: str, user_prompt: str,
                 effort: str = "medium", web_search: bool = False,
                 web_search_max_uses: int = 6, max_tokens: int = 8000,
                 print_stream: bool | None = None) -> str:
        """Free-form analysis. Streams to stdout when enabled; returns full text."""
        print_stream = self.stream_to_stdout if print_stream is None else print_stream
        tools = []
        if web_search:
            tools.append({"type": "web_search_20260209", "name": "web_search",
                          "max_uses": web_search_max_uses})

        messages: list[dict] = [{"role": "user", "content": user_prompt}]
        chunks: list[str] = []

        # Server tools may return `pause_turn`; continue the turn a few times.
        for _ in range(4):
            kwargs = dict(
                model=self.model,
                max_tokens=max_tokens,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                system=self._system_blocks(shared_context, role_prompt),
                messages=messages,
            )
            if tools:
                kwargs["tools"] = tools

            with self.client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    chunks.append(text)
                    if print_stream:
                        print(text, end="", flush=True)
                final = stream.get_final_message()

            self.usage.add(final.usage)
            self._check_refusal(final, who)
            if final.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": final.content})
                continue
            break

        if print_stream:
            print()
        return "".join(chunks).strip()

    # ------------------------------------------------------------------ #
    def ask_structured(self, who: str, shared_context: str, role_prompt: str,
                       user_prompt: str, schema: Type[T], effort: str = "high",
                       max_tokens: int = 16000) -> T:
        """Structured decision validated against a Pydantic model."""
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            system=self._system_blocks(shared_context, role_prompt),
            messages=[{"role": "user", "content": user_prompt}],
            output_format=schema,
        )
        self.usage.add(response.usage)
        self._check_refusal(response, who)
        if response.parsed_output is None:
            raise RuntimeError(f"{who} 未回傳可解析的結構化輸出 (stop_reason={response.stop_reason})")
        return response.parsed_output


def api_key_present() -> bool:
    """True when the SDK will find credentials without prompting."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    cfg = os.path.expanduser("~/.config/anthropic")
    return os.path.isdir(cfg) and any(os.scandir(cfg))
