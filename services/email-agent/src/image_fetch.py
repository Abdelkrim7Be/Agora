"""Fetch a signature image from a URL, safely.

A signature that names a remote URL renders as `<img src="https://…">`, and
most mail clients block remote images by default — so the picture the owner
carefully chose simply does not appear for the recipient. An uploaded image
travels as an inline `cid:` part and always shows. Fetching the URL once, at
save time, turns the unreliable path into the reliable one.

Doing it server-side means the server makes a request to an address a user
supplies, which is a server-side request forgery primitive: without the checks
below, `http://169.254.169.254/…` or `http://postgres:5432/` would be fetched
from *inside* the private network by a component that is allowed to be there.
Hence: https only, public addresses only, no redirects, hard size and time caps.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

# Generous for a signature logo, small enough that a hostile URL cannot stream
# the process out of memory. Enforced twice: on the declared length and on the
# bytes actually read.
MAX_IMAGE_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 10.0


class ImageFetchError(ValueError):
    """The URL could not be turned into an image we are willing to store."""


def _is_public_address(host: str) -> bool:
    """True when every address the host resolves to is publicly routable.

    Every address, not just the first: a name that returns one public and one
    loopback address would otherwise pass the check and then be connected to on
    whichever the stack picks.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ImageFetchError(f"nom de domaine introuvable : {host}") from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return False
    return bool(infos)


def fetch_image_bytes(url: str) -> bytes:
    """Download `url` and return its bytes, or raise ImageFetchError."""
    parsed = urlparse((url or "").strip())

    # https only. Plain http would let a network position downgrade the picture
    # in every outgoing signature, and it is not a real constraint for a logo.
    if parsed.scheme != "https":
        raise ImageFetchError("l'adresse doit commencer par https://")
    if not parsed.hostname:
        raise ImageFetchError("adresse invalide")
    if not _is_public_address(parsed.hostname):
        raise ImageFetchError(
            "cette adresse pointe vers le réseau interne ; seules les adresses publiques sont acceptées"
        )

    try:
        # follow_redirects stays off on purpose: a redirect is how a public URL
        # sends the request to an internal address after the check has passed.
        with httpx.Client(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise ImageFetchError(f"téléchargement impossible : {exc}") from exc

    if response.status_code >= 400:
        raise ImageFetchError(f"le serveur a répondu {response.status_code}")
    if response.status_code >= 300:
        raise ImageFetchError("les redirections ne sont pas suivies ; donnez l'adresse finale de l'image")

    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_IMAGE_BYTES:
        raise ImageFetchError("image trop lourde (2 Mo maximum)")

    data = response.content
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageFetchError("image trop lourde (2 Mo maximum)")
    if not data:
        raise ImageFetchError("l'adresse ne renvoie aucune donnée")

    # The bytes are validated as a real image by save_signature_image; the
    # content-type header is only a hint and is not trusted here.
    return data
