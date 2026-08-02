# Installation

## Voraussetzungen

- **Python 3.10 oder neuer** (das Projekt läuft unter 3.14)
- **Windows** auf dem aktuellen Rechner, wobei außer den folgenden Befehlen nichts Windows-spezifisch ist
- Zugriff im lokalen Netz auf die Geräte (Enphase-Gateway, Tuya-Steckdosen) und Internetzugang für die Schnittstellen (Solcast, RTE Tempo, Cozytouch, Hi-Kumo)

## Einrichtung

```
cd C:\Dev\Homotic
..\.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
```

`requirements.txt` installiert:

| Paket | Zweck |
| --- | --- |
| `django` | der Web-Unterbau |
| `requests` | HTTP-Aufrufe an die Schnittstellen |
| `apscheduler` | **der Scheduler**: periodische Aufgaben und zeitgesteuerte Szenarien |
| `pyoverkiz` | Cozytouch-Warmwasserspeicher und Hi-Kumo-Klimageräte |

> **APScheduler ist nicht optional.** Ohne ihn startet die Anwendung und zeigt alles normal an, aber **kein Szenario wird ausgelöst und keine Daten werden im Hintergrund aktualisiert**. Das ist der stillste Ausfall des Projekts: siehe *Fehlerbehebung*.

## Start

```
cd C:\Dev\Homotic
..\.venv\Scripts\activate
python manage.py runserver 0.0.0.0:8100
```

Dann <http://localhost:8100/> öffnen.

Das Lauschen auf `0.0.0.0` macht die Anwendung von anderen Geräten im lokalen Netz erreichbar (Telefon, Tablet) unter `http://<pc-ip>:8100/`.

Port 8100 erlaubt den Parallelbetrieb mit v1, die den Standardport verwendet.

![Startbildschirm beim ersten Start](images/01-premier-demarrage.png)

## Was beim Start geschieht

1. Django lädt den Unterbau `core` **und die aktivierten Module** — im Reiter Konfiguration angehakte Module werden zu vollwertigen Django-Apps (siehe `homotic/settings.py`).
2. Der **Scheduler startet** und registriert die von den aktiven Modulen deklarierten periodischen Aufgaben sowie die Szenarien mit Zeit-, Rechenzeit-, Intervall- oder Wertänderungsauslöser.
3. Eine Zeile „Scheduler démarré: N Aufgabe(n), M Szenario/Szenarien" wird ins **Protokoll** geschrieben. Fehlt sie, ist er nicht gestartet.

Der Scheduler startet **nur mit `runserver`**: `migrate`, `shell` und `makemigrations` starten ihn absichtlich nicht.

## Projekt aktualisieren

Nach dem Einspielen einer neuen Codeversion:

```
..\.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8100
```

`migrate` ist nötig, sobald sich ein Datenmodell ändert (neues Feld eines Blocks, neue Einstellung). Im Zweifel schadet es nicht: Gibt es nichts zu tun, passiert nichts.

## Sicherung

Der gesamte Zustand der Anwendung steckt in **`db.sqlite3`**: Einstellungen, API-Schlüssel, Bedienelemente, Szenarien, Variablen, Anordnung des Dashboards, Protokoll. Diese Datei zu kopieren genügt als vollständige Sicherung.

Die API-Schlüssel liegen in der Datenbank und sind in der Oberfläche maskiert, aber **nicht verschlüsselt**: Die Datenbankdatei verdient dieselbe Sorgfalt wie eine Passwortdatei.

# Erste Schritte — der Reiter Konfiguration

Alles wird im Reiter **Configuration** eingerichtet, gegliedert in fünf Abschnitte: Taster & Schalter, Module, globale Variablen, Scheduler, Szenarien.

![Reiter Konfiguration](images/02-configuration.png)

## Module aktivieren

Der Abschnitt **Modules** listet auf, was im Verzeichnis `modules/` vorhanden ist. Ein Modul anhaken und **Valider** klicken:

1. Das Modul wird in der Datenbank eingetragen;
2. der Server startet automatisch neu;
3. sein Reiter erscheint in der Navigationsleiste, und sein Block auf dem Dashboard, sofern es einen liefert.

![Erkannte Module](images/02-modules.png)

Abhaken und bestätigen entfernt Reiter und Block, **ohne die Einstellungen des Moduls zu löschen**: Späteres erneutes Anhaken stellt die Konfiguration wieder her.

Ein Modul mit ungültiger `conf.py` bleibt mit seiner Fehlermeldung gelistet, statt den Start der gesamten Anwendung zu verhindern.

## Taster und Schalter

Das sind die Bedienelemente im Block **Scénarios** des Dashboards und die einfachsten Auslöser für ein Szenario.

| Typ | Verhalten | Typische Verwendung |
| --- | --- | --- |
| **Taster** | ein Impuls, kein gespeicherter Zustand | „Jetzt aufheizen" |
| **Schalter** | bleibt EIN oder AUS | Modus „Viele Duschen", „Sommer / Winter" |

Ein Schalter kann zu einer **exklusiven Gruppe** gehören: Einen einzuschalten schaltet die anderen der Gruppe automatisch aus. Genau das verbindet die Schalter „Été" und „Hiver" des Moduls Startzeit.

![Anlegen eines Tasters oder Schalters](images/02-controles.png)

> Vorsicht bei Dubletten: Der interne Name eines Bedienelements ist das, was Szenarien und Module lesen. Zwei Schalter, die beide „Hiver" anzeigen, aber `hiver` und `Hiver` heißen, sind zwei verschiedene Objekte, und nur einer wird gelesen.

## Globale Variablen

Eine Variable ist ein benannter Wert, den alle Module und alle Szenarien teilen. Sie ist **in Bedingungen prüfbar**, **durch Aktionen änderbar** und auf dieser Seite von Hand editierbar.

![Globale Variablen](images/02-variables.png)

Sie dienen drei Zwecken:

- **einen Messwert veröffentlichen** für Szenarien — Module speisen `enphase_production_w`, `solcast_prevu_aujourdhui_kwh`;
- **einen Zustand merken** zwischen zwei Durchläufen — `Clim_allumer`, `Chauffe_Eau_Plein`;
- **einen Sollwert halten, der ohne Codeänderung anpassbar ist** — `heure_demarrage_chauffe_eau`.

