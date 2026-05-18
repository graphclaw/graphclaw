-- infrastructure/postgres/init/00-extensions.sql
-- Creates AGE and pgvector extensions. Must run before any schema or grant scripts.

CREATE EXTENSION IF NOT EXISTS age;
CREATE EXTENSION IF NOT EXISTS vector;

LOAD 'age';
SET search_path = ag_catalog, "$user", public;
