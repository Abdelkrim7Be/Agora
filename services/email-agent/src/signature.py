from __future__ import annotations

from pydantic import BaseModel, Field
import yaml

from src.config import SERVICE_ROOT
from src.instance_config import read_instance_text, write_instance_text

DEFAULT_SIGNATURE_PATH = SERVICE_ROOT / "signature.yaml"
SIGNATURE_TOOLS = {"write_email", "reply_all", "create_draft"}

# Default render width of the signature image, in CSS pixels. The HTML email body
# is capped at 640px, so this is roughly two thirds of the readable column — wide
# enough for a logo to be legible next to the signature text rather than reading
# as a stamp, without crowding the message above it. `gmail_client` mirrors this
# number as a last-resort fallback for when the signature config cannot be read.
DEFAULT_SIGNATURE_IMAGE_WIDTH = 420
MAX_SIGNATURE_IMAGE_WIDTH = 640

SIGNATURE_MODES = (
    "preserve_provider_signature",
    "append_platform_signature",
    "replace_detected_signature",
    "ask_each_time",
)


class SignatureConfig(BaseModel):
    enabled: bool = False
    # append_platform_signature preserves pre-mode behavior for existing installs
    # whose signature.yaml predates this field.
    mode: str = "append_platform_signature"
    # The provider-side signature block detected from sent mail during onboarding
    # (see instance_setup.py's detect_signature step) — what replace_detected_signature
    # strips before appending the platform signature. Not the platform's own block.
    detected_block: str | None = None
    text: str = ""
    image_url: str | None = None
    image_alt: str = "Signature"
    # Rendered width of the signature image, in CSS pixels.
    #
    # Mail clients render an <img> with no dimensions at the file's natural pixel
    # size, so a small logo arrived as a stamp next to the text. Constraining the
    # width and letting the height follow keeps the image aligned with the text
    # block whatever the source resolution, and a high-DPI file is scaled down
    # rather than displayed enormous. Height is never set: forcing one would
    # distort or crop the upload, so a taller image comes from a taller source
    # file, not from a setting.
    image_width: int = Field(
        default=DEFAULT_SIGNATURE_IMAGE_WIDTH, ge=48, le=MAX_SIGNATURE_IMAGE_WIDTH
    )
    # Structured fields (§4): when any is filled they compose the signature
    # block; the legacy free-text `text` keeps working unchanged when they are
    # all empty.
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    company: str = ""
    phone: str = ""
    website: str = ""

    def has_structured_fields(self) -> bool:
        return any(
            value.strip()
            for value in (
                self.first_name, self.last_name, self.title,
                self.company, self.phone, self.website,
            )
        )

    def structured_lines(self) -> list[str]:
        """The signature block composed from structured fields, line by line."""
        lines: list[str] = []
        name = " ".join(part for part in (self.first_name.strip(), self.last_name.strip()) if part)
        if name:
            lines.append(name)
        title_company = " — ".join(
            part for part in (self.title.strip(), self.company.strip()) if part
        )
        if title_company:
            lines.append(title_company)
        if self.phone.strip():
            lines.append(f"Tél. : {self.phone.strip()}")
        if self.website.strip():
            lines.append(self.website.strip())
        return lines


def load_signature(agent_instance_id: str | None = None) -> SignatureConfig:
    raw = read_instance_text("signature", DEFAULT_SIGNATURE_PATH, agent_instance_id)
    data = yaml.safe_load(raw or "") or {}
    return SignatureConfig(**data)


def save_signature(signature: SignatureConfig, agent_instance_id: str | None = None) -> None:
    write_instance_text(
        "signature",
        yaml.safe_dump(signature.model_dump(), sort_keys=False),
        DEFAULT_SIGNATURE_PATH,
        agent_instance_id,
    )


def _signature_block(signature: SignatureConfig) -> str | None:
    """Compose the RFC-3676-delimited signature block, or None if empty.

    No internal marker is embedded here on purpose: this text is shown
    verbatim to the human approver and mailed as-is, so it must contain
    nothing but the actual signature. Idempotency (append/strip) instead
    compares against this exact composed block.
    """
    from src.media import SIGNATURE_CID, find_signature_image

    structured = signature.structured_lines()
    text = signature.text.strip()
    image_url = (signature.image_url or "").strip()
    stored_image = find_signature_image() is not None
    if not structured and not text and not image_url and not stored_image:
        return None

    parts = []
    if structured:
        # Structured fields compose the block; the free text becomes an
        # optional extra line under it (e.g. a tagline).
        parts.extend(structured)
        if text:
            parts.append(text)
    elif text:
        parts.append(text)
    alt = (signature.image_alt or "Signature").strip() or "Signature"
    if stored_image:
        # Uploaded image is embedded as an inline cid attachment at send time —
        # no external link that can break or get blocked by the mail client.
        parts.append(f"![{alt}](cid:{SIGNATURE_CID})")
    elif image_url:
        parts.append(f"![{alt}]({image_url})")

    return "-- \n" + "\n".join(parts)


