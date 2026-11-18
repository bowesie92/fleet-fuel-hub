"""Offline tests for the data-fetcher: EIA diesel, OSM stations and anonymous customer points.

The network is replaced by small fixtures in tests/fixtures/fetch/. Nothing here needs an API key.
The last group checks the real committed files against docs/contracts.md and skips if absent.
"""

from __future__ import annotations

import copy
import csv
import json
from pathlib import Path

import pytest

from fleetfuel import config, fetch
from fleetfuel.fetch import cache, eia, osm

FIXTURES = Path(__file__).parent / "fixtures" / "fetch"
FAKE_KEY = "TEST-KEY-123"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    """Just enough of requests.Response for the fetchers."""

    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return copy.deepcopy(self._payload)


class FakeNetwork:
    """Stands in for requests.get (EIA) and requests.post (Overpass) and counts the calls."""

    def __init__(self):
        self.eia = load_fixture("eia_fake_api.json")
        self.stations = load_fixture("overpass_stations.json")
        self.customers = load_fixture("overpass_customers.json")
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        assert params["api_key"] == FAKE_KEY
        path = url.split("/v2/", 1)[1].strip("/")
        if path == "petroleum/pri/gnd/data":
            return FakeResponse(self.eia["data_pages"][0 if int(params["offset"]) == 0 else 1])
        return FakeResponse(self.eia[path])

    def post(self, endpoint, data=None, headers=None, timeout=None):
        self.calls += 1
        assert "User-Agent" in headers
        return FakeResponse(self.stations if "fuel" in data["data"] else self.customers)


@pytest.fixture
def cfg():
    cfg = config.load_assumptions()
    cfg["fetch"]["eia"]["page_size"] = 2
    cfg["fetch"]["customers"]["max_points"] = 3
    return cfg


@pytest.fixture
def net(monkeypatch):
    fake = FakeNetwork()
    monkeypatch.setattr(eia.requests, "get", fake.get)
    monkeypatch.setattr(osm.requests, "post", fake.post)
    monkeypatch.setattr(cache, "pause", lambda seconds: None)
    monkeypatch.setattr(osm, "reachable_by_truck", lambda cfg: (lambda pts: [True] * len(pts)))  # no live snap calls
    monkeypatch.setenv("EIA_API_KEY", FAKE_KEY)
    return fake


# ---------------------------------------------------------------- stations

ALIASES = ["Love's", "Loves", "Pilot", "Flying J", "TA", "Petro"]


def test_truck_tags_are_recognised():
    assert osm.is_truck_tagged({"hgv": "yes"})
    assert osm.is_truck_tagged({"fuel:HGV_diesel": "yes"})
    assert not osm.is_truck_tagged({"hgv": "no"})
    assert not osm.is_truck_tagged({"brand": "Shell"})


def test_brand_alias_matches_whole_words_only():
    assert osm.matches_truck_brand({"brand": "Pilot"}, ALIASES)
    assert osm.matches_truck_brand({"name": "TA Travel Center"}, ALIASES)
    assert osm.matches_truck_brand({"name": "pilot flying j #12"}, ALIASES)
    assert osm.matches_truck_brand({"brand": "Love’s"}, ALIASES)
    assert not osm.matches_truck_brand({"name": "Vista Gas", "brand": "Chevron"}, ALIASES)
    assert not osm.matches_truck_brand({"name": "Metro Gas"}, ALIASES)


BANNED = ["zzbanned"]


def build_stations(cfg, terms=BANNED):
    response = load_fixture("overpass_stations.json")
    candidates, conflicts = osm.screen_stations(response, osm.station_rules(cfg), terms)
    stations, removed = osm.dedupe_stations(candidates, config.get(cfg, "fetch.overpass.dedupe_distance_m"))
    return stations, removed, conflicts


def props_by_ref(cfg):
    stations, _, _ = build_stations(cfg)
    return {f["properties"]["osm_ref"]: f["properties"] for f in osm.to_geojson(stations)["features"]}


