# 10. Mise en ligne — accès depuis Internet

Objectif : atteindre `https://homotic.rodica-romain.fr` depuis n'importe où,
protégé par un identifiant et un mot de passe.

Trois endroits à configurer, dans cet ordre : **le serveur**, puis **Ionos**
(le nom de domaine), puis **Freebox OS** (la porte d'entrée). Faire le
serveur en dernier obligerait à ouvrir le port avant que l'application soit
prête à recevoir des visiteurs — autant éviter.

> **Ce que ce document change au fonctionnement.** L'application reste lancée
> par `manage.py runserver`, donc le scheduler démarre comme avant (voir
> `core/apps.py`) et les scénarios continuent de tourner à l'identique. Ce qui
> s'ajoute : un formulaire de connexion, nginx devant pour le HTTPS, et un
> service systemd pour que tout redémarre seul.

---

## 1. Sur le serveur

### 1.1 Créer l'utilisateur du service

Faire tourner l'application en `root` signifie qu'une faille dans Django ou
dans une de ses dépendances donne la machine entière. Un compte dédié, sans
shell, limite les dégâts à l'application elle-même.

```bash
sudo useradd -r -s /usr/sbin/nologin homotic
sudo chown -R homotic:homotic /home/apps/app_python/Homotic
```

### 1.2 Générer la clé secrète et le fichier d'environnement

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

```bash
sudo cp deploiement/homotic.env.exemple /etc/homotic.env
sudo chmod 600 /etc/homotic.env
sudo nano /etc/homotic.env      # coller la clé, vérifier le nom de domaine
```

`chmod 600` n'est pas décoratif : cette clé signe les cookies de session.
Qui la lit peut fabriquer une session valide et entrer sans mot de passe.

Ce fichier est lu à deux endroits : par systemd pour le service, et par
`settings.py` pour les commandes `manage.py` lancées à la main. Sans cette
seconde lecture, `collectstatic` et `createsuperuser` échoueraient faute de
clé alors que le service, lui, fonctionnerait.

### 1.3 Préparer les fichiers statiques

Hors mode développement, Django ne sert plus le CSS ni les icônes : c'est
nginx qui lira le répertoire produit ici.

```bash
cd /home/apps/app_python/Homotic
source .venv/bin/activate
python manage.py collectstatic --noinput
```

### 1.4 Créer le compte

```bash
python manage.py createsuperuser
```

Douze caractères minimum, et un mot de passe qui ne sert nulle part ailleurs
— c'est la seule chose entre Internet et le pilotage du chauffe-eau.

### 1.5 Installer le service

```bash
sudo cp deploiement/homotic.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now homotic
sudo systemctl status homotic
```

L'application écoute désormais sur `127.0.0.1:8000` — joignable seulement
depuis le serveur lui-même. C'est voulu : seul nginx doit lui parler.

### 1.6 Installer nginx et le certificat

Cela se fait en deux temps, et l'ordre a son importance. La configuration
finale référence un certificat : l'installer avant de l'avoir obtenu ferait
échouer `nginx -t`, et nginx cesserait de recharger quoi que ce soit. Mais
certbot, de son côté, a besoin qu'un bloc `server` réponde déjà pour ton
domaine — le site `default` ne suffit pas, il peut très bien être occupé à
autre chose.

D'où un fichier intermédiaire, `nginx-homotic-http.conf`, qui sert la
vérification de certbot et rend l'application accessible en HTTP.

```bash
sudo apt install nginx certbot
sudo systemctl enable --now nginx

sudo cp deploiement/nginx-homotic-http.conf /etc/nginx/sites-available/homotic
sudo ln -s /etc/nginx/sites-available/homotic /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Vérifier que la vérification pourra aboutir — l'en-tête `Host` est
indispensable, sans lui la requête tombe sur le site par défaut :

```bash
sudo mkdir -p /var/www/html/.well-known/acme-challenge
echo bonjour | sudo tee /var/www/html/.well-known/acme-challenge/test
curl -H "Host: homotic.rodica-romain.fr" \
     http://127.0.0.1/.well-known/acme-challenge/test
```

`bonjour` doit s'afficher. Alors seulement :

```bash
sudo certbot certonly --webroot -w /var/www/html -d homotic.rodica-romain.fr
```

Certbot a besoin que le domaine pointe déjà sur la Freebox **et** que le port
80 soit redirigé : faire les étapes 2 et 3 avant cette commande.

Le certificat obtenu, on bascule sur la configuration HTTPS :

```bash
sudo cp deploiement/nginx-homotic.conf /etc/nginx/sites-available/homotic
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Retirer le site `default` évite qu'une requête adressée à l'IP brute, sans nom
de domaine, tombe sur la page d'accueil de nginx — une information de moins
donnée aux robots qui scannent.

> Ne pas se connecter à l'application pendant la phase HTTP : le mot de passe
> circulerait en clair. Ce n'est de toute façon pas possible, les cookies de
> session étant marqués `Secure` — le formulaire refusera la connexion tant
> que le HTTPS n'est pas actif. La vérification de certbot, elle, ne demande
> aucune identification.

Le renouvellement est automatique (`certbot.timer`). Pour le vérifier :

```bash
sudo certbot renew --dry-run
```

---

## 2. Sur Ionos — l'enregistrement DNS

Espace client → **Domaines & SSL** → `rodica-romain.fr` → **DNS**.

Ajouter un enregistrement :

| Champ | Valeur |
| --- | --- |
| Type | `A` |
| Nom d'hôte | `homotic` |
| Valeur | l'IP publique de la Freebox (la même que `nas`) |
| TTL | 1 heure |

Vérifier depuis n'importe où, une fois la propagation faite :

```bash
dig +short homotic.rodica-romain.fr
```

> **Si l'IP publique de la Freebox change**, l'enregistrement devient faux et
> le site tombe. Les Freebox en IP fixe ne sont pas concernées ; sinon il faut
> un script de DNS dynamique qui met à jour l'enregistrement chez Ionos.

---

## 3. Sur Freebox OS — les redirections de ports

`http://mafreebox.freebox.fr` → **Paramètres de la Freebox** → **Gestion des
ports** → **Redirections de ports** → **Ajouter une redirection**.

Deux redirections, toutes deux en TCP vers l'IP locale du serveur Debian :

| Port externe | Vers | Port de destination | Rôle |
| --- | --- | --- | --- |
| 80 | IP du serveur | 80 | Vérification et renouvellement du certificat |
| 443 | IP du serveur | 443 | Le trafic du site |

Deux points qui bloquent souvent :

- **Le port 80 est peut-être déjà pris** par l'accès distant à Freebox OS.
  Dans ce cas : Paramètres → Mode avancé → Accès distant, et changer son port
  (par exemple 8080) pour libérer le 80.
- **L'IP locale du serveur doit être fixe.** Dans Freebox OS → DHCP → Baux
  statiques, associer l'IP à l'adresse MAC de la VM. Sinon un redémarrage lui
  donne une autre adresse et les redirections pointent dans le vide.

Ne rediriger que 80 et 443. Ni le 8000 (l'application, qui doit rester
inaccessible directement), ni le 22 (SSH).

---

## 4. Vérifications

```bash
# Depuis l'extérieur — partage de connexion du téléphone, pas le wifi maison
curl -I https://homotic.rodica-romain.fr
```

Attendu :

- `HTTP/2 302` avec `location: /comptes/login/?next=/` — l'application est
  bien fermée aux visiteurs anonymes ;
- l'en-tête `strict-transport-security` présent ;
- `http://homotic.rodica-romain.fr` (sans le « s ») répond `301` vers HTTPS.

Puis dans un navigateur : le formulaire de connexion, le cadenas fermé, et le
tableau de bord après identification.

Un test qui compte autant que les autres — l'application ne doit **pas** être
joignable en direct :

```bash
curl -I http://homotic.rodica-romain.fr:8000     # doit échouer
```

---

## 5. Ce qui reste à surveiller

L'ouverture d'un port change la nature de la machine : elle est désormais
scannée en permanence. Trois habitudes suffisent.

**Les mises à jour.** Une faille dans Django ou dans OpenSSL n'est plus un
sujet théorique.

```bash
sudo apt update && sudo apt upgrade
source .venv/bin/activate && pip list --outdated
```

**Les tentatives de connexion.** nginx limite déjà la page de login à
5 essais par minute et par adresse IP (voir `deploiement/nginx-homotic.conf`),
ce qui rend la force brute inexploitable. Pour bannir les IP insistantes,
`fail2ban` complète utilement :

```bash
sudo apt install fail2ban
```

**Les erreurs.** `DEBUG` étant désactivé, une erreur n'affiche plus de page
détaillée dans le navigateur. Le diagnostic se fait par :

```bash
sudo journalctl -u homotic -f       # erreurs Python
sudo tail -f /var/log/nginx/error.log
```

et par l'onglet **Journal** de l'application, qui reste la première chose à
regarder.

---

## Annexe — travailler en local après ces changements

Sur la machine de développement, créer un fichier `.env` à la racine du
projet (déjà ignoré par git) :

```
HOMOTIC_DEBUG=1
```

Cela suffit à retrouver le comportement d'avant : `runserver` sert les
fichiers statiques, les pages d'erreur redeviennent détaillées, et aucune clé
secrète n'est réclamée. La suite de tests, elle, tourne sans configuration.

Le formulaire de connexion reste actif en local — créer un compte avec
`python manage.py createsuperuser` sur la base de développement aussi.
