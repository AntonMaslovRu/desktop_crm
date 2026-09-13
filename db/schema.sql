-- Envo Desk — схема базы. Postgres 15+.
-- В центре событие: город и площадка → событие → клиенты → заказы → билеты.
-- Приоритет источников на уровне поля живёт в event_facts, а не в колонках событий.

CREATE TABLE users (
    id            bigserial PRIMARY KEY,
    login         text NOT NULL UNIQUE,
    password_hash text NOT NULL,             -- argon2id
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);

-- ---------- каталог ----------

CREATE TABLE cities (
    id       bigserial PRIMARY KEY,
    country  text NOT NULL,
    name     text NOT NULL,
    tracked  boolean NOT NULL DEFAULT true,  -- участвует в запросах ленты
    UNIQUE (country, name)
);

CREATE TABLE venues (
    id       bigserial PRIMARY KEY,
    city_id  bigint NOT NULL REFERENCES cities ON DELETE RESTRICT,
    name     text NOT NULL,
    capacity integer,
    url      text,
    tracked  boolean NOT NULL DEFAULT true,
    UNIQUE (city_id, name)
);

CREATE TABLE events (
    id           bigserial PRIMARY KEY,
    afisha_id    bigint UNIQUE,              -- NULL у событий, заведённых руками
    title        text NOT NULL,
    display_name text NOT NULL,
    venue_id     bigint REFERENCES venues ON DELETE SET NULL,
    starts_at    timestamp NOT NULL,         -- как отдаёт Афиша, без пояса: местное время площадки
    kind         text,                       -- концерт, спорт, шоу
    tracking     boolean NOT NULL DEFAULT true,
    afisha_status text,
    afisha_venue_id bigint,
    raw          jsonb,
    letter_single text,
    letter_multi  text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON events (starts_at);

-- Обогащение. Ручное значение приоритетнее парсинга, парсинг — приоритетнее Афиши.
CREATE TABLE event_facts (
    event_id   bigint NOT NULL REFERENCES events ON DELETE CASCADE,
    field      text NOT NULL,
    value      text NOT NULL,
    source     text NOT NULL CHECK (source IN ('manual', 'parser', 'afisha')),
    source_url text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, field)
);

CREATE TABLE categories (
    id       bigserial PRIMARY KEY,
    event_id bigint NOT NULL REFERENCES events ON DELETE CASCADE,
    name     text NOT NULL,
    UNIQUE (event_id, name)
);

-- «Категория 6» и «Верхний ярус секторов 101–115» — одно и то же. Заполняется руками.
CREATE TABLE category_aliases (
    id          bigserial PRIMARY KEY,
    category_id bigint NOT NULL REFERENCES categories ON DELETE CASCADE,
    alias       text NOT NULL,
    UNIQUE (category_id, alias)
);

-- Ступени наблюдаются, а не управляются: цену ставит человек в кабинете Афиши.
CREATE TABLE steps (
    id          bigserial PRIMARY KEY,
    category_id bigint NOT NULL REFERENCES categories ON DELETE CASCADE,
    idx         integer NOT NULL,
    price       numeric(12,2) NOT NULL,
    quota       integer NOT NULL,
    sold        integer NOT NULL DEFAULT 0,
    opened_at   timestamptz,
    closed_at   timestamptz,
    UNIQUE (category_id, idx)
);

-- ---------- клиенты и продажи ----------

