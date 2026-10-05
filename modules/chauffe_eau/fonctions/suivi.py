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

Prévu contre réel : une chauffe qui démarre autour de l'heure prévue est
enregistrée avec ce que la prévision annonçait pour elle (durée, énergie),
lu par le besoin ``prevision_chauffe``. À la clôture, l'écart entre les
deux dit si la prévision était bonne ; ``bilan_prevision`` en fait la
moyenne sur les dernières chauffes, pour savoir dans quel sens la corriger.

Fin de chauffe : la passerelle ne repousse l'état du ballon que toutes les
dix minutes, l'arrêt est donc vu en retard. À la clôture, la durée réelle
est estimée et l'énergie intégrée jusque-là seulement (``releves.py``).
C'est aussi là qu'on note si de l'eau a été tirée en cours de chauffe, et
au démarrage si la chauffe part à l'heure prévue : le modèle de durée
(``modele.py``) n'apprend que des chauffes planifiées et sans tirage.

Entre le calcul et la chauffe : la prévision repose sur la température du
ballon au moment du calcul, parfois des heures avant le départ. Avec la
prévision, on fige donc cette température et l'heure du calcul ;
``bilan_depart`` dit de combien le ballon a bougé entre les deux. Rien
n'est corrigé pour l'instant : on mesure d'abord, pour savoir si l'écart
mérite une correction et laquelle.
"""

from datetime import datetime, timedelta

from django.utils import timezone

from core.models import LogEntry
from core.services import get_setting, journal

from . import api, releves as lecture

MODULE = "chauffe_eau"

# Bilan de la prévision : on juge sur les chauffes récentes (la saison
# déplace la consommation, une moyenne sur l'année ne dirait rien du mois
# en cours), et pas avant d'en avoir quelques-unes.
BILAN_CHAUFFES_MAX = 20
BILAN_CHAUFFES_MIN = 3
# Écart moyen en dessous duquel la prévision est tenue pour juste (%).
BILAN_TOLERANCE_PCT = 10

# Écart de température entre le calcul et le départ : mêmes principes, sur
# les chauffes récentes, et pas de conclusion avant d'en avoir assez.
DEPART_CHAUFFES_MAX = 20
DEPART_CHAUFFES_MIN = 10
# Au-delà, la prévision date d'un autre jour que la chauffe (calcul non
# refait) : l'écart ne dirait rien du délai habituel. Vingt heures laissent
# passer la chauffe de nuit décidée la veille en journée.
DEPART_DELAI_MAX_MIN = 20 * 60


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
    return _autour_de(heure, maintenant or timezone.localtime())


def _autour_de(heure, maintenant):
    """Vrai si ``maintenant`` tombe dans la fenêtre de guet de ``HH:MM``."""
    parts = str(heure or "").strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return False
    try:
        prevue = maintenant.replace(
            hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0
        )
    except ValueError:  # « 25:70 » : pas une heure
        return False
    debut = prevue - timedelta(minutes=_reglage_int("suivi_fenetre_avant", 10))
    fin = prevue + timedelta(minutes=_reglage_int("suivi_fenetre_apres", 20))
    return debut <= maintenant <= fin


def prevision_de_la_chauffe(maintenant=None):
    """Ce que la prévision annonçait pour une chauffe qui démarre maintenant.

    Retourne les champs ``prevu_*`` à poser sur la ``ChauffeSession``, ou
    ``{}`` s'il n'y a rien à comparer : besoin non branché, calcul jamais
    lancé, ou chauffe partie en dehors de la fenêtre de l'heure prévue. Ce
    dernier cas est le plus fréquent — un boost demandé le soir, le ballon
    qui se relance seul après une douche : ces chauffes-là n'ont jamais été
    prévues, les rapprocher de la prévision du jour la ferait passer pour
    fausse.

    Ne lève jamais : une prévision illisible ne doit pas coûter
    l'enregistrement de la chauffe.
    """
    from core.liaisons import lire_besoin

    try:
        prevision, _err = lire_besoin(MODULE, "prevision_chauffe")
        if not isinstance(prevision, dict):
            return {}
        heure = str(prevision.get("heure") or "").strip()
        if not _autour_de(heure, maintenant or timezone.localtime()):
            return {}

        champs = {"prevu_heure": heure[:5]}
        kwh = _valeur(prevision, "besoin_kwh")
        if kwh and kwh > 0:
            champs["prevu_wh"] = round(kwh * 1000, 1)
        duree = _valeur(prevision, "duree_min")
        if duree and duree > 0:
            champs["prevu_duree_min"] = int(round(duree))
        # Sur quoi la prévision reposait : la température du ballon au
        # moment du calcul, et l'instant de ce calcul.
        temperature = _valeur(prevision, "temperature")
        if temperature is not None:
            champs["prevu_temp"] = round(temperature, 1)
        calcule_a = _instant(prevision.get("calcule_a"))
        if calcule_a is not None:
            champs["prevu_calcule_a"] = calcule_a
        return champs
    except Exception:
        return {}


def _instant(texte):
    """Instant lu dans un texte ISO, ou ``None``.

    Un instant sans fuseau est pris en heure locale du serveur : c'est ainsi
    que les modules datent leurs calculs.
    """
    if not texte:
        return None
    try:
        instant = datetime.fromisoformat(str(texte))
    except ValueError:
        return None
    if timezone.is_naive(instant):
        instant = timezone.make_aware(instant)
    return instant


def _duree_lisible(minutes):
    """« 45 min », « 2 h 05 » — pour un délai."""
    minutes = int(round(minutes))
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d}"


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
        # La prévision est figée ici, et pas relue à la clôture : un
        # recalcul lancé pendant la chauffe en donnerait une autre.
        prevu = prevision_de_la_chauffe(timezone.localtime(maintenant))
        session = ChauffeSession.objects.create(
            debut=maintenant,
            temp_debut=mesures["temp_milieu"],
            consigne=mesures["consigne"],
            # Autour de l'heure prévue : c'est une chauffe que le calcul
            # avait à prévoir, elle pourra régler le modèle de durée.
            planifiee=bool(prevu.get("prevu_heure")) or dans_fenetre_de_guet(
                timezone.localtime(maintenant)
            ),
            **prevu,
        )
        annonce = ""
        if prevu.get("prevu_wh"):
            annonce = f" — prévu {prevu['prevu_wh']:.0f} Wh"
            if prevu.get("prevu_duree_min"):
                annonce += f" en {prevu['prevu_duree_min']} min"
        if session.baisse_avant_chauffe is not None:
            annonce += f" — prévision faite avec un ballon à {session.prevu_temp} °C"
            if session.delai_avant_chauffe_min is not None:
                annonce += f", {_duree_lisible(session.delai_avant_chauffe_min)} plus tôt"
        journal(
            f"Début de chauffe enregistré — départ à {mesures['temp_milieu']} °C, "
            f"consigne {mesures['consigne']} °C{annonce}",
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

    # L'arrêt vient d'être vu, mais il a eu lieu avant : la passerelle ne
    # rafraîchit le ballon que toutes les dix minutes. On estime la fin
    # réelle, et l'énergie n'est intégrée que jusque-là.
    lu = lecture.bilan(session.mesures.order_by("quand"), session.debut)

    session.fin = maintenant
    session.temp_fin = mesures["temp_milieu"]
    session.duree_min = max(1, round((maintenant - session.debut).total_seconds() / 60))
    session.duree_estimee_min = lu["duree_estimee_min"]
    session.tirage = lu["tirage"]
    session.energie_elec_wh = lu["elec_wh"]
    session.energie_pac_wh = lu["pac_wh"]
    session.energie_wh = round(lu["elec_wh"] + lu["pac_wh"], 1)
    session.save()

    detail = ""
    if session.duree_estimee_min is not None:
        detail += f" — arrêt réel estimé à {session.duree_estimee_min:.0f} min"
    if session.tirage:
        detail += " — eau tirée pendant la chauffe"
    if session.wh_par_degre:
        detail += f" — {session.delta_temp} °C gagnés, {session.wh_par_degre} Wh/°C"
    if session.ecart_wh is not None:
        detail += (
            f" — prévu {session.prevu_wh:.0f} Wh : écart {session.ecart_wh:+.0f} Wh "
            f"({session.ecart_pct:+d} %)"
        )
        if session.ecart_duree_min is not None:
            detail += (
                f", durée prévue {session.prevu_duree_min} min "
                f"({session.ecart_duree_min:+d} min)"
            )
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


def _moyenne(valeurs):
    return sum(valeurs) / len(valeurs)


def bilan_prevision():
    """La prévision était-elle bonne ? Moyennes sur les dernières chauffes.

    Ne compte que les chauffes comparables (terminées, mesurées, parties à
    l'heure prévue), les ``BILAN_CHAUFFES_MAX`` plus récentes. Énergies en
    kWh, parce que c'est l'unité dans laquelle une prévision se règle.

    ``verdict`` : ``"juste"`` si l'écart moyen tient dans la tolérance,
    ``"trop_haute"`` si le ballon consomme moins que prévu, ``"trop_basse"``
    s'il consomme plus — et ``None`` tant qu'il n'y a pas assez de chauffes
    pour le dire. C'est l'écart **moyen** qui juge : une chauffe isolée
    s'écarte toujours, selon l'eau tirée la veille ; c'est une erreur dans
    le même sens, chauffe après chauffe, qui signale un réglage à revoir.
    """
    from ..models import ChauffeSession

    comparees = list(
        ChauffeSession.objects.filter(
            fin__isnull=False, prevu_wh__gt=0, energie_wh__gt=0
        )[:BILAN_CHAUFFES_MAX]
    )
    bilan = {
        "nombre": len(comparees),
        "minimum": BILAN_CHAUFFES_MIN,
        "tolerance_pct": BILAN_TOLERANCE_PCT,
        "verdict": None,
    }
    if not comparees:
        return bilan

    prevu = _moyenne([s.prevu_wh for s in comparees])
    reel = _moyenne([s.energie_wh for s in comparees])
    bilan.update({
        "prevu_kwh": round(prevu / 1000, 2),
        "reel_kwh": round(reel / 1000, 2),
        "reel_min_kwh": round(min(s.energie_wh for s in comparees) / 1000, 2),
        "reel_max_kwh": round(max(s.energie_wh for s in comparees) / 1000, 2),
        # Écart moyen signé : le biais de la prévision.
        "ecart_kwh": round((reel - prevu) / 1000, 2),
        "ecart_pct": round(100 * (reel - prevu) / prevu),
        # Écart moyen sans le signe : de combien une chauffe s'éloigne du
        # prévu, dans un sens ou dans l'autre.
        "ecart_absolu_kwh": round(
            _moyenne([abs(s.ecart_wh) for s in comparees]) / 1000, 2
        ),
    })

    durees = [s for s in comparees if s.prevu_duree_min]
    if durees:
        duree_prevue = _moyenne([s.prevu_duree_min for s in durees])
        duree_reelle = _moyenne([s.duree_reelle_min for s in durees])
        bilan.update({
            "duree_prevue_min": round(duree_prevue),
            "duree_reelle_min": round(duree_reelle),
            "ecart_duree_min": round(duree_reelle - duree_prevue),
        })

    if len(comparees) >= BILAN_CHAUFFES_MIN:
        if abs(bilan["ecart_pct"]) <= BILAN_TOLERANCE_PCT:
            bilan["verdict"] = "juste"
        else:
            bilan["verdict"] = "trop_haute" if reel < prevu else "trop_basse"
    return bilan


def bilan_depart():
    """De combien le ballon bouge-t-il entre la prévision et la chauffe ?

    La prévision est faite avec la température du ballon au moment du
    calcul. Si le calcul précède la chauffe de plusieurs heures et que de
    l'eau est tirée entre-temps, le ballon part plus froid et la chauffe
    dure plus longtemps que prévu. Ici on le **mesure**, sur les
    ``DEPART_CHAUFFES_MAX`` dernières chauffes parties à l'heure prévue dont
    la prévision portait une température.

    Les écarts se lisent « départ − prévision » : négatif, le ballon est
    parti plus froid qu'au moment du calcul.

    ``minutes`` traduit l'écart moyen en minutes de chauffe, avec la pente
    du modèle de durée : c'est ce qui dit si l'écart vaut une correction.
    ``suffisant`` : assez de chauffes pour s'y fier.
    """
    from ..models import ChauffeSession

    candidates = ChauffeSession.objects.filter(
        planifiee=True, prevu_temp__isnull=False, temp_debut__isnull=False,
        prevu_calcule_a__isnull=False,
    )[: DEPART_CHAUFFES_MAX * 3]
    retenues = [
        s for s in candidates
        if 0 <= s.delai_avant_chauffe_min <= DEPART_DELAI_MAX_MIN
    ][:DEPART_CHAUFFES_MAX]

    bilan = {
        "nombre": len(retenues),
        "minimum": DEPART_CHAUFFES_MIN,
        "suffisant": len(retenues) >= DEPART_CHAUFFES_MIN,
    }
    if not retenues:
        return bilan

    ecarts = [-s.baisse_avant_chauffe for s in retenues]
    delais = [s.delai_avant_chauffe_min for s in retenues]
    moyen = _moyenne(ecarts)
    bilan.update({
        "ecart_moyen": round(moyen, 1),
        "ecart_min": round(min(ecarts), 1),
        "ecart_max": round(max(ecarts), 1),
        "delai_moyen": _duree_lisible(_moyenne(delais)),
        "delai_min": _duree_lisible(min(delais)),
        "delai_max": _duree_lisible(max(delais)),
        "minutes": None,
    })
    try:
        from . import modele

        m = modele.modele()
        if m["fiable"]:
            # Pente négative : un ballon plus froid (écart négatif) allonge.
            bilan["minutes"] = round(m["pente"] * moyen)
    except Exception:
        pass  # sans modèle, l'écart reste exprimé en degrés
    return bilan


def _fournisseur_prevision():
    """Libellé du module branché sur ``prevision_chauffe``, ou ``""``.

    Sert seulement à dire où corriger la prévision : ce module ne sait pas
    qui la calcule, il lit le branchement fait dans Configuration.
    """
    from core.liaisons import liaison
    from core.models import Module

    nom = liaison(MODULE, "prevision_chauffe").partition(".")[0]
    if not nom:
        return ""
    module = Module.objects.filter(name=nom).first()
    return module.label if module else nom


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
        s.duree_reelle_min / s.delta_temp
        for s in exploitables if s.delta_temp and s.delta_temp > 0
    ]
    theorique, litres = wh_par_degre_theorique()

    dernieres = list(ChauffeSession.objects.filter(fin__isnull=False)[:10])
    for s in dernieres:
        # Pour la couleur de la ligne : même tolérance que le bilan.
        s.ecart_juste = (
            s.ecart_pct is not None and abs(s.ecart_pct) <= BILAN_TOLERANCE_PCT
        )
        delai = s.delai_avant_chauffe_min
        s.delai_lisible = _duree_lisible(delai) if delai is not None and delai >= 0 else ""

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
        "dernieres": dernieres,
        "bilan": bilan_prevision(),
        "fournisseur_prevision": _fournisseur_prevision(),
        "modele": _modele_pour_l_onglet(),
        "depart": bilan_depart(),
    }


def _modele_pour_l_onglet():
    """Le modèle de durée et son estimation du moment, ou une erreur lisible.

    Ne lève jamais : le modèle est un plus, il ne doit pas coûter
    l'affichage du suivi.
    """
    try:
        from . import modele

        return {"modele": modele.modele(), "estimation": modele.estimation()}
    except Exception as exc:
        return {"erreur": str(exc)}
