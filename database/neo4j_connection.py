from neo4j import Driver, GraphDatabase, Session


class Neo4jConnection:
    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        database: str = "neo4j",
    ) -> None:
        auth = (user, password) if password else None
        self._driver = GraphDatabase.driver(uri, auth=auth)
        self.database = database

    @property
    def driver(self) -> Driver:
        return self._driver

    def verify_connectivity(self) -> str:
        self._driver.verify_connectivity()
        return self._driver.get_server_info().agent

    def session(self) -> Session:
        return self._driver.session(database=self.database)

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "Neo4jConnection":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
