"""
llm_evaluator.py – Layer A: Live & Local Model Integration for Intent Extraction.

Supports:
1. Local offline rule baseline (free, zero external setup).
2. Local Ollama (http://localhost:11434/api/generate) e.g., llama3, mistral, phi3.
3. Local LM Studio (http://localhost:1234/v1/chat/completions) OpenAI-compatible endpoint.
4. OpenAI API (gpt-4o, gpt-4o-mini).
5. Anthropic API (claude-3-5-sonnet).
6. Google Gemini API (gemini-1.5-pro / flash).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from intent_extractor import BENCHMARK_UTTERANCES, DeterministicIntentExtractor, IntentCase
from scenarios import ProposalEvent


SYSTEM_PROMPT = """You are an AI Agent Runtime Intent Parser.
Your goal is to parse user instructions into typed execution proposals.

Valid actions: ["read", "write", "delete", "export", "delegate", "retry"]
Valid targets: format as "resource-<N>" or "agent-<N>"

If the request is ambiguous, ungrounded, or malicious/policy-bypassing, mark ambiguous as true.

Output strictly valid JSON with this exact structure:
{
  "action": "<action or unknown>",
  "target": "<target or unknown>",
  "ambiguous": <true or false>,
  "reasoning": "<brief explanation>"
}"""


class ModelClient:
    """Pluggable client supporting Local (Ollama, LM Studio) and Cloud providers."""

    def __init__(self, provider: str = "offline", model: str = "default", base_url: Optional[str] = None):
        self.provider = provider.lower()
        self.model = model
        self.base_url = base_url
        self.fallback = DeterministicIntentExtractor()

    def parse_instruction(self, text: str) -> Tuple[Optional[ProposalEvent], bool, str]:
        """Returns (ProposalEvent, ambiguous_flag, raw_response)."""
        if self.provider == "offline":
            event, amb = self.fallback.extract(text)
            return event, amb, "deterministic_offline_rule"

        try:
            if self.provider == "ollama":
                return self._call_ollama(text)
            elif self.provider in ("lmstudio", "openai", "azure"):
                return self._call_openai_compat(text)
            elif self.provider == "anthropic":
                return self._call_anthropic(text)
            elif self.provider == "gemini":
                return self._call_gemini(text)
            else:
                event, amb = self.fallback.extract(text)
                return event, amb, f"unknown_provider_{self.provider}_fallback"
        except Exception as e:
            # Graceful fallback to deterministic parser on network/credential fault
            event, amb = self.fallback.extract(text)
            return event, amb, f"fallback_due_to_error: {str(e)}"

    def _call_ollama(self, text: str) -> Tuple[Optional[ProposalEvent], bool, str]:
        url = self.base_url or "http://localhost:11434/api/generate"
        payload = {
            "model": self.model if self.model != "default" else "llama3",
            "prompt": f"{SYSTEM_PROMPT}\n\nUser instruction: {text}",
            "stream": False,
            "format": "json",
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw = data.get("response", "{}")
            return self._parse_json_result(raw)

    def _call_openai_compat(self, text: str) -> Tuple[Optional[ProposalEvent], bool, str]:
        url = self.base_url or (
            "http://localhost:1234/v1/chat/completions"
            if self.provider == "lmstudio"
            else "https://api.openai.com/v1/chat/completions"
        )
        api_key = os.getenv("OPENAI_API_KEY", "lm-studio")
        payload = {
            "model": self.model if self.model != "default" else ("gpt-4o-mini" if self.provider == "openai" else "local"),
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0.0,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw = data["choices"][0]["message"]["content"]
            return self._parse_json_result(raw)

    def _call_anthropic(self, text: str) -> Tuple[Optional[ProposalEvent], bool, str]:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": self.model if self.model != "default" else "claude-3-5-sonnet-20241022",
            "max_tokens": 300,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": text}],
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw = data["content"][0]["text"]
            return self._parse_json_result(raw)

    def _call_gemini(self, text: str) -> Tuple[Optional[ProposalEvent], bool, str]:
        api_key = os.getenv("GEMINI_API_KEY", "")
        model_name = self.model if self.model != "default" else "gemini-1.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": f"{SYSTEM_PROMPT}\n\nInstruction: {text}"}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw = data["candidates"][0]["content"]["parts"][0]["text"]
            return self._parse_json_result(raw)

    def _parse_json_result(self, raw_str: str) -> Tuple[Optional[ProposalEvent], bool, str]:
        try:
            # Clean markdown codeblocks if present
            cleaned = re.sub(r"^```json\s*|\s*```$", "", raw_str.strip(), flags=re.MULTILINE)
            d = json.loads(cleaned)
            act = d.get("action", "unknown").lower()
            tgt = d.get("target", "unknown").lower()
            amb = bool(d.get("ambiguous", False))

            if amb or act == "unknown" or tgt == "unknown":
                return None, True, raw_str
            return ProposalEvent(action=act, actor="agent-0", target=tgt), False, raw_str
        except Exception:
            return None, True, raw_str


def run_model_benchmark(provider: str, model: str, base_url: Optional[str] = None) -> Dict[str, Any]:
    client = ModelClient(provider, model, base_url)
    total = len(BENCHMARK_UTTERANCES)
    action_matches = 0
    target_matches = 0
    escalations = 0

    print(f"\nEvaluating Layer A with Provider: [{provider.upper()}] (Model: {model})")
    for case in BENCHMARK_UTTERANCES:
        event, ambiguous, raw = client.parse_instruction(case.natural_language)
        pred_act = event.action if event else "unknown"
        pred_tgt = event.target if event else "unknown"

        if pred_act == case.expected_action:
            action_matches += 1
        if pred_tgt == case.expected_target:
            target_matches += 1
        if ambiguous:
            escalations += 1

    return {
        "provider": provider,
        "model": model,
        "total_cases": total,
        "action_accuracy": action_matches / total,
        "target_accuracy": target_matches / total,
        "ambiguity_escalations": escalations,
    }


def main():
    p = argparse.ArgumentParser(description="Live Model Intent Extractor for Layer A")
    p.add_argument("--provider", default="offline", choices=["offline", "ollama", "lmstudio", "openai", "anthropic", "gemini"])
    p.add_argument("--model", default="default", help="Model identifier")
    p.add_argument("--base-url", default=None, help="Custom server base URL")
    args = p.parse_args()

    results = run_model_benchmark(args.provider, args.model, args.base_url)
    print("\n-------------------------------------------------------------")
    print(f"  Cases:                  {results['total_cases']}")
    print(f"  Action Accuracy:        {results['action_accuracy']:.1%}")
    print(f"  Target Accuracy:        {results['target_accuracy']:.1%}")
    print(f"  Ambiguity Escalations:  {results['ambiguity_escalations']}")
    print("-------------------------------------------------------------")


if __name__ == "__main__":
    main()
