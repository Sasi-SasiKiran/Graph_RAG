import hashlib
import re
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from neo4j import Session

from database.neo4j_connection import Neo4jConnection
from models.schemas import Chunk, ExtractionResult, LoadedDocument
from services.entity_extractor import normalize_entity_name

STOP_WORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "because",
    "been",
    "before",
    "being",
    "between",
    "can",
    "could",
    "do",
    "does",
    "from",
    "have",
    "into",
    "is",
    "its",
    "more",
    "most",
    "that",
    "the",
    "their",
    "there",
    "these",
    "this",
    "through",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "will",
    "with",
    "would",
}

DOCUMENT_QUERY = """
MERGE (d:Document {id: $document_id})
SET d:Source,
    d.title = $title,
    d.source_id = $source_id,
    d.workspace_id = $workspace_id,
    d.generation_id = $generation_id,
    d.processing_status = 'Processed',
    d.filename = $title,
    d.text = $text,
    d.original_text = $original_text,
    d.media_type = $media_type,
    d.page_count = $page_count,
    d.updated_at = $updated_at
WITH d
MATCH (g:Generation {id: $generation_id, workspace_id: $workspace_id})
MERGE (g)-[:CONTAINS]->(d)
WITH d
UNWIND $chunks AS item
MERGE (c:Chunk {id: item.id})
SET c.text = item.text,
    c.original_text = item.original_text,
    c.source_id = item.source_id,
    c.workspace_id = $workspace_id,
    c.generation_id = $generation_id,
    c.filename = $title,
    c.index = item.index,
    c.page_number = item.page_number,
    c.section = item.section
MERGE (d)-[:CONTAINS]->(c)
RETURN count(DISTINCT d) AS sources
"""

MENTION_QUERY = """
UNWIND $mentions AS item
MATCH (c:Chunk {id: item.chunk_id})
MERGE (e:Entity {id: item.id})
SET e.name = item.name,
    e.type = item.type
MERGE (c)-[:MENTIONS]->(e)
"""

FACT_QUERY = """
UNWIND $facts AS item
MATCH (c:Chunk {id: item.chunk_id})
MATCH (subject:Entity {id: item.subject_id})
MATCH (object:Entity {id: item.object_id})
MERGE (f:Fact {id: item.id})
SET f.relationship = item.relationship,
    f.evidence = item.evidence,
    f.source_id = item.source_id,
    f.filename = item.filename,
    f.source_chunk_id = item.chunk_id,
    f.page_number = item.page_number,
    f.section = item.section
MERGE (c)-[:SUPPORTS]->(f)
MERGE (f)-[:SUBJECT]->(subject)
MERGE (f)-[:OBJECT]->(object)
"""

ENTITY_SEARCH_QUERY = """
CALL db.index.fulltext.queryNodes('entity_name_fulltext_index', $search_query)
YIELD node AS entity, score
WHERE EXISTS {
    MATCH (c:Chunk {workspace_id: $workspace_id, generation_id: $generation_id})
          -[:MENTIONS]->(entity)
}
RETURN entity.id AS id,
       entity.name AS name,
       entity.type AS type,
       score
ORDER BY score DESC, name
LIMIT $limit
"""

CHUNK_SEARCH_QUERY = """
CALL db.index.fulltext.queryNodes('chunk_text_fulltext_index', $search_query)
YIELD node AS c, score
MATCH (s:Source {workspace_id: $workspace_id, generation_id: $generation_id})
      -[:CONTAINS]->(c)
OPTIONAL MATCH (c)-[:MENTIONS]->(mentioned:Entity)
WITH s, c, score, collect(DISTINCT mentioned.name) AS entities
OPTIONAL MATCH (c)-[:SUPPORTS]->(f:Fact)-[:SUBJECT]->(subject:Entity)
OPTIONAL MATCH (f)-[:OBJECT]->(object:Entity)
RETURN c.id AS chunk_id,
       s.title AS source,
       s.source_id AS source_id,
       c.index AS chunk_index,
       c.page_number AS page_number,
       c.section AS section,
       c.text AS text,
       c.original_text AS original_text,
       entities,
       collect(DISTINCT CASE
           WHEN f IS NULL THEN null
           ELSE {
               source: subject.name,
               predicate: f.relationship,
               target: object.name
           }
       END) AS relationships,
       score
ORDER BY score DESC, source, chunk_index
LIMIT $limit
"""

