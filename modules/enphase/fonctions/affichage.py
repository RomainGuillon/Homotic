"""Schéma animé des flux d'énergie (repris de la v1 web/charts.py),
couleurs en dur pour le thème sombre de la v2."""

FLOW_ACTIVE_W = 20

AMBER = "#f59e0b"
STEEL = "#94a3b8"
TERRACOTTA = "#e76f51"
SAGE = "#4ade80"
FAINT = "#5a6473"
NODE_BG = "#141d2b"
LINK_BG = "#24303f"
TEXT = "#e5e7eb"
SUB = "#9ca3af"

_FONT = "-apple-system,Segoe UI,Roboto,sans-serif"


def _kw(w):
    return f"{w / 1000:.2f} kW"


def _kwh(wh):
    return f"{wh / 1000:.1f} kWh"


def _node(cx, cy, title, l1, l2, color, compact=False):
    demi_l, demi_h = (72, 30) if compact else (82, 44)
    x, y = cx - demi_l, cy - demi_h
    t_titre, t_val, t_sub = (13, 13, 10) if compact else (16, 15, 11)
    y_titre, y_val, y_sub = (18, 36, 51) if compact else (30, 55, 73)
    return (
        f'<g>'
        f'<rect x="{x}" y="{y}" width="{demi_l * 2}" height="{demi_h * 2}" rx="12" '
        f'fill="{NODE_BG}" stroke="{color}" stroke-width="2"/>'
        f'<text x="{cx}" y="{y + y_titre}" text-anchor="middle" fill="{TEXT}" '
        f'font-size="{t_titre}" font-weight="700" font-family="{_FONT}">{title}</text>'
        f'<text x="{cx}" y="{y + y_val}" text-anchor="middle" fill="{color}" '
        f'font-size="{t_val}" font-weight="700" font-family="{_FONT}">{l1}</text>'
        f'<text x="{cx}" y="{y + y_sub}" text-anchor="middle" fill="{SUB}" '
        f'font-size="{t_sub}" font-family="{_FONT}">{l2}</text>'
        f'</g>'
    )


def _link(x1, y1, x2, y2, active, color, reverse=False):
    base = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{LINK_BG}" stroke-width="4"/>'
    if not active:
        return base
    ax1, ay1, ax2, ay2 = (x2, y2, x1, y1) if reverse else (x1, y1, x2, y2)
    flow = (
        f'<path d="M {ax1} {ay1} L {ax2} {ay2}" class="fl" stroke="{color}" '
        f'stroke-width="4" fill="none" stroke-dasharray="10 14" stroke-linecap="round"/>'
    )
    return base + flow


def _label(x, y, text, color):
    return (
        f'<text x="{x}" y="{y}" text-anchor="middle" fill="{color}" font-size="12" '
        f'font-weight="700" font-family="{_FONT}">{text}</text>'
    )


def production_vs_consumption_chart(day, prod_points, cons_points):
    """Courbes réelles de la journée : production (aire ambre) et
    consommation (ligne tiretée acier) — reprise de la v1."""
    from datetime import datetime, time, timedelta

    W, H = 720, 220
    PAD_L, PAD_R, PAD_T, PAD_B = 40, 10, 12, 24
    day_start = datetime.combine(day, time.min)
    span = 24 * 3600.0

    prod = sorted((t, kw) for t, kw in (prod_points or []) if t.date() == day)
    cons = sorted((t, kw) for t, kw in (cons_points or []) if t.date() == day)
    all_kw = [kw for _t, kw in prod + cons]
    top = max(max(all_kw) if all_kw else 0.0, 0.5) * 1.15
    baseline = H - PAD_B

    def x(t):
        return PAD_L + (t - day_start).total_seconds() / span * (W - PAD_L - PAD_R)

    def y(kw):
        return PAD_T + (1 - kw / top) * (baseline - PAD_T)

    parts = [
        f'<svg viewBox="0 0 {W} {H}" style="width:100%;height:auto" '
        f'preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">'
    ]
    for h in (0, 6, 12, 18, 24):
        gx = x(day_start + timedelta(hours=h))
        parts.append(f'<line x1="{gx:.1f}" y1="{PAD_T}" x2="{gx:.1f}" y2="{baseline}" '
                     f'stroke="{LINK_BG}" stroke-width="1"/>')
        anchor = "start" if h == 0 else ("end" if h == 24 else "middle")
        parts.append(f'<text x="{gx:.1f}" y="{H - 6}" text-anchor="{anchor}" fill="{SUB}" '
                     f'font-size="11" font-family="{_FONT}">{h}h</text>')
    parts.append(f'<line x1="{PAD_L}" y1="{baseline}" x2="{W - PAD_R}" y2="{baseline}" '
                 f'stroke="{LINK_BG}" stroke-width="2"/>')
    parts.append(f'<text x="{PAD_L - 4}" y="{PAD_T + 10}" text-anchor="end" fill="{SUB}" '
                 f'font-size="11" font-family="{_FONT}">{top:.1f} kW</text>')

    if prod:
        coords = " ".join(f"{x(t):.1f},{y(kw):.1f}" for t, kw in prod)
        parts.append(f'<polygon points="{x(prod[0][0]):.1f},{baseline} {coords} '
                     f'{x(prod[-1][0]):.1f},{baseline}" fill="{AMBER}" opacity="0.45"/>')
        parts.append(f'<polyline points="{coords}" fill="none" stroke="{AMBER}" stroke-width="2"/>')
    if cons:
        coords = " ".join(f"{x(t):.1f},{y(kw):.1f}" for t, kw in cons)
        parts.append(f'<polyline points="{coords}" fill="none" stroke="{STEEL}" '
                     f'stroke-width="2" stroke-dasharray="6 5"/>')

    parts.append("</svg>")
    return "".join(parts)


