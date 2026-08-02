"""Configuration Django du projet Homotic.

Tableau de bord domestique modulaire. Port conseille 8100.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


# Fichiers de reglages lus au demarrage, dans l'ordre de priorite :
#
# - « .env » a la racine du projet : machine de developpement (ignore par git) ;
# - « /etc/homotic.env » : le meme fichier que celui donne au service systemd.
#
# Le second evite un piege : systemd fournit ces variables au service, mais
# pas au shell. Sans cette lecture, « manage.py collectstatic » ou
# « createsuperuser » lances a la main echouent faute de SECRET_KEY, alors
# que le service, lui, tourne tres bien. Le fichier etant en chmod 600, un
# utilisateur sans droits ne le lit pas — il n'y a donc rien a divulguer.
FICHIERS_ENV = (BASE_DIR / ".env", Path("/etc/homotic.env"))


def _charger_env():
    """Charge les fichiers de reglages presents et lisibles.

    Une variable deja definie dans l'environnement gagne toujours : un
    reglage explicite ne doit pas etre ecrase par un fichier oublie.
    """
    for fichier in FICHIERS_ENV:
        try:
            contenu = fichier.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # absent, ou illisible faute de droits : sans importance
        for ligne in contenu.splitlines():
            ligne = ligne.strip()
            if not ligne or ligne.startswith("#") or "=" not in ligne:
                continue
            cle, _, valeur = ligne.partition("=")
            os.environ.setdefault(cle.strip(), valeur.strip().strip("\"'"))


_charger_env()

# Suite de tests : base jetable, aucun reseau, rien a proteger. Exiger une
# cle de production ici empecherait simplement de lancer « manage.py test »
# sur une copie fraiche du depot.
EN_TEST = "test" in sys.argv


def _env_bool(nom, defaut):
    """Lit un booleen d'environnement (« 1 », « oui », « true »...)."""
    valeur = os.environ.get(nom)
    if valeur is None:
        return defaut
    return valeur.strip().lower() in {"1", "oui", "yes", "true", "on"}


def _env_liste(nom, defaut):
    """Lit une liste separee par des virgules, vides ignorees."""
    valeur = os.environ.get(nom)
    if not valeur:
        return list(defaut)
    return [x.strip() for x in valeur.split(",") if x.strip()]

# Identite affichee (barre de navigation, titre des pages, « A propos »)
APP_NOM = "Homotic"
APP_VERSION = "v1"
APP_AUTEUR = "Romain Guillon"
# Annee de creation : la mention de copyright affiche « 2026 » puis
# « 2026-2027 » des que l'annee courante change, sans rien a modifier.
APP_ANNEE_DEBUT = 2026

# Repertoire qui contiendra les modules "plugins" (etape 3)
MODULES_DIR = BASE_DIR / "modules"

# --------------------------------------------------------------------------
# Reglages sensibles a l'environnement
#
# Homotic tourne dans deux contextes tres differents : la machine de
# developpement, et un serveur joignable depuis Internet. Tout ce qui doit
# changer entre les deux passe par des variables d'environnement, fournies en
# production par le fichier EnvironmentFile du service systemd (voir
# deploiement/homotic.service). Les defauts sont ceux du cas le plus sur :
# oublier une variable en production doit casser bruyamment, jamais ouvrir
# discretement une porte.
# --------------------------------------------------------------------------

DEBUG = _env_bool("HOMOTIC_DEBUG", False)

# La cle de dev ne sert que quand DEBUG est actif. Hors DEBUG, l'absence de
# HOMOTIC_SECRET_KEY arrete le demarrage : une cle publiquement connue permet
# de forger des sessions et des jetons CSRF, donc de se faire passer pour
# n'importe qui — c'est exactement l'inverse du login qu'on met en place.
CLE_DE_DEV = "django-insecure-homotic-dev-key-a-changer-si-exposition-externe"
SECRET_KEY = os.environ.get("HOMOTIC_SECRET_KEY") or (
    CLE_DE_DEV if (DEBUG or EN_TEST) else ""
)
if not SECRET_KEY:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "HOMOTIC_SECRET_KEY absente alors que DEBUG est desactive. "
        "Generer une cle avec :\n"
        "  python -c \"import secrets; print(secrets.token_urlsafe(64))\"\n"
        "puis la placer dans le fichier d'environnement du service."
    )

# En dev on accepte tout ; en production la liste est explicite, sinon
# n'importe quel nom de domaine pointe sur le serveur repondrait.
ALLOWED_HOSTS = _env_liste(
    "HOMOTIC_HOSTS", ["*"] if DEBUG else ["localhost", "127.0.0.1"]
)

