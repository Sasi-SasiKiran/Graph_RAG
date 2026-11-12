import streamlit as st
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable
from pydantic import ValidationError

from config import Settings, load_settings
from database.neo4j_connection import Neo4jConnection
from models.schemas import SourceCitation
from services.document_loader import load_document
from services.entity_extractor import EntityExtractor
from services.graph_service import GraphService
from services.ollama_service import OllamaService, OllamaServiceError
from services.rag_service import RagService
from services.text_chunker import chunk_segments

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_CHUNKS_PER_DOCUMENT = 80

GRAPH_HTML = """
<div class="graph-root" role="group" aria-label="Interactive knowledge graph"></div>
"""

GRAPH_CSS = """
.graph-root {
  width: 100%;
  min-height: 420px;
  overflow: hidden;
  border: 1px solid var(--st-border-color);
  border-radius: 0.75rem;
  background: var(--st-background-color);
}
svg {
  display: block;
  width: 100%;
  height: 520px;
}
.edge {
  stroke: var(--st-text-color);
  stroke-opacity: 0.3;
  stroke-width: 1.5;
}
.edge-label {
  fill: var(--st-text-color);
  font: 11px sans-serif;
  text-anchor: middle;
  paint-order: stroke;
  stroke: var(--st-background-color);
  stroke-width: 4px;
  stroke-linejoin: round;
}
.node {
  cursor: pointer;
  outline: none;
}
.node circle {
  fill: var(--st-secondary-background-color);
  stroke: var(--st-primary-color);
  stroke-width: 2;
}
.node:hover circle,
.node.selected circle,
.node:focus circle {
  fill: var(--st-primary-color);
}
.node-label {
  fill: var(--st-text-color);
  font: 12px sans-serif;
  text-anchor: middle;
  pointer-events: none;
}
.empty-message {
  padding: 2rem;
  color: var(--st-secondary-text-color);
  text-align: center;
  font: 14px sans-serif;
}
"""

GRAPH_JS = """
export default function(component) {
  const { data, parentElement, setStateValue } = component;
  const root = parentElement.querySelector(".graph-root");
  if (!root) return;

  const nodes = Array.isArray(data?.nodes) ? data.nodes : [];
  const edges = Array.isArray(data?.edges) ? data.edges : [];
  if (nodes.length === 0) {
    const message = document.createElement("div");
    message.className = "empty-message";
    message.textContent = "No entities in the active workspace generation yet.";
    root.replaceChildren(message);
    return;
  }

  const namespace = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(namespace, "svg");
  svg.setAttribute("viewBox", "0 0 900 600");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Knowledge graph. Select an entity to inspect it.");
  const positions = new Map();
  const centerX = 450;
  const centerY = 285;
  const radius = Math.min(205, 55 + nodes.length * 2.4);

  nodes.forEach((node, index) => {
    const angle = (2 * Math.PI * index / nodes.length) - Math.PI / 2;
    positions.set(node.id, {
      x: centerX + radius * Math.cos(angle),
      y: centerY + radius * Math.sin(angle)
    });
  });

  edges.forEach(edge => {
    const source = positions.get(edge.source);
    const target = positions.get(edge.target);
    if (!source || !target) return;

    const line = document.createElementNS(namespace, "line");
    line.setAttribute("x1", source.x);
    line.setAttribute("y1", source.y);
    line.setAttribute("x2", target.x);
    line.setAttribute("y2", target.y);
    line.setAttribute("class", "edge");
    svg.appendChild(line);

    const label = document.createElementNS(namespace, "text");
    label.setAttribute("x", (source.x + target.x) / 2);
    label.setAttribute("y", (source.y + target.y) / 2 - 5);
    label.setAttribute("class", "edge-label");
    label.textContent = edge.label || "";
    svg.appendChild(label);
  });

  nodes.forEach(node => {
    const point = positions.get(node.id);
    const group = document.createElementNS(namespace, "g");
    group.setAttribute("class", "node");
    group.setAttribute("tabindex", "0");
    group.setAttribute("role", "button");
    group.setAttribute("aria-label", "Inspect " + (node.label || "entity"));
    if (node.id === data.selected) group.classList.add("selected");

    const circle = document.createElementNS(namespace, "circle");
    circle.setAttribute("cx", point.x);
    circle.setAttribute("cy", point.y);
    circle.setAttribute("r", "23");
    group.appendChild(circle);

    const label = document.createElementNS(namespace, "text");
    label.setAttribute("x", point.x);
    label.setAttribute("y", point.y + 42);
    label.setAttribute("class", "node-label");
    label.textContent = node.label || "";
    group.appendChild(label);

    const selectNode = () => setStateValue("selected", node.id);
    group.addEventListener("click", selectNode);
    group.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectNode();
      }
    });
    svg.appendChild(group);
  });

  root.replaceChildren(svg);
}
"""

