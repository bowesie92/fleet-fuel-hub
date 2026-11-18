# Fleet Fuel Hub

**SYNTHETIC DATA. Hypothetical distributor. Demo only.**

A portfolio demo showing how a regional food distributor could join its fuel card, telematics and routing exports into one clean data layer, then use it to find fuel stops that save money **after** detour costs.

**Live site:** https://bowesie92.github.io/fleet-fuel-hub/

See `SPEC.md` for the plan and `docs/contracts.md` for how the pieces fit.

- Real public data: EIA weekly diesel prices, OpenStreetMap stations, openrouteservice truck routing, and published Tucson congestion indices (Texas A&M Urban Mobility Report, with the FHWA Urban Congestion Report as a cross-check).
- Everything about the fleet is synthetic and generated from `config/assumptions.yaml`.

```
uv sync
uv run pytest
uv run fleetfuel build
```

## Licence

- **Code:** MIT, see [`LICENSE`](LICENSE).
- **Data:** each public source keeps its own licence (OpenStreetMap ODbL, openrouteservice CC BY-SA 4.0, EIA public data), as set out in [`DATA_LICENSES.md`](DATA_LICENSES.md).

## Author

Built by Cass Bowes ([GitHub](https://github.com/bowesie92)).

## How this would run in production

The demo prices detour time with published, averaged congestion indices: Tucson's Travel Time Index for peak hours, and optionally the Planning Time Index as a reliability buffer. It never calls a live traffic service, because every result here is cached and published from that cache.

In production, a recommended fuel stop would be re-checked just before the driver commits to it:

- **Live traffic:** a licensed live-traffic feed would replace the averaged index with the current travel time for the detour. Commercial traffic APIs (for example the large mapping and traffic providers) generally require a paid license for this use. Their free developer tiers typically limit use to internal evaluation and forbid caching results or building derived data, which this demo's publish-from-cache design depends on.
- **Work zones:** Arizona's AZ511 work-zone data, published by ADOT as a WZDx (Work Zone Data Exchange) feed, would flag lane closures or detours near the station before the stop is recommended.

Neither is built here. The demo stays on public, cacheable data so that a fresh clone rebuilds the whole site with no API keys.
