"""API Arlo — mode de surveillance et caméras.

Trois choses apprises en reconnaissance, qui expliquent la forme de ce module :

1. **Les modes passent par l'« emplacement », pas par la station de base.**
   Sur la nouvelle expérience Arlo (v3), la station renvoie
   `mode=unknown, modes=['disarmed','armed']` — inutilisable. C'est
   l'emplacement qui porte les vrais modes : `standby`, `armHome`, `armAway`.
   L'intégration Home Assistant s'y est cassé les dents ; on ne refait pas
   l'erreur.

2. **Le direct n'est pas affichable.** `get_stream()` renvoie une URL RTSPS,
   quel que soit le client annoncé. Aucun navigateur ne la lit. On s'appuie
   donc sur l'instantané : l'URL de la dernière image est signée et valable
   une trentaine d'heures, donc directement utilisable dans un `<img>`.

3. **La connexion coûte cher et réclame un code à deux facteurs.** On garde
   donc un unique client en mémoire pour tout le processus, et la connexion
   se fait dans un fil d'exécution séparé : jamais dans une requête web, qui
   resterait suspendue le temps que le code arrive.

Le code 2FA est demandé dans l'onglet par défaut. `pyaarlo` accepte un objet
de source 2FA quelconque pourvu qu'il expose start/get/stop — c'est ce qui
permet de ne pas avoir à stocker les identifiants d'une boîte mail.
"""

import threading
import time as time_mod
from datetime import datetime, timedelta

from django.conf import settings as django_settings

from core.models import LogEntry
from core.services import get_setting, journal, set_setting

MODULE = "arlo"

# Identifiants Arlo -> libellés affichés. Ce sont les trois modes de la
# nouvelle expérience Arlo, relevés sur l'installation.
MODES = {
    "armAway": "En absence",
    "armHome": "En présence",
    "standby": "En veille",
}
ORDRE_MODES = ["armAway", "armHome", "standby"]

ETAT_TTL_MIN = 5

# Délai laissé à l'utilisateur pour saisir le code 2FA dans l'onglet.
ATTENTE_CODE_S = 300
PAS_ATTENTE_S = 3

# Arlo confirme un changement de mode de façon asynchrone : on lui laisse ce
# délai avant de relire, sinon le cache repartirait sur l'ancien mode.
ATTENTE_CONFIRMATION_S = 4

# États de connexion, tels qu'affichés par l'onglet.
DECONNECTE = "deconnecte"
EN_COURS = "en_cours"
ATTENTE_CODE = "attente_code"
CONNECTE = "connecte"
ERREUR = "erreur"

_client = None                 # instance PyArlo, unique pour le processus
_verrou = threading.Lock()     # sérialise les accès au client
_fil_connexion = None          # fil de la connexion en cours
_fil_photo = None              # fil de l'instantané en cours
_derniere_tentative = 0.0      # monotonic() de la dernière reconnexion tentée

# Délai minimal entre deux reconnexions automatiques. Sans lui, une session
# définitivement perdue ferait redemander un code à chaque battement de la
# tâche : un mail d'Arlo toutes les cinq minutes jusqu'au retour de
# l'utilisateur.
DELAI_RETENTE_S = 900


class ErreurArlo(RuntimeError):
    """Échec fonctionnel côté Arlo."""


# ----------------------------------------------------------------------
# Paramètres
# ----------------------------------------------------------------------

def identifiants():
    return (
        get_setting("identifiant", module=MODULE, default="") or "",
        get_setting("mot_de_passe", module=MODULE, default="") or "",
    )


def configured():
    identifiant, mot_de_passe = identifiants()
    return bool(identifiant and mot_de_passe)


def source_2fa():
    """« onglet » (défaut) ou « imap »."""
    valeur = get_setting("source_2fa", module=MODULE, default="onglet") or "onglet"
    return valeur if valeur in ("onglet", "imap") else "onglet"


def etat_connexion():
    """État de connexion affiché par l'onglet.

    Le réglage est enregistré en base, donc il survit aux redémarrages du
    service — alors que le client, lui, disparaît avec le processus. Un
    « connecté » hérité d'avant un redémarrage ne veut donc rien dire :
    c'est la présence du client en mémoire qui fait foi.
    """
    valeur = get_setting("etat_connexion", module=MODULE, default=DECONNECTE) or DECONNECTE
    if valeur == CONNECTE:
        with _verrou:
            if _client is None:
                return DECONNECTE
    return valeur