def test_stations_keep_only_truck_capable(cfg):
    stations, removed, _ = build_stations(cfg)
    refs = [s["osm_ref"] for s in stations]
    assert refs == [
        "node/1001", "node/1002", "node/1003", "node/1004", "node/1005", "node/1008",
        "node/303", "node/606", "node/909", "node/910", "relation/707", "way/202",
    ]
    assert removed == 1


def test_node_and_way_of_one_station_become_one(cfg):
    stations, _, _ = build_stations(cfg)
    merged = next(s for s in stations if s["osm_ref"] == "way/202")
    assert merged["operator"] == "Acme Ops" and merged["city"] == "Casa Grande"
    assert merged["truck_tagged"] is True


def test_different_brands_next_to_each_other_stay_separate(cfg):
    stations, _, _ = build_stations(cfg)
    assert {"node/909", "node/910"} <= {s["osm_ref"] for s in stations}


def test_tucson_box_keeps_cardlock_fleet_and_evidence_sites(cfg):
    p = props_by_ref(cfg)
    assert (p["node/1001"]["access_basis"], p["node/1001"]["truck_access"], p["node/1001"]["station_class"]) == ("cardlock", "confirmed", "cardlock")
    assert (p["node/1002"]["access_basis"], p["node/1002"]["truck_access"]) == ("fleet_access", "confirmed")
    assert (p["node/1003"]["access_basis"], p["node/1003"]["truck_access"]) == ("truck_evidence", "unverified")
    assert (p["node/1005"]["access_basis"], p["node/1005"]["truck_access"]) == ("truck_evidence", "unverified")


def test_diesel_without_evidence_is_kept_as_diesel_only(cfg):
    p = props_by_ref(cfg)["node/1004"]
    assert (p["access_basis"], p["truck_access"]) == ("diesel_only", "unverified")


def test_wider_rules_apply_inside_the_tucson_box_only(cfg):
    p = props_by_ref(cfg)
    assert "node/1006" not in p and "node/1007" not in p


def test_hgv_designated_counts_as_a_truck_tag(cfg):
    assert props_by_ref(cfg)["node/1008"]["access_basis"] == "truck_tag"
    assert props_by_ref(cfg)["node/1008"]["truck_access"] == "confirmed"


def test_name_conflicts_are_dropped_and_only_the_ref_is_reported(cfg):
    stations, _, conflicts = build_stations(cfg)
    assert conflicts == ["node/1009"]
    assert "node/1009" not in {s["osm_ref"] for s in stations}
    assert build_stations(cfg, terms=[])[2] == []


def test_name_conflicts_never_reach_the_cache(cfg, net, tmp_path):
    result = osm.fetch_stations(cfg, osm.OverpassSession(cfg), refresh=False, raw_dir=tmp_path / "raw",
                                cache_dir=tmp_path / "osm", terms=BANNED)
    assert result["name_conflicts"] == ["node/1009"]
    disk = (tmp_path / "osm" / "stations_overpass.json").read_text() + (tmp_path / "raw" / "stations.geojson").read_text()
    assert "zzbanned" not in disk.lower()
    assert "Corner Gas" not in disk  # stations with no diesel and no truck evidence are not cached


def test_merged_station_keeps_the_stronger_access_basis():
    a = {"osm_ref": "node/1", "lat": 0, "lon": 0, "name": None, "brand": None, "operator": None, "city": None,
         "truck_tagged": False, "truck_brand": False, "access_basis": "truck_evidence", "_key": "", "_tag_count": 2}
    b = {**a, "osm_ref": "way/2", "access_basis": "truck_tag", "truck_tagged": True}
    assert osm._merge(a, b)["access_basis"] == "truck_tag"


