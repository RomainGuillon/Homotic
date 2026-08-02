"""Affichage du module Solaire : courbe SVG d'une journée (aire de
prévision ambre, zone verte du créneau de chauffe, ligne bleue du réel),
grille pointillée et valeurs au survol — présentation reprise de la v1."""

from datetime import datetime, time, timedelta

W, H = 720, 240
PAD_L, PAD_R, PAD_T, PAD_B = 46, 12, 14, 26

AMBER = "#d9a65c"
BLUE = "#7c9bb5"
GREEN = "#7fa88b"
GRID = "#3a4551"
TEXT = "#98a2b0"
_FONT = "-apple-system,Segoe UI,Roboto,sans-serif"


def _naive(dt):
    """Heure locale sans fuseau."""
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _echelle_y(top):
    """Graduations lisibles (0, puis pas de 0,5 / 1 / 2 kW selon le maximum)."""
    pas = 0.5 if top <= 2 else (1.0 if top <= 6 else 2.0)
    valeurs, v = [], 0.0
    while v <= top:
        valeurs.append(v)
        v += pas
    return valeurs


def day_chart(day, forecast_points, zone=None, actual_points=None,
              zone_label="créneau de chauffe"):
    """Courbe 00h-24h d'une journée.

    - forecast_points : [(datetime, kw)] prévision (aire ambre)
    - zone : {start, end} créneau à surligner (rectangle vert)
    - actual_points : [(datetime, kw)] réel/estimé (ligne bleue)
    """
    day_start = datetime.combine(day, time.min)
    span = 24 * 3600.0

    fc = sorted((_naive(t), kw) for t, kw in (forecast_points or [])
                if _naive(t).date() == day)
    ac = sorted((_naive(t), kw) for t, kw in (actual_points or [])
                if _naive(t).date() == day)

    all_kw = [kw for _t, kw in fc + ac]
    top = max(max(all_kw) if all_kw else 0.0, 0.5) * 1.15
    baseline = H - PAD_B

    def x(t):
        return PAD_L + (t - day_start).total_seconds() / span * (W - PAD_L - PAD_R)

    def y(kw):
        return PAD_T + (1 - kw / top) * (baseline - PAD_T)

    parts = [
        f'<svg viewBox="0 0 {W} {H}" style="width:100%;height:auto" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="{_FONT}">'
    ]

    # --- Grille horizontale pointillée + échelle kW ---
    for v in _echelle_y(top):
        gy = y(v)
        parts.append(
            f'<line x1="{PAD_L}" y1="{gy:.1f}" x2="{W - PAD_R}" y2="{gy:.1f}" '
            f'stroke="{GRID}" stroke-width="1" stroke-dasharray="3 4"/>'
        )
        parts.append(
            f'<text x="{PAD_L - 6}" y="{gy + 3.5:.1f}" text-anchor="end" '
            f'fill="{TEXT}" font-size="10">{v:.1f}</text>'
        )
    parts.append(
        f'<text x="{PAD_L - 6}" y="{PAD_T - 4}" text-anchor="end" fill="{TEXT}" '
        f'font-size="10">kW</text>'
    )

    # --- Grille verticale pointillée (toutes les 2 h) + axe horaire ---
    for h in range(0, 25, 2):
        gx = x(day_start + timedelta(hours=h))
        parts.append(
            f'<line x1="{gx:.1f}" y1="{PAD_T}" x2="{gx:.1f}" y2="{baseline}" '
            f'stroke="{GRID}" stroke-width="1" stroke-dasharray="3 4"/>'
        )
        if h % 4 == 0 or h == 24:
            anchor = "start" if h == 0 else ("end" if h == 24 else "middle")
            parts.append(
                f'<text x="{gx:.1f}" y="{H - 8}" text-anchor="{anchor}" '
                f'fill="{TEXT}" font-size="10">{h}h</text>'
            )
    parts.append(
        f'<line x1="{PAD_L}" y1="{baseline}" x2="{W - PAD_R}" y2="{baseline}" '
        f'stroke="{GRID}" stroke-width="1.5"/>'
    )

    # --- Zone du créneau de chauffe ---
    if zone:
        zx1, zx2 = x(_naive(zone["start"])), x(_naive(zone["end"]))
        parts.append(
            f'<rect x="{zx1:.1f}" y="{PAD_T}" width="{max(zx2 - zx1, 1):.1f}" '
            f'height="{baseline - PAD_T:.1f}" fill="{GREEN}" opacity="0.22">'
            f'<title>{zone_label} : {_naive(zone["start"]):%H:%M}–'
            f'{_naive(zone["end"]):%H:%M}</title></rect>'
        )

    # --- Aire de prévision ---
    if fc:
        coords = " ".join(f"{x(t):.1f},{y(kw):.1f}" for t, kw in fc)
        first_x, last_x = x(fc[0][0]), x(fc[-1][0])
        parts.append(
            f'<polygon points="{first_x:.1f},{baseline} {coords} '
            f'{last_x:.1f},{baseline}" fill="{AMBER}" opacity="0.45"/>'
        )
        parts.append(
            f'<polyline points="{coords}" fill="none" stroke="{AMBER}" stroke-width="2"/>'
        )

    # --- Ligne du réel ---
    if ac:
        coords = " ".join(f"{x(t):.1f},{y(kw):.1f}" for t, kw in ac)
        parts.append(
            f'<polyline points="{coords}" fill="none" stroke="{BLUE}" stroke-width="2.5"/>'
        )

    # --- Survol : une bande par pas, avec l'heure et les valeurs ---
    reel = {t: kw for t, kw in ac}
    for i, (t, kw) in enumerate(fc):
        x1 = x(t - timedelta(minutes=15))
        x2 = x(t + timedelta(minutes=15))
        infos = f"{t:%H:%M} — prévu {kw:.2f} kW"
        proche = min(reel, key=lambda rt: abs((rt - t).total_seconds()), default=None)
        if proche is not None and abs((proche - t).total_seconds()) <= 900:
            infos += f" · réel {reel[proche]:.2f} kW"
        parts.append(
            f'<rect x="{x1:.1f}" y="{PAD_T}" width="{max(x2 - x1, 1):.1f}" '
            f'height="{baseline - PAD_T:.1f}" fill="transparent" '
            f'style="cursor:crosshair" '
            f'onmouseover="this.setAttribute(\'fill\',\'rgba(255,255,255,.06)\')" '
            f'onmouseout="this.setAttribute(\'fill\',\'transparent\')">'
            f'<title>{infos}</title></rect>'
        )
    # Points du réel sans prévision correspondante (début de journée)
    for t, kw in ac:
        if not any(abs((t - ft).total_seconds()) <= 900 for ft, _ in fc):
            parts.append(
                f'<circle cx="{x(t):.1f}" cy="{y(kw):.1f}" r="4" fill="transparent">'
                f'<title>{t:%H:%M} — réel {kw:.2f} kW</title></circle>'
            )

    parts.append("</svg>")
    return "".join(parts)