# Django exige l'origine complete (schema compris) pour valider un POST
# derriere un proxy : sans elle, chaque formulaire echouerait en 403.
CSRF_TRUSTED_ORIGINS = _env_liste(
    "HOMOTIC_ORIGINES",
    [f"https://{h}" for h in ALLOWED_HOSTS if h not in {"*", "localhost", "127.0.0.1"}],
)

if not DEBUG and not EN_TEST:
    # nginx fait la terminaison TLS et parle en clair a l'application : sans
    # cet en-tete, Django croit la requete non chiffree, refuse de poser des
    # cookies « secure » et boucle sur la redirection HTTPS.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    # Cookies de session et CSRF reserves aux connexions chiffrees.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # HSTS : une fois la premiere visite faite en HTTPS, le navigateur refuse
    # de repasser en clair. Un an, sous-domaines compris. A n'activer qu'une
    # fois le certificat en place et verifie — revenir en arriere demande
    # d'attendre l'expiration cote navigateur.
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"

    # Deconnexion automatique apres deux semaines d'inactivite.
    SESSION_COOKIE_AGE = 14 * 24 * 3600
    SESSION_SAVE_EVERY_REQUEST = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Apps du projet
    "core",
]


def _modules_actifs():
    """Lit en base (SQL brut, car Django n'est pas encore charge) la liste
    des modules actives dans l'onglet Configuration."""
    import sqlite3

    db = BASE_DIR / "db.sqlite3"
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(db)
        rows = con.execute("SELECT name FROM core_module WHERE enabled = 1").fetchall()
        con.close()
    except sqlite3.Error:
        return []
    # On ne garde que les modules encore presents sur le disque
    return [r[0] for r in rows if (MODULES_DIR / r[0] / "conf.py").exists()]


# Les modules coches et valides dans l'onglet Configuration deviennent des
# apps Django a part entiere (templates, modeles, migrations...).
INSTALLED_APPS += [f"modules.{name}" for name in _modules_actifs()]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Ferme l'application par defaut : toute vue exige une session, sauf
    # celles marquees « login_not_required » (la page de connexion elle-meme).
    # Un decorateur par vue aurait laisse passer la premiere vue ecrite en
    # oubliant de le mettre — ici l'oubli ferme au lieu d'ouvrir.
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "homotic.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.nav_modules",
                "core.context_processors.version_theme",
                "core.context_processors.identite",
            ],
        },
    },
]

WSGI_APPLICATION = "homotic.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            # Le scheduler fait tourner une dizaine de tâches et scénarios en
            # parallèle (threads APScheduler), qui écrivent chacun en base.
            # SQLite n'autorise qu'un seul écrivain à la fois : sans ce
            # réglage, deux écritures qui tombent à la même seconde (le
            # suivi du chauffe-eau tourne à la minute) donnent
            # « database is locked » au lieu d'attendre leur tour.
            #
            # - journal_mode=WAL : les lecteurs ne bloquent plus les
            #   écrivains (et inversement) — la vraie cause du problème.
            # - transaction_mode=IMMEDIATE : la transaction réserve le
            #   verrou d'écriture dès son ouverture plutôt qu'à la première
            #   écriture ; sans ça, deux transactions peuvent toutes les deux
            #   commencer en lecture puis se disputer le verrou en même
            #   temps, ce qui échoue même avec un délai d'attente généreux.
            # - timeout=20 : si le verrou est vraiment pris, on attend
            #   20 s avant d'abandonner au lieu des 5 s par défaut de sqlite3.
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            "transaction_mode": "IMMEDIATE",
            "timeout": 20,
        },
    }
}

# Le compte protege un appareil reel (chauffe-eau, scenarios) depuis
# Internet : un mot de passe court ou courant n'est plus acceptable.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "login"

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
# Destination de « collectstatic » : hors DEBUG, Django ne sert plus les
# fichiers statiques lui-meme, c'est nginx qui lit ce repertoire.
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Journalisation
#
# La configuration par defaut de Django n'ecrit les erreurs sur la console
# que si DEBUG est actif : en production, une erreur 500 ne laissait donc
# aucune trace nulle part. On retablit une sortie systematique vers stderr,
# que systemd collecte — « journalctl -u homotic » devient le bon reflexe.
#
# L'onglet Journal de l'application, lui, sert a suivre les modules et les
# scenarios ; il ne peut pas recueillir une erreur qui casse la requete
# avant d'arriver au code applicatif.
# --------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "console": {
            "format": "{levelname} {asctime} {name} — {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        # Les exceptions non rattrapees d'une vue passent par ici, avec leur
        # trace complete. C'est la ligne qui manquait.
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "homotic": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
