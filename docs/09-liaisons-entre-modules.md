[← Sommaire](README.md)

# 9. Liaisons entre modules

> **Statut : en place.** Les cinq liens sont passés par les liaisons, plus
> aucun module n'en importe un autre, et un test (`core/tests.py`) échoue si
> un import croisé réapparaît.

## Le problème

La règle du projet est « un module ne connaît que le socle ». Cinq liens la
violent aujourd'hui, par import direct d'un module dans un autre :

| De → vers | Ce qui est importé |
| --- | --- |
| `heure_demarrage` → `solcast` | `api.get_forecast()` |
| `heure_demarrage` → `tempo` | `get_colors_cached()`, `get_prices()` |
| `enphase` → `tempo` | `get_colors_cached()`, `get_prices()` |
| `solcast` → `enphase` | `cloud.get_production_curve_cached()`, `historique.points_du_jour()` |
| `solcast` → `heure_demarrage` | `calcul.dernier_resultat()` |

Trois conséquences :

1. **Un cycle** — `solcast → heure_demarrage → solcast`. Il ne tient que
   parce que les imports sont tardifs ; en imports de tête, l'application ne
   démarrerait pas.
2. **Un couplage au nom du module** — renommer `solcast` casse
   `heure_demarrage`, alors qu'il ne lui faut qu'*une prévision de
   production*, peu importe qui la fournit.
3. **Rien n'est paramétrable** — la source est écrite dans le code, l'utilisateur
   ne peut ni la voir ni la changer.

## Le principe

Le mécanisme existe déjà, à un endroit : dans l'éditeur de scénarios, on
choisit une **info** dans une liste, sans jamais nommer de fonction Python.
On généralise ce mécanisme aux échanges entre modules.

- un module **publie** des infos, typées ;
- un module **déclare ses besoins**, typés ;
- l'utilisateur **relie** un besoin à une info dans la page de configuration
  du module ;
- le socle résout la liaison au moment de l'appel.

Un module ne connaît donc jamais un autre module : il connaît ses propres
besoins. Le fournisseur, lui, ignore qui le consomme.

## `INFOS` — un champ `type`

Aujourd'hui une info renvoie une valeur simple. Les échanges entre modules
demandent des structures (une courbe, un jeu de tarifs). On ajoute un champ
`type`, **absent = `valeur`**, donc sans rien casser :

| `type` | Contenu | Visible dans l'éditeur de scénarios |
| --- | --- | --- |
| `valeur` | nombre, texte ou `None` | **oui** |
| `serie` | `[(datetime, float)]`, triée par date | non |
| `table` | `[{...}]` homogènes | non |
| `objet` | `dict` | non |

Les types structurés ne sont pas proposés dans l'éditeur de scénarios : une
condition « courbe > 5 » n'a pas de sens. Ils n'existent que pour les
liaisons entre modules.

`unite` accompagne obligatoirement `serie` (`"kW"`, `"°C"`…) : c'est ce qui
permet au socle de ne proposer que des sources compatibles.

```python
INFOS = [
    {"nom": "prevision_pv",
     "fonction": "fonctions.info.courbe_prevue",
     "description": "Prévision de production (pas 30 min)",
     "type": "serie", "unite": "kW"},
]
```

## `BESOINS` — ce qu'un module attend des autres

```python
BESOINS = [
    {"nom": "prevision_pv",
     "libelle": "Prévision de production solaire",
     "type": "serie", "unite": "kW",
     "obligatoire": True,
     "sans": "pas d'arbitrage solaire, repli sur les heures creuses"},
]
```

- `nom` est **local au module consommateur** : c'est lui qui nomme ce dont il
  a besoin, pas le fournisseur ;
- `sans` décrit ce que fait le module quand le besoin n'est pas branché. Ce
  champ n'est pas décoratif : l'écrire oblige à concevoir la dégradation, et
  il s'affiche dans la configuration pour expliquer ce qu'on perd.

## Résolution

La liaison est stockée comme un réglage du module consommateur :
`besoin_<nom>` = `"solcast.prevision_pv"` (module . info).

Côté module, un seul appel, qui ne lève jamais d'exception :

```python
from core.liaisons import lire_besoin

courbe, err = lire_besoin("heure_demarrage", "prevision_pv")
if err:
    return [], err          # « module Solaire désactivé », « besoin non branché »…
```

`lire_besoin` retourne `(valeur, None)` ou `(None, raison lisible)`. Les
raisons possibles : besoin non branché, module fournisseur désactivé ou
absent, info disparue, erreur pendant l'appel. Le module consommateur ne
voit qu'une chaîne à journaliser ou à afficher — jamais une trace Python.