Die Schaltfläche ✓ rechts neben einer Variablen speichert den eingegebenen Wert. So erzwingt man einen Wert von Hand, etwa um eine berechnete Startzeit zu korrigieren.

## Scheduler

Die Karte **Scheduler** ist die Diagnose der Hintergrundausführung:

- **aktiv / gestoppt**, gegebenenfalls mit dem Startfehler;
- die Liste der registrierten Aufgaben und Szenarien, **in der Reihenfolge ihrer Ausführung**, mit der nächsten Ausführung.

![Karte Scheduler](images/02-scheduler.png)

Angezeigt werden die Namen der Szenarien und Module; die technische Kennung (`scenario.4`, `solcast.previsions`) steht zur Fehlersuche darunter.

Zeigt diese Karte „gestoppt", läuft **nichts automatisch**: Dort anfangen, bevor man sucht, warum ein Szenario nicht auslöst.

## Protokoll

Der Reiter **Journal** hält alles fest, was die Anwendung tut: Szenariodurchläufe, API-Fehler, Änderungen von Einstellungen. Er lässt sich nach Modul und Stufe filtern (Info, Warnung, Fehler).

![Protokoll](images/02-journal.png)

Es ist die erste Anlaufstelle, wenn ein Verhalten überrascht: Szenarien schreiben dort ihren Auslösegrund und bei einem Fehlschlag die nicht erfüllte Bedingung.

# Das Dashboard

![Dashboard](images/03-tableau-de-bord.png)

Das Dashboard setzt sich zusammen aus:

- dem Block **Scénarios** — den in der Konfiguration angelegten Tastern und Schaltern;
- einem oder mehreren Blöcken **je aktivem Modul**: Jedes Modul entscheidet, was es anzeigt.

Ein Modul kann mehrere Blöcke liefern (das Modul Energie bietet zwei: „Énergie maintenant" und „La journée").

## Aktionsleiste

Die Befehle des Dashboards liegen in der Navigationsleiste rechts neben den Reitern:

| Befehl | Wirkung |
| --- | --- |
| ⟳ | Lädt die Seite sofort neu |
| `auto N min` | Automatische Aktualisierung mit Countdown |
| ✥ | Wechselt in den Modus **Organisieren** |

Die automatische Aktualisierung ist eine **in der Datenbank gespeicherte Einstellung**, verhält sich also am PC und am Telefon gleich. Der Countdown wird zurückgesetzt, wenn der Reiter in den Hintergrund gerät, damit die Seite nicht in der Sekunde neu lädt, in der man zurückkehrt.

> Die Schaltfläche Aktualisieren **lädt die Seite neu**; sie stößt keine Berechnung an und löst keinen kontingentierten API-Aufruf aus. Sie frischt die lokalen Messwerte auf (Enphase, Tuya, Warmwasserspeicher). Die Startzeit des Speichers und die Solarprognose bewegen sich nicht — siehe *Szenarien* und *Mitgelieferte Module*.

## Modus Organisieren

![Modus Organisieren](images/03-organiser.png)

Im Modus Organisieren lässt sich jeder Block:

- per **Ziehen und Ablegen verschieben**, um die Reihenfolge zu ändern;
- **in der Breite ändern** über die Auswahl: ein Viertel, ein Drittel, die Hälfte, zwei Drittel, volle Breite;
- **in der Höhe ändern** über den Griff am unteren Rand des Blocks oder durch Eingabe eines Pixelwerts.

Die Schaltfläche ⤡ neben dem Feld setzt die Höhe auf **automatisch** zurück.

Drei Hinweise zur Höhe:

- **Automatisch (0)**: Der Block nimmt die Höhe seines Inhalts an und richtet sich am höchsten Block seiner Zeile aus.
- **Feste Höhe**: Der Block hat genau diese Größe. Größer als sein Inhalt schafft er Platz und erlaubt es, eine Zeile auszurichten; kleiner, scrollt sein Inhalt im Inneren.
- Ein Block mit fester Höhe **wird nicht mehr vom höchsten Block seiner Zeile gedehnt**: Genau das gibt die Kontrolle über eine unausgewogene Zeile zurück.

Die automatische Aktualisierung ist im Modus Organisieren **ausgesetzt**: Ein Neuladen mitten im Ziehen würde die Anordnung verlieren.

**Enregistrer** speichert die Anordnung, **Annuler** verlässt sie ohne Änderung, **Par défaut** löscht die eigene Anordnung und stellt die ursprüngliche Platzierung wieder her.

## Blöcke und Module

Ein fehlerhafter Block verhindert die Anzeige des Dashboards nicht: Der Unterbau fängt die Ausnahme ab, schreibt die Zeile ins Protokoll und geht zum nächsten Block über. Ein fehlender Block bei aktivem Modul ist daher oft ein Anzeigefehler, den man im Protokoll findet.

# Szenarien

Ein Szenario besteht aus drei Teilen:

```
AUSLÖSER     →  BEDINGUNGEN →  AKTIONEN
wann?           falls?          was tun?
```

Es wird über **Configuration → Nouveau scénario** angelegt.

![Szenario-Editor](images/04-editeur.png)

## Auslöser — wann soll das Szenario laufen?

| Auslöser | Wann | Anmerkungen |
| --- | --- | --- |
| **Manuell** | Schaltfläche ▶ Testen oder Aufruf durch ein anderes Szenario | Der einzige, der nie von selbst startet |
| **Täglich zu fester Uhrzeit** | Zur angegebenen Zeit | Cron-Auftrag |
| **Täglich zu berechneter Uhrzeit** | Zur Zeit aus einer Variablen oder einer Modul-Info | Nur ein Start pro Tag |
| **Bei Wertänderung** | Wenn sich eine Variable oder eine Info ändert | Einstellbare Abfrage, optionaler Filter auf den Zielwert |
| **Alle X Minuten** | Periodisch, mit optionalem Zeitfenster | Dient als „solange" / „bis" |
| **Tastendruck** | Sofort | |
| **Schalterumschaltung** | Wenn der Schalter auf EIN oder AUS geht | |

