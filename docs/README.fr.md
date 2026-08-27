# Win11 Magic Upgrade (FR)

Programme **portable one-click** pour migrer Windows 10 (y compris **1511**) et Windows 11 anciens vers **Windows 11 latest**, **sans perdre fichiers ni applications**.

Inspiré de **Flyby11 / FlyOOBE**, sans dépendance .NET moderne.

## Démarrage

1. Télécharger l’artifact **Portable** depuis les Releases, ou builder localement.
2. Exécuter en Administrateur : `Win11MagicUpgrade.exe` ou `Win11MagicUpgrade.cmd`.

## Partition reservee / EFI

Erreur **Impossible de mettre a jour la partition reservee au systeme** : le programme nettoie l’ESP / System Reserved (polices, dumps OEM) et, si besoin, agrandit via une nouvelle partition boot ~512 Mo (shrink de C: + `bcdboot`), sans effacer les donnees. Bouton **Corriger ESP/SRP** ou `--cli --srp`.

## Documentation

- [Architecture](ARCHITECTURE.md)
- [Bugs de migration & patches](MIGRATION_BUGS.md)
- [README principal (EN)](../README.md)

## Licence

MIT — voir `LICENSE`. Fido : voir `NOTICE`.
