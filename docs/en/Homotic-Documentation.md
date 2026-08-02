# Installation

## Requirements

- **Python 3.10 or later** (the project runs on 3.14)
- **Windows** on the current machine, though nothing is Windows-specific apart from the commands below
- Local network access to the equipment (Enphase gateway, Tuya plugs) and Internet access for the APIs (Solcast, RTE Tempo, Cozytouch, Hi-Kumo)

## Setting up

```
cd C:\Dev\Homotic
..\.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
```

`requirements.txt` installs:

| Package | Purpose |
| --- | --- |
| `django` | the web framework |
| `requests` | HTTP calls to the APIs |
| `apscheduler` | **the scheduler**: periodic tasks and time-based scenarios |
| `pyoverkiz` | Cozytouch water heater and Hi-Kumo air conditioners |

> **APScheduler is not optional.** Without it the application starts and displays normally, but **no scenario fires and no data refreshes in the background**. It is the quietest failure in the project: see *Troubleshooting*.

## Starting the application

```
cd C:\Dev\Homotic
..\.venv\Scripts\activate
python manage.py runserver 0.0.0.0:8100
```

Then open <http://localhost:8100/>.

Listening on `0.0.0.0` makes the application reachable from other devices on the local network (phone, tablet) at `http://<pc-ip>:8100/`.

Port 8100 lets it coexist with v1, which uses the default port.

![Home screen on first start](images/01-premier-demarrage.png)

## What happens at start-up

1. Django loads the `core` framework **and the enabled modules** — modules ticked in the Configuration tab become full Django apps (see `homotic/settings.py`).
2. The **scheduler starts** and registers the periodic tasks declared by the active modules, and the scenarios triggered by time, computed time, interval or value change.
3. A line "Scheduler démarré: N task(s), M scenario(s)" is written to the **Log**. Its absence means it did not start.

The scheduler only starts with `runserver`: `migrate`, `shell` and `makemigrations` do not launch it, by design.

## Updating the project

After pulling a new version of the code:

```
..\.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8100
```

`migrate` is required whenever a data model changes (a new block field, a new setting). When in doubt, running it costs nothing: it does nothing if there is nothing to do.

## Backups

The entire state of the application lives in **`db.sqlite3`**: settings, API keys, controls, scenarios, variables, dashboard layout, log. Copying that file is a complete backup.

API keys are stored in the database and masked in the interface, but **not encrypted**: the database file deserves the same care as a password file.

# Getting started — the Configuration tab

Everything is set up from the **Configuration** tab, organised into five sections: buttons & switches, modules, global variables, scheduler, scenarios.

![Configuration tab](images/02-configuration.png)

## Enabling modules

The **Modules** section lists what is present in the `modules/` directory. Tick a module then click **Valider**:

1. the module is registered in the database;
2. the server restarts automatically;
3. its tab appears in the navigation bar, and its block on the dashboard if it provides one.

![Detected modules](images/02-modules.png)

Unticking and validating removes the tab and the block **without deleting the module's settings**: ticking it again later restores the configuration.

A module with an invalid `conf.py` stays listed with its error message rather than preventing the whole application from starting.

## Push buttons and switches

These are the controls shown in the **Scénarios** block of the dashboard, and the simplest triggers for a scenario.

| Type | Behaviour | Typical use |
| --- | --- | --- |
| **Push button** | a single impulse, no state kept | "Start heating now" |
| **Switch** | stays ON or OFF | "Many showers" mode, "Summer / Winter" |

A switch can belong to an **exclusive group**: turning one on automatically turns off the others in the group. That is what links the "Été" and "Hiver" switches of the Start time module.

![Creating a button or a switch](images/02-controles.png)

> Beware of duplicates: a control's internal name is what scenarios and modules read. Two switches both displaying "Hiver" but named `hiver` and `Hiver` are two different objects, and only one will be read.

## Global variables

A variable is a named value shared by every module and every scenario. It can be **tested in a condition**, **changed by an action**, and edited by hand from this page.

![Global variables](images/02-variables.png)

They serve three purposes:

