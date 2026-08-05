from src.signature import (
    SIGNATURE_MODES,
    SignatureConfig,
    append_signature,
    apply_signature,
    apply_signature_to_args,
    detect_from_sent,
    strip_detected_signature,
    strip_signature,
)


def test_disabled_signature_leaves_content_unchanged():
    signature = SignatureConfig(enabled=False, text="Karim")
    assert append_signature("Bonjour", signature) == "Bonjour"


def test_signature_appends_text_and_image_once():
    signature = SignatureConfig(
        enabled=True,
        text="Karim\nAgora Consulting",
        image_url="https://example.com/signature.png",
        image_alt="Logo Agora",
    )

    signed = append_signature("Bonjour", signature)

    assert "<!--" not in signed and "-->" not in signed
    assert "-- \n" in signed
    assert "Karim\nAgora Consulting" in signed
    assert "![Logo Agora](https://example.com/signature.png)" in signed
    assert append_signature(signed, signature) == signed
    assert strip_signature(signed, signature) == "Bonjour"


def test_apply_signature_only_touches_email_content_tools():
    signature = SignatureConfig(enabled=True, text="Karim")
    args = {"content": "Bonjour"}

    assert apply_signature_to_args("write_email", args, signature)["content"] == append_signature("Bonjour", signature)
    assert apply_signature_to_args("archive_email", args) == args


def test_structured_fields_compose_signature_block():
    signature = SignatureConfig(
        enabled=True,
        first_name="Karim",
        last_name="Bellagnech",
        title="Chargé RH",
        company="Agora",
        phone="+212 6 00 00 00 00",
        website="https://agora.example",
    )

    signed = append_signature("Bonjour", signature)

    assert "Karim Bellagnech" in signed
    assert "Chargé RH — Agora" in signed
    assert "Tél. : +212 6 00 00 00 00" in signed
    assert "https://agora.example" in signed
    assert append_signature(signed, signature) == signed


def test_structured_fields_keep_free_text_as_extra_line():
    signature = SignatureConfig(
        enabled=True, first_name="Karim", text="L'humain d'abord."
    )
    signed = append_signature("Bonjour", signature)
    assert signed.index("Karim") < signed.index("L'humain d'abord.")


def test_legacy_text_only_signature_unchanged():
    signature = SignatureConfig(enabled=True, text="Karim\nAgora Consulting")
    signed = append_signature("Bonjour", signature)
    assert "-- \nKarim\nAgora Consulting" in signed


def test_strip_signature_is_the_inverse_of_append():
    signature = SignatureConfig(enabled=True, text="Karim\nAgora Consulting")
    signed = append_signature("Bonjour,\n\nDetails.", signature)
    assert strip_signature(signed, signature) == "Bonjour,\n\nDetails."


def test_strip_signature_noop_when_not_signed():
    signature = SignatureConfig(enabled=True, text="Karim")
    assert strip_signature("Bonjour", signature) == "Bonjour"


def test_existing_config_without_mode_defaults_to_append():
    signature = SignatureConfig(**{"enabled": True, "text": "Karim"})
    assert signature.mode == "append_platform_signature"


def test_mode_preserve_appends_nothing():
    signature = SignatureConfig(enabled=True, text="Karim", mode="preserve_provider_signature")
    assert apply_signature("Bonjour", signature) == "Bonjour"


def test_mode_append_appends_once():
    signature = SignatureConfig(enabled=True, text="Karim", mode="append_platform_signature")
    signed = apply_signature("Bonjour", signature)
    assert "Karim" in signed
    assert signed != "Bonjour"


def test_apply_is_idempotent_on_second_call():
    signature = SignatureConfig(enabled=True, text="Karim", mode="append_platform_signature")
    once = apply_signature("Bonjour", signature)
    twice = apply_signature(once, signature)
    assert once == twice


def test_mode_replace_strips_then_appends():
    signature = SignatureConfig(
        enabled=True,
        text="Karim Nouveau",
        mode="replace_detected_signature",
        detected_block="-- \nKarim Ancien",
    )
    body_with_old_block = "Bonjour,\n\nDetails.\n\n-- \nKarim Ancien"
    result = apply_signature(body_with_old_block, signature)
    assert "Karim Ancien" not in result
    assert "Karim Nouveau" in result


