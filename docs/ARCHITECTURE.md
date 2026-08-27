# Architecture

```
Win11MagicUpgrade.exe / .cmd
        │
        ▼
src/Win11MagicUpgrade.ps1          Orchestrator (admin, resume, CLI)
        │
        ├── Logging.ps1            State + logs under %LOCALAPPDATA%\Win11MagicUpgrade
        ├── SystemDetect.ps1       OS build, disk MBR/GPT, CPU SSE4.2, TPM, firmware
        ├── BypassChecks.ps1       MoSetup / LabConfig / HwReqChk + /product server args
        ├── MigrationPatches.ps1   Researched fixes for 0xC1900101, AV, filters, caches…
        ├── CommonFixes.ps1        Free space, $WINDOWS.~BT cleanup, RunOnce
        ├── MbrToGpt.ps1           mbr2gpt validate/convert + layout repair
        ├── IsoDownload.ps1        Fido → Microsoft CDN ISO (BITS)
        └── UpgradeEngine.ps1      Intermediate Win10 22H2 → Win11 latest pipeline
```

## Upgrade decision tree

1. If CPU lacks SSE4.2/POPCNT → **stop** (24H2+ will not boot).
2. If Win10 build &lt; 17763 (1809) → download Win10 22H2 ISO → inplace → RunOnce resume.
3. If system disk is MBR and `mbr2gpt` exists → convert (no wipe) → remind UEFI firmware.
4. Apply registry bypasses + migration patches.
5. Download Win11 latest ISO (Fido) → `setupprep.exe` / `setup.exe` **`/product server`** `/auto upgrade`.

## Why `/product server`

Documented community / Flyby11 approach: Server setup path skips client TPM/Secure Boot/CPU allow-list checks while still installing client Windows 11 from a client ISO.

## State / resume

`%LOCALAPPDATA%\Win11MagicUpgrade\state.json` + `HKLM\...\RunOnce\Win11MagicUpgrade` after intermediate upgrades.