GRAPH_VIEW = st.components.v2.component(
    "simple_graphrag_graph_view",
    html=GRAPH_HTML,
    css=GRAPH_CSS,
    js=GRAPH_JS,
)


@st.cache_resource
def get_graph_service(
    uri: str,
    user: str,
    password: str,
    database: str,
    workspace: str,
) -> GraphService:
    return GraphService(
        Neo4jConnection(uri, user, password, database),
        workspace_id=workspace,
    )


@st.cache_resource
def get_ollama_service(
    base_url: str,
    model: str,
    timeout_seconds: int,
) -> OllamaService:
    return OllamaService(base_url, model, timeout_seconds)


def _citation_rows(sources: list[SourceCitation]) -> list[dict[str, object]]:
    return [
        {
            "Document": source.title,
            "Source ID": source.source_id,
            "Chunk": source.chunk_index,
            "PDF page": source.page_number,
            "HTML/Markdown section": source.section,
            "Entities": ", ".join(source.entities),
        }
        for source in sources
    ]


def _neo4j_error_status(error: Neo4jError, settings: Settings | None) -> str:
    if isinstance(error, AuthError):
        if settings and not settings.neo4j_password.get_secret_value():
            return "Authentication required: set NEO4J_PASSWORD in your local .env."
        return "Authentication failed: check NEO4J_USERNAME and NEO4J_PASSWORD in .env."

    detail = str(error)
    if settings:
        for value in (
            settings.neo4j_password.get_secret_value(),
            settings.neo4j_uri,
            settings.neo4j_user,
        ):
            if value:
                detail = detail.replace(value, "[redacted]")
    return f"{type(error).__name__}: {detail}"


st.set_page_config(
    page_title="SimpleGraphRAG",
    page_icon=":material/hub:",
    layout="wide",
)
st.title("SimpleGraphRAG")
st.caption("Explore your local documents through Neo4j and Ollama.")

settings: Settings | None = None
graph: GraphService | None = None
ollama: OllamaService | None = None
configuration_error: str | None = None
try:
    settings = load_settings()
    graph = get_graph_service(
        settings.neo4j_uri,
        settings.neo4j_user,
        settings.neo4j_password.get_secret_value(),
        settings.neo4j_database,
        settings.workspace,
    )
    ollama = get_ollama_service(
        settings.ollama_base_url,
        settings.ollama_model,
        settings.ollama_timeout_seconds,
    )
except ValidationError as exc:
    configuration_error = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    )

graph_ready = False
ollama_reachable = False
model_available = False
generation_id: str | None = None
neo4j_status = "Not configured"
ollama_status = "Not configured"
model_status = "Not checked"

if graph is not None:
    try:
        server_info = graph.verify_connectivity()
        graph.ensure_schema()
        generation_id = graph.ensure_workspace()
        graph_ready = True
        neo4j_status = f"Connected ({server_info})"
    except Neo4jError as exc:
        neo4j_status = _neo4j_error_status(exc, settings)
    except ServiceUnavailable:
        neo4j_status = (
            "Cannot connect to Neo4j. Start the local database and verify "
            "its Bolt address and port in .env."
        )

if ollama is not None:
    try:
        model_available = ollama.is_model_available()
        ollama_reachable = True
        ollama_status = "Connected"
        model_status = "Available" if model_available else "Not installed"
    except OllamaServiceError:
        ollama_status = "Connection failed; check the local Ollama service."
        model_status = "Unavailable"

if configuration_error:
    st.error(f"Configuration needs attention: {configuration_error}")

with st.sidebar:
    st.subheader("System status")
    st.markdown(
        f"**Neo4j**  \n"
        f"{':green-badge[Connected]' if graph_ready else ':red-badge[Unavailable]'}"
    )
    st.caption(neo4j_status)
    st.markdown(
        f"**Ollama**  \n"
        f"{':green-badge[Connected]' if ollama_reachable else ':red-badge[Unavailable]'}"
    )
    st.caption(ollama_status)
    st.markdown(
        f"**{settings.ollama_model if settings else 'Ollama model'}**  \n"
        f"{':green-badge[Available]' if model_available else ':red-badge[Unavailable]'}"
    )
    st.caption(model_status)
    if settings:
        st.caption(f"Workspace: {settings.workspace}")

if graph_ready and graph is not None and generation_id is not None:
    try:
        overview = graph.get_overview(generation_id)
        metric_columns = st.columns(4)
        for column, label, value in zip(
            metric_columns,
            ("Sources", "Chunks", "Entities", "Relationships"),
            (
                overview["documents"],
                overview["chunks"],
                overview["entities"],
                overview["relationships"],
            ),
        ):
            column.metric(label, value)
    except Neo4jError:
        st.warning("Connected to Neo4j, but could not read workspace statistics.")

