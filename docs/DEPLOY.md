# Развёртывание

Сервер: виртуалка, Ubuntu/Debian, Postgres 15+, Python 3.11+. Ядро работает как
systemd-служба `envo` под пользователем `envo`; секреты в `/etc/envo/env` с правами 600.

## Идентификаторы (не секреты)

| Что | Значение |
|---|---|
| Entra ID · Tenant | `0594300e-2002-44ff-bfe1-e4a03e6ef2fc` |
| Entra ID · Application (client) | `ccdde7bc-969d-4ecd-88e0-62b80547c93e` |
| Ящик отправки | `support@envo.live` |
| CRM Афиши · city_id | из `/etc/envo/env` |

Регистрация проверена 13.09.2026: device-code поток запускается. Права `Mail.Send`,
`Mail.Read` (delegated) с admin consent — проверяются при `envoctl mail-login`.

## Порядок

1. `sudo ENVO_API_HOST=api.envo.live bash deploy/bootstrap.sh` — ставит Postgres, Python и
   Caddy, заводит пользователя, каталоги, базу и схему, виртуальное окружение, две службы
   (ядро и API) и TLS для API. Идемпотентен. Хост API должен A-записью смотреть на сервер.
2. Заполнить `/etc/envo/env` по `.env.example`. Секреты — только новые, после ротации.
3. `sudo -u envo /opt/envo/venv/bin/envoctl check` — шесть доменов должны ответить.
4. `sudo -u envo /opt/envo/venv/bin/envoctl mail-login` — код на экране, вход в браузере
   под support@envo.live. Один раз; токен в `/var/lib/envo/graph_token.json`.
5. `sudo -u envo /opt/envo/venv/bin/envoctl seed` — справочник событий и ступени.
6. `sudo -u envo /opt/envo/venv/bin/envoctl user-add anton` — пользователь приложения.
7. `sudo systemctl enable --now envo envo-api` — ядро стартует, проверяет сеть и пишет
   в Telegram; API слушает 127.0.0.1:8765 за Caddy.
8. Параллельная работа с Cowork-рутинами: сутки для лестницы, трое суток для продаж,
   двадцать писем для почты. Затем рутины выключаются по одной.

## Секреты, которые нужно ротировать до запуска

Пароль CRM `Maslov_crm_api`, shared secret Apps Script, токен Telegram-бота — они лежали
открытым текстом в промптах рутин. Новые значения вписываются только в `/etc/envo/env`.

## Бэкап

`pg_dump envo | gzip > /var/backups/envo/$(date +%F).sql.gz` плюс каталог
`/var/lib/envo/files` (квитанции) — по cron раз в сутки, копия на шифрованный носитель.