CREATE TABLE contacts (
    id         bigserial PRIMARY KEY,
    afisha_customer_id text,                     -- стабильный id покупателя у Афиши, первый ключ склейки
    email      text,
    phone      text,
    name       text,
    salutation text,
    consent    boolean NOT NULL DEFAULT false,   -- согласие на анонсы, наше
    consent_afisha boolean,                      -- is_subscripted у Афиши
    merged_into bigint REFERENCES contacts ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ON contacts (email) WHERE email IS NOT NULL AND merged_into IS NULL;
CREATE UNIQUE INDEX ON contacts (afisha_customer_id)
    WHERE afisha_customer_id IS NOT NULL AND merged_into IS NULL;
CREATE INDEX ON contacts (phone) WHERE phone IS NOT NULL;

-- Человек меняет почту и телефон. Основное поле — самое свежее, остальные помним:
-- по ним ищется переписка в Outlook и ловятся дубли.
CREATE TABLE contact_emails (
    contact_id    bigint NOT NULL REFERENCES contacts ON DELETE CASCADE,
    email         text NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (contact_id, email)
);

CREATE TABLE contact_phones (
    contact_id    bigint NOT NULL REFERENCES contacts ON DELETE CASCADE,
    phone         text NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (contact_id, phone)
);

CREATE TABLE contact_tags (
    contact_id bigint NOT NULL REFERENCES contacts ON DELETE CASCADE,
    tag        text NOT NULL,
    manual     boolean NOT NULL DEFAULT true,     -- false у автосегментов
    PRIMARY KEY (contact_id, tag)
);

CREATE TABLE orders (
    id           bigserial PRIMARY KEY,
    afisha_id    text UNIQUE,
    event_id     bigint REFERENCES events ON DELETE SET NULL,
    contact_id   bigint REFERENCES contacts ON DELETE SET NULL,
    channel      text NOT NULL DEFAULT 'afisha' CHECK (channel IN ('afisha', 'direct')),
    -- cart: не оплачен · paid · refund · unknown: статус Афиши, которого мы не знаем
    status       text NOT NULL CHECK (status IN ('cart', 'paid', 'refund', 'unknown')),
    afisha_status integer,
    agent_id     bigint,                       -- витрина: Афиша, виджет, закрытые продажи
    showcase     text,
    ordered_at   timestamptz,
    total        numeric(12,2) NOT NULL DEFAULT 0,
    tickets_count integer NOT NULL DEFAULT 0,
    fingerprint  text,                          -- что менялось с прошлого прогона; иначе order.info не дёргаем
    welcome_sent_at timestamptz,
    tickets_sent_at timestamptz,
    tickets_sent_by text CHECK (tickets_sent_by IN ('outlook', 'manual')),
    raw          jsonb,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON orders (event_id, ordered_at DESC);
CREATE INDEX ON orders (contact_id);

CREATE TABLE tickets (
    order_id    bigint NOT NULL REFERENCES orders ON DELETE CASCADE,
    afisha_id   text NOT NULL,
    category_id bigint REFERENCES categories ON DELETE SET NULL,
    sector      text,
    barcode     text,
    price       numeric(12,2) NOT NULL DEFAULT 0,
    refundable  boolean,                        -- из скобок в названии сектора
    status      text NOT NULL DEFAULT 'sold' CHECK (status IN ('sold', 'refund', 'cart')),
    afisha_status text,
    sold_at     timestamptz,
    PRIMARY KEY (order_id, afisha_id)          -- ключ дедупа: запись билета идемпотентна
);

-- Догон брошенных корзин. Заказ со статусом cart — сама корзина; здесь — что мы с ней делали.
CREATE TABLE cart_followups (
    order_id       bigint PRIMARY KEY REFERENCES orders ON DELETE CASCADE,
    state          text NOT NULL DEFAULT 'quarantine'
                   CHECK (state IN ('quarantine', 'ready', 'sent', 'replied', 'bought', 'excluded')),
    reason         text,                       -- почему исключена: купил сам, событие снято, поздно
    letter_sent_at timestamptz,
    replied_at     timestamptz,
    result         text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------- закупка ----------

CREATE TABLE suppliers (
    id           bigserial PRIMARY KEY,
    name         text NOT NULL UNIQUE,
    kind         text CHECK (kind IN ('official', 'aggregator', 'resale', 'private')),
    url          text,
    currency     text,
    default_fee  numeric(5,4) DEFAULT 0.10,
    reliability  text,
    notes        text
);

CREATE TABLE payment_accounts (
    id       bigserial PRIMARY KEY,
    name     text NOT NULL UNIQUE,           -- «карта ·4417», «счёт AED»
    kind     text CHECK (kind IN ('card', 'account', 'crypto', 'cash')),
    owner    text,
    currency text
);

-- Покупка — один платёж. Строки внутри могут быть из разных категорий и событий.
CREATE TABLE purchases (
    id           bigserial PRIMARY KEY,
    supplier_id  bigint REFERENCES suppliers ON DELETE SET NULL,
    account_id   bigint REFERENCES payment_accounts ON DELETE SET NULL,
    paid_at      date NOT NULL,
    currency     text NOT NULL,
    rate         numeric(12,6) NOT NULL,      -- курс ЦБ на дату
    rate_buffer  numeric(6,2) NOT NULL DEFAULT 1.0,
    actual_charged numeric(14,2),             -- сколько реально списал банк, если известно
    status       text NOT NULL DEFAULT 'paid'
                 CHECK (status IN ('paid', 'received', 'problem')),
    reference    text,
    notes        text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE purchase_lines (
    id          bigserial PRIMARY KEY,
    purchase_id bigint NOT NULL REFERENCES purchases ON DELETE CASCADE,
    event_id    bigint NOT NULL REFERENCES events ON DELETE RESTRICT,
    category_id bigint REFERENCES categories ON DELETE SET NULL,
    qty         integer NOT NULL CHECK (qty > 0),
    unit_price  numeric(12,2) NOT NULL,       -- в валюте поставщика, без его сборов
    seller_fee  numeric(5,4) NOT NULL DEFAULT 0.10,
    unit_cost_rub numeric(12,2) NOT NULL,     -- посчитано при сохранении
    written_off_at timestamptz                -- остаток, списанный в убыток после события
);
CREATE INDEX ON purchase_lines (event_id);

CREATE TABLE purchase_files (
    id          bigserial PRIMARY KEY,
    purchase_id bigint NOT NULL REFERENCES purchases ON DELETE CASCADE,
    path        text NOT NULL,                -- рядом с базой, попадает в шифрованный бэкап
    filename    text NOT NULL,
    uploaded_at timestamptz NOT NULL DEFAULT now()
);

-- ---------- общение ----------

CREATE TABLE letters (
    id          bigserial PRIMARY KEY,
    kind        text NOT NULL,                -- welcome | cart | tickets | reply | announce
    ref_id      text NOT NULL,                -- заказ, корзина или входящее письмо
    contact_id  bigint REFERENCES contacts ON DELETE SET NULL,
    to_addr     text NOT NULL,
    subject     text NOT NULL,
    body_html   text NOT NULL,
    state       text NOT NULL DEFAULT 'queued'
                CHECK (state IN ('queued', 'held', 'sent', 'failed', 'skipped')),
    provider_message_id text,
    attempts    integer NOT NULL DEFAULT 0,
    last_error  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    sent_at     timestamptz,
    UNIQUE (kind, ref_id)                     -- одно письмо на заказ или корзину
);

CREATE TABLE inbox (
    id          bigserial PRIMARY KEY,
    channel     text NOT NULL CHECK (channel IN ('outlook', 'telegram')),
    external_id text NOT NULL,
    contact_id  bigint REFERENCES contacts ON DELETE SET NULL,
    order_id    bigint REFERENCES orders ON DELETE SET NULL,
    topic       text,                          -- где билеты, возврат, смена имени…
    received_at timestamptz NOT NULL,
    body        text NOT NULL,
    draft       text,
    state       text NOT NULL DEFAULT 'new'
                CHECK (state IN ('new', 'drafted', 'answered', 'ignored')),
    edited      boolean NOT NULL DEFAULT false, -- черновик правили руками
    UNIQUE (channel, external_id)
);

-- Копилка одобренных ответов: это и есть «обучение» системы.
CREATE TABLE reply_templates (
    id         bigserial PRIMARY KEY,
    topic      text NOT NULL,
    body       text NOT NULL,
    approvals  integer NOT NULL DEFAULT 0,
    clean_run  integer NOT NULL DEFAULT 0,     -- подряд без правок
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- ---------- рынок ----------

CREATE TABLE feed (
    id          bigserial PRIMARY KEY,
    kind        text NOT NULL CHECK (kind IN ('tour', 'onsale', 'soldout', 'moved', 'other')),
    title       text NOT NULL,
    url         text NOT NULL,
    source      text NOT NULL,
    city_id     bigint REFERENCES cities ON DELETE SET NULL,
    venue_id    bigint REFERENCES venues ON DELETE SET NULL,
    cluster_key text NOT NULL,                 -- дубликаты схлопываются по нему
    seen_at     timestamptz NOT NULL DEFAULT now(),
    state       text NOT NULL DEFAULT 'new' CHECK (state IN ('new', 'hidden', 'used')),
    UNIQUE (source, url)
);
CREATE INDEX ON feed (cluster_key);

CREATE TABLE feed_queries (
    id       bigserial PRIMARY KEY,
    query    text NOT NULL,
    lang     text NOT NULL DEFAULT 'ru',
    city_id  bigint REFERENCES cities ON DELETE CASCADE,
    venue_id bigint REFERENCES venues ON DELETE CASCADE,
    manual   boolean NOT NULL DEFAULT false,   -- правится руками
    enabled  boolean NOT NULL DEFAULT true
);

CREATE TABLE watch_items (
    id          bigserial PRIMARY KEY,
    event_id    bigint NOT NULL REFERENCES events ON DELETE CASCADE,
    category_id bigint REFERENCES categories ON DELETE SET NULL,
    supplier_id bigint REFERENCES suppliers ON DELETE SET NULL,
    url         text NOT NULL,
    method      text NOT NULL CHECK (method IN ('api', 'jsonld', 'page', 'manual')),
    selector    text,
    currency    text NOT NULL,
    period_hours integer NOT NULL DEFAULT 3,
    last_checked_at timestamptz,
    last_error  text
);

CREATE TABLE price_points (
    id       bigserial PRIMARY KEY,
    watch_id bigint NOT NULL REFERENCES watch_items ON DELETE CASCADE,
    price    numeric(12,2) NOT NULL,
    fee      numeric(5,4),
    rate     numeric(12,6),
    cost_rub numeric(12,2),
    seen_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON price_points (watch_id, seen_at DESC);

-- ---------- служебное ----------

CREATE TABLE runs (
    id          bigserial PRIMARY KEY,
    job         text NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'ok', 'failed')),
    stats       jsonb,
    error       text
);
CREATE INDEX ON runs (job, started_at DESC);

-- Шина событий: модули не вызывают друг друга, правила подписываются на сообщения.
CREATE TABLE events_log (
    id           bigserial PRIMARY KEY,
    topic        text NOT NULL,                -- order.created, price.changed, step.closed…
    payload      jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
CREATE INDEX ON events_log (topic, created_at DESC);
CREATE INDEX ON events_log (processed_at) WHERE processed_at IS NULL;

CREATE TABLE audit (
    id         bigserial PRIMARY KEY,
    actor      text NOT NULL,                  -- login или «система»
    action     text NOT NULL,
    payload    jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE kv (
    key   text PRIMARY KEY,
    value text NOT NULL
);
