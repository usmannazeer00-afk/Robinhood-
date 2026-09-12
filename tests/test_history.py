import json
from pathlib import Path

from robinhood_sniper.providers.history import TokenHistoryStore


def test_record_and_read_price_series(tmp_path):
    store = TokenHistoryStore(path=str(tmp_path / "hist.json"))
    store.record_price("0xPool", 1.0)
    store.record_price("0xPool", 1.5)
    series = store.price_series("0xPool")
    assert [p for _, p in series] == [1.0, 1.5]


def test_buyer_wallets_accumulate_across_calls(tmp_path):
    store = TokenHistoryStore(path=str(tmp_path / "hist.json"))
    store.record_buyers("0xPool", {"0xA", "0xB"}, last_block=100)
    store.record_buyers("0xPool", {"0xB", "0xC"}, last_block=200)
    assert store.buyer_series("0xPool") == [2, 3]
    assert store.get("0xPool")["last_scanned_block"] == 200


def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "hist.json")
    store = TokenHistoryStore(path=path)
    store.record_price("0xPool", 2.0)
    store.save()
    reloaded = TokenHistoryStore(path=path)
    assert reloaded.price_series("0xPool")[0][1] == 2.0


def test_prune_drops_stale_entries(tmp_path):
    path = tmp_path / "hist.json"
    stale_data = {
        "0xstale": {
            "pool_created_block": 1,
            "last_scanned_block": 1,
            "price_points": [["2020-01-01T00:00:00+00:00", 1.0]],
            "buyer_wallets": [],
            "buyer_series": [],
        }
    }
    path.write_text(json.dumps(stale_data))
    store = TokenHistoryStore(path=str(path))
    store.prune(max_age_minutes=45)
    assert "0xstale" not in store._data


def test_corrupt_file_loads_as_empty(tmp_path):
    path = tmp_path / "hist.json"
    path.write_text("not json")
    store = TokenHistoryStore(path=str(path))
    assert store.price_series("0xPool") == []


def test_get_backfills_entries_from_an_older_schema(tmp_path):
    path = tmp_path / "hist.json"
    old_schema_entry = {
        "0xpool": {
            "pool_created_block": 1,
            "last_scanned_block": 1,
            "price_points": [],
            "buyer_wallets": [],
            "buyer_series": [],
            # no "symbol" / "name" keys -- as persisted before that schema change
        }
    }
    path.write_text(json.dumps(old_schema_entry))
    store = TokenHistoryStore(path=str(path))
    entry = store.get("0xPool")
    assert entry["symbol"] is None
    assert entry["name"] is None
