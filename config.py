import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, field_validator


class Settings(BaseModel):
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")
    neo4j_database: str = "neo4j"
    workspace: str = "LOCAL_GRAPHRAG"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b"
    ollama_timeout_seconds: int = Field(default=180, ge=1, le=900)
    chunk_size: int = Field(default=1200, ge=200, le=5000)
    chunk_overlap: int = Field(default=150, ge=0, le=1000)

    @field_validator("neo4j_database", "workspace")
    @classmethod
    def require_nonempty_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("This setting must not be empty.")
        return normalized

    @field_validator("neo4j_uri")
    @classmethod
    def require_local_neo4j(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"bolt", "neo4j", "bolt+s", "neo4j+s"}:
            raise ValueError("Use a Neo4j Bolt URI.")
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Neo4j must use a local host.")
        return value

    @field_validator("ollama_base_url")
    @classmethod
    def require_local_ollama(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "http" or parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("Ollama must use a local HTTP endpoint.")
        return value.rstrip("/")

    @field_validator("chunk_overlap")
    @classmethod
    def overlap_must_be_less_than_chunk_size(cls, value: int, info) -> int:
        chunk_size = info.data.get("chunk_size", 1200)
        if value >= chunk_size:
            raise ValueError("Chunk overlap must be smaller than chunk size.")
        return value


def load_settings() -> Settings:
    load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")
    return Settings(
        neo4j_uri=os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
        neo4j_user=os.getenv("NEO4J_USERNAME", os.getenv("NEO4J_USER", "neo4j")),
        neo4j_password=os.getenv("NEO4J_PASSWORD", ""),
        neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j"),
        workspace=os.getenv("WORKSPACE", "LOCAL_GRAPHRAG"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen3:4b"),
        ollama_timeout_seconds=os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"),
        chunk_size=os.getenv("CHUNK_SIZE", "1200"),
        chunk_overlap=os.getenv("CHUNK_OVERLAP", "150"),
    )
