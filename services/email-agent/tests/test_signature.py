from src.signature import SignatureConfig, append_signature, apply_signature_to_args, strip_signature


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
