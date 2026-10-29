import os
import json
import hashlib
from datetime import datetime, timedelta, timezone
import requests
import duckdb
import h3
import pystac_client
import planetary_computer

DB_FILE = "data/satellite_feed.db"
HISTORY_DIR = "ledger"
DATA_DIR = "data"
EXPORT_DIR = "public"
API_DIR = os.path.join(EXPORT_DIR, "v1")
SCENES_DIR = os.path.join(API_DIR, "scenes")
CORRIDOR_DIR = os.path.join(API_DIR, "corridors")
DELTAS_DIR = os.path.join(API_DIR, "deltas")

CORRIDORS_FILE = "corridors.json"
H3_RESOLUTION = 6

def compute_sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()

def extract_h3_cells_for_bbox(bbox: list, resolution: int = H3_RESOLUTION) -> list[str]:
    min_lon, min_lat, max_lon, max_lat = bbox
    geo_json = {
        "type": "Polygon",
        "coordinates": [[
            [min_lon, min_lat],
            [max_lon, min_lat],
            [max_lon, max_lat],
            [min_lon, max_lat],
            [min_lon, min_lat]
        ]]
    }
    try:
        cells = list(h3.polyfill(geo_json, resolution, geo_json_conform=True))
        if cells:
            return cells
    except Exception:
        pass
    c_lat = (min_lat + max_lat) / 2.0
    c_lon = (min_lon + max_lon) / 2.0
    return [h3.geo_to_h3(c_lat, c_lon, resolution)]

def query_optical_sentinel2(catalog, bbox: list, date_range: str, max_cloud: float) -> list:
    try:
        search = catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=bbox,
            datetime=date_range,
            query={"eo:cloud_cover": {"lt": max_cloud}}
        )
        return list(search.items())
    except Exception as e:
        print(f"Optical Sentinel-2 query failed: {e}")
        return []

def query_radar_sar_sentinel1(catalog, bbox: list, date_range: str) -> list:
    try:
        search = catalog.search(
            collections=["sentinel-1-grd"],
            bbox=bbox,
            datetime=date_range,
            query={
                "sar:instrument_mode": {"eq": "IW"}
            }
        )
        return list(search.items())
    except Exception as e:
        print(f"Radar SAR Sentinel-1 query failed: {e}")
        return []

