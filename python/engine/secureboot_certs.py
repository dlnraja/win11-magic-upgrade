"""Secure Boot certificate gate for UEFI boot-file changes.

Microsoft retires the 2011 Secure Boot certificates in 2026. The Windows boot
manager moves from "Microsoft Windows Production PCA 2011" to "Windows UEFI CA
2023". A firmware db that does not contain the 2023 CA will reject a boot
manager signed only by that CA. After the 2011 PCA is added to dbx, or after a
DBX update raises the boot-manager SVN, an older loader will not pass Secure
Boot either. Either mismatch leaves the PC unbootable if we write it as the
active loader.

This module reads only local, built-in state. No paid API and no PowerShell:

  * Registry ``SecureBoot\\State\\UEFISecureBootEnabled``
  * Registry ``SecureBoot\\Servicing`` values published for the 2023 rollout
    (``WindowsUEFICA2023Capable``, ``UEFICA2023Status``, ``UEFICA2023Error``)
  * Firmware variables ``db``, ``dbx``, and ``SbatLevel`` through
    ``GetFirmwareEnvironmentVariableEx`` when the process can read them
  * The Authenticode certificate table inside ``bootmgfw.efi`` / ``bootx64.efi``

Certificate fingerprints were computed from the public DER files in
https://github.com/microsoft/secureboot_objects (``windows uefi ca 2023.der``
and ``MicWinProPCA2011_2011-10-19.der``). The SVN marker
``BOOTMGRSECURITYVERSIONNUMBER`` is the same token Rufus records for the
Microsoft SVN revocation (https://github.com/pbatard/rufus/issues/2244).

Legacy BIOS, CSM, and 32-bit paths are not gated: Secure Boot is off or does
not check ``bootmgr``. The gate refuses a UEFI rewrite only when Secure Boot
is on and the target loader is not acceptable. Snapshot restore still runs
for a loader we cannot classify; it skips a loader that is positively revoked.
"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from .logutil import log

# Subject CNs embedded in the db and in Authenticode certificates.
CN_WINDOWS_UEFI_CA_2023 = "Windows UEFI CA 2023"
CN_WINDOWS_PCA_2011 = "Microsoft Windows Production PCA 2011"

SIGNER_WINDOWS_UEFI_CA_2023 = "windows_uefi_ca_2023"
SIGNER_WINDOWS_PCA_2011 = "windows_pca_2011"

# SHA-1 of the whole certificate, and SHA-256 of the TBS certificate.
# DBX entries of type EFI_CERT_X509_SHA256 store the TBS hash, not the CN.
_WINDOWS_UEFI_CA_2023_SHA1 = bytes.fromhex("45a0fa32604773c82433c3b7d59e7466b3ac0c67")
_WINDOWS_PCA_2011_SHA1 = bytes.fromhex("580a6f4cc4e4b669b9ebdc1b2b3e087b80d0678d")
_WINDOWS_PCA_2011_SHA256 = bytes.fromhex(
    "e8e95f0733a55e8bad7be0a1413ee23c51fcea64b3c8fa6a786935fddcc71961"
)
_WINDOWS_PCA_2011_TBS_SHA256 = bytes.fromhex(
    "4e80be107c860de896384b3eff50504dc2d76ac7151df3102a4450637a032146"
)

_EFI_GLOBAL = "{8BE4DF61-93CA-11D2-AA0D-00E098032B8C}"
_SBAT_GUID = "{605dab50-e046-4300-abb6-3dd810dd8b23}"
_STATE_KEY = r"SYSTEM\CurrentControlSet\Control\SecureBoot\State"
_SERVICING_KEY = r"SYSTEM\CurrentControlSet\Control\SecureBoot\Servicing"

_SVN_ASCII = re.compile(rb"BOOTMGRSECURITYVERSIONNUMBER\s*,\s*0x([0-9A-Fa-f]+)")
_SVN_UTF16_PREFIX = "BOOTMGRSECURITYVERSIONNUMBER,0x".encode("utf-16le")

_MEDIA_NAMES = ("bootmgfw.efi", "bootmgr.efi", "bootx64.efi")


@dataclass(frozen=True)
class LoaderIdentity:
    """Signers and SVN found in one boot-loader file."""

    signers: frozenset[str]
    svn: int | None
    present: bool

    @staticmethod
    def missing() -> LoaderIdentity:
        return LoaderIdentity(frozenset(), None, False)


@dataclass(frozen=True)
class SecureBootCertState:
    """What the firmware will accept, as far as local reads can show."""

    secure_boot: str  # on | off | unknown
    db_has_windows_uefi_ca_2023: bool | None
    db_has_windows_pca_2011: bool | None
    pca2011_revoked_in_dbx: bool | None
    firmware_min_svn: int | None
    uefica2023_status: str
    uefica2023_error: int | None
    windows_uefi_ca_2023_capable: int | None
    source: str


@dataclass(frozen=True)
class BootRewriteDecision:
    """Whether a boot-file write is allowed to proceed."""

    allow: bool
    reason: str
    proven: bool


def _contains_text(blob: bytes, text: str) -> bool:
    raw = text.encode("ascii")
    return raw in blob or text.encode("utf-16le") in blob


def parse_bootmgr_svn(blob: bytes) -> int | None:
    """Highest BOOTMGRSECURITYVERSIONNUMBER in the blob, or None."""
    if not blob:
        return None
    found: list[int] = [int(m.group(1), 16) for m in _SVN_ASCII.finditer(blob)]
    start = 0
    while True:
        index = blob.find(_SVN_UTF16_PREFIX, start)
        if index < 0:
            break
        cursor = index + len(_SVN_UTF16_PREFIX)
        digits: list[str] = []
        while cursor + 1 < len(blob):
            try:
                char = blob[cursor : cursor + 2].decode("utf-16le")
            except UnicodeDecodeError:
                break
            if char not in "0123456789abcdefABCDEF":
                break
            digits.append(char)
            cursor += 2
        if digits:
            found.append(int("".join(digits), 16))
        start = index + 2
    return max(found) if found else None


def db_contains_windows_uefi_ca_2023(db: bytes) -> bool:
    if not db:
        return False
    return _contains_text(db, CN_WINDOWS_UEFI_CA_2023) or _WINDOWS_UEFI_CA_2023_SHA1 in db


def db_contains_windows_pca_2011(db: bytes) -> bool:
    if not db:
        return False
    return _contains_text(db, CN_WINDOWS_PCA_2011) or _WINDOWS_PCA_2011_SHA1 in db


def dbx_revokes_windows_pca_2011(dbx: bytes) -> bool:
    """True when dbx carries the 2011 Windows PCA certificate or its hash."""
    if not dbx:
        return False
    if _contains_text(dbx, CN_WINDOWS_PCA_2011):
        return True
    return any(
        needle in dbx
        for needle in (
            _WINDOWS_PCA_2011_SHA1,
            _WINDOWS_PCA_2011_SHA256,
            _WINDOWS_PCA_2011_TBS_SHA256,
        )
    )


def pe_security_directory(data: bytes) -> bytes:
    """Authenticode certificate table, or the whole buffer when it is not a PE."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        return data
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if e_lfanew <= 0 or e_lfanew + 24 > len(data) or data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        return data
    coff = e_lfanew + 4
    size_opt = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    if size_opt < 120 or opt + size_opt > len(data):
        return data
    magic = struct.unpack_from("<H", data, opt)[0]
    dir_off = 112 if magic == 0x20B else 96
    if dir_off + 8 * 5 > size_opt:
        return data
    sec_off, sec_size = struct.unpack_from("<II", data, opt + dir_off + 4 * 8)
    if sec_off <= 0 or sec_size <= 0 or sec_off + sec_size > len(data):
        return b""
    return data[sec_off : sec_off + sec_size]


