# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Appareils Tuya lus et pilotés sur le réseau local, sans passer par le cloud.

Pourquoi ce module existe : l'API Cloud de Tuya est plafonnée (voir l'en-tête
de ``api.py``) et une suspension du quota coupe purement et simplement les
capteurs. En LAN, il n'y a ni quota, ni compte, ni panne d'internet qui
tienne — et les mesures arrivent plus vite.

Topologie de l'installation
---------------------------
Tous les appareils sont des sous-appareils Zigbee derrière une passerelle.
Eux n'ont pas d'adresse IP : on ouvre une connexion vers la passerelle, puis
on interroge chaque appareil par son ``node_id`` (que tinytuya appelle
``cid``) à travers cette même connexion. D'où ``persist=True`` : une seule
socket sert les onze appareils.

Une seule clé locale est donc nécessaire, celle de la passerelle. Elle est
partagée par ses sous-appareils.

Configuration
-------------
Tout vient du ``devices.json`` produit par ``python -m tinytuya wizard`` :
adresse et clé de la passerelle, ``node_id`` et correspondance des « DP »
(les mesures, numérotées, que chaque appareil expose). ``configurer()``
importe ce fichier une fois pour toutes dans les réglages du module ; après
quoi le cloud n'est plus jamais sollicité.
"""

import json

from core.services import get_setting, set_setting

MODULE = "tuya"

# Version du protocole local. 3.4 sur la passerelle multi-mode ; les modèles
# plus anciens parlent 3.3. Le mauvais numéro donne une absence de réponse,
# pas une erreur explicite — d'où le réglage plutôt qu'une constante.
VERSION_DEFAUT = "3.4"

# Codes de mesure reconnus, dans l'ordre de préférence. Mêmes conventions
# que côté cloud : c'est le même appareil qui parle, seul le transport change.
CODES_TEMP = ("va_temperature", "temp_current", "temperature")
CODES_HUM = ("va_humidity", "humidity_value", "humidity")
CODES_BATTERIE = ("battery_percentage", "battery_state", "battery")

# Prises : consommation instantanée et index d'énergie. Non affichés
# aujourd'hui, mais lus dès maintenant — ils ne coûtent rien de plus et
# serviront au volet économies.
CODE_PUISSANCE = "cur_power"
CODE_ENERGIE = "add_ele"


# ----------------------------------------------------------------------
# Import du devices.json produit par le wizard
# ----------------------------------------------------------------------

def _categorie(entree):
    return str(entree.get("category", ""))


def _est_passerelle(entree):
    """La passerelle est le seul appareil joignable directement.

    Elle se reconnaît à l'absence de ``node_id`` — elle n'est le
    sous-appareil de personne — et à sa catégorie (``wg…`` : gateway). Le
    second critère écarte les appareils virtuels « vdevo », qui n'ont pas
    de node_id non plus mais n'existent que dans le cloud.
    """
    return not entree.get("node_id") and _categorie(entree).startswith("wg")


def _mapping_dps(entree):
    """{numéro de DP: (code, échelle)} — pour traduire ce que renvoie l'appareil.

    Tuya transmet des entiers : 253 pour 25,3 °C. L'échelle (nombre de
    décimales) est donnée par le wizard, appareil par appareil : on ne la
    devine pas, on la lit.
    """
    table = {}
    for numero, info in (entree.get("mapping") or {}).items():
        valeurs = info.get("values")
        echelle = valeurs.get("scale", 0) if isinstance(valeurs, dict) else 0
        table[str(numero)] = (str(info.get("code", "")), int(echelle or 0))
    return table


def _genre(table_dps):
    """« sensor », « plug », ou None si l'appareil ne nous intéresse pas."""
    codes = {code for code, _ in table_dps.values()}
    if codes & set(CODES_TEMP) or codes & set(CODES_HUM):
        return "sensor"
    if any(c.startswith("switch") and c != "switch_alarm_sound" for c in codes):
        return "plug"
    return None


def _code_switch(table_dps):
    """Numéro et code du DP qui commande la prise (« 1 », « switch_1 »)."""
    for numero, (code, _echelle) in sorted(table_dps.items(), key=lambda kv: int(kv[0])):
        if code.startswith("switch") and code != "switch_alarm_sound":
            return numero, code
    return None, None