**Liaison toujours explicite.** Aucun branchement automatique, même quand un
seul fournisseur conviendrait : ce qui est branché est visible dans la
configuration, et rien ne change dans le dos de l'utilisateur quand un
nouveau module est installé. En contrepartie, l'installation comporte une
étape de branchement, que la page de configuration doit rendre évidente
(besoin non branché = mention explicite de ce qu'on perd, reprise de `sans`).

## Configuration

Une section **Liaisons** dans la page de configuration du module : une ligne
par besoin, un menu déroulant listant les infos compatibles (même `type`,
même `unite`), l'état à droite.

```
Liaisons — Heure de démarrage

  Prévision de production solaire    [ Solaire — Prévision de production ▾ ]   ✔ branché
  Tarifs du jour                     [ — non branché —                   ▾ ]   ⚠ sans : arbitrage
                                                                                  jour/nuit désactivé
```

## Les cinq liens, une fois traduits

| Consommateur | Besoin | Type | Fournisseur | Pourquoi |
| --- | --- | --- | --- | --- |
| `heure_demarrage` | `prevision_pv` | `serie` kW | `solcast.prevision_pv` | Trouver le créneau où le surplus couvre les 2,4 kWh du ballon |
| `heure_demarrage` | `tarifs_jour` | `objet` | `tempo.tarifs_jour` | Comparer chauffe solaire de jour et heures creuses de nuit |
| `enphase` | `tarifs_jour` | `objet` | `tempo.tarifs_jour` | Chiffrer la journée mesurée en euros |
| `solcast` | `production_reelle` | `serie` kW | `enphase.production_reelle` | Superposer le réalisé au prévu |
| `solcast` | `creneau_chauffe` | `objet` | `heure_demarrage.creneau_retenu` | Surligner le créneau sur la courbe du jour |
| `chauffe_eau` | `heure_chauffe_prevue` | `valeur` | `heure_demarrage.heure_demarrage` | Passer à la relève à la minute autour de la chauffe attendue |

Le cycle disparaît : plus aucun module n'en importe un autre, tout passe par
le socle.

### Forme des objets échangés

C'est **la** partie qui compte : un `objet` sans forme documentée ne vaut pas
mieux qu'un import.

```python
# tarifs_jour — la couleur de la veille est nécessaire car les heures
# creuses vont de 22h à 6h : la nuit en cours relève du jour précédent.
{"couleur": "BLUE", "couleur_veille": "BLUE",
 "hp": 0.1609, "hc": 0.1296,          # €/kWh
 "hc_debut": "22:00", "hc_fin": "06:00"}

# creneau_retenu — l'heure effectivement retenue, forçage manuel compris.
{"heure": "13:30", "duree_min": 60, "mode": "solaire", "forcee": False}
```

Un fournisseur qui ne peut pas répondre renvoie `None` (Tempo sans couleur
connue, calcul jamais lancé) : c'est un cas normal, pas une erreur.

## Étapes

1. ✅ **Socle** — `core/liaisons.py` (résolution, erreurs lisibles), champ
   `type` sur `INFOS`, filtrage de l'éditeur de scénarios sur `type=valeur`.
2. ✅ **Configuration** — section Liaisons dans l'onglet Configuration, avec
   la liste des fournisseurs compatibles. *(Placée dans l'onglet du socle
   plutôt que dans la page de chaque module : elle fonctionne alors pour
   tous les modules sans qu'aucun n'ait à écrire d'interface, et l'ensemble
   des liaisons se lit d'un seul coup d'œil.)*
3. ✅ **Lien pilote** — `enphase → tempo`, avec une migration qui branche la
   liaison à l'identique pour ne rien perdre au passage.
4. ✅ **Migration** — les quatre autres liens, avec la migration `0012` qui
   les rebranche à l'identique.
5. ✅ **Garde-fou** — `core/tests.py` analyse l'arbre syntaxique de chaque
   fichier de `modules/` et échoue si l'un importe un autre module. Sans
   lui, la règle se reperdrait : un import direct est toujours plus rapide à
   écrire qu'un besoin déclaré. Lancer avec `python manage.py test core`.

> **Ce que le garde-fou ne voit pas.** Il détecte les imports, pas les
> couplages par nom : lire `get_variable("heure_demarrage_chauffe_eau")`
> depuis un autre module produit exactement la même fragilité sans qu'aucun
> import n'apparaisse. C'est ce qu'était le dernier lien traduit
> (`chauffe_eau → heure_demarrage`, migration `0013`). Règle à tenir à la
> main : **une variable globale n'est lue que par le module qui l'écrit** ;
> pour tout le reste, un besoin.

### Ce que la migration a fait perdre

Le calcul de l'heure de démarrage affichait un avertissement « prévisions
reprises des caches de la v1, pas d'appel réel ». Cette information est la
**provenance interne du fournisseur** : elle n'a plus de sens chez un
consommateur qui ignore d'où vient la courbe. L'avertissement a donc été
retiré du détail du calcul — l'onglet Solaire, lui, continue d'afficher
l'origine de ses données, à l'endroit où elle est vraie.

### Rebrancher une liaison existante

Un lien qui existait en dur devient *débranché par défaut* une fois traduit
en besoin. Chaque migration de lien s'accompagne donc d'une migration Django
qui pose le réglage `besoin_<nom>` à sa valeur d'origine, sans écraser un
choix déjà fait (`get_or_create`). Voir
`core/migrations/0011_liaison_enphase_tarifs.py`.

## Ce qui reste hors sujet

- **Pas de bus d'événements.** Les besoins sont lus quand le consommateur en
  a besoin, pas poussés. C'est suffisant ici et bien plus simple à déboguer.
- **Pas de cache dans le socle** au départ. Les infos lisent déjà des caches
  de module ; si un jour un rendu de page appelle dix fois le même besoin, on
  ajoutera un cache de quelques secondes dans `lire_besoin`.
