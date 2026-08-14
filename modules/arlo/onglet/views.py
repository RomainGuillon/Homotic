"""Onglet Caméras (Arlo) : mode de surveillance, image, paramétrage.

Rien de bloquant ici. La connexion et l'instantané tournent dans des fils
séparés ; l'onglet ne fait que lire leur état et proposer les boutons qui
conviennent. Une requête web ne doit jamais attendre qu'Arlo réveille une
caméra ou qu'un code arrive par e-mail.
"""

from django.contrib import messages
from django.shortcuts import redirect, render

from core.services import get_setting, journal, set_setting

from ..fonctions import affichage, api


def _enregistrer_params(request):
    identifiant = request.POST.get("identifiant", "").strip()
    ancien = get_setting("identifiant", module=api.MODULE, default="")
    set_setting("identifiant", identifiant, module=api.MODULE)

    mot_de_passe = request.POST.get("mot_de_passe", "").strip()
    if mot_de_passe:
        set_setting("mot_de_passe", mot_de_passe, module=api.MODULE, secret=True)

    source = request.POST.get("source_2fa", "onglet").strip()
    if source in ("onglet", "imap"):
        set_setting("source_2fa", source, module=api.MODULE)

    set_setting("imap_hote", request.POST.get("imap_hote", "").strip(), module=api.MODULE)
    set_setting("imap_utilisateur", request.POST.get("imap_utilisateur", "").strip(),
                module=api.MODULE)
    imap_mdp = request.POST.get("imap_mot_de_passe", "").strip()
    if imap_mdp:
        set_setting("imap_mot_de_passe", imap_mdp, module=api.MODULE, secret=True)

    brut = request.POST.get("tache_actualiser_minutes", "").strip()
    try:
        set_setting("tache_actualiser_minutes", str(max(0, int(brut))), module=api.MODULE)
    except ValueError:
        pass

    # Changer de compte rend la session enregistrée inutilisable : la garder
    # ferait échouer la connexion avec un message sans rapport.
    if identifiant and ancien and identifiant != ancien:
        api.oublier_session()
        messages.warning(request, "Changement de compte : il faudra se reconnecter.")

    journal("Paramètres mis à jour", module=api.MODULE)
    messages.success(request, "Paramètres Arlo enregistrés.")


def _traiter_post(request, action):
    if action == "params":
        _enregistrer_params(request)

    elif action == "connexion":
        if api.demarrer_connexion():
            messages.info(request, "Connexion lancée. Arlo va envoyer un code par e-mail.")
        else:
            messages.info(request, "Une connexion est déjà en cours.")

    elif action == "code":
        code = request.POST.get("code", "").strip()
        if not code:
            messages.error(request, "Aucun code saisi.")
        else:
            api.deposer_code(code)
            messages.info(request, "Code transmis — la connexion se termine en arrière-plan.")

    elif action == "deconnexion":
        api.deconnecter()
        messages.success(request, "Déconnecté d'Arlo.")

    elif action == "oublier":
        api.oublier_session()
        messages.warning(request, "Session oubliée : la prochaine connexion demandera un code.")

    elif action == "mode":
        libelle = api.changer_mode(request.POST.get("code_mode", ""))
        messages.success(request, f"Caméras : {libelle}.")

    elif action == "photo":
        if api.demarrer_photo():
            messages.info(request, "Instantané demandé — la caméra se réveille, "
                                   "l'image arrive dans quelques secondes.")
        else:
            messages.info(request, "Un instantané est déjà en cours.")

    elif action == "refresh":
        _etat, _ts, erreur = api.etat_cached(force=True)
        if erreur:
            messages.warning(request, erreur)
        else:
            messages.success(request, "État actualisé.")


def onglet(request):
    if request.method == "POST":
        try:
            _traiter_post(request, request.POST.get("action", ""))
        except api.ErreurArlo as exc:
            messages.error(request, f"Échec : {exc}")
        except Exception as exc:  # l'onglet ne doit jamais planter
            messages.error(request, f"Échec inattendu : {exc}")
        return redirect("core:module_tab", name="arlo")

    configure = api.configured()
    etat, ts, erreur = (None, None, "")
    if configure:
        etat, ts, erreur = api.etat_cached()

    modes = []
    for code in api.ORDRE_MODES:
        modes.append({
            "code": code,
            "libelle": api.MODES[code],
            "icone": affichage.icone(code),
            "actif": bool(etat and etat.get("code") == code),
        })

    cameras = []
    for cam in (etat or {}).get("cameras", []):
        cameras.append({**cam, "classe_batterie": affichage.classe_batterie(cam.get("batterie"))})

    return render(
        request,
        "arlo/onglet.html",
        {
            "active_tab": "module:arlo",
            "configure": configure,
            "etat": etat,
            "badge": affichage.badge_classe((etat or {}).get("code")),
            "modes": modes,
            "cameras": cameras,
            "ts": ts,
            "erreur": erreur,
            "connexion": api.etat_connexion(),
            "message_connexion": api.message_connexion(),
            "attente_code": api.etat_connexion() == api.ATTENTE_CODE,
            "connecte": api.etat_connexion() == api.CONNECTE,
            "photo_en_cours": api.photo_en_cours(),
            "params": {
                "identifiant": get_setting("identifiant", module=api.MODULE, default=""),
                "a_mot_de_passe": bool(get_setting("mot_de_passe", module=api.MODULE, default="")),
                "source_2fa": api.source_2fa(),
                "imap_hote": get_setting("imap_hote", module=api.MODULE, default=""),
                "imap_utilisateur": get_setting("imap_utilisateur", module=api.MODULE, default=""),
                "a_imap_mot_de_passe": bool(get_setting("imap_mot_de_passe", module=api.MODULE, default="")),
                "minutes": get_setting("tache_actualiser_minutes", module=api.MODULE, default="5"),
                "repertoire": api.repertoire_etat(),
            },
        },
    )
