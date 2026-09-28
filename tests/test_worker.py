"""Unit tests for worker.py's make_strands_plugin() — no real Bedrock/boto calls."""

import worker
from model_router import MODELS_BY_SIZE


class FakeBedrockModel:
    """Records every construction so we can assert it happens eagerly, not lazily."""

    instances: list["FakeBedrockModel"] = []

    def __init__(self, *, model_id=None, boto_client_config=None):
        self.model_id = model_id
        self.boto_client_config = boto_client_config
        FakeBedrockModel.instances.append(self)


def test_make_strands_plugin_constructs_every_tier_eagerly_at_startup(monkeypatch):
    """A bad model_id/config for any one tier should surface at plugin-creation time (worker
    startup) rather than only when that tier is first selected at runtime — constructing all
    three up front, instead of lazily inside each factory, is what makes that possible."""
    FakeBedrockModel.instances.clear()
    monkeypatch.setattr(worker, "BedrockModel", FakeBedrockModel)

    worker.make_strands_plugin()

    assert {instance.model_id for instance in FakeBedrockModel.instances} == set(MODELS_BY_SIZE.values())


def test_make_strands_plugin_threads_the_https_proxy_into_every_tier(monkeypatch):
    FakeBedrockModel.instances.clear()
    monkeypatch.setattr(worker, "BedrockModel", FakeBedrockModel)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8899")

    worker.make_strands_plugin()

    assert len(FakeBedrockModel.instances) == len(MODELS_BY_SIZE)
    for instance in FakeBedrockModel.instances:
        assert instance.boto_client_config.proxies == {
            "https": "http://127.0.0.1:8899",
            "http": "http://127.0.0.1:8899",
        }
