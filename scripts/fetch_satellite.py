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
CORRIDOR_DIR = os.path.join(API_DIR, "corridor")

# Target Maritime Corridor: Strait of Malacca & Singapore Maritime Highway
BBOX_MALACCA = [100.0, 1.0, 104.5, 6.0]
MAX_CLOUD_COVER = 30.0
H3_RESOLUTION = 6

def compute_sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()

def query_planetary_computer(bbox: list, max_cloud: float) -> list:
    catalog_url = "https://planetarycomputer.microsoft.com/api/stac/v1"
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    date_range = f"{yesterday_str}/{today_str}"

    print(f"Connecting to STAC catalog at {catalog_url} for interval {date_range}...")
    try:
        catalog = pystac_client.Client.open(
            catalog_url,
            modifier=planetary_computer.sign_inplace
        )

        search = catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=bbox,
            datetime=date_range,
            query={"eo:cloud_cover": {"lt": max_cloud}}
        )

        items = list(search.items())
        print(f"STAC search returned {len(items)} matching scenes.")
        return items
    except Exception as e:
        print(f"Warning: Planetary Computer STAC query encountered an issue: {e}")
        return []

def run():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(HISTORY_DIR, exist_ok=True)
    os.makedirs(SCENES_DIR, exist_ok=True)
    os.makedirs(CORRIDOR_DIR, exist_ok=True)
    os.makedirs(EXPORT_DIR, exist_ok=True)

    conn = duckdb.connect(DB_FILE)
    with open("schema.sql", "r", encoding="utf-8") as f:
        conn.execute(f.read())

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    date_str = now.strftime("%Y-%m-%d")

    raw_items = query_planetary_computer(BBOX_MALACCA, MAX_CLOUD_COVER)

    conn.execute("BEGIN TRANSACTION")

    ledger_records = []
    processed_scenes = []

    for item in raw_items:
        s_id = item.id
        props = item.properties
        bbox = item.bbox or [0.0, 0.0, 0.0, 0.0]

        min_lon, min_lat, max_lon, max_lat = bbox[0], bbox[1], bbox[2], bbox[3]
        centroid_lat = round((min_lat + max_lat) / 2.0, 6)
        centroid_lon = round((min_lon + max_lon) / 2.0, 6)

        # Compute spatial H3 index to interoperate with H3-4D-Spacetime-Tensor
        h3_cell = h3.geo_to_h3(centroid_lat, centroid_lon, H3_RESOLUTION)

        obs_time = item.datetime.isoformat() if item.datetime else now_iso
        obs_date = obs_time.split("T")[0]
        cloud_pct = round(float(props.get("eo:cloud_cover", 0.0)), 2)
        platform = props.get("platform", "Sentinel-2")
        mgrs = props.get("s2:mgrs_tile", "N/A")

        thumb = ""
        if "rendered_preview" in item.assets:
            thumb = item.assets["rendered_preview"].href
        elif "visual" in item.assets:
            thumb = item.assets["visual"].href

        canonical_data = {
            "scene_id": s_id,
            "timestamp_utc": obs_time,
            "cloud_cover_pct": cloud_pct,
            "mgrs_tile": mgrs,
            "h3_index": h3_cell,
            "bbox": [min_lon, min_lat, max_lon, max_lat]
        }
        content_hash = compute_sha256(json.dumps(canonical_data, sort_keys=True, separators=(",", ":")))

        existing = conn.execute(f"""
            SELECT content_hash, cloud_cover_pct 
            FROM satellite_scenes 
            WHERE scene_id = '{s_id}'
        """).fetchone()

        if not existing:
            conn.execute(f"""
                INSERT INTO satellite_scenes (
                    scene_id, observation_date, timestamp_utc, platform,
                    mgrs_tile, h3_index, cloud_cover_pct, min_lon, min_lat,
                    max_lon, max_lat, centroid_lat, centroid_lon, thumbnail_url,
                    content_hash, indexed_at_utc
                ) VALUES (
                    '{s_id}', '{obs_date}', '{obs_time}', '{platform}',
                    '{mgrs}', '{h3_cell}', {cloud_pct}, {min_lon}, {min_lat},
                    {max_lon}, {max_lat}, {centroid_lat}, {centroid_lon}, '{thumb}',
                    '{content_hash}', '{now_iso}'
                )
            """)

            commit_id = conn.execute("SELECT nextval('seq_commit_id')").fetchone()[0]
            conn.execute(f"""
                INSERT INTO satellite_ledger (commit_id, timestamp_utc, operation, scene_id, mgrs_tile, cloud_cover_pct, h3_index, detail)
                VALUES ({commit_id}, '{now_iso}', 'INDEX_SCENE', '{s_id}', '{mgrs}', {cloud_pct}, '{h3_cell}', 'Sentinel-2 L2A scene ingested')
            """)

            ledger_records.append({
                "commit_id": commit_id,
                "timestamp_utc": now_iso,
                "operation": "INDEX_SCENE",
                "scene_id": s_id,
                "mgrs_tile": mgrs,
                "cloud_cover_pct": cloud_pct,
                "h3_index": h3_cell
            })
        elif existing[0] != content_hash:
            conn.execute(f"""
                UPDATE satellite_scenes SET
                    cloud_cover_pct = {cloud_pct},
                    thumbnail_url = '{thumb}',
                    content_hash = '{content_hash}',
                    indexed_at_utc = '{now_iso}'
                WHERE scene_id = '{s_id}'
            """)

            commit_id = conn.execute("SELECT nextval('seq_commit_id')").fetchone()[0]
            conn.execute(f"""
                INSERT INTO satellite_ledger (commit_id, timestamp_utc, operation, scene_id, mgrs_tile, cloud_cover_pct, h3_index, detail)
                VALUES ({commit_id}, '{now_iso}', 'UPDATE_SCENE', '{s_id}', '{mgrs}', {cloud_pct}, '{h3_cell}', 'Scene assets refreshed')
            """)

            ledger_records.append({
                "commit_id": commit_id,
                "timestamp_utc": now_iso,
                "operation": "UPDATE_SCENE",
                "scene_id": s_id,
                "mgrs_tile": mgrs,
                "cloud_cover_pct": cloud_pct,
                "h3_index": h3_cell
            })

        processed_scenes.append(canonical_data)

    conn.execute("COMMIT")

    # Append to Git-tracked immutable history
    if ledger_records:
        ledger_file = os.path.join(HISTORY_DIR, "satellite-scenes.jsonl")
        with open(ledger_file, "a", encoding="utf-8") as f:
            for rec in ledger_records:
                f.write(json.dumps(rec) + "\n")

    # Columnar Parquet exports
    conn.execute(f"COPY (SELECT * FROM satellite_scenes ORDER BY timestamp_utc DESC) TO '{DATA_DIR}/archive.parquet' (FORMAT PARQUET)")
    conn.execute(f"COPY (SELECT * FROM satellite_scenes ORDER BY timestamp_utc DESC) TO '{EXPORT_DIR}/satellite-scenes.parquet' (FORMAT PARQUET)")

    # Materialize active scene catalog
    cur = conn.execute("SELECT scene_id, observation_date, timestamp_utc, platform, mgrs_tile, h3_index, cloud_cover_pct, centroid_lat, centroid_lon, thumbnail_url FROM satellite_scenes ORDER BY timestamp_utc DESC LIMIT 100")
    cols = [d[0] for d in cur.description]
    all_scenes = [dict(zip(cols, r)) for r in cur.fetchall()]

    with open(os.path.join(SCENES_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"updated_at_utc": now_iso, "count": len(all_scenes), "scenes": all_scenes}, f, indent=2)

    with open(os.path.join(DATA_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"updated_at_utc": now_iso, "count": len(all_scenes), "scenes": all_scenes}, f, indent=2)

    with open(os.path.join(CORRIDOR_DIR, "malacca-singapore.json"), "w", encoding="utf-8") as f:
        json.dump({
            "corridor": "Strait of Malacca / Singapore Strait",
            "bbox": BBOX_MALACCA,
            "max_cloud_threshold": MAX_CLOUD_COVER,
            "updated_at_utc": now_iso,
            "recent_passes_count": len(all_scenes),
            "scenes": all_scenes[:25]
        }, f, indent=2)

    with open(os.path.join(EXPORT_DIR, "today.json"), "w", encoding="utf-8") as f:
        json.dump({
            "date": date_str,
            "generated_at_utc": now_iso,
            "ingested_in_run": len(ledger_records),
            "total_scenes": len(all_scenes)
        }, f, indent=2)

    with open(os.path.join(API_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({
            "wire": "satellite-intelligence-wire",
            "schema_version": "1.0.0",
            "generated_at": now_iso,
            "source_cursor": "microsoft-planetary-computer-sentinel-2-l2a",
            "freshness_slo_seconds": 86400,
            "artifacts": [
                "today.json",
                "satellite-scenes.parquet",
                "llms.txt",
                "v1/schema.json",
                "v1/scenes/latest.json",
                "v1/corridor/malacca-singapore.json"
            ],
            "coverage": {
                "monitored_corridor": "Strait of Malacca & Singapore",
                "bbox": BBOX_MALACCA,
                "max_cloud_cover": MAX_CLOUD_COVER,
                "h3_resolution": H3_RESOLUTION
            },
            "limitations": [
                "Optical satellite coverage subject to equatorial cloud occlusion",
                "Constrained to Sentinel-2 Level-2A surface reflectance passes"
            ]
        }, f, indent=2)

    with open(os.path.join(API_DIR, "provenance.json"), "w", encoding="utf-8") as f:
        json.dump({
            "generator": "satellite-intelligence-wire",
            "runtime": "python-3.11",
            "provider": "Copernicus Sentinel-2 via Planetary Computer STAC",
            "h3_indexing": f"Resolution-{H3_RESOLUTION}",
            "generated_at_utc": now_iso
        }, f, indent=2)

    with open(os.path.join(API_DIR, "schema.json"), "w", encoding="utf-8") as f:
        json.dump({
            "$schema": "https://json-schema.org/draft-07/schema#",
            "title": "SatelliteSceneFeed",
            "type": "object",
            "properties": {
                "scene_id": {"type": "string"},
                "observation_date": {"type": "string"},
                "timestamp_utc": {"type": "string"},
                "platform": {"type": "string"},
                "mgrs_tile": {"type": "string"},
                "h3_index": {"type": "string"},
                "cloud_cover_pct": {"type": "number"},
                "centroid_lat": {"type": "number"},
                "centroid_lon": {"type": "number"},
                "thumbnail_url": {"type": "string"}
            },
            "required": ["scene_id", "timestamp_utc", "h3_index", "cloud_cover_pct"]
        }, f, indent=2)

    # Token-efficient llms.txt digest
    lines = [
        "# Satellite Intelligence Wire",
        f"> Last Sync: {now_iso} UTC",
        f"> Spatial Focus: Malacca / Singapore Maritime Corridor {BBOX_MALACCA}",
        f"> Resolution: Sentinel-2 L2A Multispectral Optical (Cloud Cover < {MAX_CLOUD_COVER}%)",
        "",
        "## Recent Optical Passes"
    ]
    if not all_scenes:
        lines.append("- No low-cloud Sentinel-2 passes detected in the current acquisition window.")
    else:
        for s in all_scenes[:20]:
            lines.append(
                f"- **Tile `{s['mgrs_tile']}`** (`{s['scene_id']}`): "
                f"Cloud: `{s['cloud_cover_pct']}%` | Time: `{s['timestamp_utc']}` | "
                f"H3: `{s['h3_index']}` (Lat: {s['centroid_lat']}, Lon: {s['centroid_lon']})"
            )

    with open(os.path.join(EXPORT_DIR, "llms.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # Checksums
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
