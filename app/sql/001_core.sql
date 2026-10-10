-- Product data (ADR-016). Column names match the pydantic models in app/models.py.
CREATE TABLE users (
    id            uuid PRIMARY KEY,
    created_at    timestamptz NOT NULL DEFAULT now(),
    api_key_hash  text NOT NULL UNIQUE
);

CREATE TABLE projects (
    id              uuid PRIMARY KEY,
    created_at      timestamptz NOT NULL DEFAULT now(),
    user_id         uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name            text NOT NULL,
    language        text NOT NULL DEFAULT 'auto',
    caption_script  text NOT NULL DEFAULT 'devanagari'
);
CREATE INDEX projects_user_idx ON projects (user_id);

CREATE TABLE assets (
    id                    uuid PRIMARY KEY,
    created_at            timestamptz NOT NULL DEFAULT now(),
    project_id            uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    kind                  text NOT NULL,
    storage_key           text NOT NULL,
    sha256                text NOT NULL,
    source_sha            text,
    source                text NOT NULL,
    license               text NOT NULL,          -- rule 7: never null
    attribution_required  boolean NOT NULL DEFAULT false,
    attribution_text      text,
    commercial_use        boolean NOT NULL DEFAULT true,
    width                 integer,
    height                integer,
    duration_ms           bigint
);
CREATE INDEX assets_project_kind_idx ON assets (project_id, kind);

CREATE TABLE timelines (
    id               uuid PRIMARY KEY,
    created_at       timestamptz NOT NULL DEFAULT now(),
    project_id       uuid NOT NULL UNIQUE REFERENCES projects (id) ON DELETE CASCADE,
    current_version  integer NOT NULL DEFAULT 1,
    style_id         text,
    trend_pack       text,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE timeline_versions (
    id              uuid PRIMARY KEY,
    created_at      timestamptz NOT NULL DEFAULT now(),
    timeline_id     uuid NOT NULL REFERENCES timelines (id) ON DELETE CASCADE,
    version         integer NOT NULL,
    edl             jsonb NOT NULL,
    parent_version  integer,
    UNIQUE (timeline_id, version)
);

CREATE TABLE messages (
    id          uuid PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    project_id  uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    role        text NOT NULL,
    content     text NOT NULL,
    tool_calls  jsonb
);
CREATE INDEX messages_project_time_idx ON messages (project_id, created_at);

CREATE TABLE jobs (
    id               uuid PRIMARY KEY,
    created_at       timestamptz NOT NULL DEFAULT now(),
    project_id       uuid REFERENCES projects (id) ON DELETE CASCADE,
    type             text NOT NULL,
    status           text NOT NULL DEFAULT 'queued',
    progress         double precision NOT NULL DEFAULT 0,
    idempotency_key  text NOT NULL UNIQUE,
    payload          jsonb NOT NULL DEFAULT '{}',
    error            text,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE renders (
    id                   uuid PRIMARY KEY,
    created_at           timestamptz NOT NULL DEFAULT now(),
    project_id           uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    timeline_version_id  uuid NOT NULL,
    timeline_hash        text NOT NULL DEFAULT '',
    profile              text NOT NULL,
    status               text NOT NULL DEFAULT 'queued',
    output_url           text,
    output_key           text,
    attributions         jsonb NOT NULL DEFAULT '[]'
);
CREATE INDEX renders_project_idx ON renders (project_id);

CREATE TABLE analyses (
    id           uuid PRIMARY KEY,
    created_at   timestamptz NOT NULL DEFAULT now(),
    project_id   uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    source_sha   text NOT NULL,
    analyzer     text NOT NULL,
    version      text NOT NULL,
    params_hash  text NOT NULL,
    bundle_key   text NOT NULL,
    UNIQUE (source_sha, analyzer, version, params_hash)
);
CREATE INDEX analyses_project_time_idx ON analyses (project_id, created_at DESC);
