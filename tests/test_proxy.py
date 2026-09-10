"""Unit tests for the network kill-switch proxy's policy logic — no real sockets."""

import proxy


class FakeWriter:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def setup_function():
    # Reset module-level state between tests.
    proxy._state["kill_all"] = False
    for key in proxy._state["services"]:
        proxy._state["services"][key] = True
    proxy._active_tunnels.clear()


def test_defaults_allow_everything():
    state = proxy.get_state()
    assert state["kill_all"] is False
    assert all(state["services"].values())


def test_kill_all_blocks_any_host():
    proxy.set_kill_all(True)
    assert proxy._is_blocked("bedrock-runtime.us-west-2.amazonaws.com")
    assert proxy._is_blocked("aws.amazon.com")
    assert proxy._is_blocked("anything.example.com")


def test_service_toggle_blocks_only_matching_host():
    proxy.set_service("bedrock", False)
    assert proxy._is_blocked("bedrock-runtime.us-west-2.amazonaws.com")
    assert not proxy._is_blocked("aws.amazon.com")


def test_set_service_rejects_unknown_key():
    try:
        proxy.set_service("not-a-real-service", False)
    except KeyError:
        return
    raise AssertionError("expected KeyError for unknown service key")


def test_kill_all_severs_already_open_tunnels():
    client_w, upstream_w = FakeWriter(), FakeWriter()
    proxy._active_tunnels.add(("bedrock-runtime.us-west-2.amazonaws.com", client_w, upstream_w))

    proxy.set_kill_all(True)

    assert client_w.closed and upstream_w.closed


def test_disabling_one_service_only_severs_its_own_tunnels():
    bedrock_client, bedrock_upstream = FakeWriter(), FakeWriter()
    feed_client, feed_upstream = FakeWriter(), FakeWriter()
    proxy._active_tunnels.add(("bedrock-runtime.us-west-2.amazonaws.com", bedrock_client, bedrock_upstream))
    proxy._active_tunnels.add(("aws.amazon.com", feed_client, feed_upstream))

    proxy.set_service("bedrock", False)

    assert bedrock_client.closed and bedrock_upstream.closed
    assert not feed_client.closed and not feed_upstream.closed
