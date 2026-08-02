[← Sommaire](README.md)

> **Un module en panne ne doit jamais emporter l'application.** Un onglet qui
> lève une exception affiche une page d'erreur isolée (le détail va au
> Journal), un bloc de tableau de bord en erreur est simplement omis, un
> module désactivé ou supprimé du disque est ignoré, et une liaison vers un
> module éteint renvoie une raison lisible au lieu d'une trace. Ces
> garanties sont vérifiées par `python manage.py test core`.

# 8. Dépannage

## Aucun scénario ne se déclenche, aucune donnée ne se rafraîchit

**Le scheduler ne tourne pas.** C'est la panne la plus fréquente et la plus
discrète : l'application s'affiche normalement, les pages calculent leurs
valeurs à la volée, et rien ne trahit le problème.

Vérifier dans l'ordre :

1. **Configuration → carte Scheduler** : elle affiche « à l'arrêt » et
   l'erreur de démarrage.
2. **Journal** : chercher « Scheduler ». Une ligne « Scheduler démarré :
   N tâche(s), M scénario(s) » doit apparaître à chaque lancement. Son
   absence confirme le diagnostic.

Causes possibles :

| Cause | Correction |
| --- | --- |
| APScheduler absent de l'environnement | `pip install -r requirements.txt` |
| Lancé autrement que par `runserver` | Le scheduler ne démarre qu'avec `runserver`, c'est voulu |
| Erreur au démarrage d'une tâche | Le message est dans le Journal et sur la carte Scheduler |

## Un scénario « heure calculée » ne part jamais

Trois causes, par ordre de fréquence :

1. **La source recalcule à chaque lecture.** Si le déclencheur lit une info
   qui refait son calcul, l'heure cible peut se déplacer au fil de la
   journée et n'être jamais atteinte. Brancher le déclencheur sur une
   **variable**, alimentée quand on le décide.
2. **L'heure visée est déjà passée** au moment où le scénario est créé ou
   le serveur redémarré. Le rattrapage ne couvre que 10 minutes ; au-delà,
   le déclenchement est reporté au lendemain.
3. **Déjà déclenché aujourd'hui** : un seul lancement par jour, même si
   l'heure change ensuite.

Le bouton ▶ permet de vérifier que les conditions et les actions sont
correctes, indépendamment du déclencheur.

## Erreur 429 sur Solcast

Le quota gratuit est de **10 appels par jour pour le compte**, et chaque
requête consomme **un appel par site**.

- L'onglet Solaire affiche les appels restants et l'heure de reprise.
- Après un 429, les appels sont **suspendus jusqu'au lendemain 6h** : c'est
  volontaire, réessayer ne ferait que multiplier les refus.
- Vérifier qu'aucun **autre client n'utilise la même clé**. La v1 partageait
  la clé et consommait le même quota ; ses appels ont été désactivés
  (`APPELS_API_AUTORISES = False` dans `solcast/forecast.py` de la v1) et
  ses tâches Windows `update_heater_schedule` supprimées.
- Réduire le nombre d'horaires dans le paramétrage de l'onglet : le coût
  s'affiche en direct sous la liste.

## Le compteur d'appels semble faux

Il ne voit que les appels passés par la v2. S'il annonce des appels
restants alors que Solcast refuse déjà, c'est qu'un autre client a consommé
le quota. Dès qu'un 429 arrive, le compteur s'aligne sur cette réalité et
affiche 0 restant.

## Une courbe s'arrête en milieu de journée

La courbe « réel » du module Solaire et le diagramme du module Énergie sont
alimentés par l'**historique local de l'Envoy**, un point toutes les
5 minutes.

- Si le scheduler ne tourne pas, l'historique n'avance que lorsqu'une page
  est affichée.
- L'historique est **remis à zéro chaque jour** : une courbe vide en début
  de matinée est normale.

## Un module n'apparaît pas

| Symptôme | Cause probable |
| --- | --- |
| Absent de la liste des modules | Pas de `conf.py`, ou répertoire commençant par `_` ou `.` |
| Listé avec un message d'erreur | `conf.py` invalide — le message donne l'exception |
| Coché mais pas d'onglet | Le serveur n'a pas redémarré : relancer `runserver` |
| Onglet présent, pas de bloc | Pas de `dashboard/views.py`, ou erreur de rendu (voir le Journal) |

## Un bloc du tableau de bord a disparu

Une exception dans un bloc est attrapée par le socle : le bloc est ignoré et
l'erreur écrite dans le Journal, filtrée sur le module concerné. Le reste du
tableau de bord continue de s'afficher.

## Du texte étrange apparaît dans une page

Du style `{# … #}` visible à l'écran : un commentaire Django multiligne.
La syntaxe `{# … #}` ne vaut que sur **une seule ligne** ; pour un
commentaire sur plusieurs lignes, utiliser
`{% comment %} … {% endcomment %}`.

## Une modification de `conf.py` reste sans effet

Les tâches et leurs périodicités sont lues **au démarrage du scheduler**.
Redémarrer le serveur. Les catalogues d'actions et d'infos, eux, sont relus
à chaque ouverture de l'éditeur de scénarios.

## Réinitialiser la disposition du tableau de bord

Mode **Organiser** → bouton **Par défaut**. Cela efface uniquement l'ordre,
les largeurs et les hauteurs des blocs.

## Repartir d'une base vierge

Arrêter le serveur, renommer `db.sqlite3`, puis :

```bat
python manage.py migrate
python manage.py runserver 0.0.0.0:8100
```

Tout est à reconfigurer : modules, clés d'API, contrôles, scénarios.
Conserver l'ancien fichier permet d'y revenir.