- **publishing a measurement** for scenarios — modules feed `enphase_production_w`, `solcast_prevu_aujourdhui_kwh`;
- **remembering a state** between runs — `Clim_allumer`, `Chauffe_Eau_Plein`;
- **holding a setpoint that can be changed without touching the code** — `heure_demarrage_chauffe_eau`.

The ✓ button to the right of a variable saves the value typed. That is how you force a value by hand, for example correcting a computed start time.

## Scheduler

The **Scheduler** card is the diagnostic view of background execution:

- **running / stopped**, with the start-up error if any;
- the list of registered tasks and scenarios, **in order of execution**, with their next run.

![Scheduler card](images/02-scheduler.png)

The names shown are those of the scenarios and modules; the technical identifier (`scenario.4`, `solcast.previsions`) is recalled underneath for troubleshooting.

If this card says stopped, **nothing runs automatically**: start there before investigating why a scenario does not fire.

## Log

The **Journal** tab records everything the application does: scenario runs, API errors, setting changes. It can be filtered by module and by level (info, warning, error).

![Log](images/02-journal.png)

It is the first place to look when a behaviour is surprising: scenarios write the origin of their trigger there, and on failure, the condition that was not met.

# The dashboard

![Dashboard](images/03-tableau-de-bord.png)

The dashboard assembles:

- the **Scénarios** block — the push buttons and switches created in Configuration;
- one or more blocks **per active module**: each module decides what it displays.

A module may provide several blocks (the Energy module offers two: "Énergie maintenant" and "La journée").

## Action bar

The dashboard commands sit in the navigation bar, to the right of the tabs:

| Command | Effect |
| --- | --- |
| ⟳ | Reloads the page immediately |
| `auto N min` | Automatic refresh, with a countdown |
| ✥ | Switches to **Organise** mode |

The automatic refresh is a **setting stored in the database**, so it behaves the same from the PC and the phone. The countdown resets when the tab goes to the background, so the page does not reload the moment you come back to it.

> The Refresh button **reloads the page**; it does not re-run any calculation and does not trigger any quota-bound API call. It refreshes local measurements (Enphase, Tuya, water heater). The water-heater start time and the solar forecast do not move — see *Scenarios* and *Bundled modules*.

## Organise mode

![Organise mode](images/03-organiser.png)

In Organise mode, each block can be:

- **moved** by drag and drop, to change the order;
- **resized in width** with the selector: a quarter, a third, a half, two thirds, full width;
- **resized in height** with the handle at the bottom of the block, or by typing a value in pixels.

The ⤡ button next to the field sets the height back to **automatic**.

Three points about height:

- **Automatic (0)**: the block takes the height of its content, and lines up with the tallest block on its row.
- **Fixed height**: the block is exactly that size. Taller than its content, it creates space and lets you align a row; shorter, its content scrolls inside.
- A block with a fixed height **is no longer stretched by the tallest block on its row**: that is what lets you take back control of an unbalanced row.

Automatic refresh is **suspended** in Organise mode: a reload in the middle of a drag would lose the layout.

**Enregistrer** saves the layout, **Annuler** leaves without changes, **Par défaut** clears the custom layout and returns to the original placement.

## Blocks and modules

A failing block does not prevent the dashboard from displaying: the framework catches the exception, writes the line to the Log and moves on to the next block. A block missing while its module is active is therefore often a rendering error to look for in the Log.

# Scenarios

A scenario is three things:

```
TRIGGER      →  CONDITIONS  →  ACTIONS
when?           if?             do what?
```

It is created from **Configuration → Nouveau scénario**.

![Scenario editor](images/04-editeur.png)

## Triggers — when should the scenario run?

| Trigger | When | Notes |
| --- | --- | --- |
| **Manual** | ▶ Test button, or called by another scenario | The only one that never fires on its own |
| **Every day at a fixed time** | At the time given | Cron job |
| **Every day at a computed time** | At the time read from a variable or a module info | One run per day only |
| **On a value change** | When a variable or an info changes | Adjustable polling, optional filter on the resulting value |
| **Every X minutes** | Periodic, with an optional time window | Acts as "while" / "until" |
| **Button press** | Immediate | |
| **Switch toggle** | When the switch goes ON or OFF | |

### Computed time

