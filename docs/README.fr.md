# Win11 Magic Upgrade (FR)

**Editeur :** [dlnraja](https://github.com/dlnraja) · EXE / ressources de version signes `dlnraja`

Programme **portable one-click autonome** pour migrer Windows 10 (y compris **1511**) et Windows 11 anciens vers **Windows 11 latest**, **sans perdre fichiers ni applications**.

Inspiré de **Flyby11 / FlyOOBE**, runtime **Python pur** (PyInstaller) :

- **Pas de .NET Framework 4.x**
- **Pas de PowerShell**
- **Pas de FlyOOBE**

**Docs :** [Architecture](ARCHITECTURE.md) · [Bugs & patches](MIGRATION_BUGS.md) · [README EN](../README.md) · [Releases](https://github.com/dlnraja/win11-magic-upgrade/releases/latest)

## Démarrage rapide

1. Télécharger le ZIP **Portable** depuis les [Releases](https://github.com/dlnraja/win11-magic-upgrade/releases/latest).
2. Lancer en Administrateur : `Win11MagicUpgrade.exe` ou `Win11MagicUpgrade.cmd`.
3. Cliquer **ONE-CLICK — Migration complete** (c’est tout).

```text
Win11MagicUpgrade.exe --cli --oneclick
```

En un clic : diagnostic → preventifs → bypass Flyby11/FlyOOBE → patches → téléchargement/montage ISO → Setup silencieux → reboot/RunOnce jusqu’à Win11.

## Partition réservée / EFI

Erreur **Impossible de mettre à jour la partition réservée au système** : nettoyage ESP / System Reserved, puis agrandissement (~512 Mo) si besoin, **idempotent** (pas de double shrink). Bouton **Corriger ESP/SRP** ou `--cli --srp`.

## CLI utiles

```text
--cli --oneclick           # Autonomie max
--cli --install-patches    # Pack préventif seul
--cli --patch              # Préventifs + runtime + SupportGuide
--cli --srp / --mbr / --diagnose / --hybrid
```

## Limites honnêtes

- Sans **SSE4.2 / POPCNT** → max **Win10 22H2** (Win11 24H2+ ne démarre pas).  
- Windows **32-bit** → max **Win10 22H2 x86** (Win11 = install propre x64).  

## Licence

MIT — voir `LICENSE`. Fido : voir `NOTICE`. Toujours faire une sauvegarde avant.
