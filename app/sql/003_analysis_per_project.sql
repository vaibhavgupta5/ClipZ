-- A content-hash cache hit must still link the bundle to the new project (otherwise the
-- Director sees no VAD/filler data). Uniqueness becomes per project; lookups by content
-- hash still find any project's bundle.
ALTER TABLE analyses DROP CONSTRAINT analyses_source_sha_analyzer_version_params_hash_key;
ALTER TABLE analyses ADD CONSTRAINT analyses_project_cache_key
    UNIQUE (project_id, source_sha, analyzer, version, params_hash);
CREATE INDEX analyses_cache_lookup_idx ON analyses (source_sha, analyzer, version, params_hash);