ENTITY_CHUNK_QUERY = """
UNWIND $entity_ids AS entity_id
MATCH (matched:Entity {id: entity_id})<-[:MENTIONS]-(c:Chunk)
MATCH (s:Source {workspace_id: $workspace_id, generation_id: $generation_id})
      -[:CONTAINS]->(c)
OPTIONAL MATCH (c)-[:MENTIONS]->(mentioned:Entity)
WITH matched, s, c, collect(DISTINCT mentioned.name) AS entities
OPTIONAL MATCH (c)-[:SUPPORTS]->(f:Fact)-[:SUBJECT]->(subject:Entity)
OPTIONAL MATCH (f)-[:OBJECT]->(object:Entity)
RETURN matched.id AS matched_id,
       c.id AS chunk_id,
       s.title AS source,
       s.source_id AS source_id,
       c.index AS chunk_index,
       c.page_number AS page_number,
       c.section AS section,
       c.text AS text,
       c.original_text AS original_text,
       entities,
       collect(DISTINCT CASE
           WHEN f IS NULL THEN null
           ELSE {
               source: subject.name,
               predicate: f.relationship,
               target: object.name
           }
       END) AS relationships
LIMIT $limit
"""

GRAPH_FACT_SEARCH_QUERY = """
UNWIND $entity_ids AS entity_id
MATCH (matched:Entity {id: entity_id})
MATCH (f:Fact)-[:SUBJECT|OBJECT]->(matched)
MATCH (c:Chunk)-[:SUPPORTS]->(f)
MATCH (s:Source {workspace_id: $workspace_id, generation_id: $generation_id})
      -[:CONTAINS]->(c)
MATCH (f)-[:SUBJECT]->(subject:Entity)
MATCH (f)-[:OBJECT]->(object:Entity)
OPTIONAL MATCH (c)-[:MENTIONS]->(mentioned:Entity)
RETURN matched.id AS matched_id,
       c.id AS chunk_id,
       s.title AS source,
       s.source_id AS source_id,
       c.index AS chunk_index,
       c.page_number AS page_number,
       c.section AS section,
       c.text AS text,
       c.original_text AS original_text,
       collect(DISTINCT mentioned.name) AS entities,
       [{
           source: subject.name,
           predicate: f.relationship,
           target: object.name
       }] AS relationships
LIMIT $limit
"""

SCHEMA_STATEMENTS = (
    "CREATE CONSTRAINT document_id_unique IF NOT EXISTS "
    "FOR (d:Document) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT source_id_unique IF NOT EXISTS "
    "FOR (s:Source) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS "
    "FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS "
    "FOR (e:Entity) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT fact_id_unique IF NOT EXISTS "
    "FOR (f:Fact) REQUIRE f.id IS UNIQUE",
    "CREATE CONSTRAINT workspace_id_unique IF NOT EXISTS "
    "FOR (w:Workspace) REQUIRE w.id IS UNIQUE",
    "CREATE CONSTRAINT generation_id_unique IF NOT EXISTS "
    "FOR (g:Generation) REQUIRE g.id IS UNIQUE",
    "CREATE INDEX entity_name_index IF NOT EXISTS "
    "FOR (e:Entity) ON (e.name)",
    "CREATE INDEX fact_relationship_index IF NOT EXISTS "
    "FOR (f:Fact) ON (f.relationship)",
    "CREATE INDEX chunk_source_id_index IF NOT EXISTS "
    "FOR (c:Chunk) ON (c.source_id)",
    "CREATE INDEX source_workspace_generation_index IF NOT EXISTS "
    "FOR (s:Source) ON (s.workspace_id, s.generation_id)",
    "CREATE INDEX chunk_workspace_generation_index IF NOT EXISTS "
    "FOR (c:Chunk) ON (c.workspace_id, c.generation_id)",
    "CREATE FULLTEXT INDEX entity_name_fulltext_index IF NOT EXISTS "
    "FOR (e:Entity) ON EACH [e.name]",
    "CREATE FULLTEXT INDEX chunk_text_fulltext_index IF NOT EXISTS "
    "FOR (c:Chunk) ON EACH [c.text]",
)