def append_signature(content: str, signature: SignatureConfig | None = None) -> str:
    signature = signature or load_signature()
    body = str(content or "")
    if not signature.enabled:
        return body

    block = _signature_block(signature)
    if not block or body.rstrip().endswith(block):
        return body

    return f"{body.rstrip()}\n\n{block}"


def strip_signature(content: str, signature: SignatureConfig | None = None) -> str:
    """Remove a previously-appended signature block, if present.

    Used before handing a draft body to an LLM (e.g. for a retouche) so the
    model edits only the human-authored text and never sees, echoes, or
    improvises around the signature.
    """
    signature = signature or load_signature()
    body = str(content or "")
    block = _signature_block(signature)
    if not block:
        return body

    stripped = body.rstrip()
    if stripped.endswith(block):
        return stripped[: -len(block)].rstrip()
    return body


def apply_signature_to_args(
    tool_name: str, args: dict, signature: SignatureConfig | None = None, *, mode: str | None = None
) -> dict:
    if tool_name not in SIGNATURE_TOOLS or not isinstance(args, dict):
        return args
    if "content" not in args:
        return args
    signed = apply_signature(str(args.get("content") or ""), signature, mode=mode)
    if signed == args.get("content"):
        return args
    return {**args, "content": signed}


def apply_signature(content: str, signature: SignatureConfig | None = None, *, mode: str | None = None) -> str:
    """Single entry point for signature application, used by the draft path.

    `mode` overrides `signature.mode` (used by ask_each_time on approve, where the
    human's per-draft choice wins). Idempotent: calling this twice on the same
    body never appends the block a second time, because append_signature already
    checks the body doesn't already end with the composed block.
    """
    signature = signature or load_signature()
    effective_mode = mode or signature.mode
    body = str(content or "")
    if not signature.enabled:
        return body
    if effective_mode == "preserve_provider_signature":
        return body
    if effective_mode == "replace_detected_signature":
        if signature.detected_block:
            body = strip_detected_signature(body, signature.detected_block)
        return append_signature(body, signature)
    if effective_mode == "ask_each_time" and mode is None:
        # No per-draft choice supplied yet — defer to the approval UI, do not append.
        return body
    # append_platform_signature (default), or ask_each_time once a mode override
    # (the human's chosen value) has been supplied. Strip the provider-side
    # block first if onboarding detected one — style learning trains on the
    # user's own sent mail, which included it, so a draft can echo it back and
    # end up with both the echoed original and the newly appended one.
    if signature.detected_block:
        body = strip_detected_signature(body, signature.detected_block)
    return append_signature(body, signature)


def strip_detected_signature(content: str, block: str) -> str:
    """Remove a trailing block (e.g. one found by detect_from_sent) from content."""
    body = str(content or "")
    if not block:
        return body
    stripped = body.rstrip()
    if stripped.endswith(block.strip()):
        return stripped[: -len(block.strip())].rstrip()
    return body


_SIGNATURE_DELIMITER = "-- "


def detect_from_sent(messages: list[dict]) -> dict:
    """Heuristic detection of a provider-side (e.g. native Gmail) signature.

    Pure function, no I/O — the caller supplies already-fetched sent-mail samples
    (each a dict with a 'body' key, as returned by gmail_client.fetch_sent).

    Heuristics: a trailing block repeated across >= 60% of samples; a leading
    '-- ' delimiter line; trailing lines containing a phone-number-like pattern
    or a URL are treated as signal that the block is a real signature.
    """
    import re
    from collections import Counter

    bodies = [str(m.get("body") or "") for m in messages if str(m.get("body") or "").strip()]
    sample_size = len(bodies)
    if sample_size == 0:
        return {"detected": False, "block": None, "confidence": 0.0, "sample_size": 0}

    trailing_blocks: list[str] = []
    for body in bodies:
        lines = body.rstrip().splitlines()
        # A '-- ' delimiter line is the strongest signal: Gmail and most clients
        # insert it right before an appended signature.
        delim_idx = None
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == _SIGNATURE_DELIMITER.strip():
                delim_idx = i
                break
        if delim_idx is not None:
            block = "\n".join(lines[delim_idx:]).strip()
            if block:
                trailing_blocks.append(block)
            continue
        # No delimiter: fall back to the last 1-4 lines when they look
        # signature-like (short lines, a phone number, or a URL).
        tail = lines[-4:] if len(lines) >= 4 else lines
        tail_text = "\n".join(tail).strip()
        if tail_text and (
            re.search(r"(\+?\d[\d .()-]{6,}\d)", tail_text)
            or re.search(r"https?://|www\.", tail_text)
        ):
            trailing_blocks.append(tail_text)

    if not trailing_blocks:
        return {"detected": False, "block": None, "confidence": 0.0, "sample_size": sample_size}

    counts = Counter(trailing_blocks)
    block, occurrences = counts.most_common(1)[0]
    confidence = occurrences / sample_size
    if occurrences < 2 or confidence < 0.6:
        return {"detected": False, "block": None, "confidence": round(confidence, 2), "sample_size": sample_size}
    return {"detected": True, "block": block, "confidence": round(confidence, 2), "sample_size": sample_size}
