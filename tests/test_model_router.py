"""Unit tests for the deterministic size->model policy — no I/O, no mocks."""

import pytest

from model_router import MODELS_BY_SIZE, pick_model_id


def test_maps_each_known_size_to_its_model():
    assert pick_model_id("small", confidence=0.9) == MODELS_BY_SIZE["small"]
    assert pick_model_id("medium", confidence=0.9) == MODELS_BY_SIZE["medium"]
    assert pick_model_id("big", confidence=0.9) == MODELS_BY_SIZE["big"]


def test_low_confidence_biases_up_to_the_big_model():
    assert pick_model_id("small", confidence=0.4) == MODELS_BY_SIZE["big"]
    assert pick_model_id("medium", confidence=0.59) == MODELS_BY_SIZE["big"]


def test_high_confidence_at_the_floor_is_not_biased():
    assert pick_model_id("small", confidence=0.6) == MODELS_BY_SIZE["small"]


def test_big_at_low_confidence_stays_big():
    assert pick_model_id("big", confidence=0.1) == MODELS_BY_SIZE["big"]


def test_unknown_size_raises():
    with pytest.raises(ValueError):
        pick_model_id("huge", confidence=0.9)
