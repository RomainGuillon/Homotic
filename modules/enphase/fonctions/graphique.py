"""Diagramme en bâtons de la journée (production / consommation / réseau).

Lecture — chaque côté de l'axe est un **empilement**, et les deux racontent
le même bilan d'énergie :

    vers le haut, d'où vient l'énergie :  bleu   production
                                          gris   + importé du réseau
    vers le bas,  où elle va :            orange consommation
                                          gris   + exporté au réseau

Les deux empilements ont donc la même hauteur, puisque

    production + importé  =  consommation + exporté

C'est le contrôle intégré du diagramme : un écart visible entre le haut et
le bas d'une tranche ne peut être que du bruit entre les deux compteurs de
l'Envoy.

Le gris est dessiné en dernier et empilé au bout de sa barre : il est donc
toujours visible, quelle que soit son ampleur. Dans une version antérieure
il partait de l'axe, en arrière-plan, et disparaissait sous la barre de
devant — sur une journée d'été, 79 % des tranches en export ne montraient
rien, y compris 2 kW exportés en plein midi.

Le signe n'est PAS affiché sur l'axe : une barre orange vers le bas reste
1,8 kW consommés, pas « −1,8 kW ». Les graduations montrent donc des
valeurs absolues et les infobulles nomment la grandeur.

Les mesures brutes arrivent toutes les 5 minutes ; elles sont regroupées en
tranches d'un quart d'heure (paramètre ``minutes``) et moyennées : une barre
= la puissance moyenne sur sa tranche, en kW, comparable aux valeurs
instantanées affichées à côté.

Données : l'historique local de l'Envoy (voir historique.py), sans aucun
appel à une API externe.
"""

from datetime import datetime, time, timedelta

BLEU = "#7c9bb5"      # production
ORANGE = "#d9a65c"    # consommation
GRIS = "#8b96a5"      # réseau (import / export)
GRILLE = "#3a4551"
TEXTE = "#98a2b0"
AXE = "#5c6773"
_FONT = "-apple-system,Segoe UI,Roboto,sans-serif"


def _naive(dt):
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def _graduations(top, demi_hauteur_px, ecart_min_px=16):
    """Valeurs de l'échelle, de -top à +top.

    Le pas est choisi d'après la place disponible, pas seulement d'après la
    valeur maximale : sur un graphique court, un pas de 0,5 kW empilait les
    étiquettes les unes sur les autres.
    """
    pas = 2.0
    for candidat in (0.25, 0.5, 1.0, 2.0, 5.0, 10.0):
        if top and (candidat / top) * demi_hauteur_px >= ecart_min_px:
            pas = candidat
            break

    valeurs, v = [], 0.0
    while v <= top:
        valeurs.append(round(v, 2))
        v += pas
    return [-v for v in reversed(valeurs[1:])] + valeurs


