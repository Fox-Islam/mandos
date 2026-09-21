"""Connection test + model discovery (token-safe)."""

from __future__ import annotations

import httpx
import respx

from orchestrator.cli.probe import check_member, list_models

BASE = "https://api.example.test/v1"
TOKEN = "sk-supersecret-token"


@respx.mock
def test_list_models_parses_data():
    respx.get(f"{BASE}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "model-a"}, {"id": "model-b"}]})
    )
    assert list_models(BASE, TOKEN) == ["model-a", "model-b"]


@respx.mock
def test_list_models_returns_none_on_failure():
    respx.get(f"{BASE}/models").mock(return_value=httpx.Response(500, text="boom"))
    assert list_models(BASE, TOKEN) is None


@respx.mock
def test_test_member_ok():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})
    )
    assert check_member(BASE, TOKEN, "m").status == "ok"


@respx.mock
def test_test_member_auth_error():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(401, text="nope"))
    assert check_member(BASE, TOKEN, "m").status == "auth_error"


@respx.mock
def test_test_member_model_not_found():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(404, text="no model"))
    assert check_member(BASE, TOKEN, "m").status == "model_not_found"


@respx.mock
def test_test_member_unreachable():
    respx.post(f"{BASE}/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    assert check_member(BASE, TOKEN, "m").status == "unreachable"


@respx.mock
def test_token_never_appears_in_results():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(401, text=TOKEN))
    result = check_member(BASE, TOKEN, "m")
    assert TOKEN not in result.detail
    assert TOKEN not in result.status
