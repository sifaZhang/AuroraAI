"""Atomic persistence and read models for the current industry snapshot."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone

from ..errors import ProviderValidationError
from ..models import IndustryMembership, IndustryNode
from ..symbol_normalizer import normalize_symbol


def _node_key(node: IndustryNode):
    return (node.classification, node.version, node.industry_code, node.industry_name,
            node.industry_level, node.parent_code, node.source)


def _membership_key(row: IndustryMembership):
    return (row.classification, row.version, row.symbol,
            row.level1_code, row.level1_name, row.level2_code, row.level2_name,
            row.level3_code, row.level3_name, row.source)


class IndustryRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def _existing_keys(self):
        nodes = {
            (row["classification"], row["classification_version"], row["industry_code"],
             row["industry_name"], row["industry_level"], row["parent_code"], row["source"])
            for row in self.connection.execute("SELECT * FROM industry_nodes")
        }
        memberships = {
            (row["classification"], row["classification_version"], row["symbol"],
             row["level1_code"], row["level1_name"], row["level2_code"], row["level2_name"],
             row["level3_code"], row["level3_name"], row["source"])
            for row in self.connection.execute("SELECT * FROM industry_memberships_current")
        }
        return nodes, memberships

    def snapshot_matches(self, *, nodes: Sequence[IndustryNode],
                         memberships: Sequence[IndustryMembership]) -> bool:
        existing_nodes, existing_memberships = self._existing_keys()
        return (existing_nodes == {_node_key(node) for node in nodes}
                and existing_memberships == {_membership_key(row) for row in memberships})

    def _before_swap(self) -> None:
        """Test seam invoked after staging and before formal tables are changed."""

    def replace_current_snapshot(self, *, nodes: Sequence[IndustryNode],
                                 memberships: Sequence[IndustryMembership],
                                 force: bool = False) -> bool:
        if not force and self.snapshot_matches(nodes=nodes, memberships=memberships):
            return False
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        connection = self.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DROP TABLE IF EXISTS temp.industry_nodes_stage")
            connection.execute("DROP TABLE IF EXISTS temp.industry_memberships_stage")
            connection.execute(
                """CREATE TEMP TABLE industry_nodes_stage(
                    classification TEXT NOT NULL,classification_version TEXT NOT NULL,
                    industry_code TEXT NOT NULL,industry_name TEXT NOT NULL,
                    industry_level INTEGER NOT NULL,parent_code TEXT,source TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(classification,classification_version,industry_code))"""
            )
            connection.execute(
                """CREATE TEMP TABLE industry_memberships_stage(
                    classification TEXT NOT NULL,classification_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,level1_code TEXT NOT NULL,level1_name TEXT NOT NULL,
                    level2_code TEXT NOT NULL,level2_name TEXT NOT NULL,
                    level3_code TEXT NOT NULL,level3_name TEXT NOT NULL,
                    source TEXT NOT NULL,updated_at TEXT NOT NULL,
                    PRIMARY KEY(classification,classification_version,symbol))"""
            )
            connection.executemany(
                "INSERT INTO industry_nodes_stage VALUES(?,?,?,?,?,?,?,?)",
                [(*_node_key(node), now) for node in nodes],
            )
            connection.executemany(
                "INSERT INTO industry_memberships_stage VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                [(*_membership_key(row), now) for row in memberships],
            )
            staged_nodes = connection.execute("SELECT COUNT(*) FROM industry_nodes_stage").fetchone()[0]
            staged_memberships = connection.execute(
                "SELECT COUNT(*) FROM industry_memberships_stage"
            ).fetchone()[0]
            if staged_nodes != len(nodes) or staged_memberships != len(memberships):
                raise ProviderValidationError("staged industry snapshot count mismatch")
            orphan_count = connection.execute(
                """SELECT COUNT(*) FROM industry_memberships_stage m
                   WHERE NOT EXISTS (SELECT 1 FROM industry_nodes_stage n
                     WHERE n.classification=m.classification
                       AND n.classification_version=m.classification_version
                       AND n.industry_code=m.level1_code)
                      OR NOT EXISTS (SELECT 1 FROM industry_nodes_stage n
                     WHERE n.classification=m.classification
                       AND n.classification_version=m.classification_version
                       AND n.industry_code=m.level2_code)
                      OR NOT EXISTS (SELECT 1 FROM industry_nodes_stage n
                     WHERE n.classification=m.classification
                       AND n.classification_version=m.classification_version
                       AND n.industry_code=m.level3_code)"""
            ).fetchone()[0]
            if orphan_count:
                raise ProviderValidationError("staged memberships reference missing industry nodes")
            self._before_swap()
            connection.execute("DELETE FROM industry_memberships_current")
            connection.execute("DELETE FROM industry_nodes")
            connection.execute("INSERT INTO industry_nodes SELECT * FROM industry_nodes_stage")
            connection.execute(
                "INSERT INTO industry_memberships_current SELECT * FROM industry_memberships_stage"
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise

    def list_nodes(self, *, level: int | None = None,
                   parent_code: str | None = None) -> list[IndustryNode]:
        if level not in {None, 1, 2, 3}:
            raise ProviderValidationError("industry level must be 1, 2 or 3")
        clauses = ["classification='SW'", "classification_version='2021'"]
        args: list[object] = []
        if level is not None:
            clauses.append("industry_level=?"); args.append(level)
        if parent_code is not None:
            clauses.append("parent_code=?"); args.append(parent_code.removesuffix(".SI"))
        rows = self.connection.execute(
            f"SELECT * FROM industry_nodes WHERE {' AND '.join(clauses)} "
            "ORDER BY industry_level,industry_code", args,
        )
        return [IndustryNode(row["classification"], row["classification_version"],
                             row["industry_code"], row["industry_name"],
                             row["industry_level"], row["parent_code"], row["source"])
                for row in rows]

    @staticmethod
    def _membership(row: sqlite3.Row) -> IndustryMembership:
        return IndustryMembership(
            row["classification"], row["classification_version"], row["symbol"], None,
            row["level1_code"], row["level1_name"], row["level2_code"], row["level2_name"],
            row["level3_code"], row["level3_name"], None, None, True, row["source"],
        )

    def get_symbol_membership(self, symbol: str) -> IndustryMembership | None:
        row = self.connection.execute(
            """SELECT * FROM industry_memberships_current
               WHERE classification='SW' AND classification_version='2021' AND symbol=?""",
            (normalize_symbol(symbol),),
        ).fetchone()
        return self._membership(row) if row else None

    def list_constituents(self, industry_code: str, *, level: int) -> list[IndustryMembership]:
        if level not in {1, 2, 3}:
            raise ProviderValidationError("industry level must be 1, 2 or 3")
        column = {1: "level1_code", 2: "level2_code", 3: "level3_code"}[level]
        rows = self.connection.execute(
            f"""SELECT * FROM industry_memberships_current
                WHERE classification='SW' AND classification_version='2021'
                  AND {column}=? ORDER BY symbol""",
            (industry_code.removesuffix(".SI"),),
        )
        return [self._membership(row) for row in rows]