upload_tab, sources_tab, graph_tab, questions_tab = st.tabs(
    ["Document upload", "Sources", "Knowledge graph", "Ask questions"]
)

with upload_tab:
    st.subheader("Document upload")
    uploaded_files = st.file_uploader(
        "Choose PDF, HTML, TXT, or Markdown documents",
        type=["pdf", "html", "htm", "txt", "md", "markdown"],
        accept_multiple_files=True,
        help="Documents are processed locally. Each file is limited to 20 MB.",
    )
    upload_mode = st.segmented_control(
        "Upload mode",
        options=["Add to current graph", "Replace active graph"],
        default="Add to current graph",
        help=(
            "Replace builds a new generation first, then activates it only if at "
            "least one document is processed successfully."
        ),
    )
    ingest_clicked = st.button(
        "Upload and process",
        type="primary",
        disabled=not (graph_ready and model_available and uploaded_files),
        width="stretch",
    )
    if not graph_ready:
        st.caption("Connect Neo4j to enable document processing.")
    elif not model_available:
        st.caption("Start Ollama and make sure qwen3:4b is installed.")

    if "last_chunk_count" in st.session_state:
        st.metric("Chunks generated in the last upload batch", st.session_state.last_chunk_count)

    if ingest_clicked and uploaded_files and graph is not None and settings is not None:
        replace_mode = upload_mode == "Replace active graph"
        try:
            target_generation = (
                graph.begin_generation()
                if replace_mode
                else graph.get_active_generation_id()
            )
        except (Neo4jError, ValueError) as exc:
            st.error(f"Could not prepare the workspace for upload: {exc}")
            st.stop()
        successful_files = 0
        failed_files = 0
        st.session_state.last_chunk_count = 0
        extractor = EntityExtractor(ollama)

        with st.status("Processing selected documents...", expanded=True) as batch_status:
            for uploaded_file in uploaded_files:
                if uploaded_file.size > MAX_UPLOAD_BYTES:
                    failed_files += 1
                    st.error(f"{uploaded_file.name} exceeds the 20 MB upload limit.")
                    continue

                with st.status(
                    f"Processing {uploaded_file.name}...",
                    expanded=False,
                ) as file_status:
                    try:
                        document = load_document(
                            uploaded_file.name,
                            uploaded_file.getvalue(),
                        )
                        source_record_id = graph.source_record_id(
                            settings.workspace,
                            target_generation,
                            document.source_id,
                        )
                        chunks = chunk_segments(
                            document.segments,
                            source_id=document.source_id,
                            chunk_size=settings.chunk_size,
                            overlap=settings.chunk_overlap,
                            chunk_id_prefix=source_record_id,
                        )
                        st.session_state.last_chunk_count += len(chunks)
                        if not chunks:
                            raise ValueError("No readable text was found in the document.")
                        if len(chunks) > MAX_CHUNKS_PER_DOCUMENT:
                            raise ValueError(
                                f"Document creates {len(chunks)} chunks; the limit is "
                                f"{MAX_CHUNKS_PER_DOCUMENT}. Use a smaller document."
                            )

                        extractions = []
                        progress = st.progress(
                            0.0,
                            text="Extracting entities and relationships",
                        )
                        for index, chunk in enumerate(chunks, start=1):
                            extractions.append(extractor.extract(chunk.text))
                            progress.progress(index / len(chunks))

                        graph.ingest_document(
                            document,
                            chunks,
                            extractions,
                            generation_id=target_generation,
                        )
                        successful_files += 1
                        file_status.update(
                            label=f"Processed {document.title} ({len(chunks)} chunks)",
                            state="complete",
                        )
                    except (ValueError, OllamaServiceError, Neo4jError) as exc:
                        failed_files += 1
                        file_status.update(
                            label=f"Could not process {uploaded_file.name}",
                            state="error",
                        )
                        st.error(f"{uploaded_file.name}: {exc}")

            if replace_mode:
                try:
                    if successful_files:
                        graph.activate_generation(target_generation)
                    else:
                        graph.discard_generation(target_generation)
                except (Neo4jError, ValueError) as exc:
                    st.error(
                        "Could not switch the active workspace generation. "
                        f"The previous generation remains active: {exc}"
                    )
                    batch_status.update(
                        label="Could not activate replacement generation",
                        state="error",
                    )
                    st.stop()

            if successful_files:
                message = (
                    f"{'Replaced' if replace_mode else 'Added'} "
                    f"{successful_files} document(s) to the workspace."
                )
                if failed_files:
                    message += f" {failed_files} file(s) could not be processed."
                st.session_state.upload_notice = (message, "success" if not failed_files else "warning")
                file_status_state = "complete"
            else:
                st.session_state.upload_notice = (
                    "No documents were processed; the existing graph was left unchanged.",
                    "error",
                )
                file_status_state = "error"
            batch_status.update(
                label="Upload batch finished",
                state=file_status_state,
            )
        st.rerun()

