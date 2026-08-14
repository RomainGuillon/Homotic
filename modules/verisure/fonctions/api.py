"""API Verisure France (Securitas Direct) — lecture de l'état de l'alarme.

Module volontairement **en lecture seule** : aucune fonction d'armement ni de
désarmement n'existe ici. Les identifiants stockés peuvent techniquement
désarmer l'alarme (l'API ne réclame aucun code PIN côté serveur), mais le
code pour le faire n'est pas écrit — c'est le seul cloisonnement dont on
dispose, Verisure ne proposant pas de rôle « lecture seule ».

Protocole
---------
API GraphQL de l'app mobile, non officielle. Requêtes et en-têtes repris de
`guerrerotook/securitas-direct-new-api`, le seul projet qui fonctionne en
France (l'intégration officielle Home Assistant, elle, n'y fonctionne pas).

Quatre choses non évidentes, chacune payée d'un aller-retour de mise au point :

1. L'authentification à deux facteurs valide un **appareil**, pas une session.
   Après `mkValidateDevice`, il faut refaire un `mkLoginToken` pour obtenir un
   jeton exploitable — la validation seule renvoie souvent `hash: null`.
2. L'identité d'appareil (`device_id`, `uuid`, `indigitall`) doit rester
   **stable** : c'est elle que Verisure marque comme de confiance. La
   régénérer redemande un SMS.
3. Toute lecture liée à une installation exige un en-tête `X-Capabilities`,
   un JWT obtenu par la requête `Srv`. Sans lui :
   « accessPermissions: Missing capabilities data ».
4. Il n'y a pas de quota mensuel, mais un pare-feu applicatif qui répond 403
   si les requêtes s'enchaînent. D'où la période de 180 s par défaut et les
   caches ci-dessous.

Coût en régime établi : **1 requête par cycle**. Le jeton de session et le
jeton de capacités portent leur propre expiration, l'installation ne change
jamais.
"""

import base64
import json
import secrets
import time as time_mod
from datetime import datetime, timedelta
from uuid import uuid4

import requests

from core.models import LogEntry
from core.services import get_setting, journal, set_setting

MODULE = "verisure"

PAYS_DEFAUT = "FR"
LANGUES = {"FR": "fr", "ES": "es", "IT": "it", "PT": "pt", "GB": "en", "IE": "en"}
DOMAINES = {
    "FR": "https://customers.securitasdirect.fr/owa-api/graphql",
    "ES": "https://customers.verisure.es/owa-api/graphql",
    "IT": "https://customers.verisure.it/owa-api/graphql",
    "PT": "https://customers.verisure.pt/owa-api/graphql",
    "GB": "https://customers.verisure.co.uk/owa-api/graphql",
    "IE": "https://customers.verisure.ie/owa-api/graphql",
}

CALLBY = "OWA_10"
PREFIXE_ID = "OWA_______________"

# On se présente comme l'app Android : le backend refuse les clients inconnus.
APPAREIL = {"brand": "samsung", "name": "SM-S901U", "os": "12", "version": "10.102.0"}

TIMEOUT = 30
ETAT_TTL_MIN = 3        # fraîcheur de l'état de l'alarme
MARGE_EXPIRATION_S = 120  # on renouvelle un jeton un peu avant son échéance

# Codes protocole -> (clé stable, libellé affiché).
#
# L'installation de Romain n'a qu'un mode partiel et pas de périphérique : les
# libellés sont les siens. Les autres codes restent mappés pour qu'un ajout de
# détecteurs ne fasse pas tomber le module sur un code inconnu.
CODES = {
    "D": ("desarmee", "Désarmée"),
    "P": ("partielle", "Partielle"),
    "Q": ("partielle", "Partielle"),
    "T": ("totale", "Totale"),
    "E": ("peripherique", "Périmètre seul"),
    "B": ("partielle_peri", "Partielle + périmètre"),
    "C": ("partielle_peri", "Partielle + périmètre"),
    "A": ("totale_peri", "Totale + périmètre"),
    "X": ("annexe", "Annexe seule"),
    "R": ("partielle_annexe", "Partielle + annexe"),
    "S": ("partielle_annexe", "Partielle + annexe"),
    "O": ("totale_annexe", "Totale + annexe"),
}

