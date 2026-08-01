from datetime import datetime, timezone

from backend.data_sources.industry_sync.service import sync_current_industries
from backend.data_sources.models import IndustryMembership, ProviderResult


def member(symbol="600519.SH", l3="850111", l3_name="三级", l2="801010"):
    return IndustryMembership("SW", "2021", symbol, "样本", "801000", "一级",
                              l2, "二级", l3, l3_name, None, None, True, "fixture")


class Provider:
    name = "fixture"
    def __init__(self, rows): self.rows, self.calls = rows, 0
    def list_memberships(self, **_kwargs):
        self.calls += 1; now = datetime.now(timezone.utc)
        return ProviderResult(self.rows, "fixture", now, now, len(self.rows))


class Repository:
    def __init__(self, matches=False, fail=False):
        self.matches, self.fail, self.writes = matches, fail, []
    def snapshot_matches(self, **_kwargs): return self.matches
    def replace_current_snapshot(self, **kwargs):
        if self.fail: raise RuntimeError("database failed")
        self.writes.append(kwargs); return True


def test_sync_writes_complete_snapshot_once_and_is_idempotent():
    provider = Provider([member()]); repository = Repository()
    result = sync_current_industries(provider=provider, repository=repository,
                                     enforce_count_ranges=False)
    assert result.status == "success" and result.changed and result.membership_written_count == 1
    assert provider.calls == 1 and len(repository.writes) == 1
    unchanged = sync_current_industries(provider=Provider([member()]),
                                        repository=Repository(matches=True),
                                        enforce_count_ranges=False)
    assert not unchanged.changed


def test_dry_run_detects_duplicate_and_conflict_without_writing(caplog):
    rows = [member(), member(), member("000001.SZ", "850111"),
            member("000001.SZ", "850112", "另一个三级")]
    repository = Repository()
    result = sync_current_industries(provider=Provider(rows), repository=repository,
                                     dry_run=True, enforce_count_ranges=False)
    assert result.status == "partial_success"
    assert result.duplicate_count == 1 and result.conflict_symbols == ("000001.SZ",)
    assert result.membership_written_count == 1 and not repository.writes
    assert "industry_membership_conflict" in caplog.text


def test_tree_conflict_and_empty_data_fail_without_write():
    conflicting_tree = [member(), IndustryMembership(
        "SW", "2021", "000001.SZ", "B", "801000", "冲突一级",
        "801010", "二级", "850111", "三级", None, None, True, "fixture")]
    repository = Repository()
    failed = sync_current_industries(provider=Provider(conflicting_tree), repository=repository,
                                     enforce_count_ranges=False)
    assert failed.status == "failed" and not repository.writes
    empty = sync_current_industries(provider=Provider([]), repository=repository,
                                    enforce_count_ranges=False)
    assert empty.status == "failed"


def test_database_failure_returns_failed_and_force_rewrites():
    failed = sync_current_industries(provider=Provider([member()]), repository=Repository(fail=True),
                                     enforce_count_ranges=False)
    assert failed.status == "failed"
    repository = Repository(matches=True)
    forced = sync_current_industries(provider=Provider([member()]), repository=repository,
                                     force=True, enforce_count_ranges=False)
    assert forced.forced and forced.changed and len(repository.writes) == 1
