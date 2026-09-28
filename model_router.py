from __future__ import annotations

# Cross-region inference-profile IDs, same three tiers as the reference demo
# (mikegc-aws/jev-strands-video/demos/model_switching) and confirmed available in this
# account via `aws bedrock list-inference-profiles`.
MODELS_BY_SIZE: dict[str, str] = {
    "small": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "medium": "global.anthropic.claude-sonnet-4-6",
    "big": "global.anthropic.claude-opus-4-6-v1",
}

MODEL_ID_TO_SIZE: dict[str, str] = {model_id: size for size, model_id in MODELS_BY_SIZE.items()}

CONFIDENCE_FLOOR = 0.6


def pick_model_id(size: str, confidence: float, *, confidence_floor: float = CONFIDENCE_FLOOR) -> str:
    """Map a Jev-classified size to a Bedrock model ID.

    Jev only classifies; this is the policy. Below the confidence floor we bias up to the
    big model rather than silently downgrading on an unsure classification — the reference
    demo's own "honest caveats" flag this exact gap as unhandled in their version.
    """
    if size not in MODELS_BY_SIZE:
        raise ValueError(f"unknown size: {size!r}")
    if confidence < confidence_floor:
        return MODELS_BY_SIZE["big"]
    return MODELS_BY_SIZE[size]
