# Win11 Magic Upgrade — pure Python engine (NO .NET Framework 4.x, NO PowerShell)
from .pipeline import (
    run_pipeline,
    run_diagnose,
    apply_bypass_only,
    convert_mbr_only,
    fix_system_reserved_only,
    deploy_hybrid_only,
    run_patch_enrichment,
    install_preventive_only,
)
from .bypass import list_registry_pack
from .autodiag import build_plan
from .chain import build_version_chain, format_chain
from .preventive import install_all_preventive_patches

__all__ = [
    "run_pipeline",
    "run_diagnose",
    "apply_bypass_only",
    "convert_mbr_only",
    "fix_system_reserved_only",
    "deploy_hybrid_only",
    "run_patch_enrichment",
    "install_preventive_only",
    "install_all_preventive_patches",
    "list_registry_pack",
    "build_plan",
    "build_version_chain",
    "format_chain",
]