def identify_loader(data: bytes | None) -> LoaderIdentity:
    """Signers and SVN from a boot-loader image. Empty input is not present."""
    if not data:
        return LoaderIdentity.missing()
    blob = pe_security_directory(data)
    signers: set[str] = set()
    haystacks = [blob, data]
    for hay in haystacks:
        if _contains_text(hay, CN_WINDOWS_UEFI_CA_2023) or _WINDOWS_UEFI_CA_2023_SHA1 in hay:
            signers.add(SIGNER_WINDOWS_UEFI_CA_2023)
        if _contains_text(hay, CN_WINDOWS_PCA_2011) or _WINDOWS_PCA_2011_SHA1 in hay:
            signers.add(SIGNER_WINDOWS_PCA_2011)
    svn = parse_bootmgr_svn(blob)
    if svn is None:
        svn = parse_bootmgr_svn(data)
    return LoaderIdentity(frozenset(signers), svn, True)


def rewrite_decision(
    state: SecureBootCertState,
    loader: LoaderIdentity,
    *,
    kind: str = "uefi_bootmgr",
) -> BootRewriteDecision:
    """Decide whether writing ``loader`` can leave Secure Boot unable to start.

    ``kind`` is ``uefi_bootmgr`` (bcdboot / setup media), ``snapshot_restore``,
    ``bios_bootmgr``, or ``hybrid_activate``. BIOS and Secure Boot off always
    allow the existing path.
    """
    if kind == "bios_bootmgr" or state.secure_boot in ("off", "unsupported"):
        return BootRewriteDecision(
            True,
            "Secure Boot is off or this is a legacy BIOS boot file. "
            "The 2023 CA rotation does not gate this path.",
            True,
        )
    if kind == "hybrid_activate":
        if state.secure_boot == "on":
            return BootRewriteDecision(
                False,
                "Secure Boot is on. CSMWrap is not signed by a certificate in db, "
                "so replacing EFI\\Boot\\bootia32.efi would make the next boot fail. "
                "The bridge stays staged until Secure Boot is turned off.",
                True,
            )
        return BootRewriteDecision(
            True,
            "Secure Boot is not on, so the hybrid bridge may replace the IA32 loader.",
            True,
        )
    if state.secure_boot != "on":
        return BootRewriteDecision(
            True,
            "Secure Boot state is unknown. No certificate mismatch was proven, "
            "so the current boot path is left available.",
            False,
        )
    if not loader.present:
        return BootRewriteDecision(
            False,
            "Secure Boot is on and the target boot loader could not be read, "
            "so its signer is unknown. Refusing to replace UEFI boot files.",
            False,
        )

    has_2023 = SIGNER_WINDOWS_UEFI_CA_2023 in loader.signers
    has_2011 = SIGNER_WINDOWS_PCA_2011 in loader.signers
    db_2023 = state.db_has_windows_uefi_ca_2023
    revoked = state.pca2011_revoked_in_dbx
    signer_label = _signer_label(loader)

    if not has_2023 and not has_2011:
        # A snapshot that does not parse is still the pre-change file. A new
        # write of an unrecognized loader is refused.
        return BootRewriteDecision(
            False,
            "Secure Boot is on and this boot loader is not signed by "
            f"{CN_WINDOWS_UEFI_CA_2023} or {CN_WINDOWS_PCA_2011} ({signer_label}). "
            "Refusing to install it. The current boot files stay in place.",
            kind != "snapshot_restore",
        )

    trusts_2011 = has_2011 and revoked is not True
    trusts_2023 = has_2023 and db_2023 is True
    if has_2023 and not has_2011 and db_2023 is False:
        return BootRewriteDecision(
            False,
            "Secure Boot is on and db does not contain Windows UEFI CA 2023. "
            "This boot loader is signed only by that CA, so the firmware would "
            "reject it. Keeping the current boot manager.",
            True,
        )
    if has_2023 and not has_2011 and db_2023 is None:
        return BootRewriteDecision(
            False,
            "Secure Boot is on and this boot loader is signed only by "
            "Windows UEFI CA 2023, but the firmware db could not be read. "
            "Refusing to install it.",
            False,
        )
    if has_2011 and not has_2023 and revoked is True:
        return BootRewriteDecision(
            False,
            "Secure Boot is on and dbx revokes Microsoft Windows Production PCA 2011. "
            "This boot loader is signed only by that CA. Keeping the current boot manager.",
            True,
        )
    if has_2011 and revoked is True and has_2023 and db_2023 is not True:
        return BootRewriteDecision(
            False,
            "Secure Boot is on, dbx revokes the 2011 Windows PCA, and db does not "
            "contain Windows UEFI CA 2023. Neither signature on this boot loader "
            "would pass. Keeping the current boot manager.",
            True,
        )

    svn_block = _svn_block(state, loader, has_2023=has_2023)
    if svn_block:
        return BootRewriteDecision(False, svn_block, True)

    if trusts_2023 or trusts_2011:
        why = []
        if trusts_2023:
            why.append("db contains Windows UEFI CA 2023")
        if trusts_2011:
            why.append("Windows Production PCA 2011 is still trusted")
        svn_note = ""
        if loader.svn is not None and state.firmware_min_svn is not None:
            svn_note = (
                f" Loader SVN 0x{loader.svn:x} meets firmware minimum "
                f"0x{state.firmware_min_svn:x}."
            )
        return BootRewriteDecision(
            True,
            f"Secure Boot accepts this loader ({signer_label}): " + "; ".join(why) + "." + svn_note,
            True,
        )
    return BootRewriteDecision(
        False,
        "Secure Boot is on and this boot loader's certificate could not be matched "
        "to db/dbx. Refusing to replace the current boot files.",
        False,
    )