The time is **re-read at every check** from a global variable or a module info. It may therefore change during the day, but the scenario fires **only once per day**.

A **10-minute catch-up** is built in: if the exact minute is missed (busy server, restart, momentarily unreadable source), the trigger still fires within the following ten minutes. Without it, a single missed minute cancelled the day's heating. The delay is set by the `rattrapage_min` setting of the `scenarios` module.

> **Pitfall**: pointing this trigger at an info that **recomputes** on every read gives a moving target. The water-heater start time calculation only keeps **future** slots: at 12:30 the 12:30 slot is no longer a candidate and the time retreats ahead of the clock. Point the trigger at a **variable**, fed when you decide.

### On a value change

Compares the current value to the previous one, stored in the database — so monitoring survives a restart. Three safeguards:

- an **unreadable source** fires nothing and does not overwrite the reference: an unreachable API must not look like a change;
- the **first reading** becomes the reference without firing, otherwise every server start would run the scenario;
- if you edit the scenario to watch something else, the reference starts afresh instead of comparing two unrelated values.

The "only if the value becomes" field restricts firing to a specific arrival value (for example the Tempo colour turning `rouge`).

The check interval is adjustable, because a module info may query a device or an API.

## Conditions — under what reservations?

Four types: **switch state**, **time range** (inside / outside), **variable** (with operators), **module info** (with operators).

No condition means the scenario always runs.

### AND / OR

From the second condition onwards, an **ET / OU** selector links each line to the previous one. **AND binds tighter than OR**, as in boolean algebra:

```
A AND B OR C AND D    reads as    (A AND B) OR (C AND D)
```

![Conditions with AND and OR](images/04-conditions.png)

When no branch is satisfied, the Log details why each one failed, instead of giving a single reason.

## Actions — what to do?

| Action | Effect |
| --- | --- |
| **Module function** | Calls a function exposed by a module (with parameters if it declares any) |
| **Set a switch** | Sets a switch to ON or OFF |
| **Run a scenario** | Chains to another scenario (3 levels maximum) |
| **Log message** | Records a step |
| **Set a variable** | Assigns a value |
| **Info → variable** | Stores the result of a module info into a variable |
| **If / Then / Else block** | Conditional branch |
| **While / Until loop** | Repetition with interval, maximum duration and early exits |

Actions run **in order**, and stop at the first error. If blocks and loops nest **3 levels** deep.

> A "set a switch" action **does not re-trigger** the scenarios of that switch: this is deliberate, to avoid accidental loops. To chain, use "run a scenario" explicitly.

## Reordering lines

Every condition and every action has **↑ ↓** arrows to move it within its block. Movement stays confined: an action in a *Then* cannot jump into the *Else*.

## Testing

The ▶ button in the scenario list runs the scenario immediately, **conditions included**. The Log shows the result, and for a failure, the condition that blocked it.

## Full example — water heater at the best moment

Two complementary scenarios:

**1. Compute the time** (named `HeureDemarage`)

- Trigger: every day at `03:00` — just after the 3 a.m. Solcast refresh
- Action: *Module function* → Heure démarrage → `recalculer`

`recalculer` writes the `heure_demarrage_chauffe_eau` variable and logs the details of the calculation (slot chosen, day/night cost comparison).

**2. Start heating** (named `Chauffe_eau_ON`)

- Trigger: *computed time* → **a global variable** → `heure_demarrage_chauffe_eau`
- Condition (optional but recommended): info `calcul_du_jour` = `oui`, so heating does not start on a time computed the day before
- Action: *Module function* → Chauffe-eau → `chauffer`

Going further, a third scenario on the **change** of `solcast_prevu_aujourdhui_kwh` can re-run `recalculer` when the day's forecast moves significantly.

# Bundled modules

Each module is configured in its own tab, "Paramétrage" section.

