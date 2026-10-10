"""다중 세트 11단계 — 마이그레이션 파일의 정적 대조. 실제 적용 시험은 '미검증'이다(Supabase 로컬 스택 · 브랜치 DB 가 없다, 명세 5-3).
여기서는 DB 없이 볼 수 있는 것만 본다: 파일 이름 규칙 · 트랜잭션 문 없음 · 엔진의 on_conflict 열이 새 기본키와 같다 · 심박 함수가 시험대 단위."""
import re
from pathlib import Path

import pytest

from cellbench.cloud import Cloud

MIG = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
FILE = MIG / "20261010174625_bench_multi_set.sql"
SQL = FILE.read_text(encoding="utf-8")


def test_name_and_no_transaction_statements():
    assert re.fullmatch(r"\d{14}_[a-z0-9_]+\.sql", FILE.name)
    code = "\n".join(l.split("--")[0] for l in SQL.splitlines())
    assert not re.search(r"\b(begin|commit)\s*;", code, re.I)             # 적용 도구가 트랜잭션을 감싼다 (B-L5)
    assert sorted(MIG.glob("*.sql"))[-1] == FILE                            # 사전순이 적용 순서 — 마지막이다


class V2(Cloud):
    def __init__(self):
        super().__init__(log=lambda m: None, url="", key="", bench_id="b")
        self.schema = 2


@pytest.mark.parametrize("table,cols", [("bench_state", "bench_id"), ("bench_sample", "bench_id,t"),
                                        ("bench_cycle", "bench_id,cycle"), ("bench_discharge", "bench_id,cycle,serial")])
def test_client_on_conflict_matches_new_primary_keys(table, cols):
    pk = re.search(rf"alter table public\.{table}\s+drop constraint {table}_pkey,\s+add primary key \(([^)]*)\)", SQL)
    assert pk, table
    want = [c.strip() for c in pk.group(1).split(",")]
    assert V2()._oc(cols)["on_conflict"].split(",") == want


def test_heartbeat_groups_by_bench_and_version_function():
    assert "group by b.bench_id" in SQL and "not coalesce(r.ended, false)" in SQL
    assert "create or replace function public.bench_schema_version() returns int language sql immutable as $$ select 2 $$;" in SQL
    assert "group by bench_id, set_id, 3" in SQL and "'resume_set'" in SQL
