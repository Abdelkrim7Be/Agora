from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
import yaml

from src.config import SERVICE_ROOT
from src.instance_config import read_instance_text, write_instance_text

DEFAULT_PERSONA_PATH = SERVICE_ROOT / "persona.yaml"

TONES = ("professionnel", "chaleureux", "direct", "formel")
LANGUES = ("fr", "en", "auto")

_TONE_LINES = {
    "professionnel": "Adopter un ton professionnel et courtois.",
    "chaleureux": "Adopter un ton chaleureux et accessible, tout en restant professionnel.",
    "direct": "Adopter un ton direct : phrases courtes, aller droit au but.",
    "formel": "Adopter un ton formel et soutenu (vouvoiement strict).",
}

_LANGUE_LINES = {
    "fr": "Toujours répondre en français.",
    "en": "Toujours répondre en anglais.",
    "auto": "Répondre dans la langue de l'expéditeur.",
}


class PersonaIdentite(BaseModel):
    prenom: str = ""
    nom: str = ""
    fonction: str = ""
    entreprise: str = ""
    langue_reponse: Literal["fr", "en", "auto"] = "fr"


class PersonaPerimetre(BaseModel):
    repond_a: list[str] = Field(default_factory=list)
    ne_repond_jamais_a: list[str] = Field(default_factory=list)
    escalade_vers: str = ""


class Persona(BaseModel):
    identite: PersonaIdentite = Field(default_factory=PersonaIdentite)
    mission: str = ""
    perimetre: PersonaPerimetre = Field(default_factory=PersonaPerimetre)
    ton: Literal["professionnel", "chaleureux", "direct", "formel"] = "professionnel"

    def is_empty(self) -> bool:
        """True when no meaningful field is filled — compilation is skipped then."""
        ident = self.identite
        peri = self.perimetre
        return not any([
            ident.prenom.strip(), ident.nom.strip(), ident.fonction.strip(),
            ident.entreprise.strip(), self.mission.strip(),
            peri.repond_a, peri.ne_repond_jamais_a, peri.escalade_vers.strip(),
        ])


def load_persona(agent_instance_id: str | None = None) -> Persona:
    raw = read_instance_text("persona", DEFAULT_PERSONA_PATH, agent_instance_id)
    data = yaml.safe_load(raw or "") or {}
    return Persona(**data)


def save_persona(persona: Persona, agent_instance_id: str | None = None) -> None:
    write_instance_text(
        "persona",
        yaml.safe_dump(persona.model_dump(), sort_keys=False, allow_unicode=True),
        DEFAULT_PERSONA_PATH,
        agent_instance_id,
    )


def _identity_sentence(persona: Persona) -> str:
    ident = persona.identite
    name = " ".join(part for part in (ident.prenom.strip(), ident.nom.strip()) if part)
    pieces = []
    if name:
        pieces.append(f"Je suis {name}")
    if ident.fonction.strip():
        pieces.append(
            f"{ident.fonction.strip()}"
            + (f" chez {ident.entreprise.strip()}" if ident.entreprise.strip() else "")
        )
    elif ident.entreprise.strip():
        pieces.append(f"au sein de {ident.entreprise.strip()}")
    if not pieces:
        return ""
    return ", ".join(pieces) + "."


def compile_persona(persona: Persona) -> tuple[str, str, str]:
    """Compile the persona form into the three agent behavior texts (French).

    Deterministic templates — no LLM: the same persona always produces the same
    instructions, offline, and the graph keeps consuming the existing
    background / triage_instructions / response_preferences fields unchanged.
    """
    ident = persona.identite
    peri = persona.perimetre

    background_parts = []
    identity = _identity_sentence(persona)
    if identity:
        background_parts.append(identity)
    if persona.mission.strip():
        background_parts.append(f"Ma mission : {persona.mission.strip()}")
    background_parts.append(
        "Je gère la boîte mail et je réponds à la place de son propriétaire."
    )
    background = " ".join(background_parts)

    triage_lines = []
    if peri.repond_a:
        triage_lines.append(
            "Emails qui méritent une réponse (classification respond) : "
            + ", ".join(item.strip() for item in peri.repond_a if item.strip()) + "."
        )
    if peri.ne_repond_jamais_a:
        triage_lines.append(
            "Ne jamais répondre à : "
            + ", ".join(item.strip() for item in peri.ne_repond_jamais_a if item.strip())
            + " (classification ignore ou notify, jamais respond)."
        )
    if peri.escalade_vers.strip():
        triage_lines.append(
            f"En cas de doute ou de demande hors périmètre, classer notify et signaler à {peri.escalade_vers.strip()}."
        )
    triage_instructions = "\n".join(triage_lines)

    response_lines = [_TONE_LINES[persona.ton], _LANGUE_LINES[ident.langue_reponse]]
    if identity:
        response_lines.append(f"Signer et parler en tant que : {identity}")
    response_preferences = "\n".join(response_lines)

    return background, triage_instructions, response_preferences


def compiled_preview(persona: Persona) -> dict:
    background, triage_instructions, response_preferences = compile_persona(persona)
    return {
        "background": background,
        "triage_instructions": triage_instructions,
        "response_preferences": response_preferences,
    }