def run():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(HISTORY_DIR, exist_ok=True)
    os.makedirs(SCENES_DIR, exist_ok=True)
    os.makedirs(CORRIDOR_DIR, exist_ok=True)
    os.makedirs(DELTAS_DIR, exist_ok=True)
    os.makedirs(EXPORT_DIR, exist_ok=True)

    with open(CORRIDORS_FILE, "r", encoding="utf-8") as f:
        corridors = json.load(f)

    conn = duckdb.connect(DB_FILE)

    # Database Schema Migration: detect and drop legacy v1 table if corridor_id is absent
    existing_tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
    if "satellite_scenes" in existing_tables:
        cols = [r[1] for r in conn.execute("PRAGMA table_info('satellite_scenes')").fetchall()]
        if "corridor_id" not in cols:
            print("Detected legacy v1 satellite_scenes table schema. Upgrading table to v2...")
            conn.execute("DROP TABLE IF EXISTS satellite_scenes")

    with open("schema.sql", "r", encoding="utf-8") as f:
        conn.execute(f.read())

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    date_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    date_range = f"{yesterday_str}/{date_str}"

    catalog_url = "https://planetarycomputer.microsoft.com/api/stac/v1"
    catalog = pystac_client.Client.open(catalog_url, modifier=planetary_computer.sign_inplace)

    conn.execute("BEGIN TRANSACTION")

    ledger_records = []
    all_indexed_scenes = []
    deltas_emitted = []

    for corridor in corridors:
        c_id = corridor["id"]
        c_name = corridor["name"]
        bbox = corridor["bbox"]
        max_cloud = corridor.get("max_cloud_optical", 25.0)

        # 1. Optical Passes (Sentinel-2 L2A)
        optical_items = query_optical_sentinel2(catalog, bbox, date_range, max_cloud)

        # 2. All-Weather Radar SAR Fallback (Sentinel-1 GRD)
        sar_items = []
        if len(optical_items) < 2:
            print(f"Corridor '{c_id}' optical yield low (< 2). Executing Sentinel-1 SAR radar fallback...")
            sar_items = query_radar_sar_sentinel1(catalog, bbox, date_range)

        combined_items = [("OPTICAL_SENTINEL2", it) for it in optical_items] + [("RADAR_SAR_SENTINEL1", it) for it in sar_items]

        corridor_scenes = []

        for sensor_type, item in combined_items:
            s_id = item.id
            coll = item.collection_id or "unknown"
            props = item.properties
            i_bbox = item.bbox or bbox

            min_lon, min_lat, max_lon, max_lat = i_bbox[0], i_bbox[1], i_bbox[2], i_bbox[3]
            c_lat = round((min_lat + max_lat) / 2.0, 6)
            c_lon = round((min_lon + max_lon) / 2.0, 6)

            obs_time = item.datetime.isoformat() if item.datetime else now_iso
            obs_date = obs_time.split("T")[0]

            if sensor_type == "OPTICAL_SENTINEL2":
                cloud_pct = round(float(props.get("eo:cloud_cover", 0.0)), 2)
                mgrs_or_orbit = props.get("s2:mgrs_tile", "N/A")
                platform = props.get("platform", "Sentinel-2")
            else:
                cloud_pct = 0.0
                mgrs_or_orbit = f"ORBIT_{props.get('sat:relative_orbit', 'N/A')}_{props.get('sat:orbit_state', 'IW')}"
                platform = props.get("platform", "Sentinel-1")

            visual_asset = item.assets.get("visual") or item.assets.get("rendered_preview") or {}
            raw_href = visual_asset.href if hasattr(visual_asset, "href") else visual_asset.get("href", "")
            canonical_href = raw_href.split("?")[0] if raw_href else ""
            thumb_url = raw_href

            canonical_data = {
                "scene_id": s_id,
                "corridor_id": c_id,
                "sensor_type": sensor_type,
                "timestamp_utc": obs_time,
                "cloud_cover_pct": cloud_pct,
                "mgrs_or_orbit": mgrs_or_orbit,
                "canonical_asset_href": canonical_href,
                "bbox": [min_lon, min_lat, max_lon, max_lat]
            }
            content_hash = compute_sha256(json.dumps(canonical_data, sort_keys=True, separators=(",", ":")))

            h3_cells = extract_h3_cells_for_bbox([min_lon, min_lat, max_lon, max_lat], H3_RESOLUTION)

            existing = conn.execute(f"SELECT content_hash FROM satellite_scenes WHERE scene_id = '{s_id}'").fetchone()

            if not existing:
                conn.execute(f"""
                    INSERT INTO satellite_scenes (
                        scene_id, corridor_id, collection, sensor_type,
                        observation_date, timestamp_utc, platform, mgrs_or_orbit,
                        cloud_cover_pct, min_lon, min_lat, max_lon, max_lat,
                        centroid_lat, centroid_lon, canonical_asset_href, thumbnail_url,
                        content_hash, indexed_at_utc
                    ) VALUES (
                        '{s_id}', '{c_id}', '{coll}', '{sensor_type}',
                        '{obs_date}', '{obs_time}', '{platform}', '{mgrs_or_orbit}',
                        {cloud_pct}, {min_lon}, {min_lat}, {max_lon}, {max_lat},
                        {c_lat}, {c_lon}, '{canonical_href}', '{thumb_url}',
                        '{content_hash}', '{now_iso}'
                    )
                """)

                for cell in h3_cells:
                    conn.execute(f"""
                        INSERT OR REPLACE INTO scene_h3_coverage (
                            h3_index, scene_id, corridor_id, sensor_type, observation_date, cloud_cover_pct
                        ) VALUES (
                            '{cell}', '{s_id}', '{c_id}', '{sensor_type}', '{obs_date}', {cloud_pct}
                        )
                    """)

                commit_id = conn.execute("SELECT nextval('seq_commit_id')").fetchone()[0]
                conn.execute(f"""
                    INSERT INTO satellite_ledger (commit_id, timestamp_utc, operation, corridor_id, scene_id, sensor_type, h3_cell_count, detail)
                    VALUES ({commit_id}, '{now_iso}', 'INDEX_SCENE', '{c_id}', '{s_id}', '{sensor_type}', {len(h3_cells)}, 'Tessellated {len(h3_cells)} H3 cells')
                """)

                ledger_records.append({
                    "commit_id": commit_id,
                    "timestamp_utc": now_iso,
                    "operation": "INDEX_SCENE",
                    "corridor_id": c_id,
                    "scene_id": s_id,
                    "sensor_type": sensor_type,
                    "h3_cell_count": len(h3_cells)
                })

                prev_pass = conn.execute(f"""
                    SELECT scene_id, timestamp_utc, cloud_cover_pct
                    FROM satellite_scenes
                    WHERE corridor_id = '{c_id}' AND mgrs_or_orbit = '{mgrs_or_orbit}' AND scene_id != '{s_id}'
                    ORDER BY timestamp_utc DESC LIMIT 1
                """).fetchone()

                if prev_pass:
                    prev_id, prev_time, prev_cloud = prev_pass
                    c_delta = round(abs(cloud_pct - prev_cloud), 2)
                    intensity = "ANOMALOUS_SURGE" if c_delta > 50.0 else ("MINOR" if c_delta > 20.0 else "NONE")
                    delta_id = f"delta_{s_id[:16]}_{prev_id[:16]}"

                    conn.execute(f"""
                        INSERT OR REPLACE INTO corridor_deltas (
                            delta_id, corridor_id, mgrs_or_orbit, latest_scene_id,
                            previous_scene_id, latest_time_utc, previous_time_utc,
                            cloud_delta_pct, change_intensity, evaluated_at_utc
                        ) VALUES (
                            '{delta_id}', '{c_id}', '{mgrs_or_orbit}', '{s_id}',
                            '{prev_id}', '{obs_time}', '{prev_time}',
                            {c_delta}, '{intensity}', '{now_iso}'
                        )
                    """)

                    deltas_emitted.append({
                        "delta_id": delta_id,
                        "corridor_id": c_id,
                        "mgrs_or_orbit": mgrs_or_orbit,
                        "latest_scene_id": s_id,
                        "previous_scene_id": prev_id,
                        "cloud_delta_pct": c_delta,
                        "change_intensity": intensity
                    })

            record_summary = {
                "scene_id": s_id,
                "corridor_id": c_id,
                "sensor_type": sensor_type,
                "observation_date": obs_date,
                "timestamp_utc": obs_time,
                "platform": platform,
                "mgrs_or_orbit": mgrs_or_orbit,
                "cloud_cover_pct": cloud_pct,
                "h3_coverage_count": len(h3_cells),
                "centroid_lat": c_lat,
                "centroid_lon": c_lon,
                "canonical_asset_href": canonical_href,
                "thumbnail_url": thumb_url
            }
            corridor_scenes.append(record_summary)
            all_indexed_scenes.append(record_summary)

        with open(os.path.join(CORRIDOR_DIR, f"{c_id}.json"), "w", encoding="utf-8") as f:
            json.dump({
                "corridor_id": c_id,
                "corridor_name": c_name,
                "bbox": bbox,
                "updated_at_utc": now_iso,
                "recent_passes_count": len(corridor_scenes),
                "scenes": corridor_scenes
            }, f, indent=2)

    conn.execute("COMMIT")

    if ledger_records:
        ledger_file = os.path.join(HISTORY_DIR, "satellite-scenes.jsonl")
        with open(ledger_file, "a", encoding="utf-8") as f:
            for rec in ledger_records:
                f.write(json.dumps(rec) + "\n")

    conn.execute(f"COPY (SELECT * FROM satellite_scenes ORDER BY timestamp_utc DESC) TO '{DATA_DIR}/archive.parquet' (FORMAT PARQUET)")
    conn.execute(f"COPY (SELECT * FROM satellite_scenes ORDER BY timestamp_utc DESC) TO '{EXPORT_DIR}/satellite-scenes.parquet' (FORMAT PARQUET)")
    conn.execute(f"COPY (SELECT * FROM scene_h3_coverage) TO '{EXPORT_DIR}/scene-h3-coverage.parquet' (FORMAT PARQUET)")
    conn.execute(f"COPY (SELECT * FROM corridor_deltas) TO '{EXPORT_DIR}/corridor-deltas.parquet' (FORMAT PARQUET)")

    with open(os.path.join(SCENES_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"updated_at_utc": now_iso, "count": len(all_indexed_scenes), "scenes": all_indexed_scenes}, f, indent=2)

    with open(os.path.join(DATA_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"updated_at_utc": now_iso, "count": len(all_indexed_scenes), "scenes": all_indexed_scenes}, f, indent=2)

    with open(os.path.join(DELTAS_DIR, "today.json"), "w", encoding="utf-8") as f:
        json.dump({"date": date_str, "updated_at_utc": now_iso, "deltas_count": len(deltas_emitted), "deltas": deltas_emitted}, f, indent=2)

    with open(os.path.join(EXPORT_DIR, "today.json"), "w", encoding="utf-8") as f:
        json.dump({
            "date": date_str,
            "generated_at_utc": now_iso,
            "new_scenes_ingested": len(ledger_records),
            "total_monitored_scenes": len(all_indexed_scenes),
            "deltas_detected": len(deltas_emitted)
        }, f, indent=2)

    with open(os.path.join(API_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({
            "wire": "satellite-intelligence-wire",
            "schema_version": "2.0.0",
            "generated_at": now_iso,
            "source_cursor": "planetary-computer-sentinel2-sentinel1-fusion",
            "freshness_slo_seconds": 86400,
            "artifacts": [
                "today.json",
                "satellite-scenes.parquet",
                "scene-h3-coverage.parquet",
                "corridor-deltas.parquet",
                "llms.txt",
                "v1/schema.json",
                "v1/scenes/latest.json",
                "v1/deltas/today.json"
            ],
            "coverage": {
                "monitored_corridors": [c["id"] for c in corridors],
                "h3_resolution": H3_RESOLUTION,
                "sensors": ["Sentinel-2 L2A Multispectral Optical", "Sentinel-1 GRD Synthetic Aperture Radar"]
            },
            "limitations": [
                "Optical channels subject to local cloud cover",
                "Downstream raw asset retrieval requires on-demand client SAS signing via planetary_computer"
            ]
        }, f, indent=2)

    with open(os.path.join(API_DIR, "provenance.json"), "w", encoding="utf-8") as f:
        json.dump({
            "generator": "satellite-intelligence-wire",
            "runtime": "python-3.11",
            "h3_indexing": f"Resolution-{H3_RESOLUTION}-Polyfill",
            "sensor_fusion": "Optical+SAR-Fallback",
            "generated_at_utc": now_iso
        }, f, indent=2)

    with open(os.path.join(API_DIR, "schema.json"), "w", encoding="utf-8") as f:
        json.dump({
            "$schema": "https://json-schema.org/draft-07/schema#",
            "title": "SatelliteIntelligenceFeed",
            "type": "object",
            "properties": {
                "scene_id": {"type": "string"},
                "corridor_id": {"type": "string"},
                "sensor_type": {"type": "string", "enum": ["OPTICAL_SENTINEL2", "RADAR_SAR_SENTINEL1"]},
                "observation_date": {"type": "string"},
                "timestamp_utc": {"type": "string"},
                "mgrs_or_orbit": {"type": "string"},
                "cloud_cover_pct": {"type": "number"},
                "h3_coverage_count": {"type": "integer"},
                "canonical_asset_href": {"type": "string"},
                "thumbnail_url": {"type": "string"}
            },
            "required": ["scene_id", "corridor_id", "sensor_type", "timestamp_utc", "canonical_asset_href"]
        }, f, indent=2)

    lines = [
        "# Satellite Intelligence Wire",
        f"> Last Sync: {now_iso} UTC",
        "> Optimization: Sensor-fused optical and Synthetic Aperture Radar (SAR) telemetry with multi-cell H3 spatial indexing.",
        "",
        "## Monitored Maritime Chokepoints & Recent Passes"
    ]
    for c in corridors:
        lines.append(f"### {c['name']} (`{c['id']}`)")
        corridor_data = [s for s in all_indexed_scenes if s["corridor_id"] == c["id"]]
        if not corridor_data:
            lines.append("  - No new passes in the current 48h temporal window.")
        else:
            for s in corridor_data[:5]:
                sensor_tag = "[SAR_RADAR]" if s["sensor_type"] == "RADAR_SAR_SENTINEL1" else f"[OPTICAL {s['cloud_cover_pct']}% cloud]"
                lines.append(f"  - `{s['mgrs_or_orbit']}` ({s['scene_id']}) {sensor_tag}: {s['timestamp_utc']} | H3 cells: {s['h3_coverage_count']}")

    lines.extend([
        "",
        "## Recent Corridor Change Detections"
    ])
    if not deltas_emitted:
        lines.append("- No significant pass-to-pass physical variance detected.")
    else:
        for d in deltas_emitted:
            lines.append(f"- **{d['corridor_id']}** (`{d['mgrs_or_orbit']}`) [{d['change_intensity']}]: Cloud delta {d['cloud_delta_pct']}% between `{d['latest_scene_id']}` and `{d['previous_scene_id']}`")

    with open(os.path.join(EXPORT_DIR, "llms.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    checksums = []
    for root, _, files in os.walk(EXPORT_DIR):
        for file in sorted(files):
            if file == "checksums.txt":
                continue
            fpath = os.path.join(root, file)
            with open(fpath, "rb") as bf:
                f_hash = hashlib.sha256(bf.read()).hexdigest()
                rel_path = os.path.relpath(fpath, EXPORT_DIR)
                checksums.append(f"{f_hash}  {rel_path}")

    with open(os.path.join(EXPORT_DIR, "checksums.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(checksums) + "\n")

    conn.close()

if __name__ == "__main__":
    run()