def test_geojson_matches_contract(cfg):
    stations, _, _ = build_stations(cfg)
    collection = osm.to_geojson(stations)
    ids = [f["properties"]["station_id"] for f in collection["features"]]
    assert ids == [f"STN-{n:03d}" for n in range(1, 13)]
    by_ref = {f["properties"]["osm_ref"]: f for f in collection["features"]}
    assert by_ref["node/303"]["properties"]["station_class"] == "travel_center"
    assert by_ref["relation/707"]["properties"]["station_class"] == "travel_center"
    assert by_ref["way/202"]["properties"]["station_class"] == "branded"
    assert by_ref["node/606"]["properties"]["station_class"] == "unbranded"
    assert by_ref["node/606"]["properties"]["brand"] is None
    assert by_ref["node/303"]["properties"]["truck_tagged"] is False
    assert by_ref["node/303"]["properties"]["truck_brand"] is True
    assert by_ref["node/303"]["properties"]["access_basis"] == "truck_brand"
    lon, lat = by_ref["relation/707"]["geometry"]["coordinates"]
    assert (lon, lat) == (-111.1, 32.1)
    assert list(by_ref["node/303"]["properties"]) == [
        "station_id", "osm_ref", "name", "brand", "operator", "city",
        "truck_tagged", "truck_brand", "station_class", "truck_access", "access_basis",
    ]


# ---------------------------------------------------------------- customers

def test_customer_points_keep_coordinates_only():
    points = osm.sanitize_points(load_fixture("overpass_customers.json"), 5)
    assert points == [[32.15, -110.85], [32.22266, -110.97471], [32.23012, -110.96057], [32.3, -110.9]]


def test_sampling_is_capped_and_unseeded():
    points = [[32.0 + i / 1000, -111.0] for i in range(50)]
    first = osm.sample_points(points, 10)
    assert len(first) == 10 and all(p in points for p in first)
    assert len(osm.sample_points(points[:5], 10)) == 5


def test_customer_csv_format():
    text = osm.render_customers([[32.15, -110.85], [32.3, -110.9]], 5)
    assert text == "customer_id,lat,lon\nCUST-0001,32.15000,-110.85000\nCUST-0002,32.30000,-110.90000\n"


def test_customer_cache_and_csv_hold_no_names_or_ids(cfg, net, tmp_path):
    osm.fetch_customers(cfg, osm.OverpassSession(cfg), refresh=False, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "osm")
    cached = json.loads((tmp_path / "osm" / "customers_points.json").read_text(encoding="utf-8"))["points"]
    assert all(isinstance(p, list) and len(p) == 2 and all(isinstance(v, float) for v in p) for p in cached)
    disk = (tmp_path / "osm" / "customers_points.json").read_text() + (tmp_path / "raw" / "customer_points.csv").read_text()
    for leak in ("Secret Name", "Another Name", "Main St", "555-0100", "amenity", "node", "way", "relation", "tags"):
        assert leak not in disk


# ---------------------------------------------------------------- EIA

def test_facet_choice_prefers_the_most_specific_match():
    facets = load_fixture("eia_fake_api.json")["petroleum/pri/gnd/facet/duoarea"]["response"]["facets"]
    assert eia.pick_facet(facets, "West Coast (PADD 5) except California")["id"] == "R5XCA"
    products = load_fixture("eia_fake_api.json")["petroleum/pri/gnd/facet/product"]["response"]["facets"]
    assert eia.pick_facet(products, "No 2 Diesel")["id"] == "EPD2D"
    with pytest.raises(LookupError):
        eia.pick_facet(facets, "Gulf Coast")


def test_eia_fetch_writes_contract_csv_and_meta(cfg, net, tmp_path):
    result = eia.fetch_eia(cfg, refresh=False, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "eia")
    assert result["rows"] == 3 and result["series_id"] == "EMD_EPD2D_PTE_R5XCA_DPG"
    text = (tmp_path / "raw" / "eia_diesel.csv").read_text(encoding="utf-8")
    assert text == (
        "week_start,price_per_gal,series_id\n"
        "2025-01-06,3.900,EMD_EPD2D_PTE_R5XCA_DPG\n"
        "2025-01-13,3.913,EMD_EPD2D_PTE_R5XCA_DPG\n"
        "2025-01-20,4.010,EMD_EPD2D_PTE_R5XCA_DPG\n"
    )
    meta = json.loads((tmp_path / "eia" / "series_meta.json").read_text(encoding="utf-8"))
    assert meta["series_id"] == "EMD_EPD2D_PTE_R5XCA_DPG"
    assert meta["facets"] == {"frequency": "weekly", "duoarea": "R5XCA", "product": "EPD2D"}
    assert meta["route"] == "petroleum/pri/gnd" and meta["units"] == "$/GAL"
    assert set(meta) == {"route", "facets", "series_id", "description", "units", "fetched_on"}