### Berechnete Uhrzeit

Die Uhrzeit wird **bei jeder Prüfung neu gelesen**, aus einer globalen Variablen oder einer Modul-Info. Sie kann sich also im Tagesverlauf ändern, das Szenario startet aber **nur einmal pro Tag**.

Ein **Nachholfenster von 10 Minuten** ist vorgesehen: Wird die genaue Minute verpasst (ausgelasteter Server, Neustart, kurzzeitig unlesbare Quelle), erfolgt die Auslösung dennoch innerhalb der folgenden zehn Minuten. Ohne dieses Fenster machte eine einzige verpasste Minute die Aufheizung des Tages zunichte. Die Dauer wird über die Einstellung `rattrapage_min` des Moduls `scenarios` festgelegt.

> **Falle**: Diesen Auslöser auf eine Info zu setzen, die bei jedem Lesen **neu rechnet**, ergibt ein wanderndes Ziel. Die Berechnung der Startzeit berücksichtigt nur **künftige** Zeitfenster: Um 12:30 Uhr kommt das Fenster 12:30 nicht mehr infrage, und die Uhrzeit weicht vor der Uhr zurück. Den Auslöser auf eine **Variable** setzen, die man selbst befüllt.

### Bei Wertänderung

Vergleicht den aktuellen Wert mit dem vorherigen, der in der Datenbank liegt — die Überwachung übersteht also einen Neustart. Drei Schutzmechanismen:

- eine **unlesbare Quelle** löst nichts aus und überschreibt die Referenz nicht: Eine nicht erreichbare Schnittstelle darf nicht als Änderung gelten;
- die **erste Lesung** dient als Referenz, ohne auszulösen, sonst würde jeder Serverstart das Szenario ausführen;
- ändert man das Szenario auf eine andere Quelle, beginnt die Referenz neu, statt zwei zusammenhanglose Werte zu vergleichen.

Das Feld „nur wenn der Wert wird" beschränkt die Auslösung auf einen bestimmten Zielwert (etwa die Tempo-Farbe, die auf `rouge` wechselt).

Das Prüfintervall ist einstellbar, denn eine Modul-Info kann ein Gerät oder eine Schnittstelle abfragen.

## Bedingungen — unter welchem Vorbehalt?

Vier Arten: **Schalterzustand**, **Zeitfenster** (innerhalb / außerhalb), **Variable** (mit Operatoren), **Modul-Info** (mit Operatoren).

Keine Bedingung bedeutet, dass das Szenario immer läuft.

### UND / ODER

Ab der zweiten Bedingung verknüpft eine Auswahl **ET / OU** jede Zeile mit der vorherigen. **UND bindet stärker als ODER**, wie in der booleschen Algebra:

```
A UND B ODER C UND D    liest sich als    (A UND B) ODER (C UND D)
```

![Bedingungen mit UND und ODER](images/04-conditions.png)

Ist kein Zweig erfüllt, führt das Protokoll auf, warum jeder einzelne scheiterte, statt nur einen Grund zu nennen.

## Aktionen — was tun?

| Aktion | Wirkung |
| --- | --- |
| **Modulfunktion** | Ruft eine von einem Modul bereitgestellte Funktion auf (mit Parametern, falls deklariert) |
| **Schalter setzen** | Setzt einen Schalter auf EIN oder AUS |
| **Szenario starten** | Verkettet zu einem anderen Szenario (höchstens 3 Ebenen) |
| **Protokollmeldung** | Hält einen Schritt fest |
| **Variable setzen** | Weist einen Wert zu |
| **Info → Variable** | Legt das Ergebnis einer Modul-Info in einer Variablen ab |
| **Wenn / Dann / Sonst** | Bedingte Verzweigung |
| **Schleife Solange / Bis** | Wiederholung mit Intervall, Höchstdauer und vorzeitigen Ausstiegen |

Aktionen laufen **der Reihe nach** und brechen beim ersten Fehler ab. Wenn-Blöcke und Schleifen lassen sich **3 Ebenen** tief verschachteln.

> Eine Aktion „Schalter setzen" **löst die Szenarien dieses Schalters nicht erneut aus**: Das ist Absicht, um unbeabsichtigte Schleifen zu vermeiden. Zum Verketten ausdrücklich „Szenario starten" verwenden.

## Zeilen umsortieren

Jede Bedingung und jede Aktion hat Pfeile **↑ ↓**, um sie innerhalb ihres Blocks zu verschieben. Die Bewegung bleibt eingegrenzt: Eine Aktion aus einem *Dann* kann nicht ins *Sonst* springen.

## Testen

Die Schaltfläche ▶ in der Szenarienliste führt das Szenario sofort aus, **einschließlich der Bedingungen**. Das Protokoll zeigt das Ergebnis und bei einem Fehlschlag die blockierende Bedingung.

## Vollständiges Beispiel — Warmwasser zum besten Zeitpunkt

Zwei Szenarien, die sich ergänzen:

**1. Die Uhrzeit berechnen** (Name `HeureDemarage`)

- Auslöser: täglich um `03:00` — direkt nach der Solcast-Aktualisierung um 3 Uhr
- Aktion: *Modulfunktion* → Heure démarrage → `recalculer`

`recalculer` schreibt die Variable `heure_demarrage_chauffe_eau` und protokolliert die Einzelheiten der Berechnung (gewähltes Zeitfenster, Kostenvergleich Tag/Nacht).

**2. Aufheizen starten** (Name `Chauffe_eau_ON`)

- Auslöser: *berechnete Uhrzeit* → **eine globale Variable** → `heure_demarrage_chauffe_eau`
- Bedingung (optional, aber empfohlen): Info `calcul_du_jour` = `oui`, damit nicht mit einer Uhrzeit von gestern gestartet wird
- Aktion: *Modulfunktion* → Chauffe-eau → `chauffer`

Weitergedacht kann ein drittes Szenario bei **Änderung** von `solcast_prevu_aujourdhui_kwh` `recalculer` erneut anstoßen, wenn sich die Tagesprognose deutlich verschiebt.