| Module | Tab | What it provides |
| --- | --- | --- |
| `chauffe_eau` | Chauffe-eau | Atlantic tank via Cozytouch: state, remaining showers, forced heating |
| `clim` | Climatisation | Hitachi Hi-Kumo air conditioners: on/off, mode, setpoint |
| `enphase` | Énergie | Local Envoy gateway: production, consumption, grid, daily chart |
| `solcast` | Solaire | Production forecast, best heating slot |
| `tempo` | Tempo | EDF Tempo day colours and tariffs |
| `tuya` | Capteurs | Tuya sensors and plugs |
| `heure_demarrage` | Heure démarrage | Computes the best time to heat the tank |

## Energy (Enphase)

Queries the **Envoy gateway locally** — no quota, no cloud.

![Energy tab](images/05-energie.png)

The dashboard block combines the flow diagram, the instantaneous power figures and the **daily chart**:

- **blue**: production, above the axis
- **orange**: consumption, below the axis
- **grey**: grid, in the background — above if imported, below if exported

The axis shows **no sign**: an orange bar pointing down is still a consumption of 1.8 kW, not "−1.8 kW".

This chart is built from a **local history**: every reading of the Envoy records a point (production, consumption, grid) in 5-minute slots, reset each night. No external call, no quota. It therefore fills up during the day, and is only complete if the scheduler is running.

Publishes the variables `enphase_production_w`, `enphase_conso_w`, `enphase_import_w`, `enphase_export_w`.

## Solar (Solcast)

Photovoltaic production forecast, and the best slot to heat the tank.

![Solar tab](images/05-solaire.png)

### The quota, to understand first

The free Solcast plan grants **10 calls per day for the account**, and each request costs **one call per site**. With two roof planes, one refresh therefore costs **2 calls** — at most 5 refreshes a day.

Three cumulative safeguards:

1. **Page views never call the API.** Only the scheduled refresh and the Refresh button may. Otherwise the number of calls would depend on how often you look at the dashboard.
2. **A daily counter** with a ceiling (setting `quota_jour`, 10 by default), reset every day, and **aligned with the truth** as soon as a 429 arrives: Solcast counted 10 calls, so we believe it rather than our local tally.
3. **A backoff**: a network error suspends calls for 30 minutes, a 429 suspends them until 6 a.m. the next day. Without it, a failure writing no cache meant every page view launched another call — hence bursts of dozens of refused calls.

The tab permanently shows the **remaining calls**, the refreshes still affordable, the next run and what is planned before the evening.

### Choosing the times

The Paramétrage section lets you **add and remove** refreshes one by one. Minutes are free (`07:30` is valid). The cost is displayed live below the list, in orange if the total exceeds the quota.

No times at all means no automatic call; only the Refresh button acts. Taken into account at the **next server start**.

## Start time

A calculation module with no equipment of its own: it combines the solar forecast, the baseline consumption of the house and the Tempo tariffs to propose the best moment to heat the tank.

![Start time tab](images/05-heure-demarrage.png)

**The calculation is never automatic.** It only happens on demand: the `recalculer` scenario action, or the Recalculer button on the tab. Its result is stored, and it is that snapshot which the dashboard, the module infos and the triggers read.

This is deliberate: the calculation only keeps **future** slots, so an info that recomputed on every read produced a time that retreated ahead of the clock, one the trigger never caught.

**The `heure_demarrage_chauffe_eau` variable is authoritative.** It is written by `recalculer`, editable by hand in Configuration, and takes precedence over the stored calculation: if the two differ, the interface shows the forced time with a note saying so.

Season: the **Hiver** switch decides. If it is off — whether "Été" is on or both are off — it is **summer**, hence the short heating duration. No automatic switching by date.

Infos useful in conditions: `heure_demarrage`, `mode_retenu`, `calcul_du_jour` (is the last calculation from today?), `heure_calcul`, `gain_estime_eur`, `surplus_creneau_kwh`.

## Tempo

EDF Tempo day colours through the RTE API, peak/off-peak tariffs by colour, season counters. Feeds the cost arbitration of the Start time module.

## Water heater, Air conditioning, Sensors

Equipment modules: they expose their state as **infos** and their commands as **scenario actions**. Configuration (Cozytouch, Hi-Kumo, Tuya credentials) is done in the module's tab.

The Atlantic water heater has no direct "heat" command: forcing a full heating cycle amounts to setting the **number of desired showers to the maximum**, and returning to normal operation to setting it back to the minimum. That is what the `chauffer` and `eteindre` functions do.

