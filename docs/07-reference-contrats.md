[← Sommaire](README.md)

# 7. Référence des contrats

Tout ce qu'un module peut déclarer, et ce que le socle en fait.

## `conf.py`

| Constante | Type | Obligatoire | Rôle |
| --- | --- | --- | --- |
| `ONGLET` | `str` | **oui** | Libellé de l'onglet |
| `ICONE` | `str` | non | Nom Bootstrap Icons, sans `bi-` (défaut : `puzzle`) |
| `DESCRIPTION` | `str` | non | Affichée dans la liste des modules |
| `TACHES` | `list` | non | Tâches de fond |
| `SCENARIO` | `list` | non | Actions proposées dans l'éditeur |
| `INFOS` | `list` | non | Lectures proposées dans l'éditeur |
| `BESOINS` | `list` | non | Données attendues d'un autre module — voir [9. Liaisons](09-liaisons-entre-modules.md) |

## `TACHES` — tâches de fond

Deux périodicités possibles.

### Toutes les X minutes

```python
TACHES = [
    {"nom": "actualiser", "fonction": "fonctions.api.tache_actualiser", "minutes": 10},
]
```

- Surchargeable en base par le réglage `tache_<nom>_minutes` du module
- `0` = tâche désactivée
- Première exécution ~10 secondes après le démarrage

### À heures fixes

```python
TACHES = [
    {"nom": "previsions", "fonction": "fonctions.api.tache_actualiser",
     "heures": ["03:00", "07:00", "11:00", "15:00", "17:00"]},
]
```

- Accepte des heures entières (`7`) ou des horaires (`"07:30"`)
- Surchargeable par le réglage `tache_<nom>_heures` (`"3,7:30,11"`),
  **vide = désactivée**
- **Aucune exécution au démarrage** : le nombre de passages par jour est
  exactement celui de la liste — indispensable face à une API à quota
- Les horaires de minutes différentes donnent plusieurs jobs cron
  (`module.tache`, `module.tache.1`…)

Dans les deux cas, `fonction` est un **chemin relatif au répertoire du
module**, sans le préfixe `modules.<nom>.`.

Seules les erreurs sont journalisées, pas les exécutions réussies.

## `SCENARIO` — actions exposées

```python
SCENARIO = [
    {"nom": "allumer",
     "fonction": "fonctions.scenario.allumer",
     "description": "Allume l'appareil",
     "params": [
         {"nom": "mode", "label": "Mode", "options": [
             ["", "(inchangé)"], ["auto", "Auto"], ["heating", "Chauffage"]]},
     ]},
]
```

- `params` est optionnel : chaque entrée devient un champ dans l'éditeur, et
  sa valeur est passée à la fonction en **argument nommé**
- Une valeur vide n'est pas transmise, ce qui permet des « (inchangé) »
- La valeur de retour est journalisée : renvoyer une chaîne courte décrivant
  ce qui a été fait est une bonne pratique

Un paramètre qui déclare des `options` devient une **liste déroulante**. Sans
`options`, c'est une **saisie libre** — pour ce qu'aucune liste ne peut
énumérer, une date par exemple :

```python
{"nom": "retour", "label": "Retour", "type": "texte", "largeur": 200,
 "defaut": "+7j 18:00", "placeholder": "27/09/2026 18:00"},
```

| Champ | Rôle |
| --- | --- |
| `options` | `[[valeur, libellé], …]` — présent ⇒ liste déroulante |
| `type` | `texte` (défaut), `nombre`, `jour` (sélecteur de date), `heure`, `date` (sélecteur date-heure) |
| `defaut` | Valeur pré-remplie à la création de l'action |
| `placeholder` | Exemple affiché dans le champ vide, et en infobulle |
| `largeur` | Largeur maxi du champ, en pixels (180 par défaut) |

> Une saisie libre arrive toujours en **texte**. La fonction du module
> l'interprète elle-même et lève une exception claire si elle n'y arrive
> pas : le moteur l'écrit alors dans le Journal et arrête le scénario.

Pour un instant, préférer **deux paramètres** — un `jour` et une `heure` —
à un seul `date`. Un scénario rejoue : « aujourd'hui à 18 h » n'est
exprimable que si le jour peut rester vide, ce qu'un sélecteur date-heure
ne permet pas. L'action `absence` du module chauffe-eau en est l'exemple.