# Mitgelieferte Module

Jedes Modul wird in seinem eigenen Reiter im Abschnitt „Paramétrage" eingerichtet.

| Modul | Reiter | Was es beisteuert |
| --- | --- | --- |
| `chauffe_eau` | Chauffe-eau | Atlantic-Speicher über Cozytouch: Zustand, verbleibende Duschen, erzwungenes Aufheizen |
| `clim` | Climatisation | Hitachi-Hi-Kumo-Klimageräte: Ein/Aus, Betriebsart, Sollwert |
| `enphase` | Énergie | Lokales Envoy-Gateway: Erzeugung, Verbrauch, Netz, Tagesdiagramm |
| `solcast` | Solaire | Erzeugungsprognose, bestes Aufheizfenster |
| `tempo` | Tempo | Farben der EDF-Tempo-Tage und Tarife |
| `tuya` | Capteurs | Tuya-Sensoren und -Steckdosen |
| `heure_demarrage` | Heure démarrage | Berechnet die beste Aufheizzeit des Speichers |

## Energie (Enphase)

Fragt das **Envoy-Gateway lokal** ab — kein Kontingent, keine Cloud.

![Reiter Energie](images/05-energie.png)

Der Dashboard-Block vereint das Flussschema, die Momentanleistungen und das **Tagesdiagramm**:

- **blau**: Erzeugung, oberhalb der Achse
- **orange**: Verbrauch, unterhalb der Achse
- **grau**: Netz, im Hintergrund — oberhalb bei Bezug, unterhalb bei Einspeisung

Die Achse zeigt **kein Vorzeichen**: Ein nach unten weisender oranger Balken bleibt ein Verbrauch von 1,8 kW und nicht „−1,8 kW".

Dieses Diagramm entsteht aus einer **lokalen Historie**: Jede Abfrage des Envoy legt einen Punkt ab (Erzeugung, Verbrauch, Netz), im 5-Minuten-Raster, jede Nacht zurückgesetzt. Kein externer Aufruf, kein Kontingent. Es füllt sich also im Tagesverlauf und ist nur vollständig, wenn der Scheduler läuft.

Veröffentlicht die Variablen `enphase_production_w`, `enphase_conso_w`, `enphase_import_w`, `enphase_export_w`.

## Solar (Solcast)

Prognose der Photovoltaikerzeugung und bestes Zeitfenster zum Aufheizen des Speichers.

![Reiter Solar](images/05-solaire.png)

### Das Kontingent, zuerst zu verstehen

Der kostenlose Solcast-Tarif gewährt **10 Aufrufe pro Tag für das Konto**, und jede Anfrage kostet **einen Aufruf je Standort**. Bei zwei Dachflächen kostet eine Aktualisierung also **2 Aufrufe** — höchstens 5 Aktualisierungen pro Tag.

Drei kumulierte Schutzmechanismen:

1. **Seitenaufrufe rufen die Schnittstelle nie auf.** Nur die geplante Aktualisierung und die Schaltfläche Aktualisieren dürfen das. Sonst hinge die Zahl der Aufrufe davon ab, wie oft man auf das Dashboard schaut.
2. **Ein Tageszähler** mit Obergrenze (Einstellung `quota_jour`, standardmäßig 10), täglich zurückgesetzt und **an der Wahrheit ausgerichtet**, sobald ein 429 eintrifft: Solcast hat 10 Aufrufe gezählt, also glauben wir Solcast und nicht unserer lokalen Zählung.
3. **Ein Backoff**: Ein Netzfehler setzt die Aufrufe 30 Minuten aus, ein 429 bis zum nächsten Tag 6 Uhr. Ohne ihn schrieb ein Fehlschlag keinen Cache, sodass jeder Seitenaufruf einen weiteren Aufruf auslöste — daher Serien von Dutzenden abgelehnter Aufrufe.

Der Reiter zeigt dauerhaft die **verbleibenden Aufrufe**, die noch finanzierbaren Aktualisierungen, den nächsten Durchlauf und das bis zum Abend Geplante.

### Die Uhrzeiten wählen

Im Abschnitt Paramétrage lassen sich die Aktualisierungen einzeln **hinzufügen und entfernen**. Die Minuten sind frei (`07:30` ist zulässig). Die Kosten erscheinen live unter der Liste, orange, wenn die Summe das Kontingent übersteigt.

Keine Uhrzeit bedeutet keinen automatischen Aufruf; nur die Schaltfläche Aktualisieren wirkt. Wird beim **nächsten Serverstart** übernommen.

## Startzeit

Ein Rechenmodul ohne eigenes Gerät: Es verknüpft die Solarprognose, den Grundverbrauch des Hauses und die Tempo-Tarife, um den besten Zeitpunkt zum Aufheizen des Speichers vorzuschlagen.

![Reiter Startzeit](images/05-heure-demarrage.png)

**Die Berechnung läuft nie automatisch.** Sie erfolgt nur auf Anforderung: über die Szenarioaktion `recalculer` oder die Schaltfläche Recalculer im Reiter. Ihr Ergebnis wird gespeichert, und diese Momentaufnahme lesen Dashboard, Modul-Infos und Auslöser.

Das ist Absicht: Die Berechnung berücksichtigt nur **künftige** Zeitfenster, sodass eine bei jedem Lesen neu rechnende Info eine Uhrzeit lieferte, die vor der Uhr zurückwich und die der Auslöser nie einholte.

**Maßgeblich ist die Variable `heure_demarrage_chauffe_eau`.** Sie wird von `recalculer` geschrieben, ist in der Konfiguration von Hand änderbar und hat Vorrang vor der gespeicherten Berechnung: Weichen beide voneinander ab, zeigt die Oberfläche die erzwungene Uhrzeit mit einem entsprechenden Hinweis.

Jahreszeit: Der Schalter **Hiver** entscheidet. Ist er aus — ob „Été" nun an ist oder beide aus sind — gilt **Sommer**, also die kurze Aufheizdauer. Kein automatischer Wechsel nach Datum.

