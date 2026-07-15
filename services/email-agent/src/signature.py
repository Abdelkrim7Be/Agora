from __future__ import annotations

from pydantic import BaseModel
import yaml

from src.config import SERVICE_ROOT
from src.instance_config import read_instance_text, write_instance_text

DEFAULT_SIGNATURE_PATH = SERVICE_ROOT / "signature.yaml"
SIGNATURE_MARKER = "<!-- agora-signature -->"
SIGNATURE_TOOLS = {"write_email", "reply_all", "create_draft"}


class SignatureConfig(BaseModel):
    enabled: bool = False
    text: str = ""
    image_url: str | None = None
    image_alt: str = "Signature"


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


def append_signature(content: str, signature: SignatureConfig | None = None) -> str:
    signature = signature or load_signature()
    body = str(content or "")
    if not signature.enabled or SIGNATURE_MARKER in body:
        return body

    text = signature.text.strip()
    image_url = (signature.image_url or "").strip()
    if not text and not image_url:
        return body

    parts = []
    if text:
        parts.append(text)
    if image_url:
        alt = (signature.image_alt or "Signature").strip() or "Signature"
        parts.append(f"![{alt}]({image_url})")

    return f"{body.rstrip()}\n\n{SIGNATURE_MARKER}\n-- \n" + "\n".join(parts)


def apply_signature_to_args(tool_name: str, args: dict, signature: SignatureConfig | None = None) -> dict:
    if tool_name not in SIGNATURE_TOOLS or not isinstance(args, dict):
        return args
    if "content" not in args:
        return args
    signed = append_signature(str(args.get("content") or ""), signature)
    if signed == args.get("content"):
        return args
    return {**args, "content": signed}
