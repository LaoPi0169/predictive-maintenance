"""Connectivity + contract probe for a real OpenAI-compatible LLM endpoint.

Run this BEFORE the agent evaluation to verify the endpoint, credentials and
JSON contract work end to end:

    # DeepSeek example (PowerShell, one-shot env for the current session)
    $env:PMA_LLM_PROVIDER = "openai_compatible"
    $env:PMA_LLM_BASE_URL = "https://api.deepseek.com/v1"
    $env:PMA_LLM_MODEL    = "deepseek-chat"
    $env:PMA_LLM_API_KEY  = "<your key>"
    .venv\\Scripts\\python scripts\\llm_probe.py

The probe never prints the API key - only whether it is set and its length.
Exit codes: 0 = ready, 1 = endpoint/contract failure, 2 = configuration
incomplete.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402
from app.agent.llm import (  # noqa: E402
    LlmResponseInvalidError,
    LlmUnavailableError,
    build_chat_model,
    check_relevance,
)
from app.schemas import Citation, KnowledgeHit  # noqa: E402


def main() -> int:
    print(f"provider: {config.LLM_PROVIDER}")
    if config.LLM_PROVIDER != "openai_compatible":
        print("PMA_LLM_PROVIDER must be 'openai_compatible' for a real endpoint, e.g.:")
        print('  $env:PMA_LLM_PROVIDER = "openai_compatible"')
        print('  $env:PMA_LLM_BASE_URL = "https://api.deepseek.com/v1"')
        print('  $env:PMA_LLM_MODEL    = "deepseek-chat"')
        print('  $env:PMA_LLM_API_KEY  = "<your key>"')
        return 2

    print(f"base_url: {config.LLM_BASE_URL}")
    print(f"model:    {config.LLM_MODEL}")
    key_state = f"set (length {len(config.LLM_API_KEY)})" if config.LLM_API_KEY else "NOT SET"
    print(f"api_key:  {key_state}")
    print(
        f"transport: timeout={config.LLM_TIMEOUT_S}s, "
        f"max_retries={config.LLM_MAX_RETRIES}, json_mode={config.LLM_JSON_MODE}"
    )
    if not config.LLM_API_KEY:
        print("PMA_LLM_API_KEY is not set - cloud endpoints require it.")
        return 2

    try:
        model = build_chat_model("openai_compatible")
    except ValueError as exc:
        print(f"CONFIG FAIL: {exc}")
        return 2

    try:
        info = model.probe()
    except (LlmUnavailableError, LlmResponseInvalidError) as exc:
        print(f"PROBE FAIL: {exc}")
        return 1
    print(
        f"probe OK: {info['latency_ms']}ms, "
        f"json_mode_effective={info['json_mode_effective']}, "
        f"content={info['content']!r}"
    )

    hit = KnowledgeHit(
        citation=Citation(
            chunk_id="KB-SOP-001#c001",
            doc_id="KB-SOP-001",
            doc_version="2.1",
            section_title="检查步骤",
            source_location="probe canned text",
            score=0.4,
        ),
        text=(
            "P-103 轴承振动检查 SOP\n检查步骤\n"
            "1. 在轴承座水平、垂直、轴向三个测点分别测量振动速度 RMS；\n"
            "2. 每个测点连续测量 3 次取平均值。"
        ),
    )
    try:
        verdict = check_relevance(model, "轴承振动复测怎么做", [hit])
    except (LlmUnavailableError, LlmResponseInvalidError) as exc:
        print(f"RELEVANCE CONTRACT FAIL: {exc}")
        return 1
    print(
        "relevance contract: checked="
        f"{verdict.checked}, relevant={verdict.relevant}, reason={verdict.reason[:60]!r}"
    )
    if not verdict.checked:
        print("PROBE FAIL: relevance verdict was not produced")
        return 1
    print("PROBE PASS - endpoint ready for the agent evaluation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
