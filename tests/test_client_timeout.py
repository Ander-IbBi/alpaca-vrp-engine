"""Every broker call has to be able to give up.

alpaca-py issues its requests without a timeout. On a healthy connection that is
invisible; on a connection that dies mid-request it is the worst kind of failure, because
the loop blocks forever on a socket that will never answer while the process stays alive
and the restart wrapper sees nothing wrong. These tests pin the bound down.
"""

from __future__ import annotations

import pytest

from vrp_engine.alpaca.client import (
    CONNECT_TIMEOUT_SECONDS,
    READ_TIMEOUT_SECONDS,
    PaperAlpaca,
    bind_request_timeout,
)
from vrp_engine.config import Settings


class FakeSession:
    """Records what the caller asked for, the way requests.Session would receive it."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return "response"


class FakeClient:
    def __init__(self) -> None:
        self._session = FakeSession()


def test_a_call_that_names_no_timeout_gets_the_default():
    client = FakeClient()
    bind_request_timeout(client)

    client._session.request("GET", "/clock")

    assert client._session.calls[0]["timeout"] == (
        CONNECT_TIMEOUT_SECONDS,
        READ_TIMEOUT_SECONDS,
    )


def test_the_bound_session_still_returns_what_the_caller_expects():
    client = FakeClient()
    bind_request_timeout(client)
    assert client._session.request("GET", "/clock") == "response"


def test_a_caller_that_wants_its_own_timeout_keeps_it():
    client = FakeClient()
    bind_request_timeout(client)

    client._session.request("POST", "/orders", timeout=90)

    assert client._session.calls[0]["timeout"] == 90


def test_binding_twice_does_not_stack_wrappers():
    client = FakeClient()
    bind_request_timeout(client)
    once = client._session.request
    bind_request_timeout(client)
    assert client._session.request is once


def test_a_client_without_a_session_is_left_alone():
    bind_request_timeout(object())  # must not raise


def test_the_read_timeout_is_generous_enough_for_an_order():
    # A POST that times out after the broker accepted it would look like a rejection, so
    # the bound has to be far above any plausible round trip rather than merely tight.
    assert READ_TIMEOUT_SECONDS >= 30.0
    assert CONNECT_TIMEOUT_SECONDS <= READ_TIMEOUT_SECONDS


@pytest.mark.parametrize("plane", ["trading", "option_data", "stock_data"])
def test_every_plane_of_the_real_client_is_bounded(plane):
    # Constructing the alpaca clients performs no I/O, so this stays a keyless, offline
    # test while still covering the wiring rather than just the helper.
    broker = PaperAlpaca(
        Settings(alpaca_api_key="test-key", alpaca_secret_key="test-secret")
    )
    session = getattr(broker, plane)._session
    assert session._vrp_timeout_bound is True
