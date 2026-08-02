# Homotic

Tableau de bord domestique modulaire : mesures en direct, scénarios
d'automatisation, et pilotage des consommations selon la production solaire
et les tarifs EDF. Projet Django, base SQLite `db.sqlite3`, port 8100.

## Lancement

```bat
cd C:\Dev\Homotic
.venv\Scripts\activate
python manage.py runserver 0.0.0.0:8100
```

Puis ouvrir http://localhost:8100/

## Interface

Thème reprenant l'identité de la v1 (`core/static/core/theme.css` : palette
ardoise, accents désaturés, cartes très arrondies, halos diffus) appliqué
par-dessus Bootstrap.

Le tableau de bord est organisable : bouton **Organiser** → glisser-déposer
pour l'ordre, sélecteur de largeur par bloc (un tiers / moitié / deux tiers /
pleine largeur), enregistrement en base (`DashboardBlock`), bouton
« Par défaut » pour revenir à la disposition d'origine.

## Structure

- `homotic/` — configuration du projet Django (settings, urls)
- `core/` — le socle : 3 onglets (Tableau de bord, Journal, Configuration),
  table de logs unique (colonne `module`), configuration clé/valeur
- `modules/` — les modules "plugins" (voir `modules/README.md`)

## Helpers pour le code des modules

```python
from core.services import journal, get_setting, set_setting, get_variable, set_variable

journal("Chauffe-eau démarré", module="chauffe_eau")
set_setting("api_key", "xxx", module="enphase", secret=True)
get_setting("api_key", module="enphase")
```

## Feuille de route

1. ✅ Socle (onglets, base, journal, configuration clé/valeur)
2. ✅ Boutons poussoirs & switchs + bloc Scénarios sur le tableau de bord
3. ✅ Contrat de module + détection dans `modules/` + activation
4. ✅ Premier module réel (Tempo) + contrat dashboard (`dashboard/views.py` → `bloc(request)`)
5. ✅ Scheduler (APScheduler) : contrat `TACHES` dans le `conf.py` des modules,
   périodicité surchargeable en base (`tache_<nom>_minutes`, 0 = désactivée)
6. ✅ Éditeur de scénarios par blocs : déclencheurs (heure fixe, bouton, switch,
   manuel), conditions (état switch, plage horaire), actions (fonction module,
   régler switch, lancer scénario, message journal) — mode avancé Python
   restreint à venir
6b. ✅ Extensions éditeur : déclencheur « heure calculée » (heure lue chaque
   minute dans une variable ou une info de module, ex. heure_demarrage),
   variables globales (conditions avec opérateurs,
   action d'affectation), déclencheur périodique (toutes les X min, fenêtre
   horaire optionnelle), plage horaire « dans / hors », bloc Si/Alors/Sinon,
   boucles Tant que / Jusqu'à ce que (intervalle + durée max + conditions de
   sortie anticipée), blocs Si/Boucle imbricables sur 3 niveaux, contrat INFOS
   (fonctions de lecture par module, utilisables en condition avec opérateurs
   et en action Info→variable), catalogues regroupés par module
7. ✅ Modules portés : chauffe_eau (Cozytouch), tuya (capteurs + prises),
   clim (Hi-Kumo), enphase (Envoy, flux animé, publie enphase_*_w),
   solcast (prévisions, meilleur créneau chauffe-eau, publie solcast_prevu_*),
   heure_demarrage (module de calcul sans bloc dashboard : meilleure heure de
   démarrage du chauffe-eau, arbitrage coût jour solaire / nuit HC, switchs
   exclusifs Été/Hiver) — présentation identique à la v1 (bloc dashboard + onglet).
   Enphase inclut la partie cloud Enlighten v4 : cumuls du jour précis,
   coût EDF par tranche (Tempo × HP/HC), courbes production/consommation
   15 min, bloc « La journée » sur le dashboard, ligne « réel » de la
   courbe Solcast
8. IA / optimisation (chauffe-eau vs production solaire vs Tempo)