def message_connexion():
    return get_setting("message_connexion", module=MODULE, default="") or ""


def _poser_etat(etat, message=""):
    set_setting("etat_connexion", etat, module=MODULE)
    set_setting("message_connexion", message, module=MODULE)


def repertoire_etat():
    """Où pyaarlo garde sa session. Doit survivre aux redémarrages, sinon
    Arlo redemande un code à chaque relance du service."""
    defaut = str(django_settings.BASE_DIR / ".arlo")
    return get_setting("repertoire_etat", module=MODULE, default=defaut) or defaut


def periode_minutes():
    try:
        return max(0, int(get_setting("tache_actualiser_minutes", module=MODULE, default="5")))
    except (TypeError, ValueError):
        return 5


# ----------------------------------------------------------------------
# Source 2FA : le code est saisi dans l'onglet
# ----------------------------------------------------------------------

class CodeDepuisOnglet:
    """Source 2FA branchée sur l'interface web.

    pyaarlo appelle start(), puis get() en boucle jusqu'à obtenir un code.
    On s'en sert pour attendre que l'utilisateur le saisisse dans l'onglet :
    le réglage `code_2fa` sert de boîte aux lettres entre les deux.
    """

    def __init__(self, arlo=None):
        self._arlo = arlo
        self._fin = 0.0

    def start(self):
        set_setting("code_2fa", "", module=MODULE)
        self._fin = time_mod.monotonic() + ATTENTE_CODE_S
        _poser_etat(ATTENTE_CODE, "Arlo a envoyé un code par e-mail — le saisir ci-dessous.")
        journal("Code à deux facteurs attendu", module=MODULE)
        return True

    def get(self):
        while time_mod.monotonic() < self._fin:
            code = (get_setting("code_2fa", module=MODULE, default="") or "").strip()
            if code:
                set_setting("code_2fa", "", module=MODULE)
                return code
            time_mod.sleep(PAS_ATTENTE_S)
        return None  # délai dépassé : pyaarlo abandonnera proprement

    def stop(self):
        set_setting("code_2fa", "", module=MODULE)


def deposer_code(code):
    """Appelé par l'onglet quand l'utilisateur saisit le code."""
    set_setting("code_2fa", str(code).strip(), module=MODULE)


# ----------------------------------------------------------------------
# Connexion
# ----------------------------------------------------------------------

def _construire():
    """Crée le client pyaarlo. Bloquant, et long : jamais dans une requête."""
    import os

    import pyaarlo

    identifiant, mot_de_passe = identifiants()
    if not identifiant or not mot_de_passe:
        raise ErreurArlo("Identifiant ou mot de passe Arlo manquant.")

    repertoire = repertoire_etat()
    os.makedirs(repertoire, exist_ok=True)

    options = {
        "username": identifiant,
        "password": mot_de_passe,
        "storage_dir": repertoire,
        "save_state": True,
        "synchronous_mode": True,
        "tfa_type": "email",
    }
    if source_2fa() == "imap":
        options.update({
            "tfa_source": "imap",
            "tfa_host": get_setting("imap_hote", module=MODULE, default="") or "",
            "tfa_username": get_setting("imap_utilisateur", module=MODULE, default="") or "",
            "tfa_password": get_setting("imap_mot_de_passe", module=MODULE, default="") or "",
        })
    else:
        # pyaarlo accepte un objet quelconque exposant start/get/stop.
        options["tfa_source"] = CodeDepuisOnglet()

    arlo = pyaarlo.PyArlo(**options)
    if not arlo.is_connected:
        raise ErreurArlo("Arlo a refusé la connexion (identifiants ou code).")
    return arlo


def _connecter():
    """Corps du fil de connexion."""
    global _client, _derniere_tentative
    try:
        _poser_etat(EN_COURS, "Connexion à Arlo en cours…")
        arlo = _construire()
    except Exception as exc:
        _poser_etat(ERREUR, str(exc))
        journal(f"Connexion Arlo échouée : {exc}", module=MODULE, level=LogEntry.ERROR)
        return
    with _verrou:
        _client = arlo
    _derniere_tentative = 0.0  # une future perte sera traitée sans attendre
    _poser_etat(CONNECTE, "")
    journal("Connecté à Arlo", module=MODULE)
    try:
        collecter()
    except Exception:
        pass


