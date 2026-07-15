"""Optional LLM classification step (disabled by default).

This never runs unless ENABLE_LLM_CLASSIFICATION=true. It only *refines* a job
that has already passed deterministic scoring — the deterministic system is the
source of truth and works with zero external cost.
"""

from __future__ import annotations

import json

from jobbot.logging import get_logger
from jobbot.parsing.models import ExtractedJob

log = get_logger(__name__)

_PROMPT = """You are classifying job postings. Given the posting below, respond \
with ONLY a compact JSON object: {{"is_software_internship": bool, \
"confidence": float 0..1, "reason": short string}}.

Title: {title}
Company: {company}
Location: {location}
Description: {description}
"""


class LLMClassifier:
    def __init__(self, api_key: str, model: str) -> None:
        # Imported lazily so the dependency is optional.
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def classify(self, job: ExtractedJob) -> tuple[bool, float, str]:
        prompt = _PROMPT.format(
            title=job.title or "",
            company=job.company or "",
            location=job.location or "",
            description=(job.description or "")[:1500],
        )
        try:
            msg = await self._client.messages.create(
                model=self._model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text  # type: ignore[union-attr]
            data = json.loads(text)
            return (
                bool(data.get("is_software_internship", False)),
                float(data.get("confidence", 0.0)),
                str(data.get("reason", "")),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("llm_classify_failed", error=str(exc))
            # Fail open: defer to the deterministic result.
            return True, 0.0, "llm unavailable"
