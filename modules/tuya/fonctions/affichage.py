"""Fonctions d'affichage du module Tuya : jauges capteur (thermomètre +
goutte d'humidité) et dessin de prise — reprises de la v1 (web/charts.py)."""

_SENSOR_TEMP_MIN, _SENSOR_TEMP_MAX = 0.0, 40.0

# Paliers de couleur du froid (bleu) au chaud (rouge).
_TEMP_STOPS = [
    (0, (37, 99, 235)),
    (14, (56, 189, 248)),
    (20, (34, 197, 94)),
    (26, (245, 158, 11)),
    (34, (239, 68, 68)),
]

# Paliers de couleur de l'humidité (bleu clair -> bleu profond).
_HUM_STOPS = [
    (0, (191, 219, 254)),
    (50, (96, 165, 250)),
    (100, (29, 78, 216)),
]

_TRACK = "#243044"      # fond des jauges
_TEXT = "#e5e7eb"
_CAPTION = "#9ca3af"
_FAINT = "#5a6473"


def _lerp_color(value, stops):
    if value <= stops[0][0]:
        return stops[0][1]
    if value >= stops[-1][0]:
        return stops[-1][1]
    for (v0, c0), (v1, c1) in zip(stops, stops[1:]):
        if v0 <= value <= v1:
            f = (value - v0) / (v1 - v0)
            return tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))
    return stops[-1][1]


def _hex(rgb):
    return "#%02x%02x%02x" % rgb


def sensor_gauges(temp, hum, key, width=220):
    """Thermomètre + goutte d'humidité d'un capteur (SVG, repris v1)."""
    if temp is not None:
        frac = max(0.0, min(1.0, (temp - _SENSOR_TEMP_MIN) /
                            (_SENSOR_TEMP_MAX - _SENSOR_TEMP_MIN)))
        t_color = _hex(_lerp_color(temp, _TEMP_STOPS))
        t_label = f"{temp:.1f}°"
    else:
        frac, t_color, t_label = 0.0, _FAINT, "—"
    tube_top, tube_bottom = 30, 180
    fill_top = tube_bottom - frac * (tube_bottom - tube_top)
    fill_h = tube_bottom - fill_top

    if hum is not None:
        h_frac = max(0.0, min(1.0, hum / 100.0))
        h_color = _hex(_lerp_color(hum, _HUM_STOPS))
        h_label = f"{hum:.0f}%"
    else:
        h_frac, h_color, h_label = 0.0, _FAINT, "—"
    drop_top, drop_bottom = 40, 206
    h_fill_top = drop_bottom - h_frac * (drop_bottom - drop_top)
    drop_path = "M205 40 L249 84 A62 62 0 1 1 161 84 Z"
    clip_id = f"drop-{key}"
    height = round(width * 250 / 300)

    return f"""<svg viewBox="0 0 300 250" width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <clipPath id="{clip_id}"><path d="{drop_path}"/></clipPath>
      </defs>

      <rect x="54" y="{tube_top}" width="36" height="{tube_bottom - tube_top}" rx="18" fill="{_TRACK}"/>
      <circle cx="72" cy="196" r="28" fill="{_TRACK}"/>
      <rect x="59" y="{fill_top:.1f}" width="26" height="{fill_h:.1f}" rx="13" fill="{t_color}"/>
      <circle cx="72" cy="196" r="22" fill="{t_color}"/>
      <rect x="61" y="{tube_top + 4}" width="6" height="{tube_bottom - tube_top - 8}" rx="3" fill="#ffffff" opacity="0.18"/>
      <text x="72" y="238" text-anchor="middle" fill="{_TEXT}" font-size="22" font-weight="700"
            font-family="-apple-system,Segoe UI,Roboto,sans-serif">{t_label}</text>
      <text x="72" y="18" text-anchor="middle" fill="{_CAPTION}" font-size="13"
            font-family="-apple-system,Segoe UI,Roboto,sans-serif">Température</text>

      <path d="{drop_path}" fill="{_TRACK}"/>
      <rect x="138" y="{h_fill_top:.1f}" width="134" height="{drop_bottom - h_fill_top + 12:.1f}"
            fill="{h_color}" clip-path="url(#{clip_id})"/>
      <ellipse cx="188" cy="90" rx="9" ry="16" fill="#ffffff" opacity="0.18"
               transform="rotate(-18 188 90)" clip-path="url(#{clip_id})"/>
      <text x="205" y="238" text-anchor="middle" fill="{_TEXT}" font-size="22" font-weight="700"
            font-family="-apple-system,Segoe UI,Roboto,sans-serif">{h_label}</text>
      <text x="205" y="18" text-anchor="middle" fill="{_CAPTION}" font-size="13"
            font-family="-apple-system,Segoe UI,Roboto,sans-serif">Humidité</text>
    </svg>"""


def plug_svg(is_on, width=128):
    """Dessin d'une prise connectée, symbole marche/arrêt coloré (repris v1)."""
    color = "#4ade80" if is_on else _FAINT
    label = "MARCHE" if is_on else "ARRÊT"
    height = round(width * 175 / 160)
    return f"""<svg viewBox="0 0 160 175" width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
      <rect x="66" y="6" width="10" height="18" rx="3" fill="#94a3b8"/>
      <rect x="84" y="6" width="10" height="18" rx="3" fill="#94a3b8"/>
      <rect x="30" y="22" width="100" height="118" rx="18" fill="none" stroke="{color}" stroke-width="3"/>
      <circle cx="118" cy="40" r="5" fill="{color}"/>
      <circle cx="80" cy="90" r="26" fill="none" stroke="{color}" stroke-width="6"/>
      <line x1="80" y1="56" x2="80" y2="90" stroke="{color}" stroke-width="6" stroke-linecap="round"/>
      <text x="80" y="162" text-anchor="middle" fill="{color}" font-size="15" font-weight="700"
            font-family="-apple-system,Segoe UI,Roboto,sans-serif">{label}</text>
    </svg>"""


_DAYS_FR = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]


def _loops_label(loops):
    """Traduit un masque de jours Tuya (ex. « 1111100 ») en libellé lisible."""
    if not loops or not isinstance(loops, str) or len(loops) != 7:
        return "une fois"
    if loops == "1111111":
        return "tous les jours"
    jours = [d for d, flag in zip(_DAYS_FR, loops) if flag == "1"]
    return ", ".join(jours) if jours else "une fois"


def schedule_lines(slots):
    """Formate les créneaux de programmation d'une prise en lignes lisibles."""
    lines = []
    for slot in slots or []:
        if slot.get("on") is True:
            action = "Allumer"
        elif slot.get("on") is False:
            action = "Éteindre"
        else:
            action = "Changer d'état"
        lines.append(f"{slot.get('time', '?')} — {action} · {_loops_label(slot.get('loops'))}")
    return lines