def demarrer_connexion():
    """Lance la connexion en tâche de fond. Rend la main tout de suite.

    Une requête web ne doit jamais attendre ici : la connexion peut prendre
    plusieurs minutes si Arlo réclame un code.
    """
    global _fil_connexion
    if _fil_connexion is not None and _fil_connexion.is_alive():
        return False
    _fil_connexion = threading.Thread(target=_connecter, name="arlo-connexion", daemon=True)
    _fil_connexion.start()
    return True


def connexion_en_cours():
    return _fil_connexion is not None and _fil_connexion.is_alive()


def deconnecter():
    """Oublie le client en mémoire. La session sur disque, elle, est gardée."""
    global _client
    with _verrou:
        ancien, _client = _client, None
    if ancien is not None:
        try:
            ancien.stop()
        except Exception:
            pass
    _poser_etat(DECONNECTE, "")


def oublier_session():
    """Supprime la session enregistrée : la prochaine connexion redemandera
    un code. À utiliser en cas de changement de compte."""
    import shutil

    deconnecter()
    try:
        shutil.rmtree(repertoire_etat(), ignore_errors=True)
    except Exception:
        pass
    journal("Session Arlo oubliée", module=MODULE)


def _exiger_client():
    with _verrou:
        client = _client
    if client is None:
        raise ErreurArlo(
            "Pas connecté à Arlo. Lancer la connexion depuis l'onglet Caméras."
        )
    return client


# ----------------------------------------------------------------------
# Lecture
# ----------------------------------------------------------------------

def _emplacement(client):
    lieux = client.locations or []
    if not lieux:
        raise ErreurArlo(
            "Aucun emplacement Arlo. Les modes de la nouvelle application "
            "passent par l'emplacement, pas par la station de base."
        )
    voulu = get_setting("emplacement_id", module=MODULE, default="")
    if voulu:
        for lieu in lieux:
            if lieu.device_id == voulu:
                return lieu
    return lieux[0]


def collecter():
    """Lit le mode courant et l'état des caméras. Écrit le cache."""
    import json

    client = _exiger_client()
    lieu = _emplacement(client)
    lieu.update_mode()

    code = lieu.mode or "inconnu"
    cameras = []
    for cam in client.cameras or []:
        cameras.append({
            "id": cam.device_id,
            "nom": cam.name,
            "modele": cam.model_id,
            "etat": cam.state,
            "batterie": cam.battery_level,
            "signal": cam.signal_strength,
            "image": cam.last_image or "",
        })

    etat = {
        "code": code,
        "libelle": MODES.get(code, f"Mode inconnu ({code})"),
        "emplacement": lieu.name,
        "emplacement_id": lieu.device_id,
        "modes_possibles": [
            {"code": c, "libelle": MODES.get(c, c)} for c in ORDRE_MODES
        ],
        "cameras": cameras,
    }
    set_setting("emplacement_id", lieu.device_id, module=MODULE)
    set_setting("cache_etat",
                json.dumps({"ts": datetime.now().isoformat(), "data": etat}),
                module=MODULE)
    return etat


def _lire_cache():
    import json

    brut = get_setting("cache_etat", module=MODULE, default="")
    if not brut:
        return None, None
    try:
        charge = json.loads(brut)
        return charge["data"], datetime.fromisoformat(charge["ts"])
    except (ValueError, KeyError, TypeError):
        return None, None


def etat_cached(force=False):
    """(état, ts, erreur). Ne tente jamais de se connecter : si le client
    n'est pas là, on rend la dernière valeur connue et un message."""
    connu, ts = _lire_cache()
    frais = ts is not None and datetime.now() - ts < timedelta(minutes=ETAT_TTL_MIN)
    if connu is not None and frais and not force:
        return connu, ts, ""

    try:
        etat = collecter()
    except ErreurArlo as exc:
        if connu is not None:
            return connu, ts, f"{exc} Dernière valeur connue."
        return None, None, str(exc)
    except Exception as exc:
        journal(f"Erreur API Arlo : {exc}", module=MODULE, level=LogEntry.ERROR)
        if connu is not None:
            return connu, ts, f"Arlo indisponible ({exc}) — dernière valeur connue."
        return None, None, str(exc)
    return etat, datetime.now(), ""


def mode_courant():
    etat, _ts, _err = etat_cached()
    return (etat or {}).get("code")


# ----------------------------------------------------------------------
# Actions
# ----------------------------------------------------------------------

