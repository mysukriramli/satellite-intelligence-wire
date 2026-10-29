```markdown
# 🛰️ Satellite Intelligence Wire

An autonomous satellite intelligence data plane running entirely on GitHub Actions. It monitors vital global maritime corridors (e.g., Strait of Malacca & Singapore Strait), pulls multispectral Sentinel-2 Level-2A imagery metadata via Microsoft Planetary Computer STAC, filters for low cloud occlusion, indexes scene footprints into Uber H3 cells, and publishes zero-token machine feeds.

## Machine Feeds
- [`/v1/manifest.json`](public/v1/manifest.json) (Wire contract & coverage bounds)
- [`/today.json`](public/today.json) (Current run execution summary)
- [`/v1/schema.json`](public/v1/schema.json) (JSON Schema for scene records)
- [`/v1/corridor/malacca-singapore.json`](public/v1/corridor/malacca-singapore.json) (Target corridor latest passes)
- [`/v1/scenes/latest.json`](public/v1/scenes/latest.json) (Full recent scene catalog)
- [`/satellite-scenes.parquet`](public/satellite-scenes.parquet) (Analytical columnar archive)
- [`/llms.txt`](public/llms.txt) (Token-efficient LLM digest)

## Architecture
- **Imagery Source**: Copernicus Sentinel-2 L2A via Planetary Computer STAC API
- **Spatial Transformation**: Centroid derivation and Uber H3 discrete hexagonal cell indexing (Resolution 6)
- **Analytical Engine**: DuckDB + Parquet
- **State Ledger**: Git-tracked append-only JSONL in `/ledger`
- **Edge CDN Delivery**: GitHub Pages (Fastly CDN)