def test_ask_each_time_defers_to_approval():
    signature = SignatureConfig(enabled=True, text="Karim", mode="ask_each_time")
    assert apply_signature("Bonjour", signature) == "Bonjour"
    # Once the approval UI supplies the human's chosen mode, it applies immediately.
    overridden = apply_signature("Bonjour", signature, mode="append_platform_signature")
    assert "Karim" in overridden


def test_invalid_mode_rejected_by_whitelist():
    assert "not_a_real_mode" not in SIGNATURE_MODES


def test_detect_from_sent_finds_repeated_block():
    messages = [
        {"body": f"Salut,\n\nMessage {i}.\n\n-- \nKarim Bellagnech\n+212 6 00 00 00 00"}
        for i in range(5)
    ]
    result = detect_from_sent(messages)
    assert result["detected"] is True
    assert "Karim Bellagnech" in result["block"]
    assert result["sample_size"] == 5
    assert result["confidence"] >= 0.6


def test_detect_from_sent_ignores_single_occurrence():
    messages = [
        {"body": "Salut,\n\nMessage 1.\n\n-- \nKarim Bellagnech\n+212 6 00 00 00 00"},
        {"body": "Salut,\n\nMessage 2, nothing signature-like here at all."},
        {"body": "Salut,\n\nMessage 3, also plain."},
    ]
    result = detect_from_sent(messages)
    assert result["detected"] is False


def test_detect_from_sent_empty_sample_returns_not_detected():
    result = detect_from_sent([])
    assert result == {"detected": False, "block": None, "confidence": 0.0, "sample_size": 0}


def test_strip_detected_signature_removes_trailing_block():
    body = "Bonjour,\n\nDetails.\n\n-- \nKarim Ancien"
    stripped = strip_detected_signature(body, "-- \nKarim Ancien")
    assert stripped == "Bonjour,\n\nDetails."


def test_signature_image_is_rendered_with_explicit_dimensions():
    """A bare <img> renders at the file's natural size.

    A small logo therefore arrived as a stamp beside the text and a large one
    blew the layout out, depending only on what was uploaded.
    """
    from src.gmail_client import render_rich_email_html
    from src.media import SIGNATURE_CID

    from src.signature import DEFAULT_SIGNATURE_IMAGE_WIDTH as default_width

    html = render_rich_email_html(f"Cordialement\n\n![Signature](cid:{SIGNATURE_CID})")

    assert f'width="{default_width}"' in html   # attribute: Outlook ignores CSS width
    assert f"width:{default_width}px" in html   # style: everyone else
    assert "height:auto" in html            # aspect ratio preserved
    assert "max-width:100%" in html         # never wider than the column


def test_signature_image_width_follows_configuration(monkeypatch):
    import src.gmail_client as gc
    from src.media import SIGNATURE_CID

    monkeypatch.setattr(gc, "_signature_image_width", lambda: 480)
    html = gc.render_rich_email_html(f"![Signature](cid:{SIGNATURE_CID})")

    assert 'width="480"' in html and "width:480px" in html


def test_ordinary_images_are_left_alone():
    """Only the signature cid is resized; body images keep whatever the author set."""
    from src.gmail_client import render_rich_email_html

    from src.signature import DEFAULT_SIGNATURE_IMAGE_WIDTH as default_width

    html = render_rich_email_html("![Schema](https://example.com/x.png)")
    assert f"width:{default_width}px" not in html

def test_default_signature_image_width_fits_the_body_column():
    """The default must stay inside the 640px body the HTML renderer emits.

    A default wider than the column would be clamped by max-width in some clients
    and overflow in the ones that ignore it, so the two numbers have to agree.
    """
    from src.signature import DEFAULT_SIGNATURE_IMAGE_WIDTH, MAX_SIGNATURE_IMAGE_WIDTH

    assert DEFAULT_SIGNATURE_IMAGE_WIDTH <= MAX_SIGNATURE_IMAGE_WIDTH == 640


def test_signature_image_width_is_bounded():
    """Reject widths that would either vanish or blow past the body column."""
    import pytest
    from pydantic import ValidationError

    from src.signature import SignatureConfig

    with pytest.raises(ValidationError):
        SignatureConfig(image_width=12)
    with pytest.raises(ValidationError):
        SignatureConfig(image_width=1200)
