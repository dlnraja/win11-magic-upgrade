# Win11 Magic Upgrade (FR)

**Editeur :** [dlnraja](https://github.com/dlnraja) · EXE / ressources de version signes `dlnraja`

Programme **portable one-click autonome** pour migrer **Windows 8 / 8.1** (y compris **Media Center Key Edition** + offre Lecteur DVD), Windows 10 (y compris **1511**) et Windows 11 anciens vers **Windows 11 latest**, **sans perdre fichiers ni applications**, en chaîne récursive multi-versions avec redémarrages.

Inspiré de **Flyby11 / FlyOOBE**, runtime **Python pur** (PyInstaller) :

- **Pas de .NET Framework 4.x**
- **Pas de PowerShell**
- **Pas de FlyOOBE**

**Docs :** [Architecture](ARCHITECTURE.md) · [Boot / Win11 latest](BOOT_FIRMWARE.md) · [Bugs FR](MIGRATION_BUGS.fr.md) · [Legacy FR](LEGACY_OS.fr.md) · [OEM FR](OEM.fr.md) · [Clean install x64](CLEAN_INSTALL_X64.md) · [README EN](../README.md) · [Releases](https://github.com/dlnraja/win11-magic-upgrade/releases/latest)

## Démarrage rapide

1. Télécharger **`Win11MagicUpgrade-Portable-v*.zip`** depuis les [Releases](https://github.com/dlnraja/win11-magic-upgrade/releases/latest)  
   (**préférez le ZIP** — Chrome bloque souvent l’`.exe` nu)
2. Extraire, puis lancer en Administrateur : `Win11MagicUpgrade.exe` ou `Win11MagicUpgrade.cmd`.
3. Cliquer **ONE-CLICK — Migration complete** (c’est tout).

Chrome dit « virus détecté » ? → **[Aide téléchargement](DOWNLOAD.md)** (Conserver, Unblock-File, SHA256, signaler FP à Google).

```text
Win11MagicUpgrade.exe --cli --oneclick
```

En un clic : diagnostic → preventifs → bypass Flyby11/FlyOOBE → patches → téléchargement/montage ISO → Setup silencieux → reboot/RunOnce jusqu’au **dernier build** (ex. 8.1 Media Center → Win10 22H2 → Lecteur DVD → Win11). Détails : [LEGACY_OS.fr.md](LEGACY_OS.fr.md).

## Partition réservée / EFI

Erreur **Impossible de mettre à jour la partition réservée au système** : nettoyage ESP / System Reserved, puis agrandissement (~512 Mo) si besoin, **idempotent** (pas de double shrink). Bouton **Corriger ESP/SRP** ou `--cli --srp`.

## CLI utiles

```text
--cli --oneclick           # Autonomie max
--cli --install-patches    # Pack préventif seul
--cli --patch              # Préventifs + runtime + SupportGuide
--cli --srp / --mbr / --diagnose / --hybrid
```

## Dernier Windows 11 et démarrage

La cible n’est pas une version figée. La page publique de release health choisit la mise à jour de fonctionnalité la plus récente **en place** (26H2 passe avant 26H1, même si le numéro de build 28000 est plus haut). Si la page échoue, le plancher est **26H2 / 26300**. Un cumulatif sur le même build passe par `UsoClient`, pas par un second ISO.

`mbr2gpt` ne tourne que si le PC peut ensuite démarrer en UEFI. Un BIOS legacy pur garde le MBR. Une clé OpenDuet/OpenCore 1.0.7 (optionnelle) peut ajouter une couche UEFI logicielle ; elle ne s’écrit sur un disque qu’avec un jeton de confirmation, et le disque système est refusé. Détail : [BOOT_FIRMWARE.md](BOOT_FIRMWARE.md). Sources gratuites uniquement (pages Microsoft, CDN, OpenCorePkg) — pas de clé d’API.

## Limites honnêtes

- Sans **SSE4.2 / POPCNT** → max **Win10 22H2** (Win11 24H2+ ne démarre pas).  
- Windows **32-bit** → max **Win10 22H2 x86** (Win11 = install propre x64).  

## Licence

MIT — voir `LICENSE`. Fido : voir `NOTICE`. Toujours faire une sauvegarde avant.
