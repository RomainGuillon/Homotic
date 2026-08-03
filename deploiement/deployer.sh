#!/usr/bin/env bash
#
# Deploiement d'Homotic sur le serveur : recupere le code, met a jour ce qui
# en depend, et redemarre le service.
#
#   sudo /home/apps/app_python/Homotic/deploiement/deployer.sh
#
# Pourquoi un script plutot que « git pull » a la main : un pull seul ne
# suffit presque jamais. Une migration oubliee, un « collectstatic » saute,
# un service non redemarre — et l'application tourne sur l'ancien code sans
# que rien ne le signale. Ces oublis nous ont deja coute une soiree.

set -euo pipefail

PROJET="/home/apps/app_python/Homotic"
VENV="$PROJET/.venv/bin"
SERVICE="homotic"
BRANCHE="main"

cd "$PROJET"

echo "→ Recuperation du code"
avant=$(git rev-parse --short HEAD)
# « origin main » explicite plutot qu'un « git pull » nu : le script ne
# depend alors pas du suivi de branche, qui n'est pas configure apres un
# rattachement par « git init » + « fetch » + « reset --hard ».
git fetch origin "$BRANCHE"
git merge --ff-only "origin/$BRANCHE"
apres=$(git rev-parse --short HEAD)

if [ "$avant" = "$apres" ]; then
    echo "  deja a jour ($avant) — on continue quand meme, au cas ou une"
    echo "  etape aurait echoue au deploiement precedent."
else
    echo "  $avant → $apres"
    git --no-pager log --oneline "$avant..$apres" | sed 's/^/    /'
fi

echo "→ Dependances"
"$VENV/pip" install -q -r requirements.txt

echo "→ Migrations"
"$VENV/python" manage.py migrate --noinput

echo "→ Fichiers statiques"
"$VENV/python" manage.py collectstatic --noinput >/dev/null
echo "  collectes dans staticfiles/"

echo "→ Tests"
# Un deploiement qui casse la suite de tests ne doit pas atteindre le
# service : mieux vaut s'arreter ici, sur l'ancien code qui tourne encore.
"$VENV/python" manage.py test

echo "→ Droits"
# Le partage SMB ecrit avec l'utilisateur du partage : sans cela, les
# fichiers arrives par git appartiendraient au mauvais compte.
chown -R homotic:homotic-dev "$PROJET"

echo "→ Redemarrage du service"
systemctl restart "$SERVICE"
sleep 3
systemctl is-active --quiet "$SERVICE" || {
    echo "ECHEC : le service n'a pas redemarre."
    journalctl -u "$SERVICE" -n 30 --no-pager
    exit 1
}

echo "→ Verification"
code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/)
if [ "$code" = "302" ]; then
    echo "  OK — l'application repond et exige une connexion"
else
    echo "  ATTENTION : code $code au lieu de 302 attendu"
    journalctl -u "$SERVICE" -n 20 --no-pager
    exit 1
fi

echo
echo "Deploiement termine — https://homotic.rodica-romain.fr"
