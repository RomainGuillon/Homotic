[← Sommaire](README.md)

# 5. Les modules livrés

Chaque module se paramètre dans son propre onglet, section « Paramétrage ».

| Module | Onglet | Ce qu'il apporte |
| --- | --- | --- |
| `chauffe_eau` | Chauffe-eau | Ballon Atlantic via Cozytouch : état, douches restantes, chauffe forcée |
| `clim` | Climatisation | Climatisations Hitachi Hi-Kumo : marche/arrêt, mode, consigne |
| `enphase` | Énergie | Passerelle Envoy locale : production, consommation, réseau, courbe du jour |
| `solcast` | Solaire | Prévisions de production, meilleur créneau de chauffe |
| `tempo` | Tempo | Couleurs des jours EDF Tempo et tarifs |
| `tuya` | Capteurs | Capteurs et prises Tuya |
| `heure_demarrage` | Heure démarrage | Calcule la meilleure heure de chauffe du ballon |

## Énergie (Enphase)

Interroge la passerelle **Envoy en local** — pas de quota, pas de cloud.

![Onglet Énergie](images/05-energie.png)

Le bloc du tableau de bord réunit le schéma des flux, les puissances
instantanées et le **diagramme de la journée** :

- **bleu** : production, au-dessus de l'axe
- **orange** : consommation, au-dessous de l'axe
- **gris** : réseau, en arrière-plan — au-dessus si importé, au-dessous si
  exporté

L'axe n'affiche **aucun signe** : une barre orange vers le bas reste une
consommation de 1,8 kW, pas « −1,8 kW ».

Ce diagramme est construit à partir d'un **historique local** : chaque
lecture de l'Envoy enregistre un point (production, consommation, réseau),
par tranche de 5 minutes, remis à zéro chaque nuit. Aucun appel externe,
aucun quota. Il se remplit donc au fil de la journée, et n'est complet que
si le scheduler tourne.

Publie les variables `enphase_production_w`, `enphase_conso_w`,
`enphase_import_w`, `enphase_export_w`.

## Solaire (Solcast)

Prévisions de production photovoltaïque, et meilleur créneau pour chauffer
le ballon.

![Onglet Solaire](images/05-solaire.png)

### Le quota, à comprendre avant tout

Le plan gratuit Solcast donne **10 appels par jour pour le compte**, et
chaque requête coûte **un appel par site**. Avec deux pans de toiture,
un rafraîchissement coûte donc **2 appels** — soit 5 rafraîchissements par
jour au maximum.

Trois garde-fous, cumulés :

1. **Les affichages de page n'appellent jamais l'API.** Seuls le
   rafraîchissement planifié et le bouton Actualiser le peuvent. Sinon le
   nombre d'appels dépendrait du nombre de fois où l'on regarde le tableau
   de bord.
2. **Un compteur journalier** plafonné (réglage `quota_jour`, 10 par
   défaut), remis à zéro chaque jour, et **aligné sur la vérité** dès qu'un
   429 arrive : Solcast a compté 10 appels, on le croit plutôt que notre
   décompte local.
3. **Un backoff** : une erreur réseau suspend les appels 30 minutes, un 429
   les suspend jusqu'au lendemain 6h. Sans lui, un échec n'écrivant aucun
   cache, chaque affichage relançait un appel — d'où des rafales de dizaines
   d'appels refusés.

L'onglet affiche en permanence les **appels restants**, les
rafraîchissements encore finançables, le prochain passage et ce qui est
prévu d'ici ce soir.

### Choisir les horaires

La section Paramétrage permet d'**ajouter et retirer** les rafraîchissements
un par un. Les minutes sont libres (`07:30` est valide). Le coût s'affiche
en direct sous la liste, en orange si le total dépasse le quota.

Aucun horaire = plus aucun appel automatique, seul le bouton Actualiser
agit. Pris en compte au **prochain démarrage du serveur**.

## Heure de démarrage

Module de calcul, sans équipement propre : il croise les prévisions
solaires, le talon de consommation de la maison et les tarifs Tempo pour
proposer le meilleur moment de chauffe du ballon.

![Onglet Heure de démarrage](images/05-heure-demarrage.png)

**Le calcul n'est jamais automatique.** Il n'a lieu que sur demande :
l'action de scénario `recalculer`, ou le bouton Recalculer de l'onglet. Son
résultat est mémorisé, et c'est cette photo que lisent le tableau de bord,
les infos du module et les déclencheurs.

C'est délibéré : le calcul ne retient que les créneaux **à venir**, donc une
info qui recalculait à chaque lecture donnait une heure qui reculait devant
l'horloge, que le déclencheur ne rattrapait jamais.

**La variable `heure_demarrage_chauffe_eau` fait foi.** Elle est écrite par
`recalculer`, modifiable à la main dans Configuration, et prime sur le
calcul mémorisé : si les deux diffèrent, l'interface affiche l'heure forcée
avec la mention correspondante.

Saison : le switch **Hiver** décide. S'il est éteint — que « Été » soit
allumé ou que les deux soient éteints — c'est **été**, donc la durée de
chauffe courte. Aucun basculement automatique par la date.

Infos utiles en condition : `heure_demarrage`, `mode_retenu`,
`calcul_du_jour` (le calcul date-t-il d'aujourd'hui ?), `heure_calcul`,
`gain_estime_eur`, `surplus_creneau_kwh`.

## Tempo

Couleurs des jours EDF Tempo via l'API RTE, tarifs heures pleines / creuses
par couleur, compteurs de saison. Alimente l'arbitrage de coût du module
Heure de démarrage.

## Chauffe-eau, Climatisation, Capteurs

Modules d'équipement : ils exposent leur état en **infos** et leurs
commandes en **actions de scénario**. Le paramétrage (identifiants
Cozytouch, Hi-Kumo, Tuya) se fait dans l'onglet du module.

Le chauffe-eau Atlantic n'a pas de commande « chauffer » directe : forcer
une chauffe complète revient à régler le **nombre de douches souhaitées au
maximum**, et revenir au fonctionnement normal à le remettre au minimum.
C'est ce que font les fonctions `chauffer` et `eteindre`.