# Jeton de session, en mémoire du processus (l'app tourne en --noreload).
_session = {"hash": None, "expire": 0.0}


class ErreurVerisure(RuntimeError):
    """Échec fonctionnel de l'API."""


class Besoin2FA(ErreurVerisure):
    """Le compte réclame une validation d'appareil par SMS."""


class BloqueParWAF(ErreurVerisure):
    """Pare-feu applicatif : ralentir, ne pas réessayer tout de suite."""


# ----------------------------------------------------------------------
# Requêtes GraphQL
# ----------------------------------------------------------------------

Q_LOGIN = (
    "mutation mkLoginToken($user: String!, $password: String!, $id: String!, "
    "$country: String!, $lang: String!, $callby: String!, $idDevice: String!, "
    "$idDeviceIndigitall: String!, $deviceType: String!, $deviceVersion: "
    "String!, $deviceResolution: String!, $deviceName: String!, $deviceBrand: "
    "String!, $deviceOsVersion: String!, $uuid: String!) { xSLoginToken(user: "
    "$user, password: $password, country: $country, lang: $lang, callby: "
    "$callby, id: $id, idDevice: $idDevice, idDeviceIndigitall: "
    "$idDeviceIndigitall, deviceType: $deviceType, deviceVersion: "
    "$deviceVersion, deviceResolution: $deviceResolution, deviceName: "
    "$deviceName, deviceBrand: $deviceBrand, deviceOsVersion: "
    "$deviceOsVersion, uuid: $uuid) { __typename res msg hash refreshToken "
    "legals changePassword needDeviceAuthorization mainUser } }"
)

Q_REFRESH = (
    "mutation RefreshLogin($refreshToken: String!, $id: String!, $country: "
    "String!, $lang: String!, $callby: String!, $idDevice: String!, "
    "$idDeviceIndigitall: String!, $deviceType: String!, $deviceVersion: "
    "String!, $deviceResolution: String!, $deviceName: String!, $deviceBrand: "
    "String!, $deviceOsVersion: String!, $uuid: String!) {\n"
    "  xSRefreshLogin(refreshToken: $refreshToken, id: $id, country: $country, "
    "lang: $lang, callby: $callby, idDevice: $idDevice, idDeviceIndigitall: "
    "$idDeviceIndigitall, deviceType: $deviceType, deviceVersion: "
    "$deviceVersion, deviceResolution: $deviceResolution, deviceName: "
    "$deviceName, deviceBrand: $deviceBrand, deviceOsVersion: "
    "$deviceOsVersion, uuid: $uuid) {\n    __typename\n    res\n    msg\n"
    "    hash\n    refreshToken\n    legals\n    changePassword\n"
    "    needDeviceAuthorization\n    mainUser\n  }\n}"
)

Q_VALIDER_APPAREIL = (
    "mutation mkValidateDevice($idDevice: String, "
    "$idDeviceIndigitall: String, "
    "$uuid: String, $deviceName: String, $deviceBrand: String, "
    "$deviceOsVersion: String, $deviceVersion: String) {\n"
    "  xSValidateDevice(idDevice: $idDevice, idDeviceIndigitall: "
    "$idDeviceIndigitall, uuid: $uuid, deviceName: $deviceName, deviceBrand: "
    "$deviceBrand, deviceOsVersion: $deviceOsVersion, deviceVersion: "
    "$deviceVersion) {\n    res\n    msg\n    hash\n    refreshToken\n"
    "    legals\n  }\n}\n"
)

Q_ENVOI_OTP = (
    "mutation mkSendOTP($recordId: Int!, $otpHash: String!) {\n"
    "  xSSendOtp(recordId: $recordId, otpHash: $otpHash) {\n    res\n    msg\n"
    "  }\n}\n"
)

Q_INSTALLATIONS = (
    "query mkInstallationList {\n  xSInstallations {\n    installations {\n"
    "      numinst\n      alias\n      panel\n      type\n    }\n  }\n}\n"
)

Q_SERVICES = (
    "query Srv($numinst: String!, $uuid: String) {\n"
    "  xSSrv(numinst: $numinst, uuid: $uuid) {\n    res\n    msg\n"
    "    installation {\n      numinst\n      role\n      alias\n"
    "      status\n      panel\n      capabilities\n    }\n  }\n}"
)