In Bedingungen nützliche Infos: `heure_demarrage`, `mode_retenu`, `calcul_du_jour` (stammt die letzte Berechnung von heute?), `heure_calcul`, `gain_estime_eur`, `surplus_creneau_kwh`.

## Tempo

Farben der EDF-Tempo-Tage über die RTE-Schnittstelle, Hoch-/Niedertarife je Farbe, Saisonzähler. Speist die Kostenabwägung des Moduls Startzeit.

## Warmwasser, Klimatisierung, Sensoren

Gerätemodule: Sie stellen ihren Zustand als **Infos** und ihre Befehle als **Szenarioaktionen** bereit. Die Einrichtung (Zugangsdaten für Cozytouch, Hi-Kumo, Tuya) erfolgt im Reiter des Moduls.

Der Atlantic-Warmwasserspeicher kennt keinen direkten Befehl „aufheizen": Eine vollständige Aufheizung zu erzwingen bedeutet, die **Anzahl der gewünschten Duschen auf das Maximum** zu setzen, und zum Normalbetrieb zurückzukehren bedeutet, sie auf das Minimum zurückzustellen. Genau das tun die Funktionen `chauffer` und `eteindre`.

# Ein Modul erstellen

Ein Modul ist ein Verzeichnis unter `modules/`. Der Unterbau kennt dessen Inhalt nicht: Er liest die `conf.py` und ruft die vom Modul deklarierten Einstiegspunkte auf. Eine Fähigkeit hinzuzufügen erfordert daher **keine Änderung am Unterbau**.

## Aufbau

```
modules/mon_module/
├── __init__.py
├── conf.py                     Manifest — die einzige Pflichtdatei
├── fonctions/
│   ├── __init__.py
│   ├── api.py                  spricht mit dem Gerät oder der Schnittstelle
│   ├── info.py                 Lesewerte für Szenarien (INFOS)
│   ├── scenario.py             Aktionen für Szenarien (SCENARIO)
│   └── affichage.py            Aufbau der Diagramme (optional)
├── onglet/
│   ├── __init__.py
│   └── views.py                Funktion onglet(request)
├── dashboard/
│   ├── __init__.py
│   └── views.py                Funktion bloc(request) oder blocs(request)
└── templates/mon_module/
    ├── onglet.html
    └── _bloc.html
```

Nur `conf.py` ist Pflicht. Ein Modul kann nur einen Reiter haben, oder nur einen Dashboard-Block, oder keines von beiden (das Modul Startzeit ist im Wesentlichen ein Rechenmodul).

Am einfachsten ist es, **`modules/exemple/` zu kopieren** und umzubenennen.

## 1. Das Manifest — `conf.py`

```
"""Manifest des Moduls Mein Modul."""

ONGLET = "Mon module"          # Pflicht: Name des Reiters
ICONE = "thermometer-half"     # Name aus Bootstrap Icons
DESCRIPTION = "Was dieses Modul tut, in einem Satz."

# Hintergrundaufgaben, ausgeführt vom Scheduler des Unterbaus
TACHES = [
    {"nom": "actualiser", "fonction": "fonctions.api.tache_actualiser", "minutes": 10},
]

# Im Szenario-Editor angebotene Aktionen
SCENARIO = [
    {"nom": "allumer", "fonction": "fonctions.scenario.allumer",
     "description": "Schaltet das Gerät ein"},
]

# In Bedingungen und in „Info → Variable" angebotene Lesewerte
INFOS = [
    {"nom": "temperature", "fonction": "fonctions.info.temperature",
     "description": "Gemessene Temperatur (°C)"},
]
```

