"""Secure Boot CA 2023 and revoked-media checks. Nothing is written to firmware.

Resetting Secure Boot keys is unsafe when the active db has the 2023 CA and
the factory dbDefault does not. Rescue media signed only by the 2011 CA is
refused once that CA is in DBX. This module never sets AvailableUpdates.
"""
from __future__ import annotations


def reset_keys_unsafe(*, db_has_2023: bool, dbdefault_has_2023: bool) -> bool:
    return bool(db_has_2023) and not bool(dbdefault_has_2023)


def reset_keys_warning() -> str:
    return (
        "Do not reset Secure Boot keys before a BIOS update whose default keys include "
        "the Windows UEFI CA 2023. Resetting keys can remove that CA, stop boot, and ask "
        "for the BitLocker recovery key. Update the OEM BIOS first. "
        "Disabling Secure Boot and running bcdboot is a last resort and needs the BitLocker key."
    )


def media_is_revoked(*, signed_by_2011: bool, dbx_has_pca2011: bool, svn_too_low: bool) -> bool:
    if svn_too_low:
        return True
    return bool(signed_by_2011 and dbx_has_pca2011)


def rescue_decision(*, signed_by_2011: bool, dbx_has_pca2011: bool, svn_too_low: bool, has_2023_loader: bool) -> dict:
    revoked = media_is_revoked(
        signed_by_2011=signed_by_2011,
        dbx_has_pca2011=dbx_has_pca2011,
        svn_too_low=svn_too_low,
    )
    return {
        "offer": not revoked or has_2023_loader,
        "rebuild_with_bootex": revoked and not has_2023_loader,
        "revoked": revoked and not has_2023_loader,
    }