Pour des entrées dynamiques (une action par prise, par climatisation…),
construire la liste dans le module et l'exposer via une fonction :

```python
try:
    from modules.mon_module.fonctions.scenario import build_scenario_entries
    SCENARIO = build_scenario_entries()
except Exception:
    SCENARIO = []
```

Le `try/except` est important : un `conf.py` qui lève une exception rend le
module indétectable.

## `INFOS` — lectures exposées

```python
INFOS = [
    {"nom": "temperature",
     "fonction": "fonctions.info.temperature",
     "description": "Température mesurée (°C)"},
]
```

Une info est une fonction **sans argument** qui retourne une valeur simple
(nombre, texte, `None`). Elle est utilisable :

- en **condition**, avec les opérateurs `=`, `≠`, `<`, `≤`, `>`, `≥` ;
- en action **Info → variable** ;
- comme source d'un déclencheur **heure calculée** (doit alors renvoyer
  `HH:MM`) ou **au changement**.

Une info peut aussi porter un champ `type` (`valeur` par défaut, ou `serie`,
`table`, `objet`) et une `unite`. Seul le type `valeur` est proposé dans
l'éditeur de scénarios ; les types structurés servent aux liaisons entre
modules, décrites au chapitre [9](09-liaisons-entre-modules.md).

> Une info peut être lue **très souvent** — chaque minute pour un
> déclencheur, à chaque affichage de page pour un bloc. Elle doit donc être
> peu coûteuse : lire un cache, pas interroger une API à quota. Et elle doit
> être **stable** : une info qui recalcule à chaque appel rend les
> déclencheurs imprévisibles.

## Points d'entrée d'affichage

| Fichier | Fonction | Retour |
| --- | --- | --- |
| `onglet/views.py` | `onglet(request)` | Réponse Django (`render(...)`) |
| `dashboard/views.py` | `bloc(request)` | HTML du bloc, ou `""` |
| `dashboard/views.py` | `blocs(request)` | `[{"titre", "icone", "html"}]` |

Si les deux existent, `blocs` a la priorité.

## Services du socle — `core.services`

| Fonction | Signature | Rôle |
| --- | --- | --- |
| `journal` | `journal(message, module="core", level=LogEntry.INFO)` | Écrit dans le Journal |
| `get_setting` | `get_setting(key, module="core", default=None)` | Lit un réglage |
| `set_setting` | `set_setting(key, value, module="core", secret=False)` | Écrit un réglage |
| `get_variable` | `get_variable(name, default=None)` | Lit une variable globale |
| `set_variable` | `set_variable(name, value)` | Écrit une variable globale |
| `set_control_state` | `set_control_state(control, on)` | Bascule un switch en respectant son groupe exclusif |

Niveaux de journal : `LogEntry.INFO`, `LogEntry.WARNING`, `LogEntry.ERROR`.

## Réglages du socle

| Clé | Module | Rôle |
| --- | --- | --- |
| `dashboard_refresh_minutes` | `core` | Rafraîchissement auto du tableau de bord (0 = désactivé) |
| `rattrapage_min` | `scenarios` | Fenêtre de rattrapage des déclencheurs « heure calculée » (10 par défaut) |
| `declenche_<pk>` | `scenarios` | Date du dernier déclenchement d'un scénario horaire |
| `valeur_<pk>` | `scenarios` | Dernière valeur vue par un déclencheur « au changement » |
| `tache_<nom>_minutes` | *module* | Périodicité d'une tâche |
| `tache_<nom>_heures` | *module* | Horaires d'une tâche |

## Modèles de données

| Modèle | Rôle |
| --- | --- |
| `Module` | Un module détecté, activé ou non |
| `Setting` | Réglage clé/valeur, cloisonné par module |
| `Variable` | Valeur globale partagée |
| `Control` | Bouton poussoir ou switch, avec groupe exclusif |
| `Scenario` | Définition JSON : déclencheur, conditions, actions |
| `DashboardBlock` | Ordre, largeur et hauteur d'un bloc |
| `LogEntry` | Une ligne de journal |