AWAIT_INDEXES_QUERY = "CALL db.awaitIndexes(300)"
ACTIVE_GENERATION_QUERY = """
MATCH (w:Workspace {id: $workspace_id})
RETURN w.active_generation_id AS generation_id
"""


def _entity_id(name: str) -> str:
    canonical_name = normalize_entity_name(name)
    return hashlib.sha256(canonical_name.encode("utf-8")).hexdigest()


def _fact_id(chunk_id: str, subject_id: str, relationship: str, object_id: str) -> str:
    canonical = "\0".join(
        (chunk_id, subject_id, relationship.casefold().strip(), object_id)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_record_id(workspace_id: str, generation_id: str, source_id: str) -> str:
    canonical = "\0".join((workspace_id, generation_id, source_id))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fulltext_query(terms: list[str]) -> str:
    return " OR ".join(f'"{term}"' for term in terms)


def build_ingestion_payload(
    document: LoadedDocument,
    chunks: list[Chunk],
    extractions: list[ExtractionResult],
    workspace_id: str = "LOCAL_GRAPHRAG",
    generation_id: str = "default",
) -> dict[str, Any]:
    if len(chunks) != len(extractions):
        raise ValueError("Each chunk must have exactly one extraction result.")

    mentions: list[dict[str, str]] = []
    facts: list[dict[str, Any]] = []
    for chunk, extraction in zip(chunks, extractions):
        entities_by_name = {
            normalize_entity_name(entity.name): entity
            for entity in extraction.entities
        }
        entity_ids = {
            key: _entity_id(entity.name)
            for key, entity in entities_by_name.items()
        }
        for entity in extraction.entities:
            mentions.append(
                {
                    "chunk_id": chunk.id,
                    "id": _entity_id(entity.name),
                    "name": entity.name,
                    "type": entity.kind,
                }
            )
        for relation in extraction.relationships:
            source = entities_by_name.get(normalize_entity_name(relation.source))
            target = entities_by_name.get(normalize_entity_name(relation.target))
            if source is None or target is None:
                raise ValueError(
                    "Cannot create a fact whose relationship endpoint is not an entity."
                )
            subject_id = entity_ids[normalize_entity_name(source.name)]
            object_id = entity_ids[normalize_entity_name(target.name)]
            facts.append(
                {
                    "chunk_id": chunk.id,
                    "id": _fact_id(
                        chunk.id,
                        subject_id,
                        relation.predicate,
                        object_id,
                    ),
                    "subject_id": subject_id,
                    "object_id": object_id,
                    "relationship": relation.predicate,
                    "evidence": chunk.original_text,
                    "source_id": document.source_id,
                    "filename": document.title,
                    "page_number": chunk.page_number,
                    "section": chunk.section,
                }
            )

    return {
        "document_id": _source_record_id(
            workspace_id,
            generation_id,
            document.source_id,
        ),
        "source_id": document.source_id,
        "workspace_id": workspace_id,
        "generation_id": generation_id,
        "title": document.title,
        "text": document.text,
        "original_text": document.original_text,
        "media_type": document.media_type,
        "page_count": document.page_count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "chunks": [chunk.model_dump() for chunk in chunks],
        "mentions": mentions,
        "facts": facts,
    }


def search_terms(question: str, limit: int = 12) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_-]{1,}", question.casefold())
    terms = list(dict.fromkeys(word for word in words if word not in STOP_WORDS))
    if not terms:
        terms = list(dict.fromkeys(words))
    return terms[:limit]