def energy_flow_chart(e, compact=False):
    """Solaire (haut) -> Maison (centre) -> Consommation (droite),
    Réseau (gauche) <-> Maison. Liaisons animées quand le flux est actif.

    ``compact`` : encadrés et espacements réduits (schéma ~30 % moins haut)
    pour le bloc du tableau de bord, où il partage la place avec le
    diagramme de la journée.
    """
    prod = e["production_w"]
    conso = e["consumption_w"]
    imp = e["grid_import_w"]
    exp = e["grid_export_w"]

    solar_active = prod > FLOW_ACTIVE_W
    load_active = conso > FLOW_ACTIVE_W
    import_active = imp > FLOW_ACTIVE_W
    export_active = exp > FLOW_ACTIVE_W

    if import_active:
        grid_color, grid_l1 = TERRACOTTA, f"soutiré {_kw(imp)}"
    elif export_active:
        grid_color, grid_l1 = SAGE, f"injecté {_kw(exp)}"
    else:
        grid_color, grid_l1 = FAINT, "0.00 kW"

    # Géométrie : (y du solaire, y de la rangée, demi-largeur, demi-hauteur,
    # hauteur totale du dessin)
    y_haut, y_rang, demi_l, demi_h, hauteur = (
        (40, 158, 72, 30, 200) if compact else (58, 248, 82, 44, 306)
    )
    n = lambda cx, cy, *a: _node(cx, cy, *a, compact=compact)  # noqa: E731

    solar = n(400, y_haut, "Solaire", _kw(prod), f"{_kwh(e['production_wh_today'])} auj.", AMBER)
    house = n(400, y_rang, "Maison", "", "", STEEL)
    grid = n(
        110, y_rang, "Réseau", grid_l1,
        f"pris {e['import_wh_today'] / 1000:.1f} / rejeté {e['export_wh_today'] / 1000:.1f} kWh",
        grid_color,
    )
    load = n(690, y_rang, "Consommation", _kw(conso),
             f"{_kwh(e['consumption_wh_today'])} auj.", STEEL)

    c_solar = _link(400, y_haut + demi_h, 400, y_rang - demi_h, solar_active, AMBER)
    c_load = _link(400 + demi_l, y_rang, 690 - demi_l, y_rang, load_active, STEEL)
    c_grid = _link(
        110 + demi_l, y_rang, 400 - demi_l, y_rang,
        import_active or export_active,
        TERRACOTTA if import_active else SAGE,
        reverse=export_active,
    )

    labels = ""
    y_milieu = (y_haut + y_rang) // 2
    if solar_active:
        labels += _label(430, y_milieu, _kw(prod), AMBER)
    if load_active:
        labels += _label(545, y_rang - 13, _kw(conso), STEEL)
    if import_active:
        labels += _label(255, y_rang - 13, _kw(imp), TERRACOTTA)
    elif export_active:
        labels += _label(255, y_rang - 13, _kw(exp), SAGE)

    return (
        f'<svg viewBox="0 0 800 {hauteur}" style="width:100%;height:auto" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<style>.fl{{animation:flowdash 1s linear infinite}}'
        f'@keyframes flowdash{{to{{stroke-dashoffset:-24}}}}</style>'
        f'{c_solar}{c_grid}{c_load}{solar}{house}{grid}{load}{labels}'
        f'</svg>'
    )
