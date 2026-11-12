# SimpleGraphRAG

SimpleGraphRAG is a local document question-answering application. It extracts
entities and facts from uploaded documents, stores them in Neo4j, and uses a
local Ollama model to answer questions with source citations.

The application uses Streamlit for its interface, Neo4j for storage and graph
retrieval, and Ollama with `qwen3:4b` for extraction and answers. It does not
use a cloud API or an embedding model. All service addresses are restricted to
the local machine, and Streamlit binds to `127.0.0.1`.

## Features

- Upload PDF, HTML, HTM, TXT, and Markdown (`.md`, `.markdown`) files; each
  file can be up to 20 MB.
- Extract entities and explicit relationships from document chunks using the
  local Ollama model.
- Browse sources, processing details, entities, relationships, and evidence in
  the Streamlit app.
- Ask questions using retrieved source passages and graph facts, with
  citations to the source, chunk, and available page or section.
- Add documents to the active graph generation or build and activate a
  replacement generation. A replacement becomes active only if at least one
  document is processed successfully.
- Delete a source and its evidence from the active generation.

## Requirements

- Windows PowerShell (commands below use PowerShell syntax)
- Python 3.10 or newer
- Neo4j 5 with Bolt enabled
- Ollama running locally with the `qwen3:4b` model installed

The Python dependencies are listed in [requirements.txt](requirements.txt).

## Set up local services

### Neo4j Desktop

Create or start a local Neo4j DBMS in Neo4j Desktop. Make sure Bolt is enabled
on port `7687`; the default app connection is
`bolt://127.0.0.1:7687`. Note the DBMS username and password for the app setup
below. The default database name used by this project is `neo4j`.

The Neo4j Browser web page is usually `http://localhost:7474`. A Bolt URI such
as `bolt://localhost:7687` is a database connection address, not a webpage.

### Docker alternative

If using Docker Desktop, start it first and wait until the Docker engine is
ready. The following creates a Neo4j 5 container, publishes its HTTP and Bolt
ports only on localhost, and keeps database files in a named volume:

```powershell
$SecurePassword = Read-Host -Prompt "Choose a Neo4j password" -AsSecureString
$Neo4jPassword = (New-Object System.Net.NetworkCredential("", $SecurePassword)).Password

docker run --name simplegraphrag-neo4j --restart unless-stopped `
  --publish 127.0.0.1:7474:7474 `
  --publish 127.0.0.1:7687:7687 `
  --env "NEO4J_AUTH=neo4j/$Neo4jPassword" `
  --volume simplegraphrag_neo4j_data:/data `
  --detach neo4j:5

Remove-Variable SecurePassword, Neo4jPassword
```

Use the same password in the app's `.env` file. If the container already
exists, start it with `docker start simplegraphrag-neo4j` rather than running
the `docker run` command again. Changing `NEO4J_AUTH` after a database has
already been initialized does not change the stored Neo4j password.

Check that the container is running and Bolt is reachable:

```powershell
docker ps --filter "name=simplegraphrag-neo4j"
Test-NetConnection 127.0.0.1 -Port 7687
```

## Install and configure the app

From the project directory:

```powershell
cd D:\Graph_RAG\SimpleGraphRAG
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Set the Neo4j password in `.env` to the password for the local DBMS. The
template includes the default local addresses and database:

```dotenv
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=
NEO4J_DATABASE=neo4j
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:4b
OLLAMA_TIMEOUT_SECONDS=180
WORKSPACE=LOCAL_GRAPHRAG
CHUNK_SIZE=1200
CHUNK_OVERLAP=150
```

Leave `NEO4J_PASSWORD` blank only if Neo4j authentication is disabled. Do not
share the password or commit `.env`; `.env` is excluded by `.gitignore`.
Restart the app after editing `.env` so the running process reloads settings.

Install Ollama once. The startup script starts its local server if needed and
downloads the configured model if it is not installed yet. To inspect or pull
the model manually:

```powershell
ollama list
ollama pull qwen3:4b
```

## Start the whole project with one command

After completing the one-time setup above, run this command from PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\Graph_RAG\SimpleGraphRAG\start.ps1"
```

The script starts Docker Desktop and the existing `simplegraphrag-neo4j`
container when Neo4j is not already reachable, starts the Ollama server when
needed, downloads the configured Ollama model if it is missing, and launches
Streamlit. The first model download can take some time. The script uses the
project virtual environment when present, otherwise it uses `python` from
`PATH`; install the dependencies as described above before starting.

If you use Neo4j Desktop rather than the Docker container, start its DBMS first.
The script detects a reachable local Neo4j server and does not start Docker in
that case.

Open the local Streamlit address printed in the terminal, usually
`http://localhost:8501`. Streamlit is configured to listen only on
`127.0.0.1`. Stop the app with `Ctrl+C` in the terminal running Streamlit.

