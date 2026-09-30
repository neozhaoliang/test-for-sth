# -*- coding: utf-8 -*-
"""Provider-agnostic external synthesis adapter for frozen snapshot replay.

An external model only supplies the structured research conclusion.  The project keeps
ownership of deterministic review, confidence capping, snapshot integrity and final
report validation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict

from pydantic import BaseModel, Field

from analysis.reviewer import review_dimension_analyses
from model.m_analysis import StructuredSummary


class ExternalSynthesisEnvelope(BaseModel):
    """Portable output contract for an LLM that lives outside this repository."""

    provider: str = Field(default="external")
    model: str = Field(default="")
    prompt_version: str = Field(default="")
    summary: StructuredSummary

    def replay_metadata(self) -> Dict[str, str]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
        }


def load_external_synthesis(path: str | Path) -> ExternalSynthesisEnvelope:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ExternalSynthesisEnvelope.model_validate(payload)


def make_external_synthesis_fn(
    envelope: ExternalSynthesisEnvelope,
) -> Callable:
    """Build a replay synthesis function without importing any model SDK."""

    async def _synthesis_fn(inputs, candidates, evidence):
        summary = envelope.summary.model_copy(deep=True)
        review = review_dimension_analyses(
            summary.dimension_analyses or {},
            evidence,
        )
        return summary, review

    return _synthesis_fn
