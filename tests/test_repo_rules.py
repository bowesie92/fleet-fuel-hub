"""Hard rules enforced on every test run: no forbidden terms, no em dashes, no secrets tracked.

The forbidden-terms check is skipped where no .local/forbidden_terms.txt exists in the
checkout or a parent folder (for example in CI). Agent worktrees find the main checkout's list. Failure messages show paths and counts only.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_repo_rules", ROOT / "scripts" / "check_repo_rules.py")
rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rules)


def test_no_em_dashes():
    hits = rules.em_dash_hits(rules.collect_texts())
    assert hits == {}, f"em dashes found in: {sorted(hits)}"


def test_no_forbidden_terms():
    terms = rules.load_terms()
    if not terms:
        pytest.skip("no .local/forbidden_terms.txt here")
    hits = rules.forbidden_term_hits(rules.collect_texts(), terms)
    assert not hits, f"forbidden terms found in: {sorted(hits)} (run scripts/check_repo_rules.py)"


def test_terms_file_found_in_parent_folder(tmp_path):
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local" / "forbidden_terms.txt").write_text("example\n", encoding="utf-8")
    nested = tmp_path / ".claude" / "worktrees" / "some-agent"
    nested.mkdir(parents=True)
    assert rules.find_terms_file(nested) == tmp_path / ".local" / "forbidden_terms.txt"


def test_terms_file_not_invented_when_absent(tmp_path):
    found = rules.find_terms_file(tmp_path)
    # Nothing under tmp_path, so any hit must come from a real parent folder, never from tmp_path itself.
    assert found is None or tmp_path not in found.parents


def test_no_secrets_tracked():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    bad = [p for p in tracked if p == ".env" or p.startswith(".local/")]
    assert bad == []


# Planted samples, built in pieces so this file never holds a real-looking secret.
SECRET_SAMPLES = {
    "GitHub token": "gh" + "p_" + "A" * 36,
    "sk- style API key": "s" + "k-" + "b" * 24,
    "AWS access key": "AK" + "IA" + "Z" * 16,
    "private key block": "-----BEGIN RSA " + "PRIVATE KEY-----",
    "key, token, secret or password set to a long value": "api_key" + " = '" + "c" * 24 + "'",
    "bearer token": "Bearer " + "d" * 24,
    "api_key in a URL": "https://example.test/v2?api_" + "key=" + "e" * 24,
}


@pytest.mark.parametrize("label", sorted(SECRET_SAMPLES))
def test_every_secret_pattern_finds_its_planted_sample(label):
    assert label in rules.secret_hits({"sample.txt": f"x {SECRET_SAMPLES[label]} y"})["sample.txt"]


def test_secret_patterns_ignore_key_names_without_values():
    assert rules.secret_hits({".env.example": "ORS_API_KEY=\nEIA_API_KEY=\n"}) == {}


def test_history_mode_finds_a_term_planted_in_an_old_commit(tmp_path):
    """A word committed once and deleted later is still in history, and history mode finds it."""
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "test@example.test")
    git("config", "user.name", "Test")
    (tmp_path / "notes.txt").write_text("planted-word\n", encoding="utf-8")
    git("add", "notes.txt")
    git("commit", "-q", "-m", "first")
    (tmp_path / "notes.txt").write_text("clean now\n", encoding="utf-8")
    git("commit", "-q", "-am", "second")
    texts = rules.history_texts(tmp_path)
    assert rules.forbidden_term_hits(texts, ["planted-word"])
    assert any(name.startswith("<commit ") for name in texts)


_offsets_spec = importlib.util.spec_from_file_location("check_customer_offsets", ROOT / "scripts" / "check_customer_offsets.py")
offsets = importlib.util.module_from_spec(_offsets_spec)
_offsets_spec.loader.exec_module(offsets)


def test_coordinate_pairs_never_span_two_csv_rows():
    text = "lat,lon\n32.20000,-110.90000\n32.30000,-110.95000\n"
    pairs = offsets.coordinate_pairs(text, "data/raw/points.csv")
    assert (32.3, -110.9) not in pairs and (32.2, -110.9) in pairs and (32.3, -110.95) in pairs


def test_coordinate_pairs_span_lines_in_indented_geojson():
    text = '{"coordinates": [\n  -110.90000,\n  32.20000\n]}'
    assert (32.2, -110.9) in offsets.coordinate_pairs(text, "site/data/routes.geojson")


def test_an_original_point_is_found_and_a_moved_one_is_not():
    index = offsets.OriginalIndex({"CUST-0001": (32.2, -110.9)})
    assert index.near((32.20001, -110.90001), 5)
    assert not index.near((32.2036, -110.9), 5)  # about 400 m north


_guard_spec = importlib.util.spec_from_file_location("public_push_guard", ROOT / "scripts" / "public_push_guard.py")
guard = importlib.util.module_from_spec(_guard_spec)
_guard_spec.loader.exec_module(guard)


def test_push_guard_refuses_old_history_and_allows_the_public_root(tmp_path, monkeypatch):
    """A branch whose history starts anywhere but the public root commit is refused; one from the root is allowed."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("config", "user.email", "test@example.test")
    git("config", "user.name", "Test")
    (tmp_path / "a.txt").write_text("old\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-q", "-m", "old private history")
    old = git("rev-parse", "HEAD")
    git("checkout", "-q", "--orphan", "public")
    git("commit", "-q", "-m", "initial public release")
    root = git("rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)
    url = "https://github.com/example/public-repo.git"
    zero = "0" * 40
    assert guard.check(url, [f"refs/heads/old {old} refs/heads/old {zero}"], url, root) == ["refs/heads/old"]
    assert guard.check(url, [f"refs/heads/public {root} refs/heads/main {zero}"], url, root) == []
    assert guard.check("https://github.com/example/dev-repo", [f"refs/heads/old {old} refs/heads/old {zero}"], url, root) == []
    assert guard.check(url, [f"refs/heads/public {root} refs/heads/main {zero}"], url, "") == ["refs/heads/public"]


def test_push_guard_matches_ssh_urls_and_fails_closed_without_a_configured_url():
    https = "https://github.com/bowesie92/fleet-fuel-hub.git"
    assert guard.is_public_target("git@github.com:bowesie92/fleet-fuel-hub.git", https)
    assert guard.is_public_target("git@github.com:bowesie92/fleet-fuel-hub.git", "")  # nothing configured: still guarded
    assert not guard.is_public_target("https://github.com/bowesie92/fleet-fuel-hub-dev.git", "")
    assert not guard.is_public_target("git@github.com:bowesie92/fleet-fuel-hub-dev.git", https)


def test_history_scan_fails_when_the_terms_list_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(rules, "load_terms", lambda *a, **k: [])
    assert rules.main(["--history"]) == 1
    assert "MISSING" in capsys.readouterr().out


def test_moving_gps_pings_are_set_apart_but_parked_ones_still_count():
    """A ping at speed is a road point; a ping at speed 0 sits at a place and must still be caught."""
    text = ("device_id,event_time,lat,lon,odometer_mi,speed_mph\n"
            "GPS-1,2026-01-01 08:00:00,32.20000,-110.90000,100.0,21\n"
            "GPS-1,2026-01-01 08:05:00,32.20000,-110.90000,101.0,0\n")
    assert offsets.moving_rows(text, 5) == {1}
    assert offsets.moving_rows(text, 25) == set()  # 21 mph is under a 25 mph floor, so it would count as a hit
    assert offsets.coordinate_pairs(text, "data/raw/telematics.csv", skip_lines={1}) == [(32.2, -110.9)]
    assert offsets.moving_rows("customer_id,lat,lon\nCUST-0001,32.2,-110.9\n", 5) == set()


def test_check_moved_fails_a_point_with_too_few_candidates(tmp_path):
    """A point with only 2 originals 300 to 600 m away fails a 3-candidate rule and passes a 2-candidate one."""
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "raw" / "customer_points.csv").write_text("customer_id,lat,lon\nCUST-0001,32.2,-110.9\n")
    index = offsets.OriginalIndex({"a": (32.2036, -110.9), "b": (32.1964, -110.9)})  # about 400 m north and south
    assert offsets.check_moved(tmp_path, {"CUST-0001"}, index, 300, 600, 50, 3) == 1
    assert offsets.check_moved(tmp_path, {"CUST-0001"}, index, 300, 600, 50, 2) == 0


def test_offset_check_counts_candidates_once_per_place():
    """The old CSV points are also in the old cache; a place listed twice is one candidate, not two."""
    index = offsets.OriginalIndex({"CUST-0001": (32.2, -110.9), "cache-0": (32.2, -110.9)})
    assert len(index.distances((32.2, -110.9), 1.0)) == 1
