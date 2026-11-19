"""
Step 7 of the worker: write to Aurora.

Running locally, the laptop cannot reach Aurora (private subnet, by design), so
this module talks to it through the RDS Data API: SQL sent over an HTTPS AWS
API call, authenticated with the Secrets Manager secret. In ECS we will swap
this for a direct connection with IAM auth; the SQL stays the same.

Data API notes:
  * Parameters are named (:name). Arrays and vectors are passed as text and
    CAST in SQL, because the Data API has no array/vector parameter types.
  * A paused Aurora cluster answers the first call with DatabaseResumingException;
    we retry while it wakes up (about 15 seconds).
"""

import time

import boto3


def _param(name, value):
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    if isinstance(value, bool):                      # check bool before int: bool is an int in Python
        return {"name": name, "value": {"booleanValue": value}}
    if isinstance(value, int):
        return {"name": name, "value": {"longValue": value}}
    return {"name": name, "value": {"stringValue": str(value)}}


def pg_text_array(items: list[str]) -> str:
    """['ENG-Design', 'QA'] -> '{"ENG-Design","QA"}', a Postgres array literal."""
    escaped = ['"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"' for s in items]
    return "{" + ",".join(escaped) + "}"


def pg_vector(values: list[float]) -> str:
    """[0.1, -0.2, ...] -> '[0.1,-0.2,...]', the pgvector text format."""
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


class Store:
    def __init__(self, cluster_arn: str, secret_arn: str, database: str, region: str):
        self.client = boto3.client("rds-data", region_name=region)
        self.base = {"resourceArn": cluster_arn, "secretArn": secret_arn, "database": database}

    # -- low level ---------------------------------------------------------
    def _call(self, fn, **kwargs):
        for attempt in range(12):
            try:
                return fn(**self.base, **kwargs)
            except self.client.exceptions.DatabaseResumingException:
                print(f"  (Aurora is waking from auto-pause, retry {attempt + 1}...)")
                time.sleep(5)
        raise RuntimeError("Aurora did not resume in time")

    def execute(self, sql: str, params: dict | None = None, tx: str | None = None):
        kwargs = {"sql": sql, "parameters": [_param(k, v) for k, v in (params or {}).items()]}
        if tx:
            kwargs["transactionId"] = tx
        return self._call(self.client.execute_statement, **kwargs)

    def begin(self) -> str:
        return self._call(self.client.begin_transaction)["transactionId"]

    def commit(self, tx: str):
        self.client.commit_transaction(resourceArn=self.base["resourceArn"],
                                       secretArn=self.base["secretArn"], transactionId=tx)

    def rollback(self, tx: str):
        self.client.rollback_transaction(resourceArn=self.base["resourceArn"],
                                         secretArn=self.base["secretArn"], transactionId=tx)

    # -- queries the worker needs ------------------------------------------
    def find_by_hash(self, content_hash: str):
        r = self.execute("SELECT id, doc_number, revision FROM documents WHERE content_hash = :h",
                         {"h": content_hash})
        rows = r.get("records", [])
        if not rows:
            return None
        row = rows[0]
        return row[0]["longValue"], row[1]["stringValue"], row[2]["stringValue"]

    def add_path(self, document_id: int, source_path: str, tx: str | None = None):
        self.execute("INSERT INTO document_paths (document_id, source_path) VALUES (:d, :p) "
                     "ON CONFLICT DO NOTHING", {"d": document_id, "p": source_path}, tx)

    def upsert_document(self, tx: str, *, meta, source_system, s3_key, content_hash,
                        groups, is_itar, embedding_model, last_modified) -> int:
        """
        One row per (doc_number, revision). If that revision was indexed before
        with different content, update it in place; the caller then replaces
        its chunks. Changing allowed_groups here fires the sync trigger.
        """
        r = self.execute(
            """
            INSERT INTO documents (doc_number, revision, title, part_numbers, release_status,
                                   source_system, s3_key, content_hash, allowed_groups, is_itar,
                                   embedding_model, last_modified)
            VALUES (:doc, :rev, :title, CAST(:parts AS text[]), :status,
                    :src, :key, :hash, CAST(:groups AS text[]), :itar,
                    :model, CAST(:modified AS timestamptz))
            ON CONFLICT (doc_number, revision) DO UPDATE SET
                title = EXCLUDED.title, part_numbers = EXCLUDED.part_numbers,
                release_status = EXCLUDED.release_status, s3_key = EXCLUDED.s3_key,
                content_hash = EXCLUDED.content_hash, allowed_groups = EXCLUDED.allowed_groups,
                is_itar = EXCLUDED.is_itar, embedding_model = EXCLUDED.embedding_model,
                last_modified = EXCLUDED.last_modified, indexed_at = now()
            RETURNING id
            """,
            {"doc": meta.doc_number, "rev": meta.revision, "title": meta.title,
             "parts": pg_text_array(meta.part_numbers), "status": meta.release_status,
             "src": source_system, "key": s3_key, "hash": content_hash,
             "groups": pg_text_array(groups), "itar": is_itar,
             "model": embedding_model, "modified": last_modified},
            tx,
        )
        return r["records"][0][0]["longValue"]

    def replace_chunks(self, tx: str, document_id: int, chunks, vectors):
        self.execute("DELETE FROM chunks WHERE document_id = :d", {"d": document_id}, tx)
        for chunk, vec in zip(chunks, vectors):
            # allowed_groups is not passed: the chunks_inherit_groups trigger copies it
            # from the parent document, so the worker cannot get it wrong.
            self.execute(
                "INSERT INTO chunks (document_id, chunk_index, page, content, embedding) "
                "VALUES (:d, :i, :p, :c, CAST(:e AS vector))",
                {"d": document_id, "i": chunk.index, "p": chunk.page,
                 "c": chunk.content, "e": pg_vector(vec)},
                tx,
            )

    def replace_material_rows(self, tx: str, document_id: int, rows):
        self.execute("DELETE FROM material_properties WHERE source_doc_id = :d", {"d": document_id}, tx)
        for material, condition, prop, value, unit in rows:
            self.execute(
                "INSERT INTO material_properties (source_doc_id, material, condition, property, value, unit, "
                "allowed_groups) SELECT :d, :m, :c, :p, CAST(:v AS numeric), :u, allowed_groups "
                "FROM documents WHERE id = :d",
                {"d": document_id, "m": material, "c": condition, "p": prop, "v": str(value), "u": unit},
                tx,
            )

    def supersede_older(self, tx: str, doc_number: str, revision: str) -> int:
        """When a Released revision arrives, mark lower revisions Superseded."""
        r = self.execute(
            "UPDATE documents SET release_status = 'Superseded' "
            "WHERE doc_number = :doc AND revision < :rev AND release_status <> 'Superseded'",
            {"doc": doc_number, "rev": revision}, tx)
        return r.get("numberOfRecordsUpdated", 0)