Q_ETAT = (
    "query Status($numinst: String!) {\n  xSStatus(numinst: $numinst) {\n"
    "    status\n    timestampUpdate\n    wifiConnected\n    exceptions {\n"
    "      status\n      deviceType\n      alias\n    }\n  }\n}"
)


# ----------------------------------------------------------------------
# Paramètres et identité d'appareil
# ----------------------------------------------------------------------

def identifiants():
    return (
        get_setting("identifiant", module=MODULE, default="") or "",
        get_setting("mot_de_passe", module=MODULE, default="") or "",
    )


def pays():
    valeur = (get_setting("pays", module=MODULE, default=PAYS_DEFAUT) or PAYS_DEFAUT).upper()
    return valeur if valeur in DOMAINES else PAYS_DEFAUT


def configured():
    """Identifiant renseigné, et de quoi s'authentifier (mot de passe ou jeton)."""
    identifiant, mot_de_passe = identifiants()
    jeton = get_setting("refresh_token", module=MODULE, default="")
    return bool(identifiant and (mot_de_passe or jeton))


def _identite():
    """Identité d'appareil, créée une fois puis figée.

    La régénérer invaliderait la confiance accordée après la 2FA et
    provoquerait un nouveau SMS — d'où l'écriture en base au premier appel.
    """
    device_id = get_setting("device_id", module=MODULE, default="")
    if not device_id:
        device_id = secrets.token_urlsafe(16) + ":APA91b" + secrets.token_urlsafe(130)[0:134]
        set_setting("device_id", device_id, module=MODULE)
    device_uuid = get_setting("device_uuid", module=MODULE, default="")
    if not device_uuid:
        device_uuid = str(uuid4()).replace("-", "")[0:16]
        set_setting("device_uuid", device_uuid, module=MODULE)
    indigitall = get_setting("device_indigitall", module=MODULE, default="")
    if not indigitall:
        indigitall = str(uuid4())
        set_setting("device_indigitall", indigitall, module=MODULE)
    return device_id, device_uuid, indigitall


def reinitialiser_appareil():
    """Oublie l'identité et les jetons : le prochain accès repassera par la 2FA.

    À utiliser quand on change de compte, ou si Verisure a révoqué l'appareil.
    """
    for cle in ("device_id", "device_uuid", "device_indigitall", "refresh_token",
                "capabilities", "capabilities_exp", "otp_hash", "otp_phones"):
        set_setting(cle, "", module=MODULE)
    _session.update({"hash": None, "expire": 0.0})


# ----------------------------------------------------------------------
# Client HTTP
# ----------------------------------------------------------------------