The sidebar shows Neo4j connectivity, Ollama connectivity, model availability,
and the active workspace. The page also displays source, chunk, entity, and
relationship counts for the active generation.

## Using the application

### 1. Upload documents

In **Document upload**, select one or more files and choose:

- **Add to current graph** to add sources to the active generation.
- **Replace active graph** to create a new generation and activate it only
  after at least one selected document succeeds. If all files fail, the
  existing generation remains active.

Each file is limited to 20 MB and 80 generated chunks. Supported extensions
are `.pdf`, `.html`, `.htm`, `.txt`, `.md`, and `.markdown`.

### 2. Review sources

The **Sources** tab lists files in the active generation with their processing
status and counts. Use a source's **Delete** control to remove it and its graph
evidence from that generation.

### 3. Explore the graph

The **Knowledge graph** tab displays up to 100 entities and 200 relationships
from the active generation. Select an entity to see its type, relationships,
and supporting source chunks.

### 4. Ask a question

In **Ask questions**, enter a question about the active generation. Answers
use retrieved passages and related graph facts. When available, citations
include the filename, chunk number, PDF page, and document section. If no
relevant passages are found, the app reports that rather than asking Ollama to
answer without evidence.

## Data processing and storage

### Document extraction and chunking

- PDFs are extracted page by page with PyMuPDF. Scanned/image-only PDFs need
  OCR before upload; OCR is not performed by this app.
- HTML is parsed with BeautifulSoup; scripts, styles, and noscript content are
  excluded. HTML and Markdown headings are retained as section metadata.
- Text is cleaned and split into chunks targeting 1,200 characters with 150
  characters of overlap. Chunk boundaries prefer sentence endings and retain
  source page/section metadata where available.
- The original extracted text and cleaned chunk text are stored separately.

### Entity and fact extraction

For each chunk, `qwen3:4b` returns schema-constrained JSON containing entities
and explicit relationships. Pydantic validates the response, entity types,
and relationship endpoints; duplicate entities and facts are normalized.
Malformed or empty responses are retried up to three times. Chunks producing
more than 30 entities or 50 relationships are rejected by the schema.

### Neo4j graph model

The main node labels are `Workspace`, `Generation`, `Source`, `Document`,
`Chunk`, `Entity`, and `Fact`. A source node also carries the `Document` label.
The graph uses these relationships:

```text
(Workspace)-[:HAS_GENERATION]->(Generation)
(Generation)-[:CONTAINS]->(Source)-[:CONTAINS]->(Chunk)
(Chunk)-[:MENTIONS]->(Entity)
(Chunk)-[:SUPPORTS]->(Fact)
(Fact)-[:SUBJECT]->(Entity)
(Fact)-[:OBJECT]->(Entity)
```

The semantic predicate (for example `FOUNDED`) is stored as the
`Fact.relationship` property. It is not created as a dynamic Neo4j relationship
type. Facts also retain evidence text, source ID, filename, and available page
and section provenance.

Each workspace has an active generation. Older generations are retained when
replaced, but app searches and views use only the active generation. IDs,
constraints, indexes, and full-text indexes are created automatically when the
app connects.

### Question answering

Neo4j full-text indexes search chunk text and entity names. Retrieval traverses
facts associated with matched entities and returns source passages and
provenance to the local Ollama model. The model answers only from that supplied
evidence; it does not generate Cypher. No embedding model is used.

## View the graph in Neo4j Desktop

In Neo4j Desktop, add a remote connection to the local DBMS using:

- **Connection URI:** `bolt://localhost:7687`
- **Username:** `neo4j` (or the value of `NEO4J_USERNAME`)
- **Password:** the value of `NEO4J_PASSWORD` in `.env`
- **Database:** `neo4j` (or the existing database named by
  `NEO4J_DATABASE`)

Open Neo4j Browser from that connection, or visit `http://localhost:7474` for
the Docker setup. Sign in with the same Neo4j credentials. Run this query to
visualize the active workspace's entity and fact nodes:

```cypher
MATCH (g:Generation {workspace_id: 'LOCAL_GRAPHRAG', status: 'active'})
      -[:CONTAINS]->(:Source)-[:CONTAINS]->(:Chunk)-[:SUPPORTS]->(f:Fact)
MATCH (f)-[:SUBJECT]->(subject:Entity)
MATCH (f)-[:OBJECT]->(object:Entity)
RETURN subject, f, object
LIMIT 100
```

The predicate is stored on the `Fact` node. To view subject-predicate-object
rows in the table result:

