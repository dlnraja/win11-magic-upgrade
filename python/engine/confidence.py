"""Pre-migration confidence score (green / orange / red)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .logutil import log, save_state


@dataclass
class ConfidenceScore:
    score: int  # 0-100
    level: str  # green | orange | red
    reasons: list[str] = field(default_factory=list)
    chain_preview: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_confidence(report: Any, *, chain_label: str = "") -> ConfidenceScore:
    score = 100
    reasons: list[str] = []

    free = float(getattr(report, "free_gb", 0) or 0)
    if free < 15:
        score -= 35
        reasons.append(f"Low free disk ({free} GB) - need ~20 GB+")
    elif free < 25:
        score -= 15
        reasons.append(f"Tight free disk ({free} GB)")

    if getattr(report, "sse42", None) is False:
        score -= 25
        reasons.append("No SSE4.2/POPCNT - max Win10 22H2 (honest)")
    if getattr(report, "architecture", "x64") != "x64":
        score -= 30
        reasons.append("32-bit OS - no inplace Win11")
    if str(getattr(report, "architecture", "")).lower() in ("arm64", "aarch64"):
        score -= 40
        reasons.append("ARM64 - limited path / no x64 ISO chain")
    if getattr(report, "partition_style", "") == "MBR":
        score -= 10
        reasons.append("MBR - mbr2gpt hop required")
    if getattr(report, "is_legacy", False):
        score -= 10
        reasons.append("Legacy Windows - multi-hop chain")
    if getattr(report, "firmware_likely_ia32", False):
        score -= 20
        reasons.append("IA32 UEFI - hybrid CSMWrap path")
    eid = str(getattr(report, "edition_id", "")).lower()
    if "ltsc" in eid or "ltsb" in eid:
        score -= 25
        reasons.append("LTSC/LTSB - retail ISO may refuse edition")
    if getattr(report, "ram_gb", 8) < 4:
        score -= 20
        reasons.append("RAM < 4 GB")

    score = max(0, min(100, score))
    if score >= 75:
        level = "green"
    elif score >= 45:
        level = "orange"
    else:
        level = "red"
    cs = ConfidenceScore(score=score, level=level, reasons=reasons, chain_preview=chain_label)
    extra = f" | {chain_label}" if chain_label else ""
    log(
        f"Confidence: {score}/100 ({level}){extra}",
        "OK" if level == "green" else "WARN",
    )
    for r in reasons:
        log(f"  - {r}", "INFO")
    save_state({"Confidence": cs.as_dict()})
    return cs
