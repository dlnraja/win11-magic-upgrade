# OS legacy (Vista → 11)

Doc EN : [LEGACY_OS.md](LEGACY_OS.md)

## Chaîne One-Click (récursive, keep apps)

`Vista / 7 / 8 / 8.1 (+ Media Center) → [8.1 local optionnel] → Win10 22H2 → offres Media Center (Lecteur DVD) → MBR2GPT → Win11 latest`

Reboots + **RunOnce** jusqu’au build le plus récent.

## Windows 8 / 8.1 Media Center Key Edition

- Détection : `ProfessionalWMC`, nom produit, fichiers `ehome`, registre Media Center
- **Inplace uniquement** (jamais clean) pour garder apps / données / éligibilité
- Sur le média **Win10** : `ei.cfg` + `pid.txt` → Professional
- Éligibilité sauvée dans `state.json` avant le hop (après upgrade l’édition devient Pro)
- Étape **`media_offers`** : Windows Update + détection `Microsoft.WindowsDVDPlayer`, puis suite vers Win11

> L’offre gratuite Lecteur DVD Microsoft était limitée dans le temps (2015). On fait quand même le bon chemin inplace + marqueurs + nudge WU.

## Win8 → 8.1 (optionnel)

CDN Microsoft ne sert plus l’ISO 8.1. Si vous en avez une :

```text
set MAGIC_WIN81_ISO=D:\ISO\Win8.1.iso
```

Sinon : **8 → Win10 22H2** directement (keep-apps).

## Points clés

- **Vista** : best-effort (backup obligatoire)
- **Éditions N / SL / LTSC / Embedded** : avertissements
- CDN : seulement Win10 22H2 + Win11 latest
