```markdown
# 🛰️ Satellite Intelligence Wire

An autonomous planetary intelligence data plane running on GitHub Actions. It monitors vital global maritime corridors (Strait of Malacca, Bab-el-Mandeb, Strait of Hormuz, Panama Canal), fuses optical Sentinel-2 imagery with cloud-penetrating Sentinel-1 C-band Synthetic Aperture Radar (SAR), tessellates scene footprints across Uber H3 cells, and detects pass-to-pass variance for AI agents.

## Machine Feeds
- [`/v1/manifest.json`](public/v1/manifest.json) (Wire contract & coverage bounds)
- [`/today.json`](public/today.json) (Current execution summary)
- [`/v1/schema.json`](public/v1/schema.json) (Feed JSON Schema)
- [`/v1/corridors/strait_of_malacca.json`](public/v1/corridors/strait_of_malacca.json) (Malacca & Singapore corridor feed)
- [`/v1/scenes/latest.json`](public/v1/scenes/latest.json) (Recent optical and SAR scenes)
- [`/v1/deltas/today.json`](public/v1/deltas/today.json) (Corridor change detection alerts)
- [`/satellite-scenes.parquet`](public/satellite-scenes.parquet) (Analytical scene database)
- [`/scene-h3-coverage.parquet`](public/scene-h3-coverage.parquet) (Spatial H3 inverted index)
- [`/llms.txt`](public/llms.txt) (Token-efficient LLM digest)

## Architecture
- **Sensors**: Sentinel-2 L2A (Multispectral Optical) + Sentinel-1 GRD (Synthetic Aperture Radar)
- **Spatial Indexing**: Uber H3 discrete hexagonal tessellation (`h3.polyfill`, Resolution 6)
- **Storage & Parquet**: DuckDB + Apache Parquet
- **Decoupled Asset Pointers**: Canonical blob storage identifiers with on-demand client-side SAS signing
- **Ledger**: Git-tracked append-only JSONL in `/ledger`
- **Edge CDN**: GitHub Pages (Fastly CDN)