class Client:
    def __init__(self):
        self.identifiant, self.mot_de_passe = identifiants()
        self.pays = pays()
        self.langue = LANGUES.get(self.pays, "en")
        self.url = DOMAINES[self.pays]
        self.device_id, self.uuid, self.indigitall = _identite()
        self.refresh_token = get_setting("refresh_token", module=MODULE, default="") or ""
        self.defi_otp = None
        self.apollo = secrets.token_hex(64)
        self.session = requests.Session()

    # -- plomberie -----------------------------------------------------

    @property
    def jeton(self):
        return _session["hash"] if _session["expire"] > time_mod.time() else None

    def _id_requete(self):
        n = datetime.now()
        return (PREFIXE_ID + self.identifiant + "_______________"
                + f"{n.year}{n.month}{n.day}{n.hour}{n.minute}{n.microsecond}")

    def _variables_appareil(self):
        return {
            "idDevice": self.device_id,
            "idDeviceIndigitall": self.indigitall,
            "deviceType": "",
            "deviceVersion": APPAREIL["version"],
            "deviceResolution": "",
            "deviceName": APPAREIL["name"],
            "deviceBrand": APPAREIL["brand"],
            "deviceOsVersion": APPAREIL["os"],
        }

    def _entetes(self, operation, numinst=None, panel=None, capacites=None):
        entetes = {
            "app": json.dumps({"appVersion": APPAREIL["version"], "origin": "native"}),
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                " AppleWebKit/537.36 (KHTML, like Gecko)"
                " Chrome/102.0.5005.124 Safari/537.36"
                " Edg/102.0.1245.41"
            ),
            "X-APOLLO-OPERATION-ID": self.apollo,
            "X-APOLLO-OPERATION-NAME": operation,
            "extension": '{"mode":"full"}',
            "content-type": "application/json; charset=utf-8",
        }
        if numinst:
            entetes["numinst"] = numinst
            entetes["panel"] = panel or ""
            if capacites:
                entetes["X-Capabilities"] = capacites

        auth = {
            "loginTimestamp": _session.get("horodatage", 0),
            "user": self.identifiant,
            "id": self._id_requete(),
            "country": self.pays,
            "lang": self.langue,
            "callby": CALLBY,
        }
        if operation in ("mkValidateDevice", "RefreshLogin", "mkSendOTP"):
            # Ces trois opérations exigent un bloc auth aux champs vides.
            entetes["auth"] = json.dumps({**auth, "hash": "", "refreshToken": ""})
        elif self.jeton:
            entetes["auth"] = json.dumps({**auth, "hash": self.jeton})

        if self.defi_otp:
            entetes["security"] = json.dumps({
                "token": self.defi_otp[1], "type": "OTP", "otpHash": self.defi_otp[0],
            })
        return entetes

    def appel(self, operation, requete, variables, numinst=None, panel=None, capacites=None):
        """POST GraphQL. Renvoie le JSON brut : le flux 2FA passe par une
        réponse d'erreur qui porte le défi, on ne peut donc pas lever ici."""
        try:
            r = self.session.post(
                self.url,
                headers=self._entetes(operation, numinst, panel, capacites),
                json={"operationName": operation, "variables": variables, "query": requete},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ErreurVerisure(f"Réseau indisponible : {exc}") from exc

        if r.status_code == 403 and "_Incapsula_Resource" in r.text:
            raise BloqueParWAF(
                "Bloqué par le pare-feu de Verisure. Espacer les lectures "
                "(augmenter la période dans le paramétrage)."
            )
        try:
            return r.json()
        except ValueError as exc:
            raise ErreurVerisure(f"Réponse illisible (HTTP {r.status_code})") from exc


def message_erreur(reponse):
    """Message lisible. Le backend renvoie souvent un message vide : on
    remonte alors la raison ou la réponse brute, sinon on ne sait rien."""
    erreurs = reponse.get("errors") or []
    if not erreurs:
        return json.dumps(reponse)[:300]
    premiere = erreurs[0] if isinstance(erreurs, list) else erreurs
    message = (premiere.get("message") or "").strip()
    donnees = premiere.get("data") or {}
    raison = donnees.get("reason") or ""
    code = donnees.get("err") or donnees.get("auth-code") or ""
    morceaux = [m for m in (message, raison) if m]
    if code:
        morceaux.append(f"code {code}")
    return " — ".join(morceaux) if morceaux else json.dumps(reponse)[:300]


def _bloc(reponse, champ):
    valeur = (reponse.get("data") or {}).get(champ)
    if valeur is None:
        raise ErreurVerisure(message_erreur(reponse))
    return valeur


def _expiration_jwt(jeton, defaut_s):
    """Échéance d'un JWT, sans vérifier la signature : les jetons viennent
    d'un endpoint HTTPS de confiance, on ne s'en sert que pour du cache."""
    try:
        charge = jeton.split(".")[1]
        charge += "=" * (-len(charge) % 4)
        exp = json.loads(base64.urlsafe_b64decode(charge)).get("exp")
        if exp:
            return float(exp) - MARGE_EXPIRATION_S
    except Exception:
        pass
    return time_mod.time() + defaut_s


def capacites_lisibles(jeton, numinst):
    """Liste des droits portés par le jeton de capacités, pour l'onglet."""
    if not jeton:
        return []
    try:
        charge = jeton.split(".")[1]
        charge += "=" * (-len(charge) % 4)
        donnees = json.loads(base64.urlsafe_b64decode(charge))
    except Exception:
        return []
    for entree in donnees.get("installations") or []:
        if str(entree.get("ins", "")) == str(numinst):
            return sorted(entree.get("cap") or [])
    return []


# ----------------------------------------------------------------------
# Authentification
# ----------------------------------------------------------------------

def _memoriser_session(client, bloc):
    if bloc.get("hash"):
        _session["hash"] = bloc["hash"]
        _session["expire"] = _expiration_jwt(bloc["hash"], 3600)
    _session["horodatage"] = int(datetime.now().timestamp() * 1000)
    if bloc.get("refreshToken"):
        client.refresh_token = bloc["refreshToken"]
        set_setting("refresh_token", bloc["refreshToken"], module=MODULE, secret=True)


def _rafraichir(client):
    if not client.refresh_token:
        return False
    variables = {
        "refreshToken": client.refresh_token,
        "id": client._id_requete(),
        "uuid": client.uuid,
        "country": client.pays,
        "lang": client.langue,
        "callby": CALLBY,
        **client._variables_appareil(),
    }
    reponse = client.appel("RefreshLogin", Q_REFRESH, variables)
    if "errors" in reponse:
        return False
    bloc = (reponse.get("data") or {}).get("xSRefreshLogin") or {}
    if bloc.get("res") != "OK" or not bloc.get("hash"):
        return False
    _memoriser_session(client, bloc)
    return True


def _connexion(client):
    """Login par mot de passe. Lève Besoin2FA si l'appareil n'est pas validé."""
    if not client.mot_de_passe:
        raise ErreurVerisure(
            "Aucun mot de passe enregistré et le jeton a expiré : "
            "ressaisir le mot de passe dans le paramétrage."
        )
    variables = {
        "user": client.identifiant,
        "password": client.mot_de_passe,
        "id": client._id_requete(),
        "country": client.pays,
        "lang": client.langue,
        "callby": CALLBY,
        "uuid": client.uuid,
        **client._variables_appareil(),
    }
    reponse = client.appel("mkLoginToken", Q_LOGIN, variables)
    bloc = (reponse.get("data") or {}).get("xSLoginToken") or {}

    if bloc.get("needDeviceAuthorization"):
        raise Besoin2FA("Validation par SMS requise pour cet appareil.")

    if "errors" in reponse:
        donnees = (reponse["errors"][0].get("data") or {}) if reponse["errors"] else {}
        if str(donnees.get("err", "")) == "60052":
            raise ErreurVerisure(
                "Compte bloqué par Verisure (trop de tentatives). "
                "Se connecter depuis l'app mobile pour le débloquer."
            )
        if (reponse.get("data") or {}).get("xSLoginToken", {}).get("needDeviceAuthorization"):
            raise Besoin2FA("Validation par SMS requise pour cet appareil.")
        raise ErreurVerisure(message_erreur(reponse))

    _memoriser_session(client, bloc)


def _authentifier():
    """Client authentifié : jeton en mémoire, sinon rafraîchissement, sinon
    mot de passe. Ordre choisi pour ne toucher au mot de passe qu'en dernier
    recours — trois échecs bloquent le compte."""
    client = Client()
    if not client.identifiant:
        raise ErreurVerisure("Identifiant Verisure manquant.")
    if client.jeton:
        return client
    if _rafraichir(client):
        return client
    _connexion(client)
    return client


# ----------------------------------------------------------------------
# Enrôlement 2FA (piloté par l'onglet)
# ----------------------------------------------------------------------

def demarrer_2fa():
    """Demande le défi OTP. Renvoie la liste [(id, numéro masqué), ...]."""
    client = Client()
    variables = {
        "idDevice": client.device_id,
        "idDeviceIndigitall": client.indigitall,
        "uuid": client.uuid,
        "deviceName": APPAREIL["name"],
        "deviceBrand": APPAREIL["brand"],
        "deviceOsVersion": APPAREIL["os"],
        "deviceVersion": APPAREIL["version"],
    }
    reponse = client.appel("mkValidateDevice", Q_VALIDER_APPAREIL, variables)
    erreurs = reponse.get("errors") or []
    donnees = (erreurs[0].get("data") or {}) if erreurs else {}
    otp_hash = donnees.get("auth-otp-hash")
    telephones = [(t["id"], t["phone"]) for t in donnees.get("auth-phones", [])]
    if not otp_hash:
        raise ErreurVerisure("Défi 2FA absent : " + message_erreur(reponse))
    set_setting("otp_hash", otp_hash, module=MODULE, secret=True)
    set_setting("otp_phones", json.dumps(telephones), module=MODULE)
    return telephones


def telephones_2fa():
    brut = get_setting("otp_phones", module=MODULE, default="")
    try:
        return [tuple(t) for t in json.loads(brut or "[]")]
    except (ValueError, TypeError):
        return []


def envoyer_sms(id_telephone):
    client = Client()
    otp_hash = get_setting("otp_hash", module=MODULE, default="")
    if not otp_hash:
        raise ErreurVerisure("Défi 2FA expiré : relancer la validation.")
    reponse = client.appel("mkSendOTP", Q_ENVOI_OTP,
                           {"recordId": int(id_telephone), "otpHash": otp_hash})
    if "errors" in reponse:
        raise ErreurVerisure("Envoi du SMS refusé : " + message_erreur(reponse))
    return _bloc(reponse, "xSSendOtp").get("res")


def valider_code(code_sms):
    """Valide l'appareil puis **refait un login** : la validation 2FA ne rend
    pas de jeton de session exploitable (souvent hash: null)."""
    client = Client()
    otp_hash = get_setting("otp_hash", module=MODULE, default="")
    if not otp_hash:
        raise ErreurVerisure("Défi 2FA expiré : relancer la validation.")

    client.defi_otp = (otp_hash, str(code_sms).strip())
    try:
        variables = {
            "idDevice": client.device_id,
            "idDeviceIndigitall": client.indigitall,
            "uuid": client.uuid,
            "deviceName": APPAREIL["name"],
            "deviceBrand": APPAREIL["brand"],
            "deviceOsVersion": APPAREIL["os"],
            "deviceVersion": APPAREIL["version"],
        }
        reponse = client.appel("mkValidateDevice", Q_VALIDER_APPAREIL, variables)
    finally:
        client.defi_otp = None

    if "errors" in reponse:
        raise ErreurVerisure("Code SMS refusé : " + message_erreur(reponse))
    _memoriser_session(client, _bloc(reponse, "xSValidateDevice"))

    set_setting("otp_hash", "", module=MODULE)
    set_setting("otp_phones", "", module=MODULE)

    _connexion(client)  # l'appareil est désormais de confiance : login direct
    journal("Appareil validé par SMS", module=MODULE)
    return True


# ----------------------------------------------------------------------
# Installation et capacités
# ----------------------------------------------------------------------

def installation(client=None, force=False):
    """(numinst, panel, alias). Mise en cache : une installation ne bouge pas."""
    numinst = get_setting("numinst", module=MODULE, default="")
    panel = get_setting("panel", module=MODULE, default="")
    alias = get_setting("alias", module=MODULE, default="")
    if numinst and panel and not force:
        return numinst, panel, alias

    client = client or _authentifier()
    reponse = client.appel("mkInstallationList", Q_INSTALLATIONS, {})
    if "errors" in reponse:
        raise ErreurVerisure("Liste des installations refusée : " + message_erreur(reponse))
    liste = _bloc(reponse, "xSInstallations").get("installations") or []
    if not liste:
        raise ErreurVerisure("Aucune installation sur ce compte.")

    choisie = liste[0]
    voulue = get_setting("numinst_choisi", module=MODULE, default="")
    if voulue:
        choisie = next((i for i in liste if str(i.get("numinst")) == voulue), choisie)

    numinst = str(choisie.get("numinst") or "")
    panel = str(choisie.get("panel") or "")
    alias = str(choisie.get("alias") or numinst)
    set_setting("numinst", numinst, module=MODULE)
    set_setting("panel", panel, module=MODULE)
    set_setting("alias", alias, module=MODULE)
    return numinst, panel, alias


def _capacites(client, numinst, panel, force=False):
    """Jeton X-Capabilities, obligatoire pour lire l'état. Mis en cache
    jusqu'à son expiration : sans ça, on doublerait le nombre de requêtes."""
    jeton = get_setting("capabilities", module=MODULE, default="")
    try:
        expire = float(get_setting("capabilities_exp", module=MODULE, default="0") or 0)
    except (TypeError, ValueError):
        expire = 0.0
    if jeton and expire > time_mod.time() and not force:
        return jeton

    reponse = client.appel("Srv", Q_SERVICES,
                           {"numinst": numinst, "uuid": client.uuid},
                           numinst=numinst, panel=panel)
    if "errors" in reponse:
        raise ErreurVerisure("Récupération des capacités refusée : " + message_erreur(reponse))
    bloc = (_bloc(reponse, "xSSrv") or {}).get("installation") or {}
    jeton = bloc.get("capabilities") or ""
    if not jeton:
        raise ErreurVerisure("Aucun jeton de capacités renvoyé par l'API.")
    set_setting("capabilities", jeton, module=MODULE, secret=True)
    set_setting("capabilities_exp", str(_expiration_jwt(jeton, 3600)), module=MODULE)
    set_setting("role", str(bloc.get("role") or ""), module=MODULE)
    return jeton


# ----------------------------------------------------------------------
# Lecture de l'état
# ----------------------------------------------------------------------

def _interpreter(brut, alias):
    code = (brut.get("status") or "").strip().upper()
    cle, libelle = CODES.get(code, ("inconnu", f"Code inconnu ({code or '?'})"))
    horodatage = None
    try:
        horodatage = datetime.fromtimestamp(int(brut["timestampUpdate"]) / 1000).isoformat()
    except (KeyError, TypeError, ValueError):
        pass
    return {
        "code": code,
        "cle": cle,
        "libelle": libelle,
        "armee": cle != "desarmee",
        "change_le": horodatage,
        "wifi": bool(brut.get("wifiConnected")),
        "anomalies": [
            {"alias": e.get("alias"), "type": e.get("deviceType"), "statut": e.get("status")}
            for e in (brut.get("exceptions") or [])
        ],
        "alias": alias,
    }


def collecter():
    """Une lecture réelle. Coût : 1 requête en régime établi (le jeton de
    session, celui de capacités et l'installation sont déjà en cache)."""
    client = _authentifier()
    numinst, panel, alias = installation(client)
    capacites = _capacites(client, numinst, panel)

    reponse = client.appel("Status", Q_ETAT, {"numinst": numinst},
                           numinst=numinst, panel=panel, capacites=capacites)
    if "errors" in reponse:
        # Un jeton de capacités périmé se manifeste ici : on réessaie une fois
        # en le renouvelant, plutôt que de laisser l'état se figer.
        capacites = _capacites(client, numinst, panel, force=True)
        reponse = client.appel("Status", Q_ETAT, {"numinst": numinst},
                               numinst=numinst, panel=panel, capacites=capacites)
        if "errors" in reponse:
            raise ErreurVerisure("Lecture d'état refusée : " + message_erreur(reponse))

    etat = _interpreter(_bloc(reponse, "xSStatus"), alias)
    set_setting("cache_etat",
                json.dumps({"ts": datetime.now().isoformat(), "data": etat}),
                module=MODULE)
    return etat


def _lire_cache():
    brut = get_setting("cache_etat", module=MODULE, default="")
    if not brut:
        return None, None
    try:
        charge = json.loads(brut)
        return charge["data"], datetime.fromisoformat(charge["ts"])
    except (ValueError, KeyError, TypeError):
        return None, None


def periode_minutes():
    try:
        return max(0, int(get_setting("tache_actualiser_minutes", module=MODULE, default="3")))
    except (TypeError, ValueError):
        return 3


def etat_cached(force=False):
    """(état, ts, erreur) — repli sur la dernière valeur connue si l'API tombe."""
    connu, ts = _lire_cache()
    frais = ts is not None and datetime.now() - ts < timedelta(minutes=ETAT_TTL_MIN)
    if connu is not None and frais and not force:
        return connu, ts, ""

    try:
        etat = collecter()
    except Besoin2FA as exc:
        return connu, ts, str(exc)
    except ErreurVerisure as exc:
        journal(f"Erreur API Verisure : {exc}", module=MODULE, level=LogEntry.ERROR)
        if connu is not None:
            return connu, ts, f"API indisponible ({exc}) — dernière valeur connue."
        return None, None, str(exc)
    return etat, datetime.now(), ""


def tache_actualiser():
    """Tâche périodique (scheduler)."""
    if not configured():
        return
    etat_cached(force=True)
