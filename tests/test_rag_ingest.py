from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.rag import ingest


class _FakeCursor:
    def __init__(self, *, fetchone_result=None, fail_on=None) -> None:
        self.fetchone_result = fetchone_result
        self.fail_on = fail_on or []
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def execute(self, query, params=None) -> None:
        query_text = str(query)
        self.executed.append((query_text, params))
        for token, error in self.fail_on:
            if token in query_text:
                raise error

    def fetchone(self):
        return self.fetchone_result


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False
        self.committed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


class RagIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._keyword_ready = ingest._KEYWORD_SEARCH_READY
        ingest._KEYWORD_SEARCH_READY = False

    def tearDown(self) -> None:
        ingest._KEYWORD_SEARCH_READY = self._keyword_ready

    def test_count_existing_chunks_closes_connection(self) -> None:
        cursor = _FakeCursor(fetchone_result=(7,))
        connection = _FakeConnection(cursor)

        with patch.object(ingest.psycopg2, "connect", return_value=connection):
            result = ingest._count_existing_chunks(
                connection_string="postgresql://example",
                company_document_id="doc-1",
                revision_number="1",
                collection_name="rag_documents",
            )

        self.assertEqual(result, 7)
        self.assertTrue(connection.closed)

    def test_setup_keyword_search_is_non_fatal_and_closes_connection(self) -> None:
        cursor = _FakeCursor(
            fetchone_result=(False, False),
            fail_on=[
                (
                    "ALTER TABLE langchain_pg_embedding",
                    RuntimeError("canceling statement due to lock timeout"),
                )
            ],
        )
        connection = _FakeConnection(cursor)

        with patch.object(ingest.psycopg2, "connect", return_value=connection):
            ingest.setup_keyword_search("postgresql://example")

        self.assertFalse(ingest._KEYWORD_SEARCH_READY)
        self.assertTrue(connection.closed)
        self.assertFalse(connection.committed)


if __name__ == "__main__":
    unittest.main()
