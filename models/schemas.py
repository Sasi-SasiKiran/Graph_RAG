from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DocumentSegment(BaseModel):
    text: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)
    section: str | None = None


class LoadedDocument(BaseModel):
    id: str
    source_id: str
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    original_text: str = Field(min_length=1)
    media_type: str
    page_count: int = Field(ge=0)
    segments: list[DocumentSegment] = Field(default_factory=list)


class Chunk(BaseModel):
    id: str
    source_id: str
    index: int = Field(ge=0)
    text: str = Field(min_length=1)
    original_text: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)
    section: str | None = None


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=50, alias="type")

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Entity name cannot be blank.")
        return value

    @field_validator("kind")
    @classmethod
    def strip_kind(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Entity type cannot be blank.")
        return value


class Relation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)
    predicate: str = Field(
        min_length=1,
        max_length=80,
        alias="relationship",
    )

    @field_validator("source", "target", "predicate")
    @classmethod
    def strip_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Relationship fields cannot be blank.")
        return value


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[Entity] = Field(max_length=30)
    relationships: list[Relation] = Field(max_length=50)

    @model_validator(mode="after")
    def validate_relationship_endpoints(self) -> "ExtractionResult":
        def normalize(name: str) -> str:
            return " ".join(name.casefold().split())

        known_names = {normalize(entity.name) for entity in self.entities}
        invalid = [
            endpoint
            for relation in self.relationships
            for endpoint in (relation.source, relation.target)
            if normalize(endpoint) not in known_names
        ]
        if invalid:
            raise ValueError(
                "Every relationship endpoint must match an extracted entity."
            )
        return self


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Question cannot be blank.")
        return value


class SourceCitation(BaseModel):
    title: str
    source_id: str
    chunk_index: int = Field(ge=0)
    entities: list[str] = Field(default_factory=list)
    page_number: int | None = Field(default=None, ge=1)
    section: str | None = None


class AnswerResult(BaseModel):
    answer: str
    sources: list[SourceCitation] = Field(default_factory=list)
    relationships: list[dict[str, str]] = Field(default_factory=list)