def test_eia_cache_never_contains_the_key(cfg, net, tmp_path):
    eia.fetch_eia(cfg, refresh=False, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "eia")
    for path in (tmp_path / "eia").glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert FAKE_KEY not in text and "api_key" not in text


def test_request_hash_ignores_the_key():
    assert cache.request_hash("a/b", {"x": 1, "api_key": "one"}) == cache.request_hash("a/b", {"x": 1, "api_key": "two"})
    assert cache.request_hash("a/b", {"x": 1}) != cache.request_hash("a/b", {"x": 2})


def test_eia_second_run_needs_no_network_and_no_key(cfg, net, tmp_path, monkeypatch):
    eia.fetch_eia(cfg, refresh=False, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "eia")
    first = net.calls
    assert first > 0
    monkeypatch.delenv("EIA_API_KEY")
    monkeypatch.setattr(config, "load_env", lambda: None)
    eia.fetch_eia(cfg, refresh=False, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "eia")
    assert net.calls == first


def test_eia_without_key_and_without_cache_is_a_clear_error(cfg, net, tmp_path, monkeypatch):
    monkeypatch.delenv("EIA_API_KEY")
    monkeypatch.setattr(config, "load_env", lambda: None)
    with pytest.raises(eia.MissingKeyError, match="EIA_API_KEY"):
        eia.fetch_eia(cfg, refresh=False, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "eia")
    assert not (tmp_path / "raw" / "eia_diesel.csv").exists()


def test_failed_call_does_not_leak_the_key(cfg, net, tmp_path, monkeypatch):
    def broken(url, params=None, timeout=None):
        return type("R", (), {"status_code": 403, "text": "", "json": lambda self: {}})()

    monkeypatch.setattr(eia.requests, "get", broken)
    with pytest.raises(RuntimeError) as err:
        eia.fetch_eia(cfg, refresh=False, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "eia")
    assert FAKE_KEY not in str(err.value) and "403" in str(err.value)


# ---------------------------------------------------------------- whole run

def test_run_writes_three_files_then_uses_only_the_cache(cfg, net, tmp_path):
    fetch.run(cfg, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "cache")
    for name in ("eia_diesel.csv", "stations.geojson", "customer_points.csv"):
        assert (tmp_path / "raw" / name).is_file()
    before = {p.name: p.read_bytes() for p in (tmp_path / "raw").iterdir()}
    calls = net.calls
    fetch.run(cfg, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "cache")
    assert net.calls == calls
    assert before == {p.name: p.read_bytes() for p in (tmp_path / "raw").iterdir()}


def test_run_refresh_calls_the_network_again(cfg, net, tmp_path):
    fetch.run(cfg, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "cache")
    calls = net.calls
    fetch.run(cfg, refresh=True, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "cache")
    assert net.calls > calls


def test_missing_key_still_writes_the_osm_files(cfg, net, tmp_path, monkeypatch):
    monkeypatch.delenv("EIA_API_KEY")
    monkeypatch.setattr(config, "load_env", lambda: None)
    with pytest.raises(RuntimeError, match="EIA_API_KEY"):
        fetch.run(cfg, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "cache")
    assert (tmp_path / "raw" / "stations.geojson").is_file()
    assert (tmp_path / "raw" / "customer_points.csv").is_file()
    assert not (tmp_path / "raw" / "eia_diesel.csv").exists()


def test_overpass_busy_answers_are_retried(cfg, monkeypatch):
    answers = [429, 504, 200]
    waits = []

    def post(endpoint, data=None, headers=None, timeout=None):
        status = answers.pop(0)
        resp = FakeResponse({"elements": []})
        resp.status_code = status
        return resp

    monkeypatch.setattr(osm.requests, "post", post)
    monkeypatch.setattr(cache, "pause", waits.append)
    assert osm.run_overpass(cfg, "q") == {"elements": []}
    wait = float(config.get(cfg, "fetch.retry.wait_s"))
    assert waits == [wait, wait * 2]


