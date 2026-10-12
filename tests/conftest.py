import json

import httpx
import pytest

from addon.api_client import ApiClient
from addon.settings import Settings


def tokens(n: int, expires_in: int = 3600) -> dict:
    return {
        "access_token": f"access{n}",
        "refresh_token": f"refresh{n}",
        "expires_in": expires_in,
    }


class FakeBackend:
    """Scriptable transport. `handlers[(method, path)]` is a list of responses
    consumed in order; the last one repeats."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.handlers: dict[tuple[str, str], list] = {}

    def on(self, method: str, path: str, *responses) -> None:
        self.handlers[(method, path)] = list(responses)

    def count(self, method: str, path: str) -> int:
        return sum(1 for c in self.calls if c.method == method and c.url.path == path)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        queue = self.handlers[(request.method, request.url.path)]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, json=body)

    def body(self, index: int) -> dict:
        return json.loads(self.calls[index].content)


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def settings() -> Settings:
    return Settings("http://localhost:8000", "a@b.c", "pw-secret", 5)


@pytest.fixture
def make_client(backend, settings):
    sleeps: list[float] = []

    def factory() -> ApiClient:
        return ApiClient(
            lambda: settings,
            transport=httpx.MockTransport(backend),
            sleep=sleeps.append,
        )

    factory.sleeps = sleeps  # type: ignore[attr-defined]
    return factory
