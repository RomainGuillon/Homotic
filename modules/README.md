# Répertoire des modules

Un sous-répertoire par module. Le socle les découvre tout seul : il suffit
de déposer le répertoire ici, puis d'aller le cocher dans l'onglet
**Configuration → Modules → Valider**. Un module coché devient une app
Django à part entière (templates, modèles, migrations).

## Modules livrés

| Répertoire | Onglet | Ce qu'il apporte |
| --- | --- | --- |
| `enphase` | Énergie | Passerelle Envoy en local : production, consommation, réseau, journée détaillée |
| `solcast` | Solaire | Prévisions de production, meilleur créneau de chauffe |
| `tempo` | Tempo | Couleurs et tarifs EDF Tempo (API RTE) |
| `chauffe_eau` | Chauffe-eau | Ballon Atlantic via Cozytouch |
| `clim` | Climatisation | Climatisations Hitachi Hi-Kumo |
| `tuya` | Capteurs | Capteurs et prises Tuya, lus sur le réseau local |
| `arlo` | Caméras | Modes de surveillance Arlo et instantanés |
| `verisure` | Alarme | État de l'alarme Verisure (lecture seule) |
| `heure_demarrage` | Heure démarrage | Calcule la meilleure heure de chauffe du ballon |
| `exemple` | Exemple | Squelette à copier pour démarrer un module |

## Structure d'un module

```
mon_module/
├── conf.py            manifest : onglet, icône, tâches, scénarios, infos, besoins
├── onglet/views.py    la page complète du module
├── dashboard/views.py le ou les blocs du tableau de bord
├── fonctions/         api.py, info.py, scenario.py, affichage.py…
└── templates/mon_module/
```

Seul `conf.py` est obligatoire. Un module peut n'avoir aucun bloc de tableau
de bord (`heure_demarrage`), ou aucune action de scénario (`verisure`).

Le manifest déclare :

- `ONGLET`, `ICONE`, `DESCRIPTION` — l'identité affichée ;
- `TACHES` — ce que le scheduler doit appeler, et à quelle fréquence
  (surchargeable en base via `tache_<nom>_minutes`, `0` = désactivée) ;
- `SCENARIO` — les fonctions exécutables depuis un scénario ou un bouton ;
- `INFOS` — les lectures utilisables en condition et en « Info → variable ».

`SCENARIO` et `INFOS` peuvent être construits dynamiquement quand la liste
dépend du matériel détecté : voir `tuya/conf.py`, qui génère une paire
allumer/éteindre par prise trouvée.

## Règles

- **Un module ne parle jamais à un autre module.** Il déclare un *besoin*,
  que l'utilisateur branche dans Configuration → Liaisons. Un import direct
  d'un module vers un autre est refusé par les tests du socle.
- **Aucun identifiant dans le code.** Clés d'API, logins et jetons passent
  par `get_setting` / `set_setting`, avec `secret=True` pour ce qui ne doit
  pas se réafficher.
- **Le paramétrage se fait dans l'onglet du module**, pas dans un fichier.

## Pour aller plus loin

- [Créer un module](../docs/06-creer-un-module.md) — le tutoriel complet
- [Référence des contrats](../docs/07-reference-contrats.md) — tous les champs
- [Liaisons entre modules](../docs/09-liaisons-entre-modules.md) — besoins et branchements