def analyser_devices_json(contenu):
    """Extrait la configuration locale du ``devices.json`` du wizard.

    Retourne (passerelle, appareils). Lève ValueError si le fichier ne
    contient pas de passerelle : sans elle, aucun sous-appareil n'est
    joignable, autant le dire tout de suite plutôt que d'échouer à la
    première collecte.
    """
    donnees = json.loads(contenu) if isinstance(contenu, str) else contenu
    if not isinstance(donnees, list):
        raise ValueError("Fichier inattendu : une liste d'appareils est attendue.")

    passerelle, appareils = None, []
    for entree in donnees:
        table = _mapping_dps(entree)
        if _est_passerelle(entree):
            passerelle = {
                "id": entree.get("id", ""),
                "name": entree.get("name", "Passerelle"),
                "key": entree.get("key", ""),
                "ip": entree.get("ip", ""),
            }
            continue

        node_id = entree.get("node_id")
        genre = _genre(table)
        if not node_id or genre is None:
            continue  # appareil virtuel, ou appareil dont on ne fait rien

        numero_switch, code_switch = _code_switch(table)
        appareils.append({
            "id": entree.get("id", ""),
            "name": entree.get("name", entree.get("id", "")),
            "node_id": node_id,
            "genre": genre,
            "dp_switch": numero_switch,
            "switch_code": code_switch,
            "dps": table,
        })

    if not passerelle or not passerelle["id"]:
        raise ValueError(
            "Aucune passerelle dans ce fichier. Vérifier qu'il s'agit bien du "
            "devices.json produit par « tinytuya wizard »."
        )
    if not appareils:
        raise ValueError("Aucun capteur ni prise exploitable dans ce fichier.")
    return passerelle, appareils


def configurer(contenu, ip=""):
    """Enregistre la configuration locale à partir du devices.json.

    ``ip`` permet de forcer l'adresse de la passerelle quand le fichier ne
    la contient pas (le wizard ne l'écrit qu'après avoir accepté « Poll
    local devices »).
    """
    passerelle, appareils = analyser_devices_json(contenu)
    adresse = (ip or passerelle["ip"] or "").strip()
    if not adresse:
        raise ValueError(
            "Adresse de la passerelle inconnue : la renseigner dans le champ "
            "prévu à côté."
        )

    set_setting("local_gateway_id", passerelle["id"], module=MODULE)
    set_setting("local_gateway_ip", adresse, module=MODULE)
    set_setting("local_key", passerelle["key"], module=MODULE, secret=True)
    set_setting("local_devices", json.dumps(appareils), module=MODULE)
    return passerelle, appareils


# ----------------------------------------------------------------------
# Lecture des réglages
# ----------------------------------------------------------------------

def appareils():
    """Liste des appareils enregistrés, vide si le module n'est pas configuré."""
    brut = get_setting("local_devices", module=MODULE, default="")
    if not brut:
        return []
    try:
        liste = json.loads(brut)
    except ValueError:
        return []
    return liste if isinstance(liste, list) else []


def configured():
    """Vrai si la lecture locale a tout ce qu'il lui faut pour fonctionner."""
    return bool(
        get_setting("local_gateway_id", module=MODULE, default="")
        and get_setting("local_gateway_ip", module=MODULE, default="")
        and get_setting("local_key", module=MODULE, default="")
        and appareils()
    )


def version():
    return get_setting("local_version", module=MODULE, default=VERSION_DEFAUT) or VERSION_DEFAUT


# ----------------------------------------------------------------------
# Décodage (fonctions pures : testables sans réseau ni appareil)
# ----------------------------------------------------------------------

def _valeurs(dps, table_dps):
    """{code: valeur} à partir des DP bruts, échelles appliquées."""
    lisible = {}
    for numero, valeur in (dps or {}).items():
        code, echelle = table_dps.get(str(numero), ("", 0))
        if not code:
            continue
        if echelle and isinstance(valeur, (int, float)) and not isinstance(valeur, bool):
            valeur = valeur / (10 ** echelle)
        lisible[code] = valeur
    return lisible


