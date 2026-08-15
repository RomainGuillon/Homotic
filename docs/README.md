# Documentation Homotic

Tableau de bord domestique modulaire : mesures en direct, scénarios
d'automatisation, et optimisation des consommations selon la production
solaire et les tarifs EDF.

## Sommaire

| Document | Contenu |
| --- | --- |
| [1. Installation](01-installation.md) | Prérequis, mise en place, premier démarrage, mise à jour |
| [2. Prise en main](02-prise-en-main.md) | Onglet Configuration : modules, boutons, switchs, variables |
| [3. Tableau de bord](03-tableau-de-bord.md) | Blocs, mode Organiser, rafraîchissement |
| [4. Scénarios](04-scenarios.md) | Déclencheurs, conditions, actions, blocs Si et boucles |
| [5. Modules livrés](05-modules-livres.md) | Énergie, Solaire, Tempo, Chauffe-eau, Clim, Capteurs, Caméras, Alarme, Heure de démarrage |
| [6. Créer un module](06-creer-un-module.md) | Structure, contrats, association au socle, exemple complet |
| [7. Référence des contrats](07-reference-contrats.md) | `conf.py`, services du socle, formats attendus |
| [8. Dépannage](08-depannage.md) | Le scheduler ne tourne pas, quotas d'API, pièges connus |
| [9. Liaisons entre modules](09-liaisons-entre-modules.md) | Infos typées, besoins déclarés, branchement dans la configuration |
| [10. Mise en ligne](10-mise-en-ligne.md) | Accès depuis Internet : authentification, HTTPS, Ionos, Freebox, systemd |
| [11. Git et déploiement](11-git-et-deploiement.md) | Dépôt, clé de déploiement, script de mise à jour du serveur |

## Vue d'ensemble

Homotic est un projet Django composé de deux parties :

- le **socle** (`core/`) — les trois onglets permanents (Tableau de bord,
  Journal, Configuration), la base de données, le moteur de scénarios et le
  scheduler ;
- les **modules** (`modules/`) — un répertoire par domaine (chauffe-eau,
  climatisation, production solaire…). Chaque module est autonome : il parle
  à ses API, fournit son onglet, son bloc de tableau de bord, et déclare ce
  qu'il expose aux scénarios.

Ajouter une capacité à l'application se fait donc en ajoutant un répertoire
dans `modules/`, sans toucher au socle.

```
Homotic/
├── core/          socle : onglets permanents, scénarios, scheduler, base
├── modules/       un répertoire par module
├── homotic/       configuration Django (settings, urls)
├── docs/          cette documentation
├── db.sqlite3     base de données
└── manage.py
```

## Captures d'écran

Les images sont dans `docs/images/`. Voir
[images/README.md](images/README.md) pour la convention de nommage et la
liste des captures attendues.