def _pas_heures(points, defaut=0.5):
    """Durée d'un pas de mesure, en heures, déduite des horodatages.

    Les sources n'ont pas le même pas : 30 min pour Solcast, 15 min pour la
    courbe cloud Enphase, 5 min pour l'historique local de l'Envoy. Sans
    cette mesure, une courbe à 5 min gonflerait les kWh d'un facteur 6.
    """
    if not points or len(points) < 2:
        return defaut
    ecarts = []
    precedent = None
    for t, _kw in points:
        t = _naive(t)
        if precedent is not None:
            delta = (t - precedent).total_seconds() / 3600.0
            if delta > 0:
                ecarts.append(delta)
        precedent = t
    if not ecarts:
        return defaut
    ecarts.sort()
    return ecarts[len(ecarts) // 2]  # médiane : insensible aux trous


def hourly_rows(day, forecast_points, actual_points, start_h=6, end_h=21):
    """Tableau horaire : kWh prévus / réels par heure (somme des kW × pas)."""
    pas_prev = _pas_heures(forecast_points)
    prev = {}
    for t, kw in forecast_points or []:
        t = _naive(t)
        if t.date() == day:
            prev[t.hour] = prev.get(t.hour, 0.0) + kw * pas_prev

    pas_reel = _pas_heures(actual_points)
    reel = {}
    for t, kw in actual_points or []:
        t = _naive(t)
        if t.date() == day:
            reel[t.hour] = reel.get(t.hour, 0.0) + kw * pas_reel

    rows = []
    for h in range(start_h, end_h):
        rows.append({
            "label": f"{h}h",
            "prevision": prev.get(h),
            "actual": reel.get(h),
        })
    return rows