def _signer_label(loader: LoaderIdentity) -> str:
    if not loader.signers:
        return "no recognized signer"
    names = []
    if SIGNER_WINDOWS_UEFI_CA_2023 in loader.signers:
        names.append(CN_WINDOWS_UEFI_CA_2023)
    if SIGNER_WINDOWS_PCA_2011 in loader.signers:
        names.append(CN_WINDOWS_PCA_2011)
    return " and ".join(names)


def _svn_block(state: SecureBootCertState, loader: LoaderIdentity, *, has_2023: bool) -> str:
    minimum = state.firmware_min_svn
    if minimum is None:
        return ""
    if loader.svn is None:
        if has_2023:
            return (
                f"Firmware dbx enforces boot-manager SVN 0x{minimum:x} and this "
                "2023-signed loader has no BOOTMGRSECURITYVERSIONNUMBER. "
                "Refusing to install it."
            )
        return ""
    if loader.svn < minimum:
        return (
            f"This boot loader SVN 0x{loader.svn:x} is below the firmware minimum "
            f"0x{minimum:x}. Restoring or installing it would fail Secure Boot. "
            "Keeping the current boot manager."
        )
    return ""


def format_cert_state(state: SecureBootCertState) -> str:
    db = _tri(state.db_has_windows_uefi_ca_2023)
    pca = _tri(state.db_has_windows_pca_2011)
    revoked = _tri(state.pca2011_revoked_in_dbx)
    svn = f"0x{state.firmware_min_svn:x}" if state.firmware_min_svn is not None else "unknown"
    status = state.uefica2023_status or "unknown"
    capable = (
        str(state.windows_uefi_ca_2023_capable)
        if state.windows_uefi_ca_2023_capable is not None
        else "unknown"
    )
    err = str(state.uefica2023_error) if state.uefica2023_error is not None else "none"
    return (
        f"Secure Boot certs: enabled={state.secure_boot} db_ca2023={db} "
        f"db_pca2011={pca} dbx_pca2011={revoked} firmware_svn={svn} "
        f"UEFICA2023Status={status} capable={capable} error={err} ({state.source})"
    )


