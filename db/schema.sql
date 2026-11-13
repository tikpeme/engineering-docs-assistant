-- =====================================================================
-- Kestrel Engineering Docs Assistant: Aurora PostgreSQL schema
-- pgvector for semantic search + Row-Level Security for permissions
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------
-- 1. Roles: one per service, least privilege
-- ---------------------------------------------------------------------
-- app_reader : used by FastAPI. Read-only. Always filtered by RLS.
-- app_ingest : used by the ingest worker. Reads and writes everything.
-- Neither role owns the tables, so RLS applies to both of them.
CREATE ROLE app_reader LOGIN;
CREATE ROLE app_ingest LOGIN;

-- ---------------------------------------------------------------------
-- 2. documents: one row per unique document revision
-- ---------------------------------------------------------------------
CREATE TABLE documents (
    id              bigserial PRIMARY KEY,
    doc_number      text        NOT NULL,             -- e.g. AMS-2759
    revision        text        NOT NULL,             -- e.g. F
    title           text,
    part_numbers    text[]      DEFAULT '{}',         -- e.g. {KPC-10442}
    release_status  text        NOT NULL DEFAULT 'Unknown'
                    CHECK (release_status IN ('Released','Superseded','Draft','Unknown')),
    source_system   text        NOT NULL
                    CHECK (source_system IN ('S-Drive','SharePoint','Teamcenter')),
    s3_key          text        NOT NULL,             -- where the raw file lives
    content_hash    char(64)    NOT NULL UNIQUE,      -- SHA-256, for dedupe
    allowed_groups  text[]      NOT NULL DEFAULT '{}',-- empty = nobody (default deny)
    is_itar         boolean     NOT NULL DEFAULT false,
    embedding_model text        NOT NULL,             -- e.g. amazon.titan-embed-text-v2:0
    last_modified   timestamptz,
    indexed_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (doc_number, revision)
);

-- Duplicate copies of the same file found in other folders.
-- Useful for Dana's cleanup report ("4 copies of AMS-2759 Rev D on S:").
CREATE TABLE document_paths (
    document_id  bigint REFERENCES documents(id) ON DELETE CASCADE,
    source_path  text   NOT NULL,
    PRIMARY KEY (document_id, source_path)
);

-- ---------------------------------------------------------------------
-- 3. chunks: the searchable pieces, each carrying its own permissions
-- ---------------------------------------------------------------------
CREATE TABLE chunks (
    id              bigserial PRIMARY KEY,
    document_id     bigint      NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     int         NOT NULL,
    page            int,
    content         text        NOT NULL,             -- includes the [doc / rev / page] header
    allowed_groups  text[]      NOT NULL DEFAULT '{}',-- copied from the parent document
    embedding       vector(1024) NOT NULL,            -- Titan Text Embeddings V2
    content_tsv     tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    UNIQUE (document_id, chunk_index)
);

-- ---------------------------------------------------------------------
-- 4. Structured data path: Excel tables loaded as real SQL, not chunks
-- ---------------------------------------------------------------------
CREATE TABLE material_properties (
    id               bigserial PRIMARY KEY,
    source_doc_id    bigint REFERENCES documents(id) ON DELETE CASCADE,
    material         text    NOT NULL,                -- e.g. 17-4 PH
    condition        text,                            -- e.g. H1025
    property         text    NOT NULL,                -- e.g. yield_strength
    value            numeric NOT NULL,
    unit             text    NOT NULL,                -- e.g. ksi
    allowed_groups   text[]  NOT NULL DEFAULT '{}'
);

-- ---------------------------------------------------------------------
-- 5. Indexes
-- ---------------------------------------------------------------------
-- Semantic search: HNSW graph index using cosine distance (<=>)
CREATE INDEX chunks_embedding_hnsw ON chunks
    USING hnsw (embedding vector_cosine_ops);

-- Keyword half of hybrid search (part numbers, spec IDs, exact phrases)
CREATE INDEX chunks_content_tsv ON chunks USING gin (content_tsv);

-- Fast permission checks (the && overlap operator)
CREATE INDEX chunks_allowed_groups    ON chunks              USING gin (allowed_groups);
CREATE INDEX documents_allowed_groups ON documents           USING gin (allowed_groups);
CREATE INDEX matprops_allowed_groups  ON material_properties USING gin (allowed_groups);

