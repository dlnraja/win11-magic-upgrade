# Win11 Magic Upgrade — pure Python engine (NO .NET Framework 4.x, NO PowerShell)
from .pipeline import (
    run_pipeline,
    run_diagnose,
    apply_bypass_only,
    convert_mbr_only,
    fix_system_reserved_only,
    deploy_hybrid_only,
    run_patch_enrichment,
)
from .bypass import list_registry_pack
from .autodiag import build_plan
from .chain import build_version_chain, format_chain

__all__ = [
    "run_pipeline",
    "run_diagnose",
    "apply_bypass_only",
    "convert_mbr_only",
    "fix_system_reserved_only",
    "deploy_hybrid_only",
    "run_patch_enrichment",
    "list_registry_pack",
    "build_plan",
    "build_version_chain",
    "format_chain",
]