class GraphService:
    def __init__(
        self,
        connection: Neo4jConnection,
        workspace_id: str = "LOCAL_GRAPHRAG",
    ) -> None:
        self.connection = connection
        self.workspace_id = workspace_id
        self._schema_ready = False
        self._schema_lock = Lock()

    def _session(self) -> Session:
        if hasattr(self.connection, "session"):
            return self.connection.session()
        return self.connection.driver.session()

    def verify_connectivity(self) -> str:
        return self.connection.verify_connectivity()

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with self._session() as session:
                for statement in SCHEMA_STATEMENTS:
                    session.run(statement).consume()
                session.run(AWAIT_INDEXES_QUERY).consume()
            self._schema_ready = True

    def ensure_workspace(self) -> str:
        candidate = uuid4().hex
        with self._session() as session:
            record = session.run(
                """
                MERGE (w:Workspace {id: $workspace_id})
                ON CREATE SET w.active_generation_id = $candidate
                WITH w
                MERGE (g:Generation {id: w.active_generation_id})
                ON CREATE SET g.workspace_id = w.id,
                              g.status = 'active',
                              g.created_at = $created_at
                MERGE (w)-[:HAS_GENERATION]->(g)
                RETURN g.id AS generation_id
                """,
                workspace_id=self.workspace_id,
                candidate=candidate,
                created_at=datetime.now(timezone.utc).isoformat(),
            ).single()
            return record["generation_id"]

    def get_active_generation_id(self) -> str:
        with self._session() as session:
            record = session.run(
                ACTIVE_GENERATION_QUERY,
                workspace_id=self.workspace_id,
            ).single()
        if record is None or not record["generation_id"]:
            return self.ensure_workspace()
        return record["generation_id"]

    def begin_generation(self) -> str:
        generation_id = uuid4().hex
        with self._session() as session:
            record = session.run(
                """
                MATCH (w:Workspace {id: $workspace_id})
                CREATE (g:Generation {
                    id: $generation_id,
                    workspace_id: $workspace_id,
                    status: 'staging',
                    created_at: $created_at
                })
                MERGE (w)-[:HAS_GENERATION]->(g)
                RETURN g.id AS generation_id
                """,
                workspace_id=self.workspace_id,
                generation_id=generation_id,
                created_at=datetime.now(timezone.utc).isoformat(),
            ).single()
        if record is None or record["generation_id"] != generation_id:
            raise ValueError("The workspace has not been initialized.")
        return generation_id

    def activate_generation(self, generation_id: str) -> None:
        with self._session() as session:
            record = session.run(
                """
                MATCH (w:Workspace {id: $workspace_id})
                MATCH (g:Generation {
                    id: $generation_id,
                    workspace_id: $workspace_id
                })
                OPTIONAL MATCH (old:Generation {id: w.active_generation_id})
                SET old.status = 'archived'
                WITH w, g
                SET g.status = 'active',
                    w.active_generation_id = g.id
                RETURN g.id AS generation_id
                """,
                workspace_id=self.workspace_id,
                generation_id=generation_id,
            ).single()
        if record is None:
            raise ValueError("The replacement generation does not exist.")

    @staticmethod
    def source_record_id(
        workspace_id: str,
        generation_id: str,
        source_id: str,
    ) -> str:
        return _source_record_id(workspace_id, generation_id, source_id)

    def ingest_document(
        self,
        document: LoadedDocument,
        chunks: list[Chunk],
        extractions: list[ExtractionResult],
        generation_id: str | None = None,
    ) -> None:
        if not chunks:
            raise ValueError("Cannot add a document without text chunks.")
        generation_id = generation_id or self.get_active_generation_id()
        payload = build_ingestion_payload(
            document,
            chunks,
            extractions,
            workspace_id=self.workspace_id,
            generation_id=generation_id,
        )
        with self._session() as session:
            session.execute_write(self._write_document, payload)

    @staticmethod
    def _write_document(tx, payload: dict[str, Any]) -> None:
        source = tx.run(
            DOCUMENT_QUERY,
            document_id=payload["document_id"],
            source_id=payload["source_id"],
            workspace_id=payload["workspace_id"],
            generation_id=payload["generation_id"],
            title=payload["title"],
            text=payload["text"],
            original_text=payload["original_text"],
            media_type=payload["media_type"],
            page_count=payload["page_count"],
            updated_at=payload["updated_at"],
            chunks=payload["chunks"],
        ).single()
        if source is None or source["sources"] != 1:
            raise ValueError("The target workspace generation does not exist.")
        if payload["mentions"]:
            tx.run(MENTION_QUERY, mentions=payload["mentions"]).consume()
        if payload["facts"]:
            tx.run(FACT_QUERY, facts=payload["facts"]).consume()

    def search(
        self,
        question: str,
        limit: int = 6,
        generation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        terms = search_terms(question)
        if not terms or limit <= 0:
            return []
        generation_id = generation_id or self.get_active_generation_id()
        scope = {
            "workspace_id": self.workspace_id,
            "generation_id": generation_id,
        }
        query = _fulltext_query(terms)
        results_by_chunk: dict[str, dict[str, Any]] = {}

        def add_result(record, score: float) -> None:
            chunk_id = record["chunk_id"]
            original_text = record["original_text"] or record["text"] or ""
            result = results_by_chunk.get(chunk_id)
            if result is None:
                result = {
                    "chunk_id": chunk_id,
                    "source": record["source"],
                    "source_id": record["source_id"],
                    "chunk_index": record["chunk_index"],
                    "page_number": record["page_number"],
                    "section": record["section"],
                    "text": original_text,
                    "original_text": original_text,
                    "cleaned_text": record["text"],
                    "entities": [],
                    "relationships": [],
                    "score": score,
                }
                results_by_chunk[chunk_id] = result
            else:
                result["score"] = max(result["score"], score)

            for name in record["entities"] or []:
                if name not in result["entities"]:
                    result["entities"].append(name)
            relationship_keys = {
                (item["source"], item["predicate"], item["target"])
                for item in result["relationships"]
            }
            for relationship in record["relationships"] or []:
                if relationship is None:
                    continue
                key = (
                    relationship["source"],
                    relationship["predicate"],
                    relationship["target"],
                )
                if key not in relationship_keys:
                    relationship_keys.add(key)
                    result["relationships"].append(relationship)

        with self._session() as session:
            entity_records = list(
                session.run(
                    ENTITY_SEARCH_QUERY,
                    search_query=query,
                    limit=limit,
                    **scope,
                )
            )
            entity_scores = {
                record["id"]: record["score"]
                for record in entity_records
            }
            for record in session.run(
                CHUNK_SEARCH_QUERY,
                search_query=query,
                limit=limit * 2,
                **scope,
            ):
                add_result(record, 1 + record["score"])

            entity_ids = list(entity_scores)
            if entity_ids:
                for record in session.run(
                    ENTITY_CHUNK_QUERY,
                    entity_ids=entity_ids,
                    limit=limit * 2,
                    **scope,
                ):
                    add_result(
                        record,
                        2 + entity_scores.get(record["matched_id"], 0),
                    )
                for record in session.run(
                    GRAPH_FACT_SEARCH_QUERY,
                    entity_ids=entity_ids,
                    limit=limit * 4,
                    **scope,
                ):
                    add_result(
                        record,
                        3 + entity_scores.get(record["matched_id"], 0),
                    )

        results = sorted(
            results_by_chunk.values(),
            key=lambda item: (-item["score"], item["source"], item["chunk_index"]),
        )
        return results[:limit]

    def get_overview(self, generation_id: str | None = None) -> dict[str, Any]:
        generation_id = generation_id or self.get_active_generation_id()
        parameters = {
            "workspace_id": self.workspace_id,
            "generation_id": generation_id,
        }
        with self._session() as session:
            counts = session.run(
                """
                MATCH (g:Generation {
                    id: $generation_id,
                    workspace_id: $workspace_id
                })
                OPTIONAL MATCH (g)-[:CONTAINS]->(s:Source)
                OPTIONAL MATCH (s)-[:CONTAINS]->(c:Chunk)
                OPTIONAL MATCH (c)-[:MENTIONS]->(e:Entity)
                OPTIONAL MATCH (c)-[:SUPPORTS]->(f:Fact)
                RETURN count(DISTINCT s) AS documents,
                       count(DISTINCT c) AS chunks,
                       count(DISTINCT e) AS entities,
                       count(DISTINCT f) AS relationships
                """,
                **parameters,
            ).single()
            entities = session.run(
                """
                MATCH (g:Generation {
                    id: $generation_id,
                    workspace_id: $workspace_id
                })-[:CONTAINS]->(:Source)-[:CONTAINS]->(:Chunk)
                  -[:MENTIONS]->(e:Entity)
                RETURN DISTINCT e.name AS name, e.type AS kind
                ORDER BY e.name
                LIMIT 30
                """,
                **parameters,
            )
            relations = session.run(
                """
                MATCH (g:Generation {
                    id: $generation_id,
                    workspace_id: $workspace_id
                })-[:CONTAINS]->(:Source)-[:CONTAINS]->(:Chunk)
                  -[:SUPPORTS]->(f:Fact)
                MATCH (f)-[:SUBJECT]->(source:Entity)
                MATCH (f)-[:OBJECT]->(target:Entity)
                RETURN DISTINCT source.name AS source,
                                f.relationship AS predicate,
                                target.name AS target
                ORDER BY source, target, predicate
                LIMIT 30
                """,
                **parameters,
            )
            return {
                "documents": counts["documents"],
                "chunks": counts["chunks"],
                "entities": counts["entities"],
                "relationships": counts["relationships"],
                "entity_samples": [dict(record) for record in entities],
                "relations": [dict(record) for record in relations],
            }

    def list_sources(
        self,
        generation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        generation_id = generation_id or self.get_active_generation_id()
        with self._session() as session:
            records = session.run(
                """
                MATCH (g:Generation {
                    id: $generation_id,
                    workspace_id: $workspace_id
                })-[:CONTAINS]->(s:Source)
                OPTIONAL MATCH (s)-[:CONTAINS]->(c:Chunk)
                OPTIONAL MATCH (c)-[:MENTIONS]->(e:Entity)
                OPTIONAL MATCH (c)-[:SUPPORTS]->(f:Fact)
                RETURN s.id AS id,
                       s.source_id AS source_id,
                       s.title AS filename,
                       s.media_type AS source_type,
                       s.processing_status AS status,
                       count(DISTINCT c) AS chunk_count,
                       count(DISTINCT e) AS entity_count,
                       count(DISTINCT f) AS relationship_count
                ORDER BY filename, id
                """,
                workspace_id=self.workspace_id,
                generation_id=generation_id,
            )
            return [dict(record) for record in records]

    def delete_source(self, source_record_id: str, generation_id: str) -> bool:
        with self._session() as session:
            deleted = session.run(
                """
                MATCH (s:Source {
                    id: $source_record_id,
                    workspace_id: $workspace_id,
                    generation_id: $generation_id
                })
                OPTIONAL MATCH (s)-[:CONTAINS]->(c:Chunk)
                OPTIONAL MATCH (c)-[:MENTIONS]->(mentioned:Entity)
                OPTIONAL MATCH (c)-[:SUPPORTS]->(f:Fact)
                OPTIONAL MATCH (f)-[:SUBJECT|OBJECT]->(endpoint:Entity)
                WITH s,
                     collect(DISTINCT c) AS chunks,
                     collect(DISTINCT f) AS facts,
                     collect(DISTINCT mentioned.id)
                       + collect(DISTINCT endpoint.id) AS entity_ids
                DETACH DELETE s
                FOREACH (chunk IN chunks | DETACH DELETE chunk)
                FOREACH (fact IN facts | DETACH DELETE fact)
                RETURN entity_ids, 1 AS deleted
                """,
                source_record_id=source_record_id,
                workspace_id=self.workspace_id,
                generation_id=generation_id,
            ).single()
            if deleted is None or not deleted["deleted"]:
                return False
            session.run(
                """
                UNWIND $entity_ids AS entity_id
                MATCH (e:Entity {id: entity_id})
                WHERE NOT EXISTS {
                    MATCH (:Chunk)-[:MENTIONS]->(e)
                }
                AND NOT EXISTS {
                    MATCH (:Fact)-[:SUBJECT|OBJECT]->(e)
                }
                DETACH DELETE e
                """,
                entity_ids=deleted["entity_ids"],
            ).consume()
            return True

    def discard_generation(self, generation_id: str) -> None:
        if generation_id == self.get_active_generation_id():
            raise ValueError("Cannot discard the active workspace generation.")
        for source in self.list_sources(generation_id):
            self.delete_source(source["id"], generation_id)
        with self._session() as session:
            session.run(
                """
                MATCH (g:Generation {
                    id: $generation_id,
                    workspace_id: $workspace_id
                })
                DETACH DELETE g
                """,
                generation_id=generation_id,
                workspace_id=self.workspace_id,
            ).consume()

    def get_graph_data(
        self,
        generation_id: str | None = None,
        node_limit: int = 100,
        edge_limit: int = 200,
    ) -> dict[str, list[dict[str, Any]]]:
        generation_id = generation_id or self.get_active_generation_id()
        parameters = {
            "workspace_id": self.workspace_id,
            "generation_id": generation_id,
        }
        with self._session() as session:
            nodes = session.run(
                """
                MATCH (g:Generation {
                    id: $generation_id,
                    workspace_id: $workspace_id
                })-[:CONTAINS]->(:Source)-[:CONTAINS]->(:Chunk)
                  -[:MENTIONS]->(e:Entity)
                RETURN DISTINCT e.id AS id,
                                e.name AS label,
                                e.type AS type
                ORDER BY label
                LIMIT $limit
                """,
                **parameters,
                limit=node_limit,
            )
            edges = session.run(
                """
                MATCH (g:Generation {
                    id: $generation_id,
                    workspace_id: $workspace_id
                })-[:CONTAINS]->(:Source)-[:CONTAINS]->(:Chunk)
                  -[:SUPPORTS]->(f:Fact)
                MATCH (f)-[:SUBJECT]->(subject:Entity)
                MATCH (f)-[:OBJECT]->(object:Entity)
                RETURN DISTINCT f.id AS id,
                                f.relationship AS label,
                                subject.id AS source,
                                object.id AS target
                ORDER BY label, id
                LIMIT $limit
                """,
                **parameters,
                limit=edge_limit,
            )
            return {
                "nodes": [dict(record) for record in nodes],
                "edges": [dict(record) for record in edges],
            }

    def get_entity_details(
        self,
        entity_id: str,
        generation_id: str | None = None,
    ) -> dict[str, Any] | None:
        generation_id = generation_id or self.get_active_generation_id()
        with self._session() as session:
            record = session.run(
                """
                MATCH (g:Generation {
                    id: $generation_id,
                    workspace_id: $workspace_id
                })-[:CONTAINS]->(s:Source)-[:CONTAINS]->(c:Chunk)
                  -[:MENTIONS]->(e:Entity {id: $entity_id})
                OPTIONAL MATCH (c)-[:SUPPORTS]->(f:Fact)
                OPTIONAL MATCH (f)-[:SUBJECT]->(subject:Entity)
                OPTIONAL MATCH (f)-[:OBJECT]->(object:Entity)
                RETURN e.id AS id,
                       e.name AS name,
                       e.type AS type,
                       collect(DISTINCT {
                           filename: s.title,
                           source_id: s.source_id,
                           chunk_index: c.index,
                           page_number: c.page_number,
                           section: c.section,
                           text: c.original_text
                       }) AS evidence,
                       collect(DISTINCT CASE
                           WHEN f IS NULL THEN null
                           ELSE {
                               source: subject.name,
                               predicate: f.relationship,
                               target: object.name
                           }
                       END) AS relationships
                """,
                entity_id=entity_id,
                workspace_id=self.workspace_id,
                generation_id=generation_id,
            ).single()
            if record is None:
                return None
            details = dict(record)
            details["relationships"] = [
                relation for relation in details["relationships"] if relation
            ]
            return details