-- Exact lookups by part number
CREATE INDEX documents_part_numbers ON documents USING gin (part_numbers);

-- ---------------------------------------------------------------------
-- 5b. Triggers: keep chunk permissions in lockstep with their document
-- ---------------------------------------------------------------------
-- (a) When a document's permissions change, push them to all its chunks
--     in the same transaction. No window where they disagree.
CREATE FUNCTION sync_chunk_groups() RETURNS trigger AS $$
BEGIN
    UPDATE chunks SET allowed_groups = NEW.allowed_groups
    WHERE document_id = NEW.id;
    UPDATE material_properties SET allowed_groups = NEW.allowed_groups
    WHERE source_doc_id = NEW.id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER documents_groups_changed
    AFTER UPDATE OF allowed_groups ON documents
    FOR EACH ROW
    WHEN (OLD.allowed_groups IS DISTINCT FROM NEW.allowed_groups)
    EXECUTE FUNCTION sync_chunk_groups();

-- (b) When a chunk is inserted, ignore whatever groups the code passed in
--     and copy them from the parent document. Chunks can never disagree.
CREATE FUNCTION inherit_doc_groups() RETURNS trigger AS $$
BEGIN
    SELECT allowed_groups INTO NEW.allowed_groups
    FROM documents WHERE id = NEW.document_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER chunks_inherit_groups
    BEFORE INSERT OR UPDATE OF allowed_groups ON chunks
    FOR EACH ROW
    EXECUTE FUNCTION inherit_doc_groups();

-- ---------------------------------------------------------------------
-- 6. Row-Level Security
-- ---------------------------------------------------------------------
-- FastAPI sets the caller's groups at the start of every request:
--     SELECT set_config('app.user_groups', 'ENG-Design,QA', true);
-- The 'true' makes it transaction-local, so a pooled connection can never
-- carry one user's groups into the next user's request.
--
-- If the setting is missing, current_setting(..., true) returns NULL,
-- the overlap check is NULL, and NO rows are returned (default deny).

ALTER TABLE documents           ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks              ENABLE ROW LEVEL SECURITY;
ALTER TABLE material_properties ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_paths      ENABLE ROW LEVEL SECURITY;

-- Readers: only rows whose allowed_groups share at least one group with the caller
CREATE POLICY reader_documents ON documents FOR SELECT TO app_reader
    USING (allowed_groups && string_to_array(current_setting('app.user_groups', true), ','));

CREATE POLICY reader_chunks ON chunks FOR SELECT TO app_reader
    USING (allowed_groups && string_to_array(current_setting('app.user_groups', true), ','));

CREATE POLICY reader_matprops ON material_properties FOR SELECT TO app_reader
    USING (allowed_groups && string_to_array(current_setting('app.user_groups', true), ','));

CREATE POLICY reader_paths ON document_paths FOR SELECT TO app_reader
    USING (EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id));
    -- the subquery on documents is itself filtered by reader_documents

-- Ingest worker: full access (it has to write every document)
CREATE POLICY ingest_documents ON documents           FOR ALL TO app_ingest USING (true) WITH CHECK (true);
CREATE POLICY ingest_chunks    ON chunks              FOR ALL TO app_ingest USING (true) WITH CHECK (true);
CREATE POLICY ingest_matprops  ON material_properties FOR ALL TO app_ingest USING (true) WITH CHECK (true);
CREATE POLICY ingest_paths     ON document_paths      FOR ALL TO app_ingest USING (true) WITH CHECK (true);

-- ---------------------------------------------------------------------
-- 7. Grants
-- ---------------------------------------------------------------------
GRANT SELECT ON documents, chunks, material_properties, document_paths TO app_reader;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON documents, chunks, material_properties, document_paths TO app_ingest;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_ingest;

-- =====================================================================
-- Example query FastAPI runs (inside one transaction)
-- =====================================================================
-- BEGIN;
-- SELECT set_config('app.user_groups', :groups_from_verified_token, true);
--
-- SELECT c.content, d.doc_number, d.revision, d.release_status, c.page,
--        c.embedding <=> :question_vector AS distance
-- FROM chunks c
-- JOIN documents d ON d.id = c.document_id
-- ORDER BY distance
-- LIMIT 8;
-- -- FastAPI then labels any Superseded hit ("superseded by Rev F") and,
-- -- if a newer Released revision exists, pulls that one in instead.
-- COMMIT;
