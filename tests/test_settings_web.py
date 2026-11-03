from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from blueprint_ai.settings import read_settings
from blueprint_ai.settings_web import ASSETS, settings_server


@pytest.fixture
def local_settings(tmp_path: Path):
    with settings_server(tmp_path) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f"http://127.0.0.1:{server.server_port}"
        with urlopen(url, timeout=3) as page:
            html = page.read().decode()
            assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
        token_match = re.search(r'name="settings-token" content="([^"]+)"', html)
        assert token_match is not None
        token = token_match.group(1)
        try:
            yield url, token, tmp_path
        finally:
            server.shutdown()
            worker.join(timeout=3)


def request(url: str, token: str, body: dict, headers: dict | None = None):
    call = Request(
        url + "/api/settings",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Settings-Token": token,
            **(headers or {}),
        },
    )
    with urlopen(call, timeout=3) as response:
        return json.load(response)


def test_browser_api_shares_native_defaults_preview_save_and_stale_reset(local_settings) -> None:
    url, token, root = local_settings
    with urlopen(url + "/api/settings", timeout=3) as response:
        assert json.load(response) == read_settings(root)
    body = {
        "action": "preview",
        "updates": {"tool_timeout": 60},
        "resets": [],
        "expectedRevision": "missing",
    }
    assert request(url, token, body)["saved"] is False
    assert read_settings(root)["revision"] == "missing"
    body["action"] = "save"
    saved = request(url, token, body)
    assert saved["saved"] is True
    assert read_settings(root)["revision"] == saved["revision"]
    with pytest.raises(HTTPError) as error:
        request(url, token, body)
    assert error.value.code == 409
    body.update(updates={}, resets=["tool_timeout"], expectedRevision=saved["revision"])
    reset = request(url, token, body)
    assert next(s for s in reset["settings"] if s["id"] == "tool_timeout")["source"] == "default"


@pytest.mark.parametrize(
    "headers,token_override",
    [
        ({"Origin": "https://external.example.test"}, None),
        ({"Host": "external.example.test"}, None),
        ({"Sec-Fetch-Site": "cross-site"}, None),
        ({}, "wrong-token"),
    ],
)
def test_cross_origin_and_unbound_writes_are_refused(
    local_settings, headers, token_override
) -> None:
    url, token, root = local_settings
    body = {
        "action": "save",
        "updates": {"max_workers": 8},
        "resets": [],
        "expectedRevision": "missing",
    }
    with pytest.raises(HTTPError) as error:
        request(url, token if token_override is None else token_override, body, headers)
    assert error.value.code == 403
    assert read_settings(root)["revision"] == "missing"


def test_invalid_request_and_unsafe_control_have_no_effect(local_settings) -> None:
    url, token, root = local_settings
    cases: list[dict[str, object]] = [
        {"action": "save"},
        {
            "action": "save",
            "updates": {"model_mode": "on"},
            "resets": [],
            "expectedRevision": "missing",
        },
    ]
    for body in cases:
        with pytest.raises(HTTPError) as error:
            request(url, token, body)
        assert error.value.code == 422
    assert read_settings(root)["revision"] == "missing"


def test_shared_appearance_assets_are_exact_and_same_origin(local_settings) -> None:
    url, _token, root = local_settings
    before = read_settings(root)
    for route, name, content_type in (
        ("/appearance.js", "appearance.js", "text/javascript"),
        ("/theme/index.js", "theme/index.js", "text/javascript"),
        ("/theme/LICENSE", "theme/LICENSE", "text/plain"),
    ):
        with urlopen(url + route, timeout=3) as response:
            assert response.read() == (ASSETS / name).read_bytes()
            assert response.headers["Content-Type"].startswith(content_type)
            assert response.headers["X-Content-Type-Options"] == "nosniff"
    for route in ("/theme/../settings.py", "/theme/package.json", "/appearance.js?other"):
        with pytest.raises(HTTPError) as error:
            urlopen(url + route, timeout=3)
        assert error.value.code == 404
    assert read_settings(root) == before
