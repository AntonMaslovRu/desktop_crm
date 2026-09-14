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

## Доступ сервера к репозиторию

Репозиторий приватный. На сервере: `sudo -u envo ssh-keygen -t ed25519 -N '' -f /var/lib/envo/.ssh/id_ed25519`,
публичный ключ — в GitHub → репозиторий → Settings → Deploy keys (только чтение). Тогда
`ENVO_REPO=git@github.com:AntonMaslovRu/desktop_crm.git` для bootstrap.sh.

## DNS и порты

`api.envo.live` — A-запись на `185.50.203.184`. В панели хостера открыть входящие
**TCP 80 и 443** — иначе Let's Encrypt не достучится и Caddy не выпустит сертификат.

## Что видно с сервера (проверено 14.09.2026)

Афиша, ЦБ, Microsoft Graph — да. **api.telegram.org и news.google.com — нет**: российский
датацентр. Поэтому алерты владельцу идут письмом через Graph, а Telegram доставляет
Mac-приложение (с Mac эти адреса доступны). Лента новостей — через источники, доступные
из РФ, либо через Mac-воркер.

## Порядок

1. `sudo ENVO_API_HOST=api.envo.live bash deploy/bootstrap.sh` — ставит Postgres, Python и
   Caddy, заводит пользователя, каталоги, базу и схему, виртуальное окружение, две службы
   (ядро и API) и TLS для API. Идемпотентен. Хост API должен A-записью смотреть на сервер.
2. Заполнить `/etc/envo/env` по `.env.example`. Секреты — только новые, после ротации.
3. `/opt/envo/run envoctl check` — обёртка подставляет секреты из `/etc/envo/env`
   (systemd-шный `EnvironmentFile` ручным командам ничего не даёт).
4. `/opt/envo/run envoctl mail-login` — код на экране, вход в браузере
   под support@envo.live. Один раз; токен в `/var/lib/envo/graph_token.json`.
5. `/opt/envo/run envoctl seed` — справочник событий и ступени.
6. `/opt/envo/run envoctl user-add anton` — пользователь приложения.
7. `sudo systemctl enable --now envo envo-api` — ядро стартует, проверяет сеть и пишет
   в Telegram; API слушает 127.0.0.1:8765 за Caddy.
8. Параллельная работа с Cowork-рутинами: сутки для лестницы, трое суток для продаж,
   двадцать писем для почты. Затем рутины выключаются по одной.

## Секреты, которые нужно ротировать до запуска

Пароль CRM `Maslov_crm_api`, shared secret Apps Script, токен Telegram-бота — они лежали
открытым текстом в промптах рутин. Новые значения вписываются только в `/etc/envo/env`.

## Mac-приложение: сборка в GitHub Actions

Workflow `.github/workflows/mac.yml` собирает `Envo Desk.app` на macOS-раннере и кладёт
`EnvoDesk.dmg` в артефакты и в Release. Без секретов — неподписанная сборка: первый запуск
через правый клик → «Открыть». Для подписи и нотаризации — секреты репозитория
(Settings → Secrets and variables → Actions):

| Секрет | Что это |
|---|---|
| `APPLE_TEAM_ID` | Team ID из developer.apple.com → Membership |
| `MAC_CERT_P12` | сертификат Developer ID Application, экспорт из Keychain в .p12, в base64: `base64 -i cert.p12 \| pbcopy` |
| `MAC_CERT_PASSWORD` | пароль экспорта .p12 |
| `NOTARY_KEY_ID`, `NOTARY_ISSUER_ID`, `NOTARY_KEY_P8` | ключ App Store Connect API (Users and Access → Integrations → Team Keys, роль Developer); `.p8` целиком |

## Бэкап

`pg_dump envo | gzip > /var/backups/envo/$(date +%F).sql.gz` плюс каталог
`/var/lib/envo/files` (квитанции) — по cron раз в сутки, копия на шифрованный носитель.
