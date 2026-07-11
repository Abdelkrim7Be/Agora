from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr
import json
from pathlib import Path
import re

import yaml
from pydantic import BaseModel, Field, field_validator
from typing_extensions import Any

from src.config import SERVICE_ROOT

DEFAULT_RULES_PATH = SERVICE_ROOT / "rules.yaml"
_SLA_PATTERN = re.compile(
    r"(?P<value>\d+)\s*(?P<unit>d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes)\b",
    re.IGNORECASE,
)


class RuleWhen(BaseModel):
    """Deterministic predicates for matching an email before the agent LLM runs.

    Rules are evaluated pre-triage (cheap, before the LLM), so they intentionally
    have no classification predicate — classification-driven organization is handled
    by `auto_organize` (Phase 5) after triage.
    """

    sender_contains: list[str] = Field(default_factory=list)
    sender_domain: list[str] = Field(default_factory=list)
    subject_contains: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)


class RuleThen(BaseModel):
    """Actions a matched rule may request through the normal tool path."""

    labels: list[str] = Field(default_factory=list)
    archive: bool = False
    mark_read: bool = False
    notify: bool = False
    respond: str | None = None
    snooze_days: int | None = Field(default=None, ge=1)

    @field_validator("respond")
    @classmethod
    def non_blank_response(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("respond must not be blank")
        return value


class AutomationRule(BaseModel):
    name: str = Field(min_length=1)
    enabled: bool = True
    when: RuleWhen = Field(default_factory=RuleWhen)
    then: RuleThen


class DigestConfig(BaseModel):
    enabled: bool = False
    hour: int = Field(default=18, ge=0, le=23)
    statuses: list[str] = Field(default_factory=lambda: ["notify", "pending_approval"])


class SnoozeConfig(BaseModel):
    enabled: bool = False
    label_prefix: str = "Snoozed"
    max_resurface_per_run: int = Field(default=20, ge=1)


class FollowUpConfig(BaseModel):
    enabled: bool = False
    label: str = "Awaiting Reply"
    after_days: int = Field(default=3, ge=1)
    max_results: int = Field(default=10, ge=1)
    nudge: str = "Just following up on this."


class LearningConfig(BaseModel):
    enabled: bool = False
    suggestions_path: str = "logs/rule_suggestions.jsonl"


class RulesConfig(BaseModel):
    enabled: bool = False
    rules: list[AutomationRule] = Field(default_factory=list)
    digest: DigestConfig = Field(default_factory=DigestConfig)
    snooze: SnoozeConfig = Field(default_factory=SnoozeConfig)
    follow_ups: FollowUpConfig = Field(default_factory=FollowUpConfig)
    learning: LearningConfig = Field(default_factory=LearningConfig)


def load_rules(path: str | Path | None = None) -> RulesConfig:
    """Load automation rules. Missing or empty files mean automation stays off."""
    rules_path = Path(path) if path else DEFAULT_RULES_PATH
    if path is None:
        from src.instance_config import read_instance_text

        raw = read_instance_text("rules", rules_path)
    elif rules_path.is_file():
        raw = rules_path.read_text()
    else:
        raw = ""
    data = yaml.safe_load(raw) or {}
    if data.get("rules") is None:
        data["rules"] = []
    return RulesConfig(**data)


def snooze_label(prefix: str, target: date) -> str:
    return f"{prefix.rstrip('/')}/{target.isoformat()}"


def _contains_any(haystack: str, needles: list[str]) -> bool:
    text = haystack.lower()
    return any(needle.lower() in text for needle in needles)


def _sender_domain(sender: str) -> str:
    _name, address = parseaddr(sender)
    if "@" not in address:
        return ""
    return address.rsplit("@", 1)[1].lower()


def _email_address(sender: str) -> str:
    _name, address = parseaddr(sender)
    return address or sender


def _normalize_route_targets(value: Any) -> list[str]:
    if value is None:
        return []
    raw_values = value if isinstance(value, list) else [value]
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        cleaned = str(raw).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(cleaned)
    return normalized


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "workflow"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_sla_duration(value: str | None) -> timedelta | None:
    """Parse a simple SLA duration from workflow instructions text."""
    if value is None or not str(value).strip():
        return None
    total = timedelta()
    matched = False
    for match in _SLA_PATTERN.finditer(str(value)):
        matched = True
        amount = int(match.group("value"))
        unit = match.group("unit").lower()
        if unit.startswith("d"):
            total += timedelta(days=amount)
        elif unit.startswith("h"):
            total += timedelta(hours=amount)
        else:
            total += timedelta(minutes=amount)
    return total if matched else None


def workflow_sla_snapshot(
    run: dict,
    categories_config,
    escalation_state: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Return SLA metadata for a pending approval run."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    category_name = run.get("category")
    category = next((item for item in categories_config.categories if item.name == category_name), None)
    instructions = category.instructions.model_dump(exclude_none=True) if category and category.instructions else {}
    created_at = _parse_datetime(run.get("created_at"))
    sla_label = instructions.get("sla")
    duration = parse_sla_duration(sla_label)
    due_at = created_at + duration if created_at and duration else None
    overdue = bool(due_at and now > due_at)
    overdue_by_seconds = max(0, int((now - due_at).total_seconds())) if overdue and due_at else 0
    state_row = (escalation_state or {}).get("runs", {}).get(run.get("run_id"), {})
    return {
        "sla_label": sla_label,
        "due_at": due_at.isoformat(timespec="seconds") if due_at else None,
        "overdue": overdue,
        "overdue_by_seconds": overdue_by_seconds,
        "escalated_at": state_row.get("escalated_at"),
        "escalation_target": state_row.get("escalation_target"),
        "workflow_escalation": instructions.get("escalation"),
    }


def _matches_rule(rule: AutomationRule, email_input: dict) -> bool:
    when = rule.when
    sender = email_input.get("author", "")
    subject = email_input.get("subject", "")
    labels = set(email_input.get("labels", []))

    if when.sender_contains and not _contains_any(sender, when.sender_contains):
        return False
    if when.sender_domain:
        domain = _sender_domain(sender)
        if domain not in {d.lower() for d in when.sender_domain}:
            return False
    if when.subject_contains and not _contains_any(subject, when.subject_contains):
        return False
    if when.labels and not set(when.labels).issubset(labels):
        return False
    return True


def _tool_call(name: str, args: dict, call_id: str) -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _re_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def build_rule_plan(
    email_input: dict,
    rules_config: RulesConfig,
    today: date | None = None,
) -> dict | None:
    """Return deterministic automation actions for matching rules."""
    if not rules_config.enabled:
        return None

    today = today or date.today()
    tool_calls: list[dict] = []
    matched: list[str] = []
    terminal_status: str | None = None

    for idx, rule in enumerate(rules_config.rules):
        if not rule.enabled or not _matches_rule(rule, email_input):
            continue
        matched.append(rule.name)
        prefix = f"rule_{idx}"
        for label_idx, label in enumerate(rule.then.labels):
            tool_calls.append(
                _tool_call("apply_label", {"label": label}, f"{prefix}_label_{label_idx}")
            )
        if rule.then.snooze_days is not None:
            target = today + timedelta(days=rule.then.snooze_days)
            tool_calls.append(
                _tool_call(
                    "apply_label",
                    {"label": snooze_label(rules_config.snooze.label_prefix, target)},
                    f"{prefix}_snooze_label",
                )
            )
            tool_calls.append(_tool_call("archive_email", {}, f"{prefix}_snooze_archive"))
        if rule.then.archive:
            tool_calls.append(_tool_call("archive_email", {}, f"{prefix}_archive"))
        if rule.then.mark_read:
            tool_calls.append(_tool_call("mark_read", {}, f"{prefix}_mark_read"))
        if rule.then.respond:
            tool_calls.append(
                _tool_call(
                    "write_email",
                    {
                        "to": _email_address(email_input.get("author", "")),
                        "subject": _re_subject(email_input.get("subject", "No Subject")),
                        "content": rule.then.respond,
                    },
                    f"{prefix}_respond",
                )
            )
        if rule.then.notify:
            terminal_status = "notify"

    if not matched:
        return None
    return {
        "matched_rules": matched,
        "tool_calls": tool_calls,
        "terminal_status": terminal_status,
    }


def _state_path(path: str | Path | None, default_name: str) -> Path:
    if path is None:
        return SERVICE_ROOT / default_name
    p = Path(path)
    return p if p.is_absolute() else SERVICE_ROOT / p


def _read_json(path: Path, default: dict) -> dict:
    if not path.is_file():
        return default
    return json.loads(path.read_text())


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def load_escalation_state(path: str | Path | None = None) -> dict:
    return _read_json(_state_path(path, "logs/approval_sla_state.json"), {"runs": {}})


def mark_run_escalated(
    run_id: str,
    escalation_target: str,
    path: str | Path | None = None,
    now: datetime | None = None,
) -> dict:
    state_path = _state_path(path, "logs/approval_sla_state.json")
    state = _read_json(state_path, {"runs": {}})
    when = now or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    else:
        when = when.astimezone(timezone.utc)
    state.setdefault("runs", {})[run_id] = {
        "escalated_at": when.isoformat(timespec="seconds"),
        "escalation_target": escalation_target,
    }
    _write_json(state_path, state)
    return state["runs"][run_id]


def record_digest_item(
    rules_config: RulesConfig,
    status: str,
    email_input: dict,
    run_id: str,
    state_path: str | Path | None = None,
    now: datetime | None = None,
) -> bool:
    """Persist one digest candidate when daily digest is enabled for the status."""
    if not rules_config.digest.enabled or status not in rules_config.digest.statuses:
        return False
    now = now or datetime.now()
    path = _state_path(state_path, "digest_state.json")
    state = _read_json(path, {"items": [], "last_emitted": None})
    state.setdefault("items", []).append({
        "status": status,
        "run_id": run_id,
        "subject": email_input.get("subject", "No Subject"),
        "author": email_input.get("author", "Unknown Sender"),
        "recorded_at": now.isoformat(timespec="seconds"),
    })
    _write_json(path, state)
    return True


def maybe_emit_daily_digest(
    rules_config: RulesConfig,
    state_path: str | Path | None = None,
    now: datetime | None = None,
    emit=print,
) -> str | None:
    """Emit and clear the digest once per local date when the configured hour is reached."""
    if not rules_config.digest.enabled:
        return None
    now = now or datetime.now()
    if now.hour < rules_config.digest.hour:
        return None

    path = _state_path(state_path, "digest_state.json")
    state = _read_json(path, {"items": [], "last_emitted": None})
    today = now.date().isoformat()
    items = state.get("items") or []
    if state.get("last_emitted") == today or not items:
        return None

    lines = [f"Daily email digest for {today}"]
    for item in items:
        lines.append(
            f"- [{item['status']}] {item['subject']} - {item['author']} (run {item['run_id']})"
        )
    digest = "\n".join(lines)
    emit(digest)
    _write_json(path, {"items": [], "last_emitted": today})
    return digest


def _parse_snooze_date(label_name: str, prefix: str) -> date | None:
    expected = f"{prefix.rstrip('/')}/"
    if not label_name.startswith(expected):
        return None
    raw = label_name.removeprefix(expected)
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def due_snooze_labels(
    labels: list[dict],
    rules_config: RulesConfig,
    today: date | None = None,
) -> list[dict]:
    """Return Gmail labels whose Snoozed/YYYY-MM-DD date is due."""
    if not rules_config.snooze.enabled:
        return []
    today = today or date.today()
    due: list[dict] = []
    for label in labels:
        label_date = _parse_snooze_date(label.get("name", ""), rules_config.snooze.label_prefix)
        if label_date and label_date <= today:
            due.append(label)
    return due


def build_follow_up_plan(email_input: dict, rules_config: RulesConfig) -> dict | None:
    """Build a HITL-gated nudge for a follow-up candidate thread."""
    if not rules_config.follow_ups.enabled:
        return None
    return {
        "matched_rules": ["follow_up"],
        "tool_calls": [
            _tool_call(
                "write_email",
                {
                    "to": _email_address(email_input.get("to", "")),
                    "subject": _re_subject(email_input.get("subject", "No Subject")),
                    "content": rules_config.follow_ups.nudge,
                },
                "follow_up_nudge",
            )
        ],
        "terminal_status": "respond",
    }


def follow_up_query(rules_config: RulesConfig) -> str:
    label = rules_config.follow_ups.label.replace('"', '')
    return f'label:"{label}" older_than:{rules_config.follow_ups.after_days}d'


def _subject_keywords(subject: str) -> list[str]:
    words = [w.strip(' ,.:;!?()[]{}\"').lower() for w in subject.split()]
    return [w for w in words if len(w) >= 4][:3]


def suggest_rule_from_correction(
    rules_config: RulesConfig,
    email_input: dict,
    correction_type: str,
    details: dict | None = None,
    state_path: str | Path | None = None,
    now: datetime | None = None,
) -> bool:
    """Append a disabled rule suggestion derived from a human correction."""
    if not rules_config.learning.enabled:
        return False

    now = now or datetime.now()
    sender = email_input.get("author", "")
    subject = email_input.get("subject", "")
    domain = _sender_domain(sender)
    category_name = str(email_input.get("category") or "").strip()
    category_display_name = str(email_input.get("category_display_name") or category_name or "").strip()
    when = {
        "sender_domain": [domain] if domain else [],
        "subject_contains": _subject_keywords(subject),
    }
    suggestion: dict[str, Any] = {
        "created_at": now.isoformat(timespec="seconds"),
        "correction_type": correction_type,
        "source": {
            "author": sender,
            "subject": subject,
            "email_id": email_input.get("email_id"),
            "gmail_thread_id": email_input.get("gmail_thread_id"),
            "category": category_name or None,
        },
        "details": details or {},
    }
    tool_name = str((details or {}).get("tool") or "")
    edited = (details or {}).get("edited") or {}
    if tool_name == "forward_email":
        route_to = _normalize_route_targets(edited.get("to"))
        workflow_name = category_name or _slugify(category_display_name or domain or sender or subject or "workflow")
        suggestion["suggested_workflow"] = {
            "name": workflow_name,
            "display_name": category_display_name or workflow_name.replace("_", " ").title(),
            "priority": email_input.get("priority") or "normal",
            "policy": "notify",
            "owner": email_input.get("workflow_owner"),
            "approver": email_input.get("workflow_approver"),
            "route_to": route_to,
            "when": when,
            "instructions": email_input.get("workflow_instructions"),
        }
    else:
        suggestion["suggested_rule"] = {
            "name": f"review {correction_type} for {domain or sender or 'sender'}",
            "enabled": False,
            "when": when,
            "then": {"notify": True},
        }
    path = _state_path(state_path or rules_config.learning.suggestions_path, "logs/rule_suggestions.jsonl")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(suggestion, sort_keys=True) + "\n")
    except OSError:
        return False
    return True
