from src.prompts import agent_system_prompt
from src.utils import ensure_email_paragraphs


def test_prompt_contains_structure_contract():
    assert "Email Format" in agent_system_prompt
    assert "BLANK LINE" in agent_system_prompt


def test_structured_content_untouched():
    text = "Bonjour,\n\nPremier paragraphe.\n\nCordialement,"
    assert ensure_email_paragraphs(text) == text


def test_short_content_untouched():
    text = "Bonjour, merci pour votre message. Cordialement,"
    assert ensure_email_paragraphs(text) == text


def test_dense_block_gets_split():
    body = (
        "Bonjour Monsieur Dupont, je vous remercie pour votre message concernant votre dossier. "
        "Nous avons bien pris en compte votre demande et elle est en cours de traitement par notre équipe. "
        "Vous recevrez une réponse détaillée dans les meilleurs délais avec l'ensemble des documents demandés. "
        "N'hésitez pas à nous recontacter si vous avez la moindre question sur l'avancement. "
        "Nous restons à votre entière disposition pour tout complément d'information. "
        "Cordialement"
    )
    result = ensure_email_paragraphs(body)
    assert "\n\n" in result
    blocks = result.split("\n\n")
    assert blocks[0].startswith("Bonjour Monsieur Dupont,")
    assert blocks[-1].lower().startswith("cordialement")
    assert len(blocks) >= 3


def test_empty_content_safe():
    assert ensure_email_paragraphs("") == ""
    assert ensure_email_paragraphs(None) is None
