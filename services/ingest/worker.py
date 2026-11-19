"""
Kestrel ingest worker: S3 documents -> permission-tagged, embedded chunks in Aurora.

For each file under the S3 prefix:
  1. look up its groups in the ACL manifest (no entry = no groups = nobody)
  2. SHA-256 hash it; an identical file already indexed is recorded as a duplicate path
  3. extract text (pypdf, Textract for scans, python-docx) or read Excel rows
  4. export-control scan: an ITAR marking REPLACES the groups with ITAR-Authorized
  5. pull metadata (doc number, revision, status, part numbers)
  6. chunk and embed with Bedrock Titan
  7. write everything to Aurora in one transaction

Run locally (reads the real S3 bucket, writes through the RDS Data API):
    python services/ingest/worker.py

Dry run (reads ./sample-s-drive, calls no AWS service, prints what it would do):
    python services/ingest/worker.py --dry-run
"""

import argparse
import hashlib
import io
import json
import os
import sys
from pathlib import Path, PurePosixPath

import boto3
from openpyxl import load_workbook

from chunking import chunk_pages
from extract import extract_text
from metadata import DocMeta, apply_export_control, extract_metadata

REGION = os.environ.get("AWS_REGION", "us-east-2")
PREFIX = "s-drive/"
MANIFEST_NAME = "acl_manifest.json"
EMBED_MODEL = "amazon.titan-embed-text-v2:0"


# ---------------------------------------------------------------------------
# Sources: where files come from (S3 for real runs, a local folder for dry runs)
# ---------------------------------------------------------------------------
class S3Source:
    def __init__(self, bucket: str):
        self.bucket = bucket
        self.s3 = boto3.client("s3", region_name=REGION)

    def read(self, rel: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=PREFIX + rel)["Body"].read()

    def list(self):
        """Yields (relative_path, last_modified as 'YYYY-MM-DD HH:MM:SS')."""
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=PREFIX):
            for obj in page.get("Contents", []):
                rel = obj["Key"][len(PREFIX):]
                if rel and rel != MANIFEST_NAME:
                    yield rel, obj["LastModified"].strftime("%Y-%m-%d %H:%M:%S")


class LocalSource:
    def __init__(self, root: str = "sample-s-drive"):
        self.root = Path(root)

    def read(self, rel: str) -> bytes:
        return (self.root / rel).read_bytes()

    def list(self):
        for f in sorted(self.root.rglob("*")):
            rel = f.relative_to(self.root).as_posix()
            if f.is_file() and rel != MANIFEST_NAME:
                yield rel, None


# ---------------------------------------------------------------------------
# AWS calls the pipeline needs
# ---------------------------------------------------------------------------
def make_textract_ocr(bucket: str):
    textract = boto3.client("textract", region_name=REGION)

    def ocr(rel: str):
        # Synchronous OCR handles single-page documents. Multi-page scans need the
        # async API (StartDocumentTextDetection + polling): a production TODO.
        resp = textract.detect_document_text(Document={"S3Object": {"Bucket": bucket, "Name": PREFIX + rel}})
        pages: dict[int, list[str]] = {}
        for block in resp["Blocks"]:
            if block["BlockType"] == "LINE":
                pages.setdefault(block.get("Page", 1), []).append(block["Text"])
        return [(p, "\n".join(lines)) for p, lines in sorted(pages.items())]

    return ocr


def make_embedder():
    bedrock = boto3.client("bedrock-runtime", region_name=REGION)

    def embed(text: str) -> list[float]:
        body = json.dumps({"inputText": text, "dimensions": 1024, "normalize": True})
        resp = bedrock.invoke_model(modelId=EMBED_MODEL, body=body)
        return json.loads(resp["body"].read())["embedding"]

    return embed


def dry_run_ocr(rel: str):
    return [(1, "[dry run: this scan would be sent to Textract]")]


