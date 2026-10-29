CREATE SEQUENCE IF NOT EXISTS seq_commit_id START 1;

CREATE TABLE IF NOT EXISTS satellite_scenes (
    scene_id VARCHAR PRIMARY KEY,
    corridor_id VARCHAR,
    collection VARCHAR,
    sensor_type VARCHAR,
    observation_date VARCHAR,
    timestamp_utc VARCHAR,
    platform VARCHAR,
    mgrs_or_orbit VARCHAR,
    cloud_cover_pct DOUBLE,
    min_lon DOUBLE,
    min_lat DOUBLE,
    max_lon DOUBLE,
    max_lat DOUBLE,
    centroid_lat DOUBLE,
    centroid_lon DOUBLE,
    canonical_asset_href VARCHAR,
    thumbnail_url VARCHAR,
    content_hash VARCHAR,
    indexed_at_utc VARCHAR
);

CREATE TABLE IF NOT EXISTS scene_h3_coverage (
    h3_index VARCHAR,
    scene_id VARCHAR,
    corridor_id VARCHAR,
    sensor_type VARCHAR,
    observation_date VARCHAR,
    cloud_cover_pct DOUBLE,
    PRIMARY KEY (h3_index, scene_id)
);

CREATE TABLE IF NOT EXISTS corridor_deltas (
    delta_id VARCHAR PRIMARY KEY,
    corridor_id VARCHAR,
    mgrs_or_orbit VARCHAR,
    latest_scene_id VARCHAR,
    previous_scene_id VARCHAR,
    latest_time_utc VARCHAR,
    previous_time_utc VARCHAR,
    cloud_delta_pct DOUBLE,
    change_intensity VARCHAR,
    evaluated_at_utc VARCHAR
);

CREATE TABLE IF NOT EXISTS satellite_ledger (
    commit_id BIGINT PRIMARY KEY,
    timestamp_utc VARCHAR,
    operation VARCHAR,
    corridor_id VARCHAR,
    scene_id VARCHAR,
    sensor_type VARCHAR,
    h3_cell_count INTEGER,
    detail VARCHAR
);
