# Copyright (c) 2026 Romain Guillon
#
# Distribué sous licence MIT. Vous pouvez utiliser, modifier et
# redistribuer ce fichier, y compris commercialement, à condition de
# conserver la présente mention de copyright.
# Voir le fichier LICENSE à la racine du dépôt.

"""Lecture d'une série de relevés de chauffe : ce qui s'est vraiment passé.

Les relevés sont pris chaque minute, mais la passerelle Cozytouch ne
repousse l'état du ballon que toutes les dix minutes environ : entre deux
rafraîchissements, on relit la même valeur. Constaté sur 62 chauffes
(août–octobre 2026) : températures et puissance n'avancent qu'à +10, +20,
+30 min du départ. Deux conséquences, corrigées ici :

- **la fin de chauffe est vue en retard**, jusqu'à dix minutes. Une chauffe
  de 44 min est enregistrée 50 min, et son énergie comptée 2,0 kWh au lieu
  de 1,76. On estime donc l'instant réel de l'arrêt (``fin_estimee``) ;
- **de l'eau tirée pendant la chauffe** se voit à ce que le bas de cuve
  redescend (``eau_tiree``). Ces chauffes-là durent plus longtemps sans que
  la température de départ l'explique : le modèle de durée les écarte.

Module sans dépendance : ni base de données, ni API. Les fonctions prennent
une liste d'objets portant ``quand``, ``temp_milieu``, ``temp_bas``,
``puissance_elec`` et ``puissance_pac`` — les ``ChauffeMesure`` du suivi
comme les modèles historiques d'une migration.
"""

from datetime import timedelta

# Baisse du bas de cuve (°C) entre deux rafraîchissements, en pleine
# chauffe, au-delà de laquelle on conclut que de l'eau a été tirée.
SEUIL_TIRAGE = 0.5
# Montée minimale du bas de cuve (°C/min) pour extrapoler la fin de
# chauffe : en dessous, la pente ne dit rien et on garde la fin relevée.
PENTE_MINIMALE = 0.05
# Un trou dans les relevés (service arrêté) ne crée pas d'énergie fictive
# au-delà de cette durée, en minutes.
TROU_MAXIMAL_MIN = 5


def _puissance(releve):
    return (releve.puissance_elec or 0.0) + (releve.puissance_pac or 0.0)


def _minutes(debut, fin):
    return (fin - debut).total_seconds() / 60.0


def paliers(releves):
    """Relevés où la passerelle a réellement repoussé quelque chose.

    Entre deux rafraîchissements les relevés minute se répètent : on ne
    garde que le premier de chaque série identique (températures, et
    résistance allumée ou non).
    """
    retenus, precedent = [], None
    for releve in releves:
        cle = (releve.temp_milieu, releve.temp_bas, _puissance(releve) > 0)
        if cle != precedent:
            retenus.append(releve)
            precedent = cle
    return retenus


def fin_estimee(releves):
    """Instant où la chauffe s'est réellement arrêtée, ou ``None``.

    ``releves`` se termine par le relevé de clôture, celui où l'arrêt a été
    vu. L'arrêt a eu lieu quelque part entre le dernier rafraîchissement en
    chauffe et celui-là. On le situe d'après le bas de cuve, qui monte
    régulièrement en fin de chauffe : au rythme du palier précédent, combien
    de minutes a-t-il fallu pour gagner les derniers degrés ?

    ``None`` quand on ne peut pas le dire (moins de trois paliers, bas de
    cuve non relevé ou qui ne monte pas) : la fin relevée fait alors foi.
    Le résultat reste toujours entre les deux rafraîchissements : au pire,
    l'estimation ne change rien.
    """
    etapes = paliers(releves)
    if len(etapes) < 3:
        return None
    avant, dernier, cloture = etapes[-3:]
    if None in (avant.temp_bas, dernier.temp_bas, cloture.temp_bas):
        return None
    ecoule = _minutes(avant.quand, dernier.quand)
    if ecoule <= 0:
        return None
    pente = (dernier.temp_bas - avant.temp_bas) / ecoule
    if pente < PENTE_MINIMALE:
        return None
    reste = (cloture.temp_bas - dernier.temp_bas) / pente
    reste = max(0.0, min(reste, _minutes(dernier.quand, cloture.quand)))
    return dernier.quand + timedelta(minutes=reste)


def eau_tiree(releves):
    """Vrai si de l'eau a été tirée pendant la chauffe.

    Signe : le bas de cuve, où arrive l'eau froide, redescend d'un
    rafraîchissement au suivant alors que la résistance chauffe.
    """
    bas = [r.temp_bas for r in paliers(releves) if r.temp_bas is not None]
    return any(suivant < courant - SEUIL_TIRAGE for courant, suivant in zip(bas, bas[1:]))


def energie_wh(releves, fin=None):
    """Énergie consommée, en Wh : ``(résistance, pompe à chaleur)``.

    Chaque relevé vaut pour l'intervalle qui le sépare du suivant. ``fin``
    borne l'intégration : la puissance relue après l'arrêt réel n'était
    qu'une valeur pas encore rafraîchie.
    """
    elec = pac = 0.0
    for courant, suivant in zip(releves, releves[1:]):
        limite = suivant.quand
        if fin is not None and limite > fin:
            limite = max(courant.quand, fin)
        heures = min(_minutes(courant.quand, limite), TROU_MAXIMAL_MIN) / 60.0
        elec += (courant.puissance_elec or 0.0) * heures
        pac += (courant.puissance_pac or 0.0) * heures
    return elec, pac


def bilan(releves, debut):
    """Tout ce qu'une série de relevés dit de sa chauffe.

    ``{"fin_estimee", "duree_estimee_min", "tirage", "elec_wh", "pac_wh"}``
    — ``duree_estimee_min`` à ``None`` si la fin n'a pas pu être estimée.
    """
    releves = list(releves)
    fin = fin_estimee(releves)
    elec, pac = energie_wh(releves, fin)
    return {
        "fin_estimee": fin,
        "duree_estimee_min": round(_minutes(debut, fin), 1) if fin else None,
        "tirage": eau_tiree(releves),
        "elec_wh": round(elec, 1),
        "pac_wh": round(pac, 1),
    }
