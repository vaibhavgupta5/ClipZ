-- Agent + pipeline telemetry as TimescaleDB hypertables (ADR-016). Append-only, no FKs:
-- logs outlive deleted projects. Each hypertable gets columnstore compression after
-- 7 days (created automatically by tsdb.segmentby) and a retention policy; hourly
-- continuous aggregates feed the /metrics dashboard without scanning raw rows.

-- One row per Director run (one chat message).
CREATE TABLE agent_runs (
    id                 uuid NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    project_id         uuid NOT NULL,
    model              text NOT NULL,
    prompt_hash        text NOT NULL,
    tools              text[] NOT NULL DEFAULT '{}',
    stopped            text NOT NULL DEFAULT '',
    iterations         integer NOT NULL DEFAULT 0,
    latency_ms         integer NOT NULL DEFAULT 0,
    first_token_ms     integer,
    input_tokens       integer NOT NULL DEFAULT 0,
    output_tokens      integer NOT NULL DEFAULT 0,
    cache_read_tokens  integer NOT NULL DEFAULT 0,
    cost_usd           double precision NOT NULL DEFAULT 0,
    PRIMARY KEY (id, created_at)
) WITH (tsdb.hypertable, tsdb.partition_column = 'created_at', tsdb.segmentby = 'model');

-- One row per tool call the Director made (validated or rejected).
CREATE TABLE tool_calls (
    id          uuid NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    run_id      uuid,
    project_id  uuid NOT NULL,
    tool        text NOT NULL,
    ok          boolean NOT NULL,
    error_code  text,
    latency_ms  integer NOT NULL DEFAULT 0,
    PRIMARY KEY (id, created_at)
) WITH (tsdb.hypertable, tsdb.partition_column = 'created_at', tsdb.segmentby = 'tool');

-- One row per finished pipeline stage (ingest, analyze, render-preview, render-final...).
CREATE TABLE pipeline_stages (
    id           uuid NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    project_id   uuid,
    job_id       uuid,
    stage        text NOT NULL,
    ok           boolean NOT NULL DEFAULT true,
    duration_ms  integer NOT NULL,
    media_s      double precision NOT NULL DEFAULT 0,  -- seconds of video processed
    PRIMARY KEY (id, created_at)
) WITH (tsdb.hypertable, tsdb.partition_column = 'created_at', tsdb.segmentby = 'stage');

-- High-frequency render progress ticks (the stream behind the progress bar).
CREATE TABLE render_progress (
    id          uuid NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    render_id   uuid NOT NULL,
    project_id  uuid,
    profile     text NOT NULL,
    progress    double precision NOT NULL,
    PRIMARY KEY (id, created_at)
) WITH (tsdb.hypertable, tsdb.partition_column = 'created_at', tsdb.segmentby = 'profile');

CREATE INDEX agent_runs_project_time_idx ON agent_runs (project_id, created_at DESC);
CREATE INDEX tool_calls_run_idx ON tool_calls (run_id, created_at DESC);
CREATE INDEX render_progress_render_idx ON render_progress (render_id, created_at DESC);

-- Hourly rollups (real-time: recent un-materialised rows are merged in at query time).
CREATE MATERIALIZED VIEW agent_runs_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 hour', created_at) AS bucket,
       model,
       count(*)                                                    AS runs,
       avg(latency_ms)::double precision                           AS avg_latency_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)    AS p95_latency_ms,
       avg(first_token_ms)::double precision                       AS avg_first_token_ms,
       sum(input_tokens)                                           AS input_tokens,
       sum(output_tokens)                                          AS output_tokens,
       sum(cache_read_tokens)                                      AS cache_read_tokens,
       sum(cost_usd)                                               AS cost_usd,
       count(*) FILTER (WHERE stopped IN ('answered', 'direct'))   AS answered,
       count(*) FILTER (WHERE stopped = 'asked')                   AS asked,
       count(*) FILTER (WHERE stopped IN ('cap', 'max_tokens', 'refusal')) AS failed
FROM agent_runs
GROUP BY bucket, model
WITH NO DATA;

CREATE MATERIALIZED VIEW tool_calls_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 hour', created_at) AS bucket,
       tool,
       count(*)                                     AS calls,
       count(*) FILTER (WHERE NOT ok)               AS errors,
       avg(latency_ms)::double precision            AS avg_latency_ms
FROM tool_calls
GROUP BY bucket, tool
WITH NO DATA;

CREATE MATERIALIZED VIEW pipeline_stages_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 hour', created_at) AS bucket,
       stage,
       count(*)                                                    AS runs,
       count(*) FILTER (WHERE NOT ok)                              AS failures,
       avg(duration_ms)::double precision                          AS avg_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)   AS p95_ms,
       sum(media_s)                                                AS media_s,
       sum(duration_ms) / 1000.0 / NULLIF(sum(media_s), 0)         AS realtime_factor
FROM pipeline_stages
GROUP BY bucket, stage
WITH NO DATA;

SELECT add_continuous_aggregate_policy('agent_runs_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '5 minutes');
SELECT add_continuous_aggregate_policy('tool_calls_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '5 minutes');
SELECT add_continuous_aggregate_policy('pipeline_stages_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '5 minutes');

-- Raw ticks are only useful briefly; rollups and run logs are kept longer.
SELECT add_retention_policy('render_progress', drop_after => INTERVAL '14 days');
SELECT add_retention_policy('tool_calls', drop_after => INTERVAL '180 days');
SELECT add_retention_policy('agent_runs', drop_after => INTERVAL '365 days');
SELECT add_retention_policy('pipeline_stages', drop_after => INTERVAL '365 days');