`ONGLET` dient als Beschriftung des Reiters; `ICONE` muss ein Name aus [Bootstrap Icons](https://icons.getbootstrap.com) **ohne das Präfix `bi-`** sein.

Die einzelnen Verträge sind in *Referenz der Verträge* beschrieben.

## 2. Der Reiter — `onglet/views.py`

Der Unterbau ruft die Funktion `onglet(request)` auf, wenn der Reiter angeklickt wird. Es ist eine gewöhnliche Django-View.

```
from django.shortcuts import render

from core.services import get_setting, journal

from ..fonctions import api


def onglet(request):
    if request.method == "POST" and request.POST.get("action") == "params":
        # eingegebene Einstellungen speichern
        ...

    return render(request, "mon_module/onglet.html", {
        "active_tab": "module:mon_module",   # hebt den Reiter hervor
        "mesure": api.lire(),
    })
```

`active_tab` muss `"module:<verzeichnisname>"` lauten, damit der Reiter in der Navigationsleiste als aktiv erscheint.

## 3. Der Dashboard-Block — `dashboard/views.py`

Zwei mögliche Verträge:

```
from django.template.loader import render_to_string

from ..fonctions import api


def bloc(request):
    """Ein einzelner Block: liefert HTML (oder eine leere Zeichenkette für
    keinen Block)."""
    return render_to_string("mon_module/_bloc.html", {"mesure": api.lire()})
```

```
def blocs(request):
    """Mehrere Blöcke: eine Liste von Dictionaries."""
    return [
        {"titre": "Live-Ansicht", "icone": "speedometer", "html": "<p>…</p>"},
        {"titre": "Der Tag", "icone": "calendar", "html": "<p>…</p>"},
    ]
```

Der Unterbau rahmt den Block bereits in eine Karte mit Titel: Das Modul liefert nur den Inhalt.

Eine in einem Block ausgelöste Ausnahme verhindert die Anzeige des Dashboards nicht: Sie wird protokolliert und der Block übersprungen.

## 4. Dienste des Unterbaus

```
from core.services import (
    journal, get_setting, set_setting,
    get_variable, set_variable, set_control_state,
)

journal("Aufheizen gestartet", module="mon_module")
set_setting("api_key", "xxx", module="mon_module", secret=True)
get_setting("api_key", module="mon_module", default="")
set_variable("mon_module_temperature", "21.5")
```

- **Einstellungen** (`get_setting` / `set_setting`): die Konfiguration des Moduls, nach `module=` getrennt. `secret=True` maskiert den Wert in der Oberfläche.
- **Variablen** (`get_variable` / `set_variable`): die öffentlichen Werte, geteilt mit Szenarien und anderen Modulen.
- **Protokoll** (`journal`): standardmäßig Stufe `INFO`, sonst `LogEntry.WARNING` oder `LogEntry.ERROR`.

## 5. Das Modul einbinden

1. Das Verzeichnis in `modules/` ablegen.
2. Reiter **Configuration** → Abschnitt **Modules** → Modul anhaken → **Valider**.
3. Der Server startet neu: Der Reiter erscheint, ebenso der Block, die Aufgaben werden registriert und die deklarierten Funktionen stehen im Szenario-Editor bereit.

![Ein Modul aktivieren](images/06-activation.png)

Ein aktiviertes Modul wird zu einer **vollwertigen Django-App**: Es kann eigene Templates, Modelle und Migrationen haben.

## 6. Prüfen

| Zu prüfen | Wo |
| --- | --- |
| Das Modul wird erkannt | Configuration → Modules |
| Der Reiter erscheint | Navigationsleiste |
| Der Block erscheint | Dashboard |
| Die Aufgaben sind registriert | Configuration → Scheduler |
| Aktionen und Infos werden angeboten | Szenario-Editor |
| Keine Fehler | Protokoll, nach Modul gefiltert |

## Häufige Fehler

- **Ungültige `conf.py`** — das Modul bleibt mit seiner Fehlermeldung gelistet. Vorsicht bei Importen auf Dateiebene: `conf.py` wird sehr früh geladen, und ein schwerer oder fehlschlagender Import bricht die Erkennung. Die mitgelieferten Module fassen ihre dynamischen Importe in ein `try/except`.
- **Reiter nicht hervorgehoben** — `active_tab` ist falsch gesetzt.
- **Template nicht gefunden** — Templates gehören nach `modules/<name>/templates/<name>/`; das nach dem Modul benannte Unterverzeichnis verhindert Kollisionen zwischen Modulen.
- **Eine Aufgabe läuft nicht** — die Karte Scheduler prüfen und dass der Pfad in `fonction` relativ zum Modulverzeichnis ist (`fonctions.api.tache_actualiser`, ohne vorangestelltes `modules.<name>.`).
- **Ein deaktiviertes Modul bleibt in der Leiste** — der Neustart hat nicht stattgefunden; `runserver` erneut starten.

# Referenz der Verträge

Alles, was ein Modul deklarieren kann, und was der Unterbau damit macht.

## `conf.py`

| Konstante | Typ | Pflicht | Zweck |
| --- | --- | --- | --- |
| `ONGLET` | `str` | **ja** | Beschriftung des Reiters |
| `ICONE` | `str` | nein | Name aus Bootstrap Icons, ohne `bi-` (Standard: `puzzle`) |
| `DESCRIPTION` | `str` | nein | In der Modulliste angezeigt |
| `TACHES` | `list` | nein | Hintergrundaufgaben |
| `SCENARIO` | `list` | nein | Im Editor angebotene Aktionen |
| `INFOS` | `list` | nein | Im Editor angebotene Lesewerte |

## `TACHES` — Hintergrundaufgaben

Zwei mögliche Taktungen.

### Alle X Minuten

```
TACHES = [
    {"nom": "actualiser", "fonction": "fonctions.api.tache_actualiser", "minutes": 10},
]
```

- In der Datenbank überschreibbar durch die Einstellung `tache_<name>_minutes` des Moduls
- `0` = Aufgabe deaktiviert
- Erste Ausführung etwa 10 Sekunden nach dem Start

### Zu festen Uhrzeiten

```
TACHES = [
    {"nom": "previsions", "fonction": "fonctions.api.tache_actualiser",
     "heures": ["03:00", "07:00", "11:00", "15:00", "17:00"]},
]
```

- Akzeptiert ganze Stunden (`7`) oder Uhrzeiten (`"07:30"`)
- Überschreibbar durch die Einstellung `tache_<name>_heures` (`"3,7:30,11"`), **leer = deaktiviert**
- **Keine Ausführung beim Start**: Die Zahl der Durchläufe pro Tag entspricht genau der Liste — unverzichtbar bei einer kontingentierten Schnittstelle
- Uhrzeiten mit unterschiedlichen Minuten ergeben mehrere Cron-Aufträge (`modul.aufgabe`, `modul.aufgabe.1`)

In beiden Fällen ist `fonction` ein **Pfad relativ zum Modulverzeichnis**, ohne das Präfix `modules.<name>.`.

Protokolliert werden nur Fehler, nicht die erfolgreichen Durchläufe.

## `SCENARIO` — bereitgestellte Aktionen

```
SCENARIO = [
    {"nom": "allumer",
     "fonction": "fonctions.scenario.allumer",
     "description": "Schaltet das Gerät ein",
     "params": [
         {"nom": "mode", "label": "Betriebsart", "options": [
             ["", "(unverändert)"], ["auto", "Auto"], ["heating", "Heizen"]]},
     ]},
]
```

- `params` ist optional: Jeder Eintrag wird im Editor zu einem Auswahlfeld, sein Wert wird der Funktion als **benanntes Argument** übergeben
- Ein leerer Wert wird nicht übergeben, was Optionen wie „(unverändert)" ermöglicht
- Der Rückgabewert wird protokolliert: Eine kurze Zeichenkette zurückzugeben, die das Getane beschreibt, ist gute Praxis

Für dynamische Einträge (eine Aktion je Steckdose, je Klimagerät) die Liste im Modul aufbauen und über eine Funktion bereitstellen:

```
try:
    from modules.mon_module.fonctions.scenario import build_scenario_entries
    SCENARIO = build_scenario_entries()
except Exception:
    SCENARIO = []
```

Das `try/except` ist wichtig: Eine `conf.py`, die eine Ausnahme auslöst, macht das Modul unauffindbar.

## `INFOS` — bereitgestellte Lesewerte

```
INFOS = [
    {"nom": "temperature",
     "fonction": "fonctions.info.temperature",
     "description": "Gemessene Temperatur (°C)"},
]
```

Eine Info ist eine Funktion **ohne Argument**, die einen einfachen Wert liefert (Zahl, Text, `None`). Verwendbar:

- in einer **Bedingung**, mit den Operatoren `=`, `≠`, `<`, `≤`, `>`, `≥`;
- in der Aktion **Info → Variable**;
- als Quelle eines Auslösers **berechnete Uhrzeit** (dann muss sie `HH:MM` liefern) oder **bei Wertänderung**.

> Eine Info kann **sehr häufig** gelesen werden — jede Minute für einen Auslöser, bei jedem Seitenaufruf für einen Block. Sie muss daher günstig sein: einen Cache lesen, keine kontingentierte Schnittstelle abfragen. Und sie muss **stabil** sein: Eine Info, die bei jedem Aufruf neu rechnet, macht Auslöser unvorhersehbar.

## Einstiegspunkte der Anzeige

| Datei | Funktion | Rückgabe |
| --- | --- | --- |
| `onglet/views.py` | `onglet(request)` | Django-Antwort (`render(...)`) |
| `dashboard/views.py` | `bloc(request)` | HTML des Blocks oder `""` |
| `dashboard/views.py` | `blocs(request)` | `[{"titre", "icone", "html"}]` |

Existieren beide, hat `blocs` Vorrang.

## Dienste des Unterbaus — `core.services`

| Funktion | Signatur | Zweck |
| --- | --- | --- |
| `journal` | `journal(message, module="core", level=LogEntry.INFO)` | Schreibt ins Protokoll |
| `get_setting` | `get_setting(key, module="core", default=None)` | Liest eine Einstellung |
| `set_setting` | `set_setting(key, value, module="core", secret=False)` | Schreibt eine Einstellung |
| `get_variable` | `get_variable(name, default=None)` | Liest eine globale Variable |
| `set_variable` | `set_variable(name, value)` | Schreibt eine globale Variable |
| `set_control_state` | `set_control_state(control, on)` | Schaltet einen Schalter unter Beachtung seiner exklusiven Gruppe |

Protokollstufen: `LogEntry.INFO`, `LogEntry.WARNING`, `LogEntry.ERROR`.

## Einstellungen des Unterbaus

| Schlüssel | Modul | Zweck |
| --- | --- | --- |
| `dashboard_refresh_minutes` | `core` | Automatische Aktualisierung des Dashboards (0 = aus) |
| `rattrapage_min` | `scenarios` | Nachholfenster der Auslöser „berechnete Uhrzeit" (standardmäßig 10) |
| `declenche_<pk>` | `scenarios` | Datum der letzten Auslösung eines zeitgesteuerten Szenarios |
| `valeur_<pk>` | `scenarios` | Letzter von einem Auslöser „bei Wertänderung" gesehener Wert |
| `tache_<name>_minutes` | *Modul* | Taktung einer Aufgabe |
| `tache_<name>_heures` | *Modul* | Uhrzeiten einer Aufgabe |

## Datenmodelle

| Modell | Zweck |
| --- | --- |
| `Module` | Ein erkanntes Modul, aktiviert oder nicht |
| `Setting` | Schlüssel/Wert-Einstellung, nach Modul getrennt |
| `Variable` | Geteilter globaler Wert |
| `Control` | Taster oder Schalter, mit exklusiver Gruppe |
| `Scenario` | JSON-Definition: Auslöser, Bedingungen, Aktionen |
| `DashboardBlock` | Reihenfolge, Breite und Höhe eines Blocks |
| `LogEntry` | Eine Protokollzeile |

# Fehlerbehebung

## Kein Szenario löst aus, keine Daten werden aktualisiert

**Der Scheduler läuft nicht.** Das ist der häufigste und unauffälligste Ausfall: Die Anwendung zeigt alles normal an, die Seiten berechnen ihre Werte im Moment des Aufrufs, und nichts verrät das Problem.

Der Reihe nach prüfen:

1. **Configuration → Karte Scheduler**: Sie zeigt „à l'arrêt" und den Startfehler.
2. **Protokoll**: nach „Scheduler" suchen. Eine Zeile „Scheduler démarré: N Aufgabe(n), M Szenario/Szenarien" muss bei jedem Start erscheinen. Ihr Fehlen bestätigt die Diagnose.

Mögliche Ursachen:

| Ursache | Behebung |
| --- | --- |
| APScheduler fehlt in der Umgebung | `pip install -r requirements.txt` |
| Anders als über `runserver` gestartet | Der Scheduler startet absichtlich nur mit `runserver` |
| Fehler beim Start einer Aufgabe | Die Meldung steht im Protokoll und auf der Karte Scheduler |

## Ein Szenario „berechnete Uhrzeit" löst nie aus

Drei Ursachen, nach Häufigkeit:

1. **Die Quelle rechnet bei jedem Lesen neu.** Liest der Auslöser eine Info, die ihre Berechnung wiederholt, kann die Zieluhrzeit im Tagesverlauf wandern und nie erreicht werden. Den Auslöser auf eine **Variable** setzen, die man selbst befüllt.
2. **Die Zieluhrzeit ist bereits vorbei**, wenn das Szenario angelegt oder der Server neu gestartet wird. Das Nachholfenster deckt nur 10 Minuten ab; darüber hinaus wird die Auslösung auf den nächsten Tag verschoben.
3. **Heute bereits ausgelöst**: nur ein Start pro Tag, auch wenn sich die Uhrzeit danach ändert.

Die Schaltfläche ▶ erlaubt es zu prüfen, ob Bedingungen und Aktionen stimmen, unabhängig vom Auslöser.

## Fehler 429 von Solcast

Das kostenlose Kontingent beträgt **10 Aufrufe pro Tag für das Konto**, und jede Anfrage verbraucht **einen Aufruf je Standort**.

- Der Reiter Solar zeigt die verbleibenden Aufrufe und die Uhrzeit der Wiederaufnahme.
- Nach einem 429 sind die Aufrufe **bis zum nächsten Tag 6 Uhr ausgesetzt**: bewusst so, denn ein erneuter Versuch würde die Ablehnungen nur vervielfachen.
- Prüfen, dass **kein anderer Client denselben Schlüssel verwendet**. Die v1 teilte sich den Schlüssel und verbrauchte dasselbe Kontingent; ihre Aufrufe wurden deaktiviert (`APPELS_API_AUTORISES = False` in `solcast/forecast.py` der v1) und ihre Windows-Aufgaben `update_heater_schedule` entfernt.
- Die Zahl der Uhrzeiten in den Einstellungen des Reiters verringern: Die Kosten erscheinen live unter der Liste.

## Der Aufrufzähler wirkt falsch

Er sieht nur die von der v2 getätigten Aufrufe. Meldet er verbleibende Aufrufe, während Solcast bereits ablehnt, hat ein anderer Client das Kontingent verbraucht. Sobald ein 429 eintrifft, richtet sich der Zähler an dieser Realität aus und zeigt 0 verbleibend.

## Eine Kurve endet mitten am Tag

Die Kurve „real" des Moduls Solar und das Diagramm des Moduls Energie werden aus der **lokalen Envoy-Historie** gespeist, ein Punkt alle 5 Minuten.

- Läuft der Scheduler nicht, wächst die Historie nur, wenn eine Seite angezeigt wird.
- Die Historie wird **täglich zurückgesetzt**: Eine leere Kurve am frühen Morgen ist normal.

## Ein Modul erscheint nicht

| Symptom | Wahrscheinliche Ursache |
| --- | --- |
| Fehlt in der Modulliste | Keine `conf.py`, oder Verzeichnis beginnt mit `_` oder `.` |
| Mit Fehlermeldung gelistet | Ungültige `conf.py` — die Meldung nennt die Ausnahme |
| Angehakt, aber kein Reiter | Der Server wurde nicht neu gestartet: `runserver` erneut starten |
| Reiter da, kein Block | Keine `dashboard/views.py`, oder ein Anzeigefehler (siehe Protokoll) |

## Ein Dashboard-Block ist verschwunden

Eine Ausnahme in einem Block wird vom Unterbau abgefangen: Der Block wird übersprungen und der Fehler ins Protokoll geschrieben, gefiltert nach dem betroffenen Modul. Der Rest des Dashboards wird weiter angezeigt.

## Seltsamer Text erscheint auf einer Seite

Etwas wie `{# … #}` auf dem Bildschirm: ein mehrzeiliger Django-Kommentar. Die Schreibweise `{# … #}` gilt nur für **eine einzige Zeile**; für einen mehrzeiligen Kommentar `{% comment %} … {% endcomment %}` verwenden.

## Eine Änderung an `conf.py` bleibt wirkungslos

Aufgaben und ihre Taktungen werden **beim Start des Schedulers** gelesen. Den Server neu starten. Die Kataloge der Aktionen und Infos werden dagegen bei jedem Öffnen des Szenario-Editors neu gelesen.

## Anordnung des Dashboards zurücksetzen

Modus **Organisieren** → Schaltfläche **Par défaut**. Das löscht nur Reihenfolge, Breiten und Höhen der Blöcke.

## Mit einer leeren Datenbank neu beginnen

Den Server anhalten, `db.sqlite3` umbenennen, dann:

```
python manage.py migrate
python manage.py runserver 0.0.0.0:8100
```

Alles muss neu eingerichtet werden: Module, API-Schlüssel, Bedienelemente, Szenarien. Die alte Datei aufzubewahren erlaubt die Rückkehr.

# Anhang — Screenshots

Die Bilder als **PNG** in `docs/images/` ablegen und dabei genau die folgenden Dateinamen beibehalten: Sie sind in der Dokumentation bereits referenziert.

Ein Platzhalter ohne Bild zeigt in der Markdown-Darstellung einen leeren Rahmen, ohne die Seite zu beschädigen — die Dokumentation bleibt lesbar, während die Screenshots entstehen.

| Datei | Erwarteter Inhalt | Verwendet in |
| --- | --- | --- |
| `01-premier-demarrage.png` | Die Anwendung beim ersten Start, leeres Dashboard | Installation |
| `02-configuration.png` | Der gesamte Reiter Konfiguration | Erste Schritte |
| `02-modules.png` | Der Abschnitt Module, mehrere Module angehakt | Erste Schritte |
| `02-controles.png` | Der Abschnitt Taster & Schalter oder der Anlegedialog | Erste Schritte |
| `02-variables.png` | Der Abschnitt Globale Variablen | Erste Schritte |
| `02-scheduler.png` | Die Karte Scheduler mit Aufträgen und nächster Ausführung | Erste Schritte |
| `02-journal.png` | Der Reiter Protokoll, möglichst mit gesetztem Filter | Erste Schritte |
| `03-tableau-de-bord.png` | Das vollständige Dashboard, tagsüber | Das Dashboard |
| `03-organiser.png` | Modus Organisieren, Auswahlfelder und Höhengriff sichtbar | Das Dashboard |
| `04-editeur.png` | Der Szenario-Editor mit einem vollständigen Szenario | Szenarien |
| `04-conditions.png` | Mehrere durch UND und ODER verknüpfte Bedingungen | Szenarien |
| `05-energie.png` | Der Reiter Energie mit dem Tagesdiagramm | Mitgelieferte Module |
| `05-solaire.png` | Der Reiter Solar, Kontingentzähler und Uhrzeiten sichtbar | Mitgelieferte Module |
| `05-heure-demarrage.png` | Der Reiter Startzeit mit den Einzelheiten der Berechnung | Mitgelieferte Module |
| `06-activation.png` | Der Abschnitt Module beim Anhaken eines neuen Moduls | Ein Modul erstellen |

## Hinweise

- **Den nützlichen Bereich ausschnitthaft aufnehmen** statt des ganzen Bildschirms: Ein Screenshot allein des betreffenden Blocks altert besser und bleibt auf dem Telefon lesbar.
- **Eine Breite von etwa 1400 px** genügt; darüber hinaus wächst die Datei ohne Gewinn.
- Sensible Werte vor der Veröffentlichung ausblenden: API-Schlüssel, Zugangsdaten, die E-Mail-Adresse in den Enphase-Einstellungen.
- Für einen nicht vorgesehenen Screenshot diesen mit `![Beschreibung](images/dateiname.png)` in das Dokument einfügen und diese Tabelle ergänzen.
