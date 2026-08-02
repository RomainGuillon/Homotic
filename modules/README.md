# Répertoire des modules

Chaque module de la v2 sera un sous-répertoire ici (ex : `tempo/`, `chauffe_eau/`...).

Structure d'un module (contrat défini à l'étape 3) :

- `conf.py` — manifest : nom de l'onglet, fonctions exposées aux scénarios, périodicités, champs de configuration requis (clés API, login...)
- `onglet/` — code d'affichage de l'onglet du module
- `dashboard/` — code d'affichage du bloc dans le tableau de bord
- `fonctions/` — fonctions du module (accès API, affichage onglet, affichage dashboard, actions scénario)

Pour ajouter un module : copier/coller son répertoire ici, puis onglet
Configuration → cocher le module → Valider.
