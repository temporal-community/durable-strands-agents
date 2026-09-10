"""A tiny local HTTP CONNECT proxy used to fake a network outage on stage.

It never terminates TLS — it just relays raw bytes for an allowed CONNECT
tunnel, or refuses the tunnel outright for a blocked one. Point the worker's
HTTPS_PROXY/HTTP_PROXY at this, and boto3 (Bedrock) and feedparser/urllib
(the AWS RSS tool) both route through it automatically since they already
honor those env vars.

State lives in this module's globals: the FastAPI app calls set_kill_all()/
set_service() from request handlers, and each new CONNECT checks the current
state — no polling, no shared file.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("proxy")

# Substrings matched against the CONNECT target host. True in `services` means allowed.
SERVICE_HOSTS = {
    "bedrock": ["bedrock-runtime.", "bedrock."],
    "aws_feed": ["aws.amazon.com"],
}

_state = {
    "kill_all": False,
    "services": {key: True for key in SERVICE_HOSTS},
}

# Live CONNECT tunnels, so a policy change can kill already-open ones too — otherwise a
# kill toggled mid-demo would only stop *new* connections, and boto3/urllib3's keep-alive
# connection pool would happily keep reusing an already-established tunnel to Bedrock,
# making the switch look like it does nothing.
_active_tunnels: set[tuple[str, asyncio.StreamWriter, asyncio.StreamWriter]] = set()


def get_state() -> dict:
    return {"kill_all": _state["kill_all"], "services": dict(_state["services"])}


def set_kill_all(enabled: bool) -> dict:
    _state["kill_all"] = enabled
    if enabled:
        _kill_matching_tunnels()
    return get_state()


def set_service(key: str, enabled: bool) -> dict:
    if key not in _state["services"]:
        raise KeyError(key)
    _state["services"][key] = enabled
    if not enabled:
        _kill_matching_tunnels()
    return get_state()


def _is_blocked(host: str) -> bool:
    if _state["kill_all"]:
        return True
    for key, patterns in SERVICE_HOSTS.items():
        if _state["services"].get(key, True):
            continue
        if any(p in host for p in patterns):
            return True
    return False


def _kill_matching_tunnels() -> None:
    for host, client_writer, upstream_writer in list(_active_tunnels):
        if _is_blocked(host):
            logger.info("severing already-open tunnel to %s", host)
            client_writer.close()
            upstream_writer.close()


async def _pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await src.read(65536)
            if not chunk:
                break
            dst.write(chunk)
            await dst.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        dst.close()


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request_line = await reader.readline()
        if not request_line:
            writer.close()
            return
        # Drain the rest of the CONNECT request's headers.
        while True:
            line = await reader.readline()
            if not line or line in (b"\r\n", b"\n"):
                break

        parts = request_line.decode("latin-1").split()
        if len(parts) < 2 or parts[0] != "CONNECT":
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        target = parts[1]
        host, _, port_str = target.partition(":")
        port = int(port_str) if port_str else 443

        if _is_blocked(host):
            logger.info("blocked CONNECT %s", host)
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
        except OSError:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()

        tunnel = (host, writer, upstream_writer)
        _active_tunnels.add(tunnel)
        try:
            await asyncio.gather(
                _pump(reader, upstream_writer),
                _pump(upstream_reader, writer),
                return_exceptions=True,
            )
        finally:
            _active_tunnels.discard(tunnel)
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


async def start_proxy_server(host: str = "127.0.0.1", port: int = 8899) -> asyncio.base_events.Server:
    server = await asyncio.start_server(_handle_client, host, port)
    logger.info("network-kill-switch proxy listening on %s:%s", host, port)
    return server
