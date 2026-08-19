# NOTICE

**Homotic** — plateforme domotique modulaire.

**Copyright (c) 2026 Romain Guillon.**
Distribué sous [licence MIT](./LICENSE).

---

## Ce que la licence vous permet

Homotic est un vrai logiciel libre. Vous pouvez :

- l'utiliser chez vous, y compris pour un usage professionnel ou commercial ;
- le modifier, écrire vos propres modules, l'adapter à vos équipements ;
- le redistribuer, le forker, l'intégrer dans un autre projet ;
- en vendre des services ou des adaptations.

**Une seule obligation :** conserver la mention de copyright et le texte de la
licence dans les copies et les travaux dérivés. C'est la contrepartie de la
liberté que vous accorde MIT, et la seule.

Les en-têtes présents en tête de chaque fichier source portent précisément
cette mention. Merci de ne pas les retirer — c'est ce qui permet à quelqu'un
qui tombe sur un fragment du code de savoir d'où il vient.

## Accompagnement et prestations

Le logiciel est libre ; le temps ne l'est pas. Je peux intervenir sur :

- **Développement de modules sur mesure** — intégration d'un équipement, d'une
  API ou d'un protocole non couvert par les modules livrés.
- **Installation et mise en production** — déploiement, HTTPS, systemd,
  supervision, sauvegarde.
- **Adaptation en contexte professionnel** — supervision multi-sites, pilotage
  énergétique, tableaux de bord métier.
- **Formation et transfert de compétences** — prise en main du contrat de
  module et de l'éditeur de scénarios par vos équipes.

<!-- À COMPLÉTER : URL de votre profil -->
**Contact — LinkedIn :** http://www.linkedin.com/in/romain-guillon-data

**GitHub :** https://github.com/RomainGuillon

Vous pouvez aussi ouvrir une
[issue](https://github.com/RomainGuillon/Homotic/issues) : questions d'usage,
signalements de bogue et propositions de modules y sont les bienvenus.

## Contributions

Les contributions sont acceptées. En proposant une pull request, vous acceptez
que votre contribution soit distribuée sous la même licence MIT que le reste
du projet.

Avant d'écrire un module, lisez
[`docs/06-creer-un-module.md`](docs/06-creer-un-module.md) et
[`docs/07-reference-contrats.md`](docs/07-reference-contrats.md) : le noyau
ignore délibérément tout des équipements, et un module qui respecte le contrat
se branche sans toucher au cœur.

## Composants tiers

Les dépendances externes restent régies par leurs licences respectives,
listées dans [`requirements.txt`](./requirements.txt) — Django, APScheduler et
Bootstrap au premier chef. La licence MIT de ce dépôt ne s'applique qu'aux
éléments originaux créés par l'auteur.

Les marques citées (Arlo, Verisure, Tempo et autres) appartiennent à leurs
détenteurs respectifs. Les modules correspondants sont des intégrations
indépendantes, sans affiliation ni approbation de ces sociétés.

## Sécurité

Homotic pilote des équipements réels : caméras, alarme, chauffe-eau,
climatisation. Avant toute exposition sur Internet, lisez
[`docs/10-mise-en-ligne.md`](docs/10-mise-en-ligne.md).

`HOMOTIC_SECRET_KEY` doit impérativement être fournie par l'environnement en
production — le démarrage est bloqué sans elle, précisément pour éviter qu'une
clé publiquement connue ne se retrouve en ligne.

Si vous découvrez une faille, ne l'ouvrez pas en issue publique : contactez
l'auteur directement.
