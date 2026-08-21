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
Hence: https only, public addresses only, every redirect hop re-checked, and
hard size and time caps.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

# Generous for a signature logo, small enough that a hostile URL cannot stream
# the process out of memory. Enforced twice: on the declared length and on the
# bytes actually read.
MAX_IMAGE_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 10.0
# Enough for the usual CDN hop or two, few enough that a redirect loop ends.
MAX_REDIRECTS = 3


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


def _check_target(url: str) -> None:
    """Refuse anything we are not willing to connect to."""
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


def fetch_image_bytes(url: str) -> bytes:
    """Download `url` and return its bytes, or raise ImageFetchError."""
    _check_target(url)

    try:
        # Redirects are followed by hand, not by httpx, because each hop has to
        # be re-checked: a public URL that 302s to 169.254.169.254 is precisely
        # how the address check gets bypassed. Refusing them outright was safe
        # but unusable — most CDN-hosted logos redirect at least once.
        with httpx.Client(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False) as client:
            headers = {
                # Several CDNs (Wikimedia among them) answer 403 to a client
                # that does not identify itself, so the fetch failed on exactly
                # the kind of public image this feature is for. Identifying the
                # product is also the honest thing to do when fetching someone
                # else's asset.
                "User-Agent": "AgoraSignatureFetch/1.0 (+https://agora.example)",
                "Accept": "image/*",
            }
            target = url
            for _hop in range(MAX_REDIRECTS + 1):
                response = client.get(target, headers=headers)
                if response.status_code not in (301, 302, 303, 307, 308):
                    break
                location = response.headers.get("location")
                if not location:
                    raise ImageFetchError("redirection sans destination")
                # Relative Location headers are legal, so resolve before checking.
                target = urljoin(target, location)
                _check_target(target)
            else:
                raise ImageFetchError("trop de redirections")
    except httpx.HTTPError as exc:
        raise ImageFetchError(f"téléchargement impossible : {exc}") from exc

    if response.status_code >= 400:
        raise ImageFetchError(f"le serveur a répondu {response.status_code}")
    if response.status_code >= 300:
        raise ImageFetchError("trop de redirections")

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
