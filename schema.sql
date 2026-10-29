CREATE SEQUENCE IF NOT EXISTS seq_commit_id START 1;

CREATE TABLE IF NOT EXISTS satellite_scenes (
    scene_id VARCHAR PRIMARY KEY,
    observation_date VARCHAR,
    timestamp_utc VARCHAR,
    platform VARCHAR,
    mgrs_tile VARCHAR,
    h3_index VARCHAR,
    cloud_cover_pct DOUBLE,
    min_lon DOUBLE,
    min_lat DOUBLE,
    max_lon DOUBLE,
    max_lat DOUBLE,
    centroid_lat DOUBLE,
    centroid_lon DOUBLE,
    thumbnail_url VARCHAR,
    content_hash VARCHAR,
    indexed_at_utc VARCHAR
);

CREATE TABLE IF NOT EXISTS satellite_ledger (
    commit_id BIGINT PRIMARY KEY,
    timestamp_utc VARCHAR,
    operation VARCHAR,
    scene_id VARCHAR,
    mgrs_tile VARCHAR,
    cloud_cover_pct DOUBLE,
    h3_index VARCHAR,
    detail VARCHAR
);