# ---------------------------------------------------------------------------
# Excel: the structured path
# ---------------------------------------------------------------------------
def read_material_rows(data: bytes):
    ws = load_workbook(io.BytesIO(data), read_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    header, body = rows[0], rows[1:]
    if [str(h).lower() for h in header] != ["material", "condition", "property", "value", "unit"]:
        raise ValueError(f"Unexpected material table header: {header}")
    return [r for r in body if r and r[0] is not None]


# ---------------------------------------------------------------------------
# The pipeline for one file
# ---------------------------------------------------------------------------
def process_file(rel, last_modified, source, manifest, store, ocr, embed, dry_run, report):
    data = source.read(rel)
    content_hash = hashlib.sha256(data).hexdigest()
    stem = PurePosixPath(rel).stem

    # 1. permissions from the manifest (default deny)
    groups = manifest.get(rel, [])

    # 2. dedupe
    if store:
        existing = store.find_by_hash(content_hash)
        if existing:
            doc_id, doc, rev = existing
            store.add_path(doc_id, rel)
            print(f"  DUPLICATE of {doc} Rev {rev}: recorded path only, nothing re-embedded")
            report["duplicates"].append(rel)
            return
    elif content_hash in report["seen_hashes"]:
        print(f"  DUPLICATE of {report['seen_hashes'][content_hash]}: would record path only")
        report["duplicates"].append(rel)
        return
    report["seen_hashes"][content_hash] = rel

    # 3. extract
    if rel.lower().endswith(".xlsx"):
        rows = read_material_rows(data)
        text, pages, method = " ".join(str(c) for r in rows for c in r), None, "excel-rows"
    else:
        pages, method = extract_text(rel, data, ocr)
        text = "\n".join(t for _, t in pages)

    # 4. export control (content beats folder)
    groups, is_itar, phrase = apply_export_control(text, groups)
    if is_itar and manifest.get(rel, []) != groups:
        msg = f"ITAR marking '{phrase}' in {rel}: groups {manifest.get(rel, [])} -> {groups}"
        print(f"  [REVIEW] {msg}")
        report["itar_overrides"].append(msg)

    # 5. metadata
    meta = extract_metadata(text, stem) if pages is not None else DocMeta(stem, "-", stem)
    print(f"  {meta.doc_number} Rev {meta.revision} | {meta.release_status} | via {method} | groups {groups}")

    # 6. chunk + embed
    chunks = chunk_pages(pages, meta.doc_number, meta.revision, meta.title) if pages is not None else []
    vectors = [embed(c.content) for c in chunks] if (chunks and not dry_run) else []
    if chunks:
        print(f"  {len(chunks)} chunk(s); first starts: {chunks[0].content[:90]!r}")

    if dry_run:
        report["documents"] += 1
        return

    # 7. write, all or nothing
    tx = store.begin()
    try:
        doc_id = store.upsert_document(
            tx, meta=meta, source_system="S-Drive", s3_key=PREFIX + rel, content_hash=content_hash,
            groups=groups, is_itar=is_itar,
            embedding_model=EMBED_MODEL if chunks else "none", last_modified=last_modified)
        store.add_path(doc_id, rel, tx)
        if pages is None:
            store.replace_material_rows(tx, doc_id, rows)
        else:
            store.replace_chunks(tx, doc_id, chunks, vectors)
        if meta.release_status == "Released":
            n = store.supersede_older(tx, meta.doc_number, meta.revision)
            if n:
                print(f"  marked {n} older revision(s) of {meta.doc_number} as Superseded")
        store.commit(tx)
    except Exception:
        store.rollback(tx)
        raise
    report["documents"] += 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="local files only, no AWS calls")
    args = parser.parse_args()

    if args.dry_run:
        source, store, ocr, embed = LocalSource(), None, dry_run_ocr, None
        manifest_list = json.loads((Path("sample-s-drive") / MANIFEST_NAME).read_text())
    else:
        try:
            bucket = os.environ["DOCS_BUCKET"]
            cluster_arn, secret_arn = os.environ["DB_CLUSTER_ARN"], os.environ["DB_SECRET_ARN"]
        except KeyError as missing:
            sys.exit(f"Missing environment variable {missing}. See the README for setup.")
        from store import Store
        source = S3Source(bucket)
        store = Store(cluster_arn, secret_arn, os.environ.get("DB_NAME", "docs"), REGION)
        ocr, embed = make_textract_ocr(bucket), make_embedder()
        manifest_list = json.loads(source.read(MANIFEST_NAME))

    manifest = {e["path"]: e["groups"] for e in manifest_list}
    report = {"documents": 0, "duplicates": [], "itar_overrides": [], "failed": [], "seen_hashes": {}}

    for rel, last_modified in source.list():
        print(f"\n{rel}")
        try:
            process_file(rel, last_modified, source, manifest, store, ocr, embed, args.dry_run, report)
        except Exception as e:                       # one bad file must not stop the batch
            print(f"  FAILED: {e}")
            report["failed"].append(rel)

    print("\n=== Summary ===")
    print(f"Indexed:    {report['documents']}")
    print(f"Duplicates: {len(report['duplicates'])} {report['duplicates']}")
    print(f"ITAR overrides needing review: {len(report['itar_overrides'])}")
    for m in report["itar_overrides"]:
        print(f"  - {m}")
    print(f"Failed:     {len(report['failed'])} {report['failed']}")


if __name__ == "__main__":
    main()
