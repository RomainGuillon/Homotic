# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Enregistrement des chauffes du ballon, minute par minute.

But : constituer un historique réel « énergie consommée pour passer de X °C
à la consigne », afin de calculer l'heure de démarrage d'après le besoin du
jour plutôt qu'une durée fixe.

Cadence adaptative, et c'est le point important : l'API Cozytouch est
partagée et limitée en nombre de requêtes. Interroger le ballon chaque
minute 24 h/24 pendant deux semaines, c'est 20 000 appels — le compte
finirait bridé. On suit donc à la minute **uniquement pendant la chauffe**,
et on se contente d'une veille espacée le reste du temps. Une chauffe durant
une heure par jour, cela représente environ 350 appels par jour au lieu de
1 440.

Une veille espacée retarderait cependant la détection du démarrage, et donc
fausserait la température de départ. On resserre donc à la minute autour de
l'heure de chauffe prévue, connue à l'avance par la variable
``heure_demarrage_chauffe_eau`` : c'est le meilleur des deux mondes, une
détection immédiate là où la chauffe est attendue, et une veille économe le
reste de la journée.

Réglages (module « chauffe_eau ») :

- ``suivi_actif``            : « oui » / « non » (défaut oui)
- ``suivi_minutes_veille``   : intervalle hors chauffe (défaut 5 min)
- ``suivi_fenetre_avant``    : minutes de guet avant l'heure prévue (défaut 10)
- ``suivi_fenetre_apres``    : minutes de guet après l'heure prévue (défaut 20)
- ``suivi_jours_conserves``  : purge des relevés au-delà (défaut 60 jours)
"""

from datetime import datetime, timedelta

from django.utils import timezone

from core.models import LogEntry
from core.services import get_setting, journal

from . import api

MODULE = "chauffe_eau"


def _reglage_int(cle, defaut):
    try:
        return int(get_setting(cle, module=MODULE, default=defaut))
    except (TypeError, ValueError):
        return defaut


def actif():
    return str(get_setting("suivi_actif", module=MODULE, default="oui")).lower() != "non"


def dans_fenetre_de_guet(maintenant=None):
    """Vrai si l'on est autour de l'heure de chauffe prévue.

    L'heure vient du besoin ``heure_chauffe_prevue`` (voir ``conf.py``) : ce
    module ne sait pas qui calcule cette heure, il sait qu'il lui en faut
    une. Pas d'heure prévue (besoin non branché, chauffe de nuit, calcul
    jamais lancé) : pas de fenêtre, le suivi reste en veille.
    """
    from core.liaisons import lire_besoin

    heure, _err = lire_besoin(MODULE, "heure_chauffe_prevue")
    texte = str(heure or "").strip()
    parts = texte.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return False

    maintenant = maintenant or timezone.localtime()
    prevue = maintenant.replace(
        hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0
    )
    debut = prevue - timedelta(minutes=_reglage_int("suivi_fenetre_avant", 10))
    fin = prevue + timedelta(minutes=_reglage_int("suivi_fenetre_apres", 20))
    return debut <= maintenant <= fin


def _valeur(data, *cles):
    """Première valeur non nulle parmi ``cles``, convertie en nombre."""
    for cle in cles:
        v = data.get(cle)
        if v is None or v == "":
            continue
        try:
            return float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            continue
    return None


def _mesures_brutes(statut):
    """Extrait du statut les grandeurs suivies, depuis les états Overkiz."""
    raw = statut.get("raw") or {}
    return {
        "temp_milieu": _valeur(raw, "modbuslink:MiddleWaterTemperatureState"),
        "temp_bas": _valeur(raw, "core:BottomTankWaterTemperatureState"),
        "consigne": _valeur(raw, "core:TargetDHWTemperatureState",
                            "core:WaterTargetTemperatureState"),
        # Ballon à résistance : toute l'énergie passe par PowerHeatElectrical.
        # PowerHeatPump existe dans le modèle Overkiz (commun à la gamme) mais
        # reste à 0 ici ; on l'enregistre quand même pour le vérifier plutôt
        # que le supposer, et pour ne rien perdre si le matériel change.
        "puissance_elec": _valeur(raw, "modbuslink:PowerHeatElectricalState"),
        "puissance_pac": _valeur(raw, "modbuslink:PowerHeatPumpState"),
        "douches_restantes": _valeur(raw, "core:NumberOfShowerRemainingState"),
        "litres_chauds": _valeur(raw, "core:RemainingHotWaterState"),
    }


def _en_chauffe(statut, mesures):
    """Vrai si le ballon chauffe : statut déclaré, ou puissance non nulle."""
    if api.is_heating(statut.get("heating")):
        return True
    return (mesures["puissance_elec"] or 0) + (mesures["puissance_pac"] or 0) > 0


def tache_suivi():
    """Tâche minute : relève le ballon pendant qu'il chauffe.

    Trois cadences, du plus fin au plus économe :

    - chauffe en cours, ou fenêtre de guet autour de l'heure prévue : chaque
      minute, pour ne rien perdre du démarrage ni de la montée en température ;
    - le reste du temps : une lecture toutes les ``suivi_minutes_veille``
      minutes, qui suffit à repérer une chauffe déclenchée manuellement.

    Entre deux rafraîchissements, le cache du module répond et aucun appel
    n'est envoyé à Cozytouch.
    """
    if not actif():
        return

    from ..models import ChauffeMesure, ChauffeSession

    session = ChauffeSession.objects.filter(fin__isnull=True).order_by("-debut").first()
    if session or dans_fenetre_de_guet():
        ttl = 1
    else:
        ttl = _reglage_int("suivi_minutes_veille", 5)

    statut, _ts, erreur = api.get_status_cached(ttl_minutes=ttl)
    if statut is None:
        return  # déjà journalisé par get_status_cached
    if erreur and session is None:
        return  # valeur périmée servie en secours : rien à enregistrer

    mesures = _mesures_brutes(statut)
    chauffe = _en_chauffe(statut, mesures)
    maintenant = timezone.now()

    if chauffe and session is None:
        session = ChauffeSession.objects.create(
            debut=maintenant,
            temp_debut=mesures["temp_milieu"],
            consigne=mesures["consigne"],
        )
        journal(
            f"Début de chauffe enregistré — départ à {mesures['temp_milieu']} °C, "
            f"consigne {mesures['consigne']} °C",
            module=MODULE,
        )

    if session is None:
        return  # au repos : rien à enregistrer

    ChauffeMesure.objects.create(session=session, quand=maintenant, **mesures)

    if not chauffe:
        _cloturer(session, mesures, maintenant)


def _cloturer(session, mesures, maintenant):
    """Clôt une chauffe et calcule son bilan énergétique."""
    from ..models import ChauffeMesure

    releves = list(session.mesures.order_by("quand"))
    # Intégration : chaque relevé vaut pour l'intervalle qui le sépare du
    # suivant. Un trou (serveur arrêté) ne crée donc pas d'énergie fictive
    # au-delà de 5 minutes.
    elec_wh = pac_wh = 0.0
    for courant, suivant in zip(releves, releves[1:]):
        heures = (suivant.quand - courant.quand).total_seconds() / 3600.0
        heures = min(heures, 5 / 60.0)
        elec_wh += (courant.puissance_elec or 0.0) * heures
        pac_wh += (courant.puissance_pac or 0.0) * heures

    session.fin = maintenant
    session.temp_fin = mesures["temp_milieu"]
    session.duree_min = max(1, round((maintenant - session.debut).total_seconds() / 60))
    session.energie_elec_wh = round(elec_wh, 1)
    session.energie_pac_wh = round(pac_wh, 1)
    session.energie_wh = round(elec_wh + pac_wh, 1)
    session.save()

    detail = ""
    if session.wh_par_degre:
        detail = f" — {session.delta_temp} °C gagnés, {session.wh_par_degre} Wh/°C"
    journal(
        f"Fin de chauffe : {session.duree_min} min, "
        f"{session.energie_wh:.0f} Wh (PAC {session.energie_pac_wh:.0f} / "
        f"résistance {session.energie_elec_wh:.0f}){detail}",
        module=MODULE,
    )
    _purger()


def _purger():
    """Supprime les relevés anciens (les sessions, légères, sont gardées)."""
    from ..models import ChauffeMesure

    jours = _reglage_int("suivi_jours_conserves", 60)
    if jours <= 0:
        return
    limite = timezone.now() - timedelta(days=jours)
    supprimes, _ = ChauffeMesure.objects.filter(quand__lt=limite).delete()
    if supprimes:
        journal(f"{supprimes} relevé(s) de chauffe purgé(s)", module=MODULE)


def sessions_exploitables(minimum_degres=2.0):
    """Chauffes utilisables pour un modèle de consommation."""
    from ..models import ChauffeSession

    retenues = []
    for s in ChauffeSession.objects.filter(fin__isnull=False):
        if s.delta_temp and s.delta_temp >= minimum_degres and s.energie_wh > 0:
            retenues.append(s)
    return retenues


def wh_par_degre_theorique():
    """Énergie théorique pour élever tout le ballon de 1 °C.

    1 litre d'eau demande 1,163 Wh par degré. Pour une cuve de 150 L, cela
    fait environ 175 Wh/°C. Comparer la mesure à ce repère indique quelle
    part du volume est réellement chauffée : nettement en dessous, seule la
    partie haute monte en température.
    """
    statut, _ts, _err = api.get_status_cached(ttl_minutes=24 * 60)
    litres = None
    if statut:
        try:
            litres = float(statut.get("capacity"))
        except (TypeError, ValueError):
            litres = None
    if not litres:
        return None, None
    return round(litres * 1.163), round(litres)


def resume():
    """Synthèse du suivi, pour l'onglet : avancement et premier modèle."""
    from ..models import ChauffeMesure, ChauffeSession

    total = ChauffeSession.objects.filter(fin__isnull=False).count()
    exploitables = sessions_exploitables()
    en_cours = ChauffeSession.objects.filter(fin__isnull=True).first()

    wh_deg = [s.wh_par_degre for s in exploitables if s.wh_par_degre]
    # Un ballon à résistance chauffe à puissance constante : la durée est
    # donc proportionnelle au nombre de degrés à gagner. C'est cette pente
    # (min/°C) qui permettra de viser une heure de FIN de chauffe.
    minutes_deg = [
        s.duree_min / s.delta_temp for s in exploitables if s.delta_temp and s.delta_temp > 0
    ]
    theorique, litres = wh_par_degre_theorique()

    return {
        "actif": actif(),
        "sessions": total,
        "exploitables": len(exploitables),
        "releves": ChauffeMesure.objects.count(),
        "en_cours": en_cours,
        "wh_par_degre_moyen": round(sum(wh_deg) / len(wh_deg)) if wh_deg else None,
        "wh_par_degre_theorique": theorique,
        "litres": litres,
        "minutes_par_degre": round(sum(minutes_deg) / len(minutes_deg), 1) if minutes_deg else None,
        "dernieres": list(ChauffeSession.objects.filter(fin__isnull=False)[:10]),
    }
