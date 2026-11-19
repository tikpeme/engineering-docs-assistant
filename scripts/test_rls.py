"""
Prove Row-Level Security: log in as dbadmin through the Data API, switch to the
app_reader role inside a transaction, pretend to be users with different groups,
and show exactly which documents and chunks each one can see.

One-time prerequisite (Query Editor, as dbadmin):
    GRANT app_reader TO dbadmin;      -- lets dbadmin "become" app_reader for testing

Usage (from the repo root, with DB_CLUSTER_ARN and DB_SECRET_ARN exported):
    python scripts/test_rls.py
"""

import os
import sys

sys.path.insert(0, "services/ingest")
from store import Store  # noqa: E402

CASES = [
    ("Design engineer", ["ENG-Design"]),
    ("Quality inspector", ["QA"]),
    ("Buyer", ["Procurement"]),
    ("ITAR-cleared engineer", ["ITAR-Authorized"]),
    ("Anyone ('Everyone' group)", ["Everyone"]),
    ("Groups setting missing", None),          # FastAPI forgot to set it: must see nothing
]


def visible(store: Store, groups):
    tx = store.begin()                          # one transaction = one connection
    try:
        # SET LOCAL lasts only until this transaction ends, like set_config(..., true)
        store.execute("SET LOCAL ROLE app_reader", tx=tx)
        if groups is not None:
            store.execute("SELECT set_config('app.user_groups', :g, true)", {"g": ",".join(groups)}, tx)
        docs = store.execute("SELECT doc_number, revision FROM documents ORDER BY 1, 2", tx=tx)
        chunks = store.execute("SELECT count(*) FROM chunks", tx=tx)
        names = [f"{r[0]['stringValue']} {r[1]['stringValue']}" for r in docs.get("records", [])]
        return names, chunks["records"][0][0]["longValue"]
    finally:
        store.rollback(tx)                      # read-only test: never commit anything


def main():
    store = Store(os.environ["DB_CLUSTER_ARN"], os.environ["DB_SECRET_ARN"],
                  os.environ.get("DB_NAME", "docs"), os.environ.get("AWS_REGION", "us-east-2"))
    for label, groups in CASES:
        names, n_chunks = visible(store, groups)
        print(f"\n{label}  groups={groups}")
        print(f"  sees {len(names)} document(s), {n_chunks} chunk(s)")
        for n in names:
            print(f"    - {n}")


if __name__ == "__main__":
    main()