# Creating a module

A module is a directory under `modules/`. The framework knows nothing about its contents: it reads its `conf.py` and calls the entry points the module declares. Adding a capability to the application therefore requires **no change to the framework**.

## Structure

```
modules/mon_module/
├── __init__.py
├── conf.py                     manifest — the only mandatory file
├── fonctions/
│   ├── __init__.py
│   ├── api.py                  talks to the device or the API
│   ├── info.py                 readings exposed to scenarios (INFOS)
│   ├── scenario.py             actions exposed to scenarios (SCENARIO)
│   └── affichage.py            chart building (optional)
├── onglet/
│   ├── __init__.py
│   └── views.py                onglet(request) function
├── dashboard/
│   ├── __init__.py
│   └── views.py                bloc(request) or blocs(request) function
└── templates/mon_module/
    ├── onglet.html
    └── _bloc.html
```

Only `conf.py` is mandatory. A module may have only a tab, or only a dashboard block, or neither (the Start time module is essentially a calculation module).

The simplest approach is to **copy `modules/exemple/`** and rename it.

## 1. The manifest — `conf.py`

```
"""Manifest of My Module."""

ONGLET = "Mon module"          # mandatory: tab name
ICONE = "thermometer-half"     # Bootstrap Icons name
DESCRIPTION = "What this module does, in one sentence."

# Background tasks, run by the framework scheduler
TACHES = [
    {"nom": "actualiser", "fonction": "fonctions.api.tache_actualiser", "minutes": 10},
]

# Actions offered in the scenario editor
SCENARIO = [
    {"nom": "allumer", "fonction": "fonctions.scenario.allumer",
     "description": "Turns the device on"},
]

# Readings offered in conditions and in "Info → variable"
INFOS = [
    {"nom": "temperature", "fonction": "fonctions.info.temperature",
     "description": "Measured temperature (°C)"},
]
```

