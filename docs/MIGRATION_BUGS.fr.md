# Bugs de migration (resume FR)

Voir le tableau complet EN : [MIGRATION_BUGS.md](MIGRATION_BUGS.md)  
Recherche forums : [RESEARCH_FORUMS.md](RESEARCH_FORUMS.md)

## Codes les plus fréquents

| Code | Cause typique | Que fait l’outil |
|------|---------------|------------------|
| `0xC1900101-0x20017` | Pilote storage / EDR / chiffrement | Suspend BitLocker, stop AV, map Panther |
| `0xC1900208` | App incompatible | Soften CompatData + avertissement |
| Partition réservée / ESP | Trop petite / pleine | Nettoyage + agrandissement ~512 Mo |
| `0xC1900107` | Reboot pending / ~BT | Nettoyage + reboot autonome |
| Langue ISO ≠ OS | setupprep incompatible | Mapping Fido + hint recovery |

## Dangers

- **`MAGIC_SRP_CONTINUE=1`** : force la migration malgré un échec ESP/SRP — risque de boot. À n’utiliser qu’avec restore vérifié.
- **Vista** : pas de chemin Microsoft officiel — backup + `MAGIC_ALLOW_VISTA=1`.
- **Pas de SSE4.2** : max Win10 22H2 (impossible de spoof l’ISA CPU).

## Clean install x64

Si OS 32-bit : [CLEAN_INSTALL_X64.md](CLEAN_INSTALL_X64.md)
