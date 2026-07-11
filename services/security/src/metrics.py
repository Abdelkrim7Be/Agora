from __future__ import annotations

from collections import defaultdict
from threading import Lock

_lock = Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
_metadata: dict[str, dict[str, str]] = {}


def inc_counter(name: str, amount: float = 1.0, **labels: str) -> None:
    with _lock:
        _metadata.setdefault(name, {"type": "counter", "help": name.replace("_", " ")})
        key = (name, tuple(sorted((str(k), str(v)) for k, v in labels.items())))
        _counters[key] += amount


def register_metric(name: str, metric_type: str, help_text: str) -> None:
    with _lock:
        _metadata[name] = {"type": metric_type, "help": help_text}


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{key}="{value}"' for key, value in labels) + "}"


def render_metrics() -> str:
    register_metric("agora_security_health", "gauge", "Security service health")
    with _lock:
        counters = dict(_counters)
        metadata = dict(_metadata)
    dynamic = {("agora_security_health", tuple()): 1.0}
    lines: list[str] = []
    for name in sorted(metadata):
        lines.append(f"# HELP {name} {metadata[name]['help']}")
        lines.append(f"# TYPE {name} {metadata[name]['type']}")
        family_samples = []
        for (sample_name, labels), value in counters.items():
            if sample_name == name:
                family_samples.append((labels, value))
        for (sample_name, labels), value in dynamic.items():
            if sample_name == name:
                family_samples.append((labels, value))
        if not family_samples:
            family_samples.append((tuple(), 0.0))
        for labels, value in family_samples:
            lines.append(f"{name}{_format_labels(labels)} {value}")
    return "\n".join(lines) + "\n"