```cypher
MATCH (g:Generation {workspace_id: 'LOCAL_GRAPHRAG', status: 'active'})
      -[:CONTAINS]->(:Source)-[:CONTAINS]->(:Chunk)-[:SUPPORTS]->(f:Fact)
MATCH (f)-[:SUBJECT]->(subject:Entity)
MATCH (f)-[:OBJECT]->(object:Entity)
RETURN subject.name AS subject,
       f.relationship AS relationship,
       object.name AS object
ORDER BY subject, relationship, object
LIMIT 100
```

If `WORKSPACE` in `.env` is different from `LOCAL_GRAPHRAG`, use its value in
the queries.

## Configuration reference

| Variable | Default | Description |
| --- | --- | --- |
| `NEO4J_URI` | `bolt://127.0.0.1:7687` | Local Neo4j Bolt URI. |
| `NEO4J_USERNAME` | `neo4j` | Neo4j username. `NEO4J_USER` is accepted as an alias. |
| `NEO4J_PASSWORD` | empty | Neo4j password; empty means no-auth mode. |
| `NEO4J_DATABASE` | `neo4j` | Existing Neo4j database to use. |
| `WORKSPACE` | `LOCAL_GRAPHRAG` | Isolates this app's data in Neo4j. |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Local Ollama HTTP endpoint. |
| `OLLAMA_MODEL` | `qwen3:4b` | Model used for extraction and answers. |
| `OLLAMA_TIMEOUT_SECONDS` | `180` | Ollama request timeout (1-900 seconds). |
| `CHUNK_SIZE` | `1200` | Target chunk size in characters (200-5000). |
| `CHUNK_OVERLAP` | `150` | Chunk overlap in characters; must be smaller than `CHUNK_SIZE`. |

Settings can also be supplied as process environment variables. Neo4j and
Ollama host validation accepts only local addresses.

## Project layout

```text
SimpleGraphRAG/
├── app.py                    # Streamlit interface and application workflow
├── config.py                 # Environment loading and settings validation
├── database/
│   └── neo4j_connection.py   # Neo4j driver and sessions
├── models/
│   └── schemas.py            # Pydantic document, entity, fact, and answer models
├── services/
│   ├── document_loader.py    # PDF, HTML, text, and Markdown extraction
│   ├── entity_extractor.py   # Ollama extraction and response validation
│   ├── graph_service.py      # Neo4j schema, ingestion, retrieval, and graph data
│   ├── ollama_service.py     # Local Ollama HTTP client
│   ├── rag_service.py        # Evidence-based question answering
│   └── text_chunker.py       # Sentence-aware chunking with provenance
├── start.ps1                 # Start local services and launch the app
├── tests/                    # Unit tests; external services are mocked
├── .streamlit/
│   └── config.toml           # Local bind address and upload-size limit
├── .env.example              # Local configuration template
└── requirements.txt          # Python dependencies
```

## Tests

Run the unit tests from the project directory. They do not require a running
Neo4j or Ollama service:

```powershell
python -m unittest discover -s tests -v
```

## Troubleshooting

### Neo4j connection refused

Connection refusal means no Neo4j Bolt server is listening at the configured
host and port; it is not an authentication error. Start the Neo4j DBMS in
Neo4j Desktop or start Docker Desktop and the container:

```powershell
docker start simplegraphrag-neo4j
Test-NetConnection 127.0.0.1 -Port 7687
```

`TcpTestSucceeded` should be `True`. If not, confirm Bolt is enabled and its
port matches `NEO4J_URI`. The Docker command above binds Bolt to localhost.

### Authentication failed

Confirm `NEO4J_USERNAME` and `NEO4J_PASSWORD` match the Neo4j DBMS account.
Changing `.env` alone does not change a password already stored in Neo4j.
Restart Streamlit after editing `.env`.

### Database not found

`NEO4J_DATABASE` must name a database that exists in the connected DBMS. The
default for this project is `neo4j`. A database name such as `SimpleGraphRAG`
will fail unless that database has been created and is available.

### Neo4j Browser cannot open the Bolt URI

`bolt://localhost:7687` is for Neo4j Desktop's connection settings, not a web
address. Open Neo4j Browser at `http://localhost:7474`.

### Ollama unavailable or model missing

Start the local Ollama service and check the installed models:

```powershell
ollama list
ollama pull qwen3:4b
```

The configured Ollama address must be local and the model name must match
`OLLAMA_MODEL`.

### PDF has no extractable text

Image-only or scanned PDFs are rejected because OCR is not included. Run OCR
with a separate tool and upload the resulting searchable PDF.
#   G r a p h _ R A G  
 