def test_pause_between_overpass_queries(cfg, net, monkeypatch):
    waits = []
    monkeypatch.setattr(cache, "pause", waits.append)
    session = osm.OverpassSession(cfg)
    session.query("fuel")
    session.query("food")
    assert waits == [float(config.get(cfg, "fetch.overpass.pause_between_queries_s"))]


# ---------------------------------------------------------------- the committed real files

RAW = config.RAW_DIR


def read_csv(name):
    with open(RAW / name, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.mark.skipif(not (RAW / "eia_diesel.csv").is_file(), reason="eia_diesel.csv not fetched yet")
def test_real_eia_file_matches_contract():
    rows = read_csv("eia_diesel.csv")
    weeks = [r["week_start"] for r in rows]
    assert list(rows[0]) == ["week_start", "price_per_gal", "series_id"]
    assert len(rows) >= 156
    assert weeks == sorted(set(weeks))
    assert weeks[0] >= str(config.get(config.load_assumptions(), "fetch.eia.start_date"))
    assert len({r["series_id"] for r in rows}) == 1
    assert all(len(r["price_per_gal"].split(".")[1]) == 3 and 1 < float(r["price_per_gal"]) < 12 for r in rows)
    meta = json.loads((config.CACHE_DIR / "eia" / "series_meta.json").read_text(encoding="utf-8"))
    assert meta["series_id"] == rows[0]["series_id"]


@pytest.mark.skipif(not (RAW / "stations.geojson").is_file(), reason="stations.geojson not fetched yet")
def test_real_stations_file_matches_contract():
    data = json.loads((RAW / "stations.geojson").read_text(encoding="utf-8"))
    assert data["type"] == "FeatureCollection" and data["features"]
    ids = [f["properties"]["station_id"] for f in data["features"]]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    refs = [f["properties"]["osm_ref"] for f in data["features"]]
    assert refs == sorted(refs)
    for f in data["features"]:
        p = f["properties"]
        assert f["geometry"]["type"] == "Point"
        assert p["access_basis"] in {"truck_tag", "truck_brand", "cardlock", "fleet_access", "truck_evidence", "diesel_only"}
        assert p["truck_access"] == ("unverified" if p["access_basis"] in {"truck_evidence", "diesel_only"} else "confirmed")
        assert p["station_class"] in {"travel_center", "cardlock", "branded", "unbranded"}
        assert (p["station_class"] == "travel_center") == p["truck_brand"]


@pytest.mark.skipif(not (RAW / "customer_points.csv").is_file(), reason="customer_points.csv not fetched yet")
def test_real_customer_file_matches_contract():
    rows = read_csv("customer_points.csv")
    assert list(rows[0]) == ["customer_id", "lat", "lon"]
    assert 0 < len(rows) <= config.get(config.load_assumptions(), "fetch.customers.max_points")
    assert [r["customer_id"] for r in rows] == [f"CUST-{n:04d}" for n in range(1, len(rows) + 1)]
    cache_file = config.CACHE_DIR / "osm" / "customers_points.json"
    stored = json.loads(cache_file.read_text(encoding="utf-8"))
    assert stored["offset_applied"] is True
    pairs = stored["points"]
    assert len(pairs) == len(rows)
    assert all(len(p) == 2 and all(isinstance(v, float) for v in p) for p in pairs)


def test_retry_and_timeout_settings_come_from_config_and_missing_keys_raise(cfg):
    assert cache.retry_settings(cfg) == (config.get(cfg, "fetch.retry.max_retries"), float(config.get(cfg, "fetch.retry.wait_s")))
    connect, read = cache.timeouts(cfg, min_read_s=500)
    assert connect == float(config.get(cfg, "fetch.connect_timeout_s")) and read == 500
    del cfg["fetch"]["retry"]
    with pytest.raises(KeyError, match="fetch.retry.max_retries"):
        cache.retry_settings(cfg)


def test_requests_get_a_connect_and_read_timeout_pair(cfg, net, tmp_path, monkeypatch):
    seen = []
    original = net.get
    monkeypatch.setattr(eia.requests, "get", lambda url, params=None, timeout=None: (seen.append(timeout), original(url, params, timeout))[1])
    eia.fetch_eia(cfg, refresh=False, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "eia")
    assert seen and all(isinstance(t, tuple) and len(t) == 2 for t in seen)