def _tri(value: bool | None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def secure_boot_is_on(state: SecureBootCertState | None = None) -> bool:
    current = state if state is not None else probe_secure_boot_state()
    return current.secure_boot == "on"


def snapshot_restore_allowed(
    data: bytes,
    state: SecureBootCertState | None = None,
) -> BootRewriteDecision:
    """Snapshot restore skips a positively revoked loader and keeps everything else.

    The snapshot is the pre-change file that booted this PC. A parser miss must
    not block that safety net. A loader dbx or SVN would reject is not restored.
    """
    current = state if state is not None else probe_secure_boot_state()
    if current.secure_boot != "on":
        return BootRewriteDecision(
            True,
            "Secure Boot is not on. Snapshot restore is unchanged.",
            True,
        )
    decision = rewrite_decision(current, identify_loader(data), kind="snapshot_restore")
    if decision.allow:
        return decision
    if decision.proven:
        return decision
    return BootRewriteDecision(
        True,
        "Snapshot loader signer could not be proven against db/dbx. "
        "Restoring it anyway because it is the pre-change boot file.",
        False,
    )


def efi_write_block_reason(
    mode: str = "UEFI",
    *,
    source: Path | None = None,
    file_bytes: bytes | None = None,
    require_source: bool = False,
    state: SecureBootCertState | None = None,
) -> str:
    """Empty when ``bcdboot /f MODE`` may run. Otherwise the refusal text.

    BIOS mode is always allowed. A missing Windows-tree loader does not by
    itself block repair; setup media passes ``require_source`` so an unseen
    signer refuses the launch while Secure Boot is on.
    """
    if (mode or "").upper() == "BIOS":
        return ""
    current = state if state is not None else probe_secure_boot_state()
    if current.secure_boot != "on":
        return ""
    data = file_bytes
    if data is None and source is not None and source.is_file():
        try:
            data = source.read_bytes()
        except OSError as exc:
            log(f"Secure Boot cert read {source}: {exc}", "WARN")
            data = None
    if data is None and source is None:
        tree = windows_tree_loader()
        if tree is not None:
            try:
                data = tree.read_bytes()
            except OSError as exc:
                log(f"Secure Boot cert read {tree}: {exc}", "WARN")
    if not data:
        if require_source:
            return (
                "Secure Boot is on and the target boot loader file could not be read. "
                "Refusing to replace UEFI boot files or start Setup."
            )
        return ""
    decision = rewrite_decision(current, identify_loader(data), kind="uefi_bootmgr")
    if decision.allow:
        return ""
    return decision.reason


def find_media_loader(root: Path) -> Path | None:
    """bootmgfw.efi or bootx64.efi on install media, if the ISO layout has one."""
    rels = (
        Path("efi") / "microsoft" / "boot" / "bootmgfw.efi",
        Path("efi") / "microsoft" / "boot" / "bootmgr.efi",
        Path("efi") / "boot" / "bootx64.efi",
    )
    for rel in rels:
        candidate = root / rel
        if candidate.is_file():
            return candidate
    efi = None
    if root.is_dir():
        try:
            for child in root.iterdir():
                if child.is_dir() and child.name.lower() == "efi":
                    efi = child
                    break
        except OSError:
            return None
    if efi is None:
        return None
    found: list[Path] = []
    try:
        for path in efi.rglob("*.efi"):
            if path.name.lower() in _MEDIA_NAMES:
                found.append(path)
    except OSError:
        return None
    found.sort(key=lambda path: (0 if path.name.lower() == "bootmgfw.efi" else 1, len(path.parts)))
    return found[0] if found else None


def windows_tree_loader() -> Path | None:
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    for rel in (Path("Boot") / "EFI" / "bootmgfw.efi", Path("Boot") / "EFI" / "bootmgr.efi"):
        candidate = root / rel
        if candidate.is_file():
            return candidate
    return None


def setup_media_block_reason(root: Path, state: SecureBootCertState | None = None) -> str:
    """Refuse Setup when Secure Boot would reject the media's boot manager."""
    current = state if state is not None else probe_secure_boot_state()
    if current.secure_boot != "on":
        return ""
    loader = find_media_loader(Path(root))
    if loader is None:
        return (
            "Secure Boot is on and this install media has no efi\\boot\\bootx64.efi "
            "or bootmgfw.efi whose signer can be checked. Refusing to start Setup, "
            "because the new boot manager might be signed only by Windows UEFI CA 2023."
        )
    reason = efi_write_block_reason("UEFI", source=loader, require_source=True, state=current)
    if not reason:
        ident = identify_loader(loader.read_bytes())
        log(
            f"Setup media loader {loader.name} is acceptable under Secure Boot "
            f"({_signer_label(ident)}).",
            "OK",
        )
        return ""
    return (
        f"{reason} Setup was not started. The existing snapshot is untouched and "
        "the current boot manager stays in place."
    )


def loader_for_plan() -> LoaderIdentity:
    """Identity of the boot manager bcdboot would copy from the running Windows tree."""
    path = windows_tree_loader()
    if path is None:
        return LoaderIdentity.missing()
    try:
        return identify_loader(path.read_bytes())
    except OSError:
        return LoaderIdentity.missing()


_cached_state: SecureBootCertState | None = None


def probe_secure_boot_state(*, refresh: bool = False) -> SecureBootCertState:
    """Read registry and, when permitted, the firmware db/dbx. Cached per process."""
    global _cached_state
    if _cached_state is not None and not refresh:
        return _cached_state
    state = _probe_uncached()
    _cached_state = state
    return state


def clear_probe_cache() -> None:
    global _cached_state
    _cached_state = None


def _probe_uncached() -> SecureBootCertState:
    enabled = _reg_dword(_STATE_KEY, "UEFISecureBootEnabled")
    capable = _reg_dword(_SERVICING_KEY, "WindowsUEFICA2023Capable")
    status = _reg_str(_SERVICING_KEY, "UEFICA2023Status")
    error = _reg_dword(_SERVICING_KEY, "UEFICA2023Error")
    db = _read_firmware_var("db", _EFI_GLOBAL)
    dbx = _read_firmware_var("dbx", _EFI_GLOBAL)
    sbat = _read_firmware_var("SbatLevel", _SBAT_GUID)
    if sbat is None:
        sbat = _read_firmware_var("SbatLevel", _EFI_GLOBAL)
    secure = _secure_boot_label(enabled, db)
    db_2023 = _combine_db_2023(capable, status, db)
    db_2011 = db_contains_windows_pca_2011(db) if db is not None else None
    revoked = dbx_revokes_windows_pca_2011(dbx) if dbx is not None else None
    svn_blob = b"".join(part for part in (dbx, sbat) if part)
    sources = ["registry" if enabled is not None or capable is not None or status else "no-registry"]
    if db is not None or dbx is not None:
        sources.append("firmware-var")
    return SecureBootCertState(
        secure_boot=secure,
        db_has_windows_uefi_ca_2023=db_2023,
        db_has_windows_pca_2011=db_2011,
        pca2011_revoked_in_dbx=revoked,
        firmware_min_svn=parse_bootmgr_svn(svn_blob),
        uefica2023_status=status or "",
        uefica2023_error=error,
        windows_uefi_ca_2023_capable=capable,
        source="+".join(sources),
    )


def _secure_boot_label(enabled: int | None, db: bytes | None) -> str:
    if enabled == 1:
        return "on"
    if enabled == 0:
        return "off"
    variable = _read_firmware_var("SecureBoot", _EFI_GLOBAL)
    if variable:
        if variable[:1] == b"\x01":
            return "on"
        if variable[:1] == b"\x00":
            return "off"
    if db is None and enabled is None:
        return "unknown"
    return "unknown"


def _combine_db_2023(capable: int | None, status: str, db: bytes | None) -> bool | None:
    if db is not None:
        return db_contains_windows_uefi_ca_2023(db)
    if capable is not None:
        return capable >= 1
    if status.lower() == "updated":
        return True
    if status.lower() == "notstarted":
        return False
    return None


def _reg_dword(path: str, name: str) -> int | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            value, _typ = winreg.QueryValueEx(key, name)
        return int(value)
    except (OSError, TypeError, ValueError, ImportError):
        return None


def _reg_str(path: str, name: str) -> str:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            value, _typ = winreg.QueryValueEx(key, name)
        return str(value or "").strip()
    except (OSError, TypeError, ValueError, ImportError):
        return ""


def _read_firmware_var(name: str, guid: str) -> bytes | None:
    try:
        import ctypes
        from ctypes import wintypes

        if not _enable_firmware_privilege():
            return None
        kernel32 = ctypes.windll.kernel32
        kernel32.GetFirmwareEnvironmentVariableExW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetFirmwareEnvironmentVariableExW.restype = wintypes.DWORD
        size = 256 * 1024
        buf = ctypes.create_string_buffer(size)
        attrs = wintypes.DWORD(0)
        read = kernel32.GetFirmwareEnvironmentVariableExW(
            name, guid, buf, size, ctypes.byref(attrs)
        )
        if not read:
            return None
        return buf.raw[: int(read)]
    except (AttributeError, OSError, ImportError):
        return None


def _enable_firmware_privilege() -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0028, ctypes.byref(token)):
            return False
        try:
            class _LUID(ctypes.Structure):
                _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

            class _LUID_AND_ATTRIBUTES(ctypes.Structure):
                _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]

            class _TOKEN_PRIVILEGES(ctypes.Structure):
                _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", _LUID_AND_ATTRIBUTES)]

            luid = _LUID()
            if not advapi32.LookupPrivilegeValueW(None, "SeSystemEnvironmentPrivilege", ctypes.byref(luid)):
                return False
            priv = _TOKEN_PRIVILEGES(1, _LUID_AND_ATTRIBUTES(luid, 0x00000002))
            return bool(advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(priv), 0, None, None))
        finally:
            kernel32.CloseHandle(token)
    except (AttributeError, OSError, ImportError):
        return False
