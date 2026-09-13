#!/usr/bin/env bash
# Envo Desk — первичная установка на сервер. Запускать под root, повторный запуск безопасен.
set -euo pipefail

APP_DIR=/opt/envo/app
VENV=/opt/envo/venv
REPO=${ENVO_REPO:-https://github.com/AntonMaslovRu/desktop_crm.git}
BRANCH=${ENVO_BRANCH:-main}

echo "== пакеты =="
apt-get update -qq
apt-get install -y -qq postgresql python3-venv python3-pip git curl >/dev/null

echo "== пользователь и каталоги =="
id envo >/dev/null 2>&1 || useradd --system --home /var/lib/envo --shell /usr/sbin/nologin envo
install -d -o envo -g envo -m 750 /var/lib/envo /var/lib/envo/files /var/backups/envo /opt/envo
install -d -m 750 /etc/envo

echo "== база =="
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='envo'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE ROLE envo LOGIN"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='envo'" | grep -q 1 \
  || sudo -u postgres createdb -O envo envo

echo "== код =="
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch -q origin "$BRANCH" && git -C "$APP_DIR" checkout -q "origin/$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO" "$APP_DIR"
fi
chown -R envo:envo /opt/envo
[ -x "$VENV/bin/python" ] || sudo -u envo python3 -m venv "$VENV"
sudo -u envo "$VENV/bin/pip" install -q --upgrade pip
sudo -u envo "$VENV/bin/pip" install -q -e "$APP_DIR"

echo "== схема =="
# схема применяется только в пустую базу; дальше — миграции
if [ "$(sudo -u postgres psql -d envo -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")" = "0" ]; then
  sudo -u envo psql -d envo -q -v ON_ERROR_STOP=1 -f "$APP_DIR/db/schema.sql"
  echo "схема применена"
else
  echo "база не пустая, схему не трогаю"
fi

echo "== конфиг и служба =="
if [ ! -f /etc/envo/env ]; then
  sed 's|^ENVO_DB_DSN=.*|ENVO_DB_DSN=postgresql://envo@/envo|' "$APP_DIR/.env.example" > /etc/envo/env
  chown root:envo /etc/envo/env && chmod 640 /etc/envo/env
  echo "создан /etc/envo/env — заполни секреты"
fi
install -m 644 "$APP_DIR/deploy/envo.service" /etc/systemd/system/envo.service
systemctl daemon-reload

echo "== сеть =="
sudo -u envo "$VENV/bin/envoctl" check

cat <<MSG

Готово. Дальше:
  1) nano /etc/envo/env            — секреты
  2) sudo -u envo $VENV/bin/envoctl mail-login
  3) systemctl enable --now envo && journalctl -u envo -f
MSG
