import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from addon.api_client import ApiClient
from addon.settings import Settings
from addon.token_store import TokenStore


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
        self.handlers: dict[tuple[str, str], list[Any]] = {}

    def on(self, method: str, path: str, *responses: Any) -> None:
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
        if body is None:
            return httpx.Response(status)
        return httpx.Response(status, json=body)

    def body(self, index: int) -> dict:
        return json.loads(self.calls[index].content)


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def settings() -> Settings:
    return Settings("http://localhost:8000", 5)


@pytest.fixture
def store(tmp_path: Path) -> TokenStore:
    return TokenStore(tmp_path / "user_files" / "session.json")


class ClientFactory:
    """Builds clients on the fake backend. Logs in first when a /auth/login
    response is scripted."""

    def __init__(
        self, backend: FakeBackend, settings: Settings, store: TokenStore
    ) -> None:
        self._backend = backend
        self._settings = settings
        self._store = store
        self.sleeps: list[float] = []

    def __call__(self, *, login: bool = True) -> ApiClient:
        client = ApiClient(
            lambda: self._settings,
            self._store,
            transport=httpx.MockTransport(self._backend),
            sleep=self.sleeps.append,
        )
        if login and ("POST", "/auth/login") in self._backend.handlers:
            client.login("a@b.c", "pw-secret")
        return client


@pytest.fixture
def make_client(
    backend: FakeBackend, settings: Settings, store: TokenStore
) -> ClientFactory:
    return ClientFactory(backend, settings, store)