def _premier(valeurs, codes):
    for code in codes:
        if code in valeurs:
            return valeurs[code]
    return None


def decoder(appareil, dps):
    """Transforme les DP bruts d'un appareil en dictionnaire d'affichage.

    Même forme que ce que produit la collecte cloud, pour que les vues et
    les gabarits n'aient pas à savoir d'où viennent les données.
    """
    valeurs = _valeurs(dps, appareil.get("dps") or {})
    en_ligne = bool(valeurs)

    if appareil.get("genre") == "sensor":
        return {
            "id": appareil["id"],
            "name": appareil["name"],
            "online": en_ligne,
            "temperature": _premier(valeurs, CODES_TEMP),
            "humidity": _premier(valeurs, CODES_HUM),
            "battery": _premier(valeurs, CODES_BATTERIE),
        }

    etat = valeurs.get(appareil.get("switch_code") or "")
    return {
        "id": appareil["id"],
        "name": appareil["name"],
        "online": en_ligne,
        "switch_code": appareil.get("switch_code") or "switch_1",
        "state": bool(etat) if etat is not None else None,
        "power": valeurs.get(CODE_PUISSANCE),
        "energy": valeurs.get(CODE_ENERGIE),
        "schedule_slots": [],
    }


# ----------------------------------------------------------------------
# Accès réseau
# ----------------------------------------------------------------------

def _tinytuya():
    """Import tardif : le module doit rester importable sans la dépendance.

    Sans cela, une installation où tinytuya manque casserait le chargement
    de tout le module Tuya, y compris son mode cloud qui, lui, n'en a pas
    besoin.
    """
    try:
        import tinytuya
    except ImportError as exc:
        raise RuntimeError(
            "tinytuya n'est pas installé : « pip install tinytuya » dans "
            "l'environnement du serveur."
        ) from exc
    return tinytuya


def _passerelle(tinytuya):
    return tinytuya.Device(
        get_setting("local_gateway_id", module=MODULE, default=""),
        address=get_setting("local_gateway_ip", module=MODULE, default=""),
        local_key=get_setting("local_key", module=MODULE, default=""),
        version=float(version()),
        persist=True,
    )


def collecter():
    """(capteurs, prises) lus sur le réseau local.

    Lève RuntimeError si aucun appareil ne répond : l'appelant peut alors
    basculer sur le cloud. Un appareil isolé qui ne répond pas, en
    revanche, est simplement marqué hors ligne — un capteur à pile qui
    dort ne doit pas faire échouer toute la collecte.
    """
    if not configured():
        raise RuntimeError("Lecture locale non configurée (voir le paramétrage).")

    tinytuya = _tinytuya()
    passerelle = _passerelle(tinytuya)

    capteurs, prises, reponses = [], [], 0
    for appareil in appareils():
        try:
            sous = tinytuya.Device(appareil["id"], cid=appareil["node_id"], parent=passerelle)
            etat = sous.status()
            dps = etat.get("dps") if isinstance(etat, dict) else None
        except Exception:
            dps = None

        if dps:
            reponses += 1
        lu = decoder(appareil, dps or {})
        (capteurs if appareil.get("genre") == "sensor" else prises).append(lu)

    if not reponses:
        raise RuntimeError(
            "Passerelle Tuya injoignable en local "
            f"({get_setting('local_gateway_ip', module=MODULE, default='?')})."
        )
    return capteurs, prises


def set_plug(device_id, on):
    """Allume ou éteint une prise en local. Retourne son nom."""
    cible = next((a for a in appareils() if a["id"] == device_id), None)
    if not cible or not cible.get("dp_switch"):
        raise RuntimeError(f"Prise « {device_id} » inconnue en local.")

    tinytuya = _tinytuya()
    sous = tinytuya.Device(device_id, cid=cible["node_id"], parent=_passerelle(tinytuya))
    reponse = sous.set_value(int(cible["dp_switch"]), bool(on))
    if isinstance(reponse, dict) and reponse.get("Error"):
        raise RuntimeError(str(reponse.get("Error")))
    return cible["name"]
