# Win11 Magic Upgrade - pure Python engine (NO .NET Framework 4.x, NO PowerShell)
from .pipeline import run_pipeline, run_diagnose, apply_bypass_only, convert_mbr_only
from .bypass import list_registry_pack
from .autodiag import build_plan

__all__ = [
    "run_pipeline",
    "run_diagnose",
    "apply_bypass_only",
    "convert_mbr_only",
    "list_registry_pack",
    "build_plan",
]