`ONGLET` is used as the tab label; `ICONE` must be a [Bootstrap Icons](https://icons.getbootstrap.com) name **without the `bi-` prefix**.

Each contract is detailed in *Contract reference*.

## 2. The tab — `onglet/views.py`

The framework calls the `onglet(request)` function when the tab is clicked. It is an ordinary Django view.

```
from django.shortcuts import render

from core.services import get_setting, journal

from ..fonctions import api


def onglet(request):
    if request.method == "POST" and request.POST.get("action") == "params":
        # save the configuration entered
        ...

    return render(request, "mon_module/onglet.html", {
        "active_tab": "module:mon_module",   # highlights the tab
        "mesure": api.lire(),
    })
```

`active_tab` must be `"module:<directory_name>"` for the tab to appear active in the navigation bar.

## 3. The dashboard block — `dashboard/views.py`

Two possible contracts:

```
from django.template.loader import render_to_string

from ..fonctions import api


def bloc(request):
    """A single block: returns HTML (or an empty string to show no block)."""
    return render_to_string("mon_module/_bloc.html", {"mesure": api.lire()})
```

```
def blocs(request):
    """Several blocks: a list of dictionaries."""
    return [
        {"titre": "Live view", "icone": "speedometer", "html": "<p>…</p>"},
        {"titre": "The day", "icone": "calendar", "html": "<p>…</p>"},
    ]
```

The framework already wraps the block in a card with its title: the module only supplies the content.

An exception raised in a block does not prevent the dashboard from displaying: it is logged and the block is skipped.

## 4. Framework services

```
from core.services import (
    journal, get_setting, set_setting,
    get_variable, set_variable, set_control_state,
)

journal("Heating started", module="mon_module")
set_setting("api_key", "xxx", module="mon_module", secret=True)
get_setting("api_key", module="mon_module", default="")
set_variable("mon_module_temperature", "21.5")
```

- **Settings** (`get_setting` / `set_setting`): the module's configuration, partitioned by `module=`. `secret=True` masks the value in the interface.
- **Variables** (`get_variable` / `set_variable`): public values shared with scenarios and other modules.
- **Log** (`journal`): level `INFO` by default, `LogEntry.WARNING` or `LogEntry.ERROR` otherwise.

## 5. Attaching the module to the framework

1. Place the directory in `modules/`.
2. **Configuration** tab → **Modules** section → tick the module → **Valider**.
3. The server restarts: the tab appears, so does the block, tasks are registered and the declared functions become available in the scenario editor.

![Enabling a module](images/06-activation.png)

An enabled module becomes a **full Django app**: it can have its own templates, models and migrations.

## 6. Checking

| What to check | Where |
| --- | --- |
| The module is detected | Configuration → Modules |
| The tab is shown | navigation bar |
| The block is shown | Dashboard |
| The tasks are registered | Configuration → Scheduler |
| Actions and infos are offered | scenario editor |
| No errors | Log, filtered on the module |

## Common mistakes

- **Invalid `conf.py`** — the module stays listed with its error message. Beware of top-level imports: `conf.py` is loaded very early, and a heavy or failing import breaks detection. The bundled modules wrap their dynamic imports in a `try/except`.
- **Tab not highlighted** — `active_tab` is wrong.
- **Template not found** — templates must live in `modules/<name>/templates/<name>/`; the subdirectory named after the module avoids collisions between modules.
- **A task does not run** — check the Scheduler card, and that the `fonction` path is relative to the module directory (`fonctions.api.tache_actualiser`, with no `modules.<name>.` prefix).
- **A disabled module stays in the bar** — the restart did not happen; relaunch `runserver`.

# Contract reference

Everything a module can declare, and what the framework does with it.

## `conf.py`

| Constant | Type | Mandatory | Purpose |
| --- | --- | --- | --- |
| `ONGLET` | `str` | **yes** | Tab label |
| `ICONE` | `str` | no | Bootstrap Icons name, without `bi-` (default: `puzzle`) |
| `DESCRIPTION` | `str` | no | Shown in the module list |
| `TACHES` | `list` | no | Background tasks |
| `SCENARIO` | `list` | no | Actions offered in the editor |
| `INFOS` | `list` | no | Readings offered in the editor |

## `TACHES` — background tasks

Two possible schedules.

### Every X minutes

```
TACHES = [
    {"nom": "actualiser", "fonction": "fonctions.api.tache_actualiser", "minutes": 10},
]
```

- Overridable in the database by the module's `tache_<name>_minutes` setting
- `0` = task disabled
- First run about 10 seconds after start-up

### At fixed times

```
TACHES = [
    {"nom": "previsions", "fonction": "fonctions.api.tache_actualiser",
     "heures": ["03:00", "07:00", "11:00", "15:00", "17:00"]},
]
```

- Accepts whole hours (`7`) or times (`"07:30"`)
- Overridable by the `tache_<name>_heures` setting (`"3,7:30,11"`), **empty = disabled**
- **No run at start-up**: the number of runs per day is exactly the length of the list — essential against a quota-bound API
- Times with different minutes produce several cron jobs (`module.task`, `module.task.1`)

In both cases, `fonction` is a **path relative to the module directory**, without the `modules.<name>.` prefix.

Only errors are logged, not successful runs.

## `SCENARIO` — exposed actions

```
SCENARIO = [
    {"nom": "allumer",
     "fonction": "fonctions.scenario.allumer",
     "description": "Turns the device on",
     "params": [
         {"nom": "mode", "label": "Mode", "options": [
             ["", "(unchanged)"], ["auto", "Auto"], ["heating", "Heating"]]},
     ]},
]
```

- `params` is optional: each entry becomes a dropdown in the editor, and its value is passed to the function as a **keyword argument**
- An empty value is not passed, which allows "(unchanged)" options
- The return value is logged: returning a short string describing what was done is good practice

For dynamic entries (one action per plug, per air conditioner), build the list in the module and expose it through a function:

```
try:
    from modules.mon_module.fonctions.scenario import build_scenario_entries
    SCENARIO = build_scenario_entries()
except Exception:
    SCENARIO = []
```

The `try/except` matters: a `conf.py` that raises makes the module undetectable.

## `INFOS` — exposed readings

```
INFOS = [
    {"nom": "temperature",
     "fonction": "fonctions.info.temperature",
     "description": "Measured temperature (°C)"},
]
```

An info is a function **with no argument** returning a simple value (number, text, `None`). It can be used:

- in a **condition**, with the operators `=`, `≠`, `<`, `≤`, `>`, `≥`;
- in the **Info → variable** action;
- as the source of a **computed time** trigger (it must then return `HH:MM`) or a **value change** trigger.

> An info may be read **very often** — every minute for a trigger, on every page view for a block. It must therefore be cheap: read a cache, do not query a quota-bound API. And it must be **stable**: an info that recomputes on every call makes triggers unpredictable.

## Display entry points

| File | Function | Returns |
| --- | --- | --- |
| `onglet/views.py` | `onglet(request)` | Django response (`render(...)`) |
| `dashboard/views.py` | `bloc(request)` | The block HTML, or `""` |
| `dashboard/views.py` | `blocs(request)` | `[{"titre", "icone", "html"}]` |

If both exist, `blocs` wins.

## Framework services — `core.services`

| Function | Signature | Purpose |
| --- | --- | --- |
| `journal` | `journal(message, module="core", level=LogEntry.INFO)` | Writes to the Log |
| `get_setting` | `get_setting(key, module="core", default=None)` | Reads a setting |
| `set_setting` | `set_setting(key, value, module="core", secret=False)` | Writes a setting |
| `get_variable` | `get_variable(name, default=None)` | Reads a global variable |
| `set_variable` | `set_variable(name, value)` | Writes a global variable |
| `set_control_state` | `set_control_state(control, on)` | Toggles a switch, honouring its exclusive group |

Log levels: `LogEntry.INFO`, `LogEntry.WARNING`, `LogEntry.ERROR`.

## Framework settings

| Key | Module | Purpose |
| --- | --- | --- |
| `dashboard_refresh_minutes` | `core` | Dashboard auto-refresh (0 = off) |
| `rattrapage_min` | `scenarios` | Catch-up window for "computed time" triggers (10 by default) |
| `declenche_<pk>` | `scenarios` | Date a time-based scenario last fired |
| `valeur_<pk>` | `scenarios` | Last value seen by a "value change" trigger |
| `tache_<name>_minutes` | *module* | A task's interval |
| `tache_<name>_heures` | *module* | A task's fixed times |

## Data models

| Model | Purpose |
| --- | --- |
| `Module` | A detected module, enabled or not |
| `Setting` | Key/value setting, partitioned by module |
| `Variable` | Shared global value |
| `Control` | Push button or switch, with exclusive group |
| `Scenario` | JSON definition: trigger, conditions, actions |
| `DashboardBlock` | Order, width and height of a block |
| `LogEntry` | One log line |

# Troubleshooting

## No scenario fires, no data refreshes

**The scheduler is not running.** This is the most frequent and the most discreet failure: the application displays normally, pages compute their values on the fly, and nothing betrays the problem.

Check in order:

1. **Configuration → Scheduler card**: it shows "à l'arrêt" and the start-up error.
2. **Log**: search for "Scheduler". A line "Scheduler démarré: N task(s), M scenario(s)" must appear at every launch. Its absence confirms the diagnosis.

Possible causes:

| Cause | Fix |
| --- | --- |
| APScheduler missing from the environment | `pip install -r requirements.txt` |
| Launched by something other than `runserver` | The scheduler only starts with `runserver`, by design |
| Error while starting a task | The message is in the Log and on the Scheduler card |

## A "computed time" scenario never fires

Three causes, in order of frequency:

1. **The source recomputes on every read.** If the trigger reads an info that redoes its calculation, the target time may drift through the day and never be reached. Point the trigger at a **variable**, fed when you decide.
2. **The target time has already passed** when the scenario is created or the server restarted. The catch-up only covers 10 minutes; beyond that, firing is postponed to the next day.
3. **Already fired today**: one run per day, even if the time changes afterwards.

The ▶ button lets you check that conditions and actions are correct, independently of the trigger.

## Error 429 from Solcast

The free quota is **10 calls per day for the account**, and each request consumes **one call per site**.

- The Solar tab shows the remaining calls and the resume time.
- After a 429, calls are **suspended until 6 a.m. the next day**: deliberately so, since retrying would only multiply refusals.
- Check that **no other client is using the same key**. v1 shared the key and consumed the same quota; its calls have been disabled (`APPELS_API_AUTORISES = False` in v1's `solcast/forecast.py`) and its `update_heater_schedule` Windows tasks removed.
- Reduce the number of times in the tab's settings: the cost is displayed live below the list.

## The call counter looks wrong

It only sees the calls made by v2. If it reports remaining calls while Solcast already refuses, another client has consumed the quota. As soon as a 429 arrives, the counter aligns with that reality and shows 0 remaining.

## A curve stops in the middle of the day

The "real" curve of the Solar module and the Energy module chart are fed by the **local Envoy history**, one point every 5 minutes.

- If the scheduler is not running, the history only advances when a page is displayed.
- The history is **reset every day**: an empty curve early in the morning is normal.

## A module does not appear

| Symptom | Likely cause |
| --- | --- |
| Missing from the module list | No `conf.py`, or a directory starting with `_` or `.` |
| Listed with an error message | Invalid `conf.py` — the message gives the exception |
| Ticked but no tab | The server did not restart: relaunch `runserver` |
| Tab present, no block | No `dashboard/views.py`, or a rendering error (see the Log) |

## A dashboard block has disappeared

An exception in a block is caught by the framework: the block is skipped and the error written to the Log, filtered on the module concerned. The rest of the dashboard keeps displaying.

## Strange text appears in a page

Something like `{# … #}` visible on screen: a multi-line Django comment. The `{# … #}` syntax only works on **a single line**; for a multi-line comment, use `{% comment %} … {% endcomment %}`.

## A change to `conf.py` has no effect

Tasks and their schedules are read **when the scheduler starts**. Restart the server. Action and info catalogues, however, are re-read each time the scenario editor is opened.

## Resetting the dashboard layout

**Organise** mode → **Par défaut** button. This only clears the order, widths and heights of the blocks.

## Starting from a clean database

Stop the server, rename `db.sqlite3`, then:

```
python manage.py migrate
python manage.py runserver 0.0.0.0:8100
```

Everything has to be configured again: modules, API keys, controls, scenarios. Keeping the old file allows a rollback.

# Appendix — screenshots

Drop the images in `docs/images/`, in **PNG**, keeping exactly the file names below: they are already referenced by the documentation.

A placeholder without an image shows an empty frame in the Markdown rendering without breaking the page — the documentation stays readable while the screenshots are being taken.

| File | Expected content | Used in |
| --- | --- | --- |
| `01-premier-demarrage.png` | The application on first launch, empty dashboard | Installation |
| `02-configuration.png` | The whole Configuration tab | Getting started |
| `02-modules.png` | The Modules section, several modules ticked | Getting started |
| `02-controles.png` | The Buttons & switches section, or the creation dialog | Getting started |
| `02-variables.png` | The Global variables section | Getting started |
| `02-scheduler.png` | The Scheduler card with jobs and their next run | Getting started |
| `02-journal.png` | The Log tab, ideally with a filter applied | Getting started |
| `03-tableau-de-bord.png` | The full dashboard, during the day | The dashboard |
| `03-organiser.png` | Organise mode, selectors and height handle visible | The dashboard |
| `04-editeur.png` | The scenario editor, one complete scenario on screen | Scenarios |
| `04-conditions.png` | Several conditions linked by AND and OR | Scenarios |
| `05-energie.png` | The Energy tab with the daily chart | Bundled modules |
| `05-solaire.png` | The Solar tab, quota counter and times visible | Bundled modules |
| `05-heure-demarrage.png` | The Start time tab with the calculation details | Bundled modules |
| `06-activation.png` | The Modules section while ticking a new module | Creating a module |

## Tips

- **Frame the useful area** rather than the whole screen: a screenshot of the relevant block alone ages better and stays readable on a phone.
- **A width of about 1400 px** is enough; beyond that the file grows for no gain.
- Hide sensitive values before publishing: API keys, credentials, the e-mail address in the Enphase settings.
- To add an unplanned screenshot, insert it in the document with `![Description](images/file-name.png)` and complete this table.
