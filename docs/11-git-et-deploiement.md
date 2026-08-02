# 11. Git et déploiement

Le code vit à deux endroits : ton poste de développement et le serveur. Les
recopier à la main, fichier par fichier, ne tient pas — un oubli ne se voit
pas, et se manifeste plus tard sous la forme d'une erreur qui n'a aucun
rapport apparent avec le fichier manquant.

Git règle ça : tu committes d'un côté, tu déploies de l'autre, et rien ne peut
manquer en route.

---

## Mise en place

### Sur le poste de développement

Prérequis : [Git pour Windows](https://git-scm.com/download/win).

```bash
cd C:\Dev\Homotic

git init -b main
git add .
git commit -m "Point de départ : socle, modules, mise en ligne"
```

Vérifier que rien de sensible n'est parti dans le dépôt — la commande ne doit
rien renvoyer :

```bash
git ls-files | findstr /i "sqlite3 .env staticfiles"
```

`deploiement/homotic.env.exemple` est normal : il ne contient que des
valeurs à remplacer, jamais la vraie clé.

Puis relier au dépôt privé et envoyer :

```bash
git remote add origin https://github.com/RomainGuillon/Homotic.git
git push -u origin main
```

Git pour Windows ouvrira une fenêtre d'authentification GitHub au premier
`push`, et retiendra l'accès ensuite.

### Sur le serveur

Le serveur n'a besoin que de **lire** le dépôt. Une clé de déploiement en
lecture seule vaut mieux qu'une clé personnelle : compromise, elle ne donne
accès qu'à ce dépôt, et sans droit d'écriture.

La clé appartient à `root`, parce que c'est `root` qui exécute
`deployer.sh` — une clé posée chez `homotic` ne serait jamais trouvée.

```bash
sudo ssh-keygen -t ed25519 -C "serveur homotic" \
     -f /root/.ssh/id_ed25519 -N ""
sudo ssh-keyscan github.com >> /root/.ssh/known_hosts
sudo cat /root/.ssh/id_ed25519.pub
```

`ssh-keyscan` enregistre l'empreinte de GitHub à l'avance : sans elle, le
premier `git pull` s'arrêterait sur une question de confirmation, et le script
de déploiement resterait bloqué sans rien afficher.

Coller la clé publique dans GitHub → dépôt → *Settings* → *Deploy keys* → *Add
deploy key*, **sans** cocher « Allow write access ».

Le répertoire appartient à `homotic`, alors que git s'exécutera en `root`.
Depuis git 2.35, cette différence bloque toute opération (« dubious
ownership ») : c'est une protection contre un dépôt piégé déposé par un autre
utilisateur. Ici la situation est voulue, on déclare l'exception une fois :

```bash
sudo git config --global --add safe.directory /home/apps/app_python/Homotic
```

Rattacher ensuite le répertoire existant au dépôt, sans perdre la base de
données ni l'environnement virtuel (tous deux ignorés par git, donc
intouchés) :

```bash
cd /home/apps/app_python/Homotic
sudo git init -b main
sudo git remote add origin git@github.com:RomainGuillon/Homotic.git
sudo git fetch origin
sudo git reset --hard origin/main
```

`reset --hard` remplace les fichiers suivis par ceux du dépôt. Les fichiers
non suivis — `db.sqlite3`, `.venv/`, `staticfiles/` — ne sont pas touchés.

Remettre les droits, que `git init` a pu bousculer :

```bash
sudo chown -R homotic:homotic-dev /home/apps/app_python/Homotic
sudo chmod +x /home/apps/app_python/Homotic/deploiement/deployer.sh
```

---

## Déployer

Une seule commande, désormais :

```bash
sudo /home/apps/app_python/Homotic/deploiement/deployer.sh
```

Le script enchaîne : récupération du code, dépendances, migrations, fichiers
statiques, **suite de tests**, droits, redémarrage, et vérification que
l'application répond.

Les tests sont volontairement placés avant le redémarrage : si le nouveau code
est cassé, le script s'arrête et l'ancien continue de tourner. Un déploiement
qui échoue vaut mieux qu'un déploiement qui casse la collecte de données.

Rendre le script exécutable, une fois :

```bash
chmod +x /home/apps/app_python/Homotic/deploiement/deployer.sh
```

---

## Le cycle de travail

1. Tu modifies le code sur ton poste, tu lances `python manage.py test`.
2. `git add`, `git commit`, `git push`.
3. Sur le serveur : `sudo deploiement/deployer.sh`.

Ce qui ne passe **jamais** par git, et reste propre à chaque machine :

| Fichier | Pourquoi |
| --- | --- |
| `db.sqlite3` | Le serveur a les mesures réelles, ton poste des données de test |
| `.env` / `/etc/homotic.env` | Contient la `SECRET_KEY` et les clés d'API |
| `.venv/` | Binaires compilés pour un système donné |
| `staticfiles/` | Reconstruit par `collectstatic` à chaque déploiement |

---

## Si le déploiement échoue

Le script s'arrête à la première erreur et te dit laquelle. Deux cas
fréquents :

**Les tests échouent.** L'ancien code tourne toujours, rien n'est cassé en
production. Corrige sur ton poste, committe, redéploie.

**Le service ne redémarre pas.** Le script affiche les 30 dernières lignes du
journal. Pour revenir à la version précédente le temps de comprendre :

```bash
cd /home/apps/app_python/Homotic
git log --oneline -5
git reset --hard <empreinte-precedente>
sudo systemctl restart homotic
```
