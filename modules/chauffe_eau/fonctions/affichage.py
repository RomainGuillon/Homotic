"""Fonctions d'affichage du chauffe-eau : jauge ballon SVG (reprise v1).

Dégradé proportionnel au niveau : chaud (rouge) en haut, froid (bleu) en
bas, la frontière se déplaçant selon le % d'eau chaude.
"""


def tank_svg(pct, width=200):
    known = pct is not None
    p = max(0.0, min(100.0, float(pct))) if known else 50.0
    frac = p / 100.0
    band = 0.10
    o_hot = max(0.0, min(1.0, frac - band))
    o_cold = max(0.0, min(1.0, frac + band))
    label = f"{p:.0f}%" if known else "—"
    height = round(width * 320 / 220)
    uid = f"tank{width}"  # ids uniques si 2 jauges sur la même page

    return f"""<svg viewBox="0 0 220 320" width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="{uid}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#ef4444"/>
          <stop offset="{o_hot:.3f}" stop-color="#ef4444"/>
          <stop offset="{o_cold:.3f}" stop-color="#2563eb"/>
          <stop offset="1" stop-color="#2563eb"/>
        </linearGradient>
      </defs>

      <rect x="70" y="300" width="12" height="16" rx="2" fill="#475569"/>
      <rect x="138" y="300" width="12" height="16" rx="2" fill="#475569"/>

      <rect x="45" y="20" width="130" height="285" rx="60" fill="url(#{uid})"
            stroke="#1e293b" stroke-width="4"/>
      <rect x="60" y="45" width="14" height="220" rx="7" fill="#ffffff" opacity="0.15"/>

      <rect x="80" y="8" width="12" height="18" fill="#94a3b8"/>
      <rect x="128" y="8" width="12" height="18" fill="#94a3b8"/>

      <text x="110" y="170" text-anchor="middle" fill="#ffffff" font-size="34"
            font-weight="800" style="paint-order:stroke;stroke:#0008;stroke-width:3px"
            font-family="-apple-system,Segoe UI,Roboto,sans-serif">{label}</text>
      <text x="110" y="196" text-anchor="middle" fill="#e5e7eb" font-size="12"
            font-family="-apple-system,Segoe UI,Roboto,sans-serif">eau chaude</text>
    </svg>"""