if "upload_notice" in st.session_state:
    notice, level = st.session_state.pop("upload_notice")
    getattr(st, level)(notice)

with sources_tab:
    st.subheader("Sources")
    if graph_ready and graph is not None and generation_id is not None:
        try:
            source_rows = graph.list_sources(generation_id)
        except Neo4jError:
            source_rows = []
            st.error("Could not load sources from the active workspace generation.")

        if not source_rows:
            st.caption("No documents have been added to this workspace generation.")
        for source in source_rows:
            with st.container(border=True):
                name_column, type_column, status_column, counts_column, delete_column = st.columns(
                    [3, 1.2, 1, 2, 0.8]
                )
                name_column.markdown(f"**{source['filename']}**")
                name_column.caption(f"Source ID: {source['source_id']}")
                type_column.caption(source["source_type"] or "Unknown")
                status_column.badge(source["status"] or "Unknown", color="green")
                counts_column.caption(
                    f"{source['chunk_count']} chunks · "
                    f"{source['entity_count']} entities · "
                    f"{source['relationship_count']} relationships"
                )
                with delete_column.popover("Delete"):
                    st.warning(f"Remove {source['filename']} and its graph evidence?")
                    if st.button(
                        "Confirm delete",
                        key=f"delete_{generation_id}_{source['id']}",
                        type="primary",
                    ):
                        try:
                            if graph.delete_source(source["id"], generation_id):
                                st.session_state.upload_notice = (
                                    f"Deleted {source['filename']} from the active generation.",
                                    "success",
                                )
                            st.rerun()
                        except Neo4jError:
                            st.error("Could not delete the source from Neo4j.")
    else:
        st.info("Connect Neo4j to view workspace sources.")

with graph_tab:
    st.subheader("Knowledge graph")
    if graph_ready and graph is not None and generation_id is not None:
        try:
            graph_data = graph.get_graph_data(generation_id)
            if graph_data["nodes"]:
                graph_key = f"knowledge_graph_{generation_id}"
                graph_state = st.session_state.get(graph_key, {})
                graph_result = GRAPH_VIEW(
                    data={
                        **graph_data,
                        "selected": graph_state.get("selected"),
                    },
                    key=graph_key,
                    on_selected_change=lambda: None,
                )
                selected_entity = graph_result.selected or graph_state.get("selected")
                if selected_entity:
                    details = graph.get_entity_details(selected_entity, generation_id)
                    if details:
                        st.markdown(f"### {details['name']}")
                        st.caption(f"Type: {details['type']}")
                        if details["relationships"]:
                            st.markdown("**Relationships**")
                            st.dataframe(
                                details["relationships"],
                                hide_index=True,
                                use_container_width=True,
                            )
                        if details["evidence"]:
                            st.markdown("**Supporting source chunks**")
                            for evidence in details["evidence"]:
                                location = [evidence["filename"]]
                                if evidence["page_number"] is not None:
                                    location.append(f"page {evidence['page_number']}")
                                if evidence["section"]:
                                    location.append(evidence["section"])
                                st.markdown(
                                    f"**{', '.join(location)} · chunk "
                                    f"{evidence['chunk_index']}**"
                                )
                                st.write(evidence["text"])
            else:
                st.caption("The active workspace generation has no extracted entities yet.")
        except Neo4jError:
            st.error("Could not load graph data from Neo4j.")
    else:
        st.info("Connect Neo4j to view the active workspace graph.")

with questions_tab:
    st.subheader("Ask questions")
    with st.form("question_form", clear_on_submit=True):
        question = st.text_input(
            "Question",
            placeholder="Ask about people, places, events, or relationships in your documents",
        )
        ask_clicked = st.form_submit_button(
            "Ask",
            type="primary",
            disabled=not (graph_ready and model_available),
        )

    if ask_clicked and question.strip() and graph is not None and ollama is not None:
        with st.spinner("Searching the graph and composing an answer..."):
            try:
                result = RagService(graph, ollama).ask(question)
                st.markdown(result.answer)
                citations = _citation_rows(result.sources)
                if citations:
                    st.markdown("**Supporting sources**")
                    st.dataframe(
                        citations,
                        hide_index=True,
                        use_container_width=True,
                    )
                elif not result.sources:
                    st.caption("No matching source chunks were found in the active generation.")
            except (ValueError, OllamaServiceError, Neo4jError) as exc:
                st.error(f"Could not answer the question: {exc}")
