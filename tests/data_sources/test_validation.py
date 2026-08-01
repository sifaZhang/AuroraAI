from datetime import date

import pytest

from backend.data_sources.errors import ProviderValidationError
from backend.data_sources.models import IndustryMembership, IndustryNode
from backend.data_sources.validation import validate_industry_nodes, validate_memberships


def node(level, code, name, parent=None):
    return IndustryNode("SW", "2021", code, name, level, parent, "fixture")


def member(symbol="600519.SH", l3="8501", current=True):
    return IndustryMembership("SW", "2021", symbol, "样本", "8010", "一级",
                              "80101", "二级", l3, "三级", date(2021, 1, 1),
                              None, current, "fixture")


def test_tree_parent_and_identity_conflicts_are_rejected():
    valid = [node(1, "8010", "一级"), node(2, "80101", "二级", "8010"),
             node(3, "8501", "三级", "80101")]
    assert validate_industry_nodes(valid, enforce_count_ranges=False) == valid
    with pytest.raises(ProviderValidationError, match="missing parent"):
        validate_industry_nodes([valid[0], node(3, "8501", "三级", "missing")],
                                enforce_count_ranges=False)
    with pytest.raises(ProviderValidationError, match="conflicting"):
        validate_industry_nodes([valid[0], node(1, "8010", "冲突")],
                                enforce_count_ranges=False)


def test_current_membership_uniqueness_and_date_order():
    assert validate_memberships([member()]) == [member()]
    with pytest.raises(ProviderValidationError, match="multiple current"):
        validate_memberships([member(), member(l3="8502")])
    broken = member()
    broken = IndustryMembership(*broken.__dict__.values())
    with pytest.raises(ProviderValidationError, match="in_date"):
        validate_memberships([IndustryMembership(
            "SW", "2021", "600519.SH", None, "1", "一", "2", "二", "3", "三",
            date(2022, 1, 1), date(2021, 1, 1), False, "fixture")])