def changer_mode(code):
    """Bascule l'emplacement dans le mode demandé. Renvoie le libellé."""
    if code not in MODES:
        raise ErreurArlo(f"Mode inconnu : {code}")
    client = _exiger_client()
    lieu = _emplacement(client)

    if lieu.mode == code:
        return f"Déjà {MODES[code].lower()}"

    lieu.mode = code
    time_mod.sleep(ATTENTE_CONFIRMATION_S)
    try:
        lieu.update_mode()
    except Exception:
        pass
    journal(f"Mode caméras -> {MODES[code]}", module=MODULE)
    try:
        collecter()
    except Exception:
        pass
    return MODES[code]


def photo(camera_id=None, attente=45):
    """Demande un instantané et renvoie l'URL de l'image obtenue.

    Bloquant : la caméra doit se réveiller, ce qui prend quelques secondes.
    """
    client = _exiger_client()
    cameras = client.cameras or []
    if not cameras:
        raise ErreurArlo("Aucune caméra sur ce compte.")
    cam = cameras[0]
    if camera_id:
        cam = next((c for c in cameras if c.device_id == camera_id), cam)

    cam.get_snapshot(timeout=attente)
    journal(f"Instantané demandé — {cam.name}", module=MODULE)
    try:
        collecter()
    except Exception:
        pass
    return cam.last_image or ""


def demarrer_photo(camera_id=None):
    """Lance un instantané en tâche de fond.

    Réveiller la caméra prend une trentaine de secondes : une requête web ne
    peut pas attendre ça sans risquer d'être coupée par nginx. L'onglet
    affiche « instantané en cours » et l'image apparaît au rafraîchissement
    suivant.
    """
    global _fil_photo
    if photo_en_cours():
        return False

    def _executer():
        try:
            photo(camera_id)
        except Exception as exc:
            journal(f"Instantané échoué : {exc}", module=MODULE, level=LogEntry.ERROR)
        finally:
            set_setting("photo_en_cours", "", module=MODULE)

    set_setting("photo_en_cours", "1", module=MODULE)
    _fil_photo = threading.Thread(target=_executer, name="arlo-photo", daemon=True)
    _fil_photo.start()
    return True


def photo_en_cours():
    return (get_setting("photo_en_cours", module=MODULE, default="") or "") == "1"


# ----------------------------------------------------------------------
# Tâche périodique
# ----------------------------------------------------------------------

def client_vivant():
    """Le client en mémoire répond-il encore ?

    Un jeton expiré laisse derrière lui un objet qui a l'air normal mais
    dont toutes les requêtes échouent. Sans ce contrôle, la tâche
    continuerait de le tenir pour bon et ne se reconnecterait jamais.
    """
    with _verrou:
        client = _client
    if client is None:
        return False
    try:
        return bool(client.is_connected)
    except Exception:
        return False


def _tenter_reconnexion():
    """Relance la connexion perdue, sans harceler Arlo.

    On ne se fie qu'à la mémoire du processus, jamais à l'état enregistré
    en base : celui-ci survit aux redémarrages du service et vaut encore
    « connecté » alors que le client a disparu avec l'ancien processus.
    C'est précisément ce qui laissait le module hors service — sans la
    moindre trace dans le journal — jusqu'au prochain clic sur
    « Se connecter ».
    """
    global _derniere_tentative

    if connexion_en_cours():
        return False

    maintenant = time_mod.monotonic()
    if _derniere_tentative and maintenant - _derniere_tentative < DELAI_RETENTE_S:
        return False

    _derniere_tentative = maintenant
    journal("Connexion Arlo perdue — reconnexion automatique", module=MODULE,
            level=LogEntry.WARNING)
    return demarrer_connexion()


def tache_actualiser():
    """Rafraîchit le mode et l'état des caméras.

    Relance aussi la connexion si elle a été perdue — un jeton Arlo expire,
    le service redémarre, et sans ce filet les scénarios cesseraient d'agir
    en silence. C'est justement le moment où on en a besoin.
    """
    if not configured():
        return

    with _verrou:
        absent = _client is None
    if absent:
        _tenter_reconnexion()
        return

    etat_cached(force=True)

    # La lecture ci-dessus ne lève jamais : elle se rabat sur la dernière
    # valeur connue. C'est donc ici, et seulement ici, qu'une session morte
    # se remarque.
    if not client_vivant():
        deconnecter()
        _tenter_reconnexion()