def _tranches(mesures, jour, minutes):
    """Regroupe les mesures en tranches et moyenne chaque grandeur.

    Retourne [(début de tranche, prod, conso, réseau signé)], seules les
    tranches contenant au moins une mesure étant présentes.
    """
    debut_jour = datetime.combine(jour, time.min)
    paniers = {}
    for t, p, c, r in mesures:
        t = _naive(t)
        if t.date() != jour:
            continue
        index = int((t - debut_jour).total_seconds() // (minutes * 60))
        acc = paniers.setdefault(index, [0.0, 0.0, 0.0, 0])
        acc[0] += p
        acc[1] += c
        acc[2] += r
        acc[3] += 1

    return [
        (
            debut_jour + timedelta(minutes=minutes * i),
            acc[0] / acc[3],
            acc[1] / acc[3],
            acc[2] / acc[3],
        )
        for i, acc in sorted(paniers.items())
    ]


TRANCHE_MIN = 15  # largeur d'une barre, en minutes


def journee_chart(mesures, jour=None, compact=False, minutes=None):
    """Construit le SVG du diagramme en bâtons de la journée.

    ``mesures`` : [(datetime, production_kw, consommation_kw, reseau_kw)],
    le réseau étant signé (positif importé, négatif exporté).
    ``compact`` : version resserrée pour le bloc du tableau de bord (hauteur
    réduite, repères horaires plus espacés) — la largeur des tranches, elle,
    ne change pas : le même quart d'heure doit se lire pareil des deux côtés.
    ``minutes`` : largeur d'une tranche, 15 min par défaut.
    """
    largeur, hauteur = (720, 160) if compact else (720, 270)
    pad_l, pad_r = 38, 8
    # pad_t réserve une bande en haut pour l'unité « kW », qui se superposait
    # à la graduation la plus haute.
    pad_t, pad_b = 20, 20
    try:
        minutes = max(1, min(60, int(minutes or TRANCHE_MIN)))
    except (TypeError, ValueError):
        minutes = TRANCHE_MIN

    jour = jour or (_naive(mesures[0][0]).date() if mesures else datetime.now().date())
    debut_jour = datetime.combine(jour, time.min)
    span = 24 * 3600.0
    pts = _tranches(mesures or [], jour, minutes)

    # Chaque côté est un empilement : le sommet à atteindre est celui des
    # totaux (production + import en haut, consommation + export en bas),
    # pas celui des grandeurs prises une à une.
    valeurs = [
        v
        for _t, p, c, r in pts
        for v in (p + max(r, 0.0), c + max(-r, 0.0))
    ]
    top = max(max(valeurs) if valeurs else 0.0, 0.5) * 1.12
    zero = pad_t + (hauteur - pad_t - pad_b) / 2

    def x(t):
        return pad_l + (t - debut_jour).total_seconds() / span * (largeur - pad_l - pad_r)

    def y(kw):
        return zero - (kw / top) * (hauteur - pad_t - pad_b) / 2

    # Barres jointives : une tranche occupe toute sa largeur, sans espace
    pas_px = (largeur - pad_l - pad_r) * (minutes * 60) / span
    # (la largeur exacte de chaque barre est recalculée au tracé)

    parts = [
        f'<svg viewBox="0 0 {largeur} {hauteur}" style="width:100%;height:auto" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="{_FONT}">'
    ]

    # --- Grille horizontale + échelle (valeurs absolues, sans signe) ---
    demi_hauteur = (hauteur - pad_t - pad_b) / 2
    for v in _graduations(top, demi_hauteur):
        gy = y(v)
        if abs(gy - zero) < 0.5:
            continue
        parts.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{largeur - pad_r}" y2="{gy:.1f}" '
            f'stroke="{GRILLE}" stroke-width="1" stroke-dasharray="3 4"/>'
        )
        # « 2 », « 0.5 », « 0.25 » : autant de décimales que nécessaire, pas
        # plus (un pas de 0,25 arrondi à une décimale affichait « 0.2 »).
        etiquette = f"{abs(v):g}"
        parts.append(
            f'<text x="{pad_l - 5}" y="{gy + 3.5:.1f}" text-anchor="end" '
            f'fill="{TEXTE}" font-size="10">{etiquette}</text>'
        )
    # Unité dans la bande réservée en haut, hors de la zone des graduations
    parts.append(
        f'<text x="{pad_l - 5}" y="11" text-anchor="end" fill="{TEXTE}" '
        f'font-size="10">kW</text>'
    )

    # --- Repères horaires ---
    pas_h = 4 if compact else 2
    for h in range(0, 25, pas_h):
        gx = x(debut_jour + timedelta(hours=h))
        parts.append(
            f'<line x1="{gx:.1f}" y1="{pad_t}" x2="{gx:.1f}" y2="{hauteur - pad_b}" '
            f'stroke="{GRILLE}" stroke-width="1" stroke-dasharray="3 4"/>'
        )
        if h % (pas_h * 2) == 0 or h == 24:
            ancre = "start" if h == 0 else ("end" if h == 24 else "middle")
            parts.append(
                f'<text x="{gx:.1f}" y="{hauteur - 6}" text-anchor="{ancre}" '
                f'fill="{TEXTE}" font-size="10">{h}h</text>'
            )

    # --- Les bâtons. L'ordre d'écriture SVG fait l'ordre d'empilement : le
    # gris passe en dernier, sinon les deux autres le recouvriraient. ---
    def segment(t, de_kw, a_kw, couleur, opacite):
        """Rectangle entre deux niveaux, en kW signés (positif = vers le haut)."""
        if abs(a_kw - de_kw) < 0.001:
            return
        # Largeur calculée depuis les bornes réelles de la tranche : arrondir
        # une largeur constante laissait un filet de fond entre les barres.
        gx = x(t)
        largeur_barre = max(x(t + timedelta(minutes=minutes)) - gx, 0.8)
        y1, y2 = y(de_kw), y(a_kw)
        parts.append(
            f'<rect x="{gx:.2f}" y="{min(y1, y2):.1f}" width="{largeur_barre:.2f}" '
            f'height="{abs(y2 - y1):.1f}" fill="{couleur}" opacity="{opacite}"/>'
        )

    for t, p, _c, _r in pts:  # 1. production, de l'axe vers le haut
        segment(t, 0, p, BLEU, "0.95")
    for t, _p, c, _r in pts:  # 2. consommation, de l'axe vers le bas
        segment(t, 0, -c, ORANGE, "0.95")
    for t, p, c, r in pts:  # 3. réseau, empilé au bout de chaque barre
        importe, exporte = max(r, 0.0), max(-r, 0.0)
        segment(t, p, p + importe, GRIS, "0.75")
        segment(t, -c, -(c + exporte), GRIS, "0.75")

    # --- Survol : une bande par tranche, valeurs nommées et sans signe ---
    for t, p, c, r in pts:
        gx = x(t)
        sens = "importé" if r >= 0 else "exporté"
        infos = (
            f"{t:%H:%M}–{t + timedelta(minutes=minutes):%H:%M} — "
            f"production {p:.2f} kW · consommation {c:.2f} kW · "
            f"réseau {abs(r):.2f} kW {sens}"
        )
        parts.append(
            f'<rect x="{gx:.1f}" y="{pad_t}" width="{max(pas_px, 1):.1f}" '
            f'height="{hauteur - pad_t - pad_b:.1f}" fill="transparent" '
            f'style="cursor:crosshair" '
            f'onmouseover="this.setAttribute(\'fill\',\'rgba(255,255,255,.06)\')" '
            f'onmouseout="this.setAttribute(\'fill\',\'transparent\')">'
            f'<title>{infos}</title></rect>'
        )

    # --- Axe zéro par-dessus les barres ---
    parts.append(
        f'<line x1="{pad_l}" y1="{zero:.1f}" x2="{largeur - pad_r}" y2="{zero:.1f}" '
        f'stroke="{AXE}" stroke-width="1.5"/>'
    )

    parts.append("</svg>")
    return "".join(parts)
