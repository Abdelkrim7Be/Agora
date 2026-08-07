"""Fetching a signature image from a user-supplied URL.

This is a server-side request forgery primitive by nature: the server connects
to an address a user chose, from inside the private network. Each test below is
one of the doors that has to stay shut.
"""

from __future__ import annotations

import pytest

from src.image_fetch import ImageFetchError, MAX_IMAGE_BYTES, fetch_image_bytes


def test_plain_http_is_refused():
    # Not pedantry: an http logo is rewritable by anyone on the path, and it
    # would then appear in every outgoing signature.
    with pytest.raises(ImageFetchError, match="https"):
        fetch_image_bytes("http://example.com/logo.png")


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/logo.png",
    "https://localhost/logo.png",
    "https://169.254.169.254/latest/meta-data/",   # cloud instance metadata
    "https://10.0.0.5/logo.png",
    "https://192.168.1.10/logo.png",
    "https://172.17.0.2/logo.png",                  # the docker bridge
])
def test_private_and_loopback_addresses_are_refused(url):
    with pytest.raises(ImageFetchError, match="interne|introuvable"):
        fetch_image_bytes(url)


def test_a_name_resolving_to_a_private_address_is_refused(monkeypatch):
    # The check has to happen on the resolved address, not on the text of the
    # hostname — otherwise any public name pointing at 127.0.0.1 walks through.
    import src.image_fetch as mod

    monkeypatch.setattr(
        mod.socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("10.1.2.3", 0))],
    )

    with pytest.raises(ImageFetchError, match="interne"):
        fetch_image_bytes("https://looks-public.example/logo.png")


def test_a_host_with_one_public_and_one_private_address_is_refused(monkeypatch):
    # Every address has to be public. Passing on the first one and connecting on
    # the second is exactly the bypass.
    import src.image_fetch as mod

    monkeypatch.setattr(
        mod.socket, "getaddrinfo",
        lambda host, port: [
            (2, 1, 6, "", ("93.184.216.34", 0)),
            (2, 1, 6, "", ("127.0.0.1", 0)),
        ],
    )

    with pytest.raises(ImageFetchError, match="interne"):
        fetch_image_bytes("https://mixed.example/logo.png")


def test_redirects_are_not_followed(monkeypatch):
    """A redirect is how a public URL reaches an internal address after the check."""
    import src.image_fetch as mod

    monkeypatch.setattr(
        mod.socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )

    class _Redirect:
        status_code = 302
        headers = {"location": "http://169.254.169.254/"}
        content = b""

    class _Client:
        def __init__(self, *a, **kw):
            assert kw.get("follow_redirects") is False
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url): return _Redirect()

    monkeypatch.setattr(mod.httpx, "Client", _Client)

    with pytest.raises(ImageFetchError, match="redirection"):
        fetch_image_bytes("https://example.com/logo.png")


def test_an_oversized_image_is_refused(monkeypatch):
    import src.image_fetch as mod

    monkeypatch.setattr(
        mod.socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )

    class _Big:
        status_code = 200
        headers = {}
        content = b"x" * (MAX_IMAGE_BYTES + 1)

    class _Client:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url): return _Big()

    monkeypatch.setattr(mod.httpx, "Client", _Client)

    with pytest.raises(ImageFetchError, match="lourde"):
        fetch_image_bytes("https://example.com/huge.png")


def test_a_public_image_comes_back(monkeypatch):
    import src.image_fetch as mod

    monkeypatch.setattr(
        mod.socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )

    class _Ok:
        status_code = 200
        headers = {"content-length": "4"}
        content = b"\x89PNG"

    class _Client:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url): return _Ok()

    monkeypatch.setattr(mod.httpx, "Client", _Client)

    assert fetch_image_bytes("https://example.com/logo.png") == b"\x89PNG"
