from __future__ import annotations

# Cross-region inference-profile IDs, same three tiers as the reference demo
# (mikegc-aws/jev-strands-video/demos/model_switching) and confirmed available in this
# account via `aws bedrock list-inference-profiles`.
MODELS_BY_SIZE: dict[str, str] = {
    "small": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "medium": "global.anthropic.claude-sonnet-4-6",
    "big": "global.anthropic.claude-opus-4-6-v1",
}

def build_reverse_size_map(models_by_size: dict[str, str]) -> dict[str, str]:
    """Invert a size->model_id map, raising if two sizes ever share a model_id.

    A silent positional inversion would let one size vanish from the reverse map with no
    error if a future edit ever reused a model_id — this fails loudly instead.
    """
    reverse: dict[str, str] = {}
    for size, model_id in models_by_size.items():
        if model_id in reverse:
            raise ValueError(f"model_id {model_id!r} is shared by sizes {reverse[model_id]!r} and {size!r}")
        reverse[model_id] = size
    return reverse


MODEL_ID_TO_SIZE: dict[str, str] = build_reverse_size_map(MODELS_BY_SIZE)

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
