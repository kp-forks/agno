import json
import uuid
from hashlib import md5
from os import getenv
from typing import Any, Dict, List, Optional

try:
    from warnings import filterwarnings

    import weaviate
    from weaviate import WeaviateAsyncClient
    from weaviate.classes.config import Configure, DataType, Property, Tokenization, VectorDistances
    from weaviate.classes.init import Auth
    from weaviate.classes.query import Filter

    filterwarnings("ignore", category=ResourceWarning)
except ImportError:
    raise ImportError("Weaviate is not installed. Install using 'pip install weaviate-client'.")

from agno.document import Document
from agno.embedder import Embedder
from agno.reranker.base import Reranker
from agno.utils.log import log_debug, log_info, logger
from agno.vectordb.base import VectorDb
from agno.vectordb.search import SearchType
from agno.vectordb.weaviate.index import Distance, VectorIndex


class Weaviate(VectorDb):
    """
    Weaviate class for managing vector operations with Weaviate vector database (v4 client).
    """

    def __init__(
        self,
        # Connection/Client params
        wcd_url: Optional[str] = None,
        wcd_api_key: Optional[str] = None,
        client: Optional[weaviate.WeaviateClient] = None,
        local: bool = False,
        # Collection params
        collection: str = "default",
        vector_index: VectorIndex = VectorIndex.HNSW,
        distance: Distance = Distance.COSINE,
        # Search/Embedding params
        embedder: Optional[Embedder] = None,
        search_type: SearchType = SearchType.vector,
        reranker: Optional[Reranker] = None,
        hybrid_search_alpha: float = 0.5,
    ):
        # Connection setup
        self.wcd_url = wcd_url or getenv("WCD_URL")
        self.wcd_api_key = wcd_api_key or getenv("WCD_API_KEY")
        self.local = local
        self.client = client
        self.async_client = None

        # Collection setup
        self.collection = collection
        self.vector_index = vector_index
        self.distance = distance

        # Embedder setup
        if embedder is None:
            from agno.embedder.openai import OpenAIEmbedder

            embedder = OpenAIEmbedder()
            log_info("Embedder not provided, using OpenAIEmbedder as default.")
        self.embedder: Embedder = embedder

        # Search setup
        self.search_type: SearchType = search_type
        self.reranker: Optional[Reranker] = reranker
        self.hybrid_search_alpha = hybrid_search_alpha

    def get_client(self) -> weaviate.WeaviateClient:
        """Initialize and return a Weaviate client instance.

        Attempts to create a client using WCD (Weaviate Cloud Deployment) credentials if provided,
        otherwise falls back to local connection. Maintains a singleton pattern by reusing
        an existing client if already initialized.

        Returns:
            weaviate.WeaviateClient: An initialized Weaviate client instance.
        """
        if self.client is None:
            if self.wcd_url and self.wcd_api_key and not self.local:
                log_info("Initializing Weaviate Cloud client")
                self.client = weaviate.connect_to_weaviate_cloud(
                    cluster_url=self.wcd_url, auth_credentials=Auth.api_key(self.wcd_api_key)
                )
            else:
                log_info("Initializing local Weaviate client")
                self.client = weaviate.connect_to_local()

        if not self.client.is_connected():  # type: ignore
            self.client.connect()  # type: ignore

        if not self.client.is_ready():  # type: ignore
            raise Exception("Weaviate client is not ready")

        return self.client

    async def get_async_client(self) -> WeaviateAsyncClient:
        """Get or create the async client."""
        if self.async_client is None:
            if self.wcd_url and self.wcd_api_key and not self.local:
                log_info("Initializing Weaviate Cloud async client")
                self.async_client = weaviate.use_async_with_weaviate_cloud(
                    cluster_url=self.wcd_url,
                    auth_credentials=Auth.api_key(self.wcd_api_key),  # type: ignore
                )
            else:
                log_info("Initializing local Weaviate async client")
                self.async_client = weaviate.use_async_with_local()  # type: ignore

        if not self.async_client.is_connected():  # type: ignore
            await self.async_client.connect()  # type: ignore

        if not await self.async_client.is_ready():  # type: ignore
            raise Exception("Weaviate async client is not ready")

        return self.async_client  # type: ignore

    def create(self) -> None:
        """Create the collection in Weaviate if it doesn't exist."""
        if not self.exists():
            log_debug(f"Creating collection '{self.collection}' in Weaviate.")
            self.get_client().collections.create(
                name=self.collection,
                properties=[
                    Property(name="name", data_type=DataType.TEXT),
                    Property(name="content", data_type=DataType.TEXT, tokenization=Tokenization.LOWERCASE),
                    Property(name="meta_data", data_type=DataType.TEXT),
                ],
                vectorizer_config=Configure.Vectorizer.none(),
                vector_index_config=self.get_vector_index_config(self.vector_index, self.distance),
            )
            log_debug(f"Collection '{self.collection}' created in Weaviate.")

    async def async_create(self) -> None:
        client = await self.get_async_client()
        try:
            await client.collections.create(
                name=self.collection,
                properties=[
                    Property(name="name", data_type=DataType.TEXT),
                    Property(name="content", data_type=DataType.TEXT, tokenization=Tokenization.LOWERCASE),
                    Property(name="meta_data", data_type=DataType.TEXT),
                ],
                vectorizer_config=Configure.Vectorizer.none(),
                vector_index_config=self.get_vector_index_config(self.vector_index, self.distance),
            )
            log_debug(f"Collection '{self.collection}' created in Weaviate asynchronously.")
        finally:
            await client.close()

    def doc_exists(self, document: Document) -> bool:
        """
        Validate if the document exists using consistent UUID generation.

        Args:
            document (Document): Document to validate

        Returns:
            bool: True if the document exists, False otherwise
        """
        if not document or not document.content:
            logger.warning("Invalid document: Missing content.")
            return False  # Early exit for invalid input

        cleaned_content = document.content.replace("\x00", "\ufffd")
        content_hash = md5(cleaned_content.encode()).hexdigest()
        doc_uuid = uuid.UUID(hex=content_hash[:32])

        collection = self.get_client().collections.get(self.collection)
        return collection.data.exists(doc_uuid)

    async def async_doc_exists(self, document: Document) -> bool:
        """
        Validate if the document exists using consistent UUID generation asynchronously.

        Args:
            document (Document): Document to validate

        Returns:
            bool: True if the document exists, False otherwise
        """
        if not document or not document.content:
            logger.warning("Invalid document: Missing content.")
            return False  # Early exit for invalid input

        cleaned_content = document.content.replace("\x00", "\ufffd")
        content_hash = md5(cleaned_content.encode()).hexdigest()
        doc_uuid = uuid.UUID(hex=content_hash[:32])

        client = await self.get_async_client()
        try:
            collection = client.collections.get(self.collection)
            return await collection.data.exists(doc_uuid)
        finally:
            await client.close()

    def name_exists(self, name: str) -> bool:
        """
        Validate if a document with the given name exists in Weaviate.

        Args:
            name (str): The name of the document to check.

        Returns:
            bool: True if a document with the given name exists, False otherwise.
        """
        collection = self.get_client().collections.get(self.collection)
        result = collection.query.fetch_objects(
            limit=1,
            filters=Filter.by_property("name").equal(name),
        )
        return len(result.objects) > 0

    async def async_name_exists(self, name: str) -> bool:
        """
        Asynchronously validate if a document with the given name exists in Weaviate.

        Args:
            name (str): The name of the document to check.

        Returns:
            bool: True if a document with the given name exists, False otherwise.
        """
        client = await self.get_async_client()
        try:
            collection = client.collections.get(self.collection)
            result = await collection.query.fetch_objects(
                limit=1,
                filters=Filter.by_property("name").equal(name),
            )
            return len(result.objects) > 0
        finally:
            await client.close()

    def insert(self, documents: List[Document], filters: Optional[Dict[str, Any]] = None) -> None:
        """
        Insert documents into Weaviate.

        Args:
            documents (List[Document]): List of documents to insert
            filters (Optional[Dict[str, Any]]): Filters to apply while inserting documents
        """
        log_debug(f"Inserting {len(documents)} documents into Weaviate.")
        collection = self.get_client().collections.get(self.collection)

        for document in documents:
            document.embed(embedder=self.embedder)
            if document.embedding is None:
                logger.error(f"Document embedding is None: {document.name}")
                continue

            cleaned_content = document.content.replace("\x00", "\ufffd")
            content_hash = md5(cleaned_content.encode()).hexdigest()
            doc_uuid = uuid.UUID(hex=content_hash[:32])

            # Merge filters with metadata
            meta_data = document.meta_data or {}
            if filters:
                meta_data.update(filters)

            # Serialize meta_data to JSON string
            meta_data_str = json.dumps(meta_data) if meta_data else None

            collection.data.insert(
                properties={
                    "name": document.name,
                    "content": cleaned_content,
                    "meta_data": meta_data_str,
                },
                vector=document.embedding,
                uuid=doc_uuid,
            )
            log_debug(f"Inserted document: {document.name} ({meta_data})")

    async def async_insert(self, documents: List[Document], filters: Optional[Dict[str, Any]] = None) -> None:
        """
        Insert documents into Weaviate asynchronously.

        Args:
            documents (List[Document]): List of documents to insert
            filters (Optional[Dict[str, Any]]): Filters to apply while inserting documents
        """
        log_debug(f"Inserting {len(documents)} documents into Weaviate asynchronously.")
        if not documents:
            return

        client = await self.get_async_client()
        try:
            collection = client.collections.get(self.collection)

            # Process documents first
            for document in documents:
                try:
                    # Embed document
                    document.embed(embedder=self.embedder)
                    if document.embedding is None:
                        logger.error(f"Document embedding is None: {document.name}")
                        continue

                    # Clean content and generate UUID
                    cleaned_content = document.content.replace("\x00", "\ufffd")
                    content_hash = md5(cleaned_content.encode()).hexdigest()
                    doc_uuid = uuid.UUID(hex=content_hash[:32])

                    # Serialize meta_data to JSON string
                    meta_data_str = json.dumps(document.meta_data) if document.meta_data else None

                    # Insert properties and vector separately
                    properties = {
                        "name": document.name,
                        "content": cleaned_content,
                        "meta_data": meta_data_str,
                    }

                    # Use the API correctly - properties, vector and uuid are separate parameters
                    await collection.data.insert(properties=properties, vector=document.embedding, uuid=doc_uuid)

                    log_debug(f"Inserted document asynchronously: {document.name}")

                except Exception as e:
                    logger.error(f"Error inserting document {document.name}: {str(e)}")
        finally:
            await client.close()

    def upsert(self, documents: List[Document], filters: Optional[Dict[str, Any]] = None) -> None:
        """
        Upsert documents into Weaviate.

        Args:
            documents (List[Document]): List of documents to upsert
            filters (Optional[Dict[str, Any]]): Filters to apply while upserting
        """
        log_debug(f"Upserting {len(documents)} documents into Weaviate.")
        self.insert(documents)

    async def async_upsert(self, documents: List[Document], filters: Optional[Dict[str, Any]] = None) -> None:
        """
        Upsert documents into Weaviate asynchronously.
        When documents with the same ID already exist, they will be replaced.
        Otherwise, new documents will be created.

        Args:
            documents (List[Document]): List of documents to upsert
            filters (Optional[Dict[str, Any]]): Filters to apply while upserting
        """
        if not documents:
            return

        log_debug(f"Upserting {len(documents)} documents into Weaviate asynchronously.")

        client = await self.get_async_client()
        try:
            collection = client.collections.get(self.collection)

            for document in documents:
                document.embed(embedder=self.embedder)
                if document.embedding is None:
                    logger.error(f"Document embedding is None: {document.name}")
                    continue

                cleaned_content = document.content.replace("\x00", "\ufffd")
                content_hash = md5(cleaned_content.encode()).hexdigest()
                doc_uuid = uuid.UUID(hex=content_hash[:32])

                # Serialize meta_data to JSON string
                meta_data_str = json.dumps(document.meta_data) if document.meta_data else None

                properties = {
                    "name": document.name,
                    "content": cleaned_content,
                    "meta_data": meta_data_str,
                }

                await collection.data.replace(uuid=doc_uuid, properties=properties, vector=document.embedding)

                log_debug(f"Upserted document asynchronously: {document.name}")
        finally:
            await client.close()

    def search(self, query: str, limit: int = 5, filters: Optional[Dict[str, Any]] = None) -> List[Document]:
        """
        Perform a search based on the configured search type.

        Args:
            query (str): The search query.
            limit (int): Maximum number of results to return.
            filters (Optional[Dict[str, Any]]): Filters to apply to the search.

        Returns:
            List[Document]: List of matching documents.
        """
        if self.search_type == SearchType.vector:
            return self.vector_search(query, limit, filters)
        elif self.search_type == SearchType.keyword:
            return self.keyword_search(query, limit, filters)
        elif self.search_type == SearchType.hybrid:
            return self.hybrid_search(query, limit, filters)
        else:
            logger.error(f"Invalid search type '{self.search_type}'.")
            return []

    async def async_search(
        self, query: str, limit: int = 5, filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """
        Perform a search based on the configured search type asynchronously.

        Args:
            query (str): The search query.
            limit (int): Maximum number of results to return.
            filters (Optional[Dict[str, Any]]): Filters to apply to the search.

        Returns:
            List[Document]: List of matching documents.
        """
        if self.search_type == SearchType.vector:
            return await self.async_vector_search(query, limit, filters)
        elif self.search_type == SearchType.keyword:
            return await self.async_keyword_search(query, limit, filters)
        elif self.search_type == SearchType.hybrid:
            return await self.async_hybrid_search(query, limit, filters)
        else:
            logger.error(f"Invalid search type '{self.search_type}'.")
            return []

    def vector_search(self, query: str, limit: int = 5, filters: Optional[Dict[str, Any]] = None) -> List[Document]:
        try:
            query_embedding = self.embedder.get_embedding(query)
            if query_embedding is None:
                logger.error(f"Error getting embedding for query: {query}")
                return []

            collection = self.get_client().collections.get(self.collection)
            filter_expr = self._build_filter_expression(filters)

            response = collection.query.near_vector(
                near_vector=query_embedding,
                limit=limit,
                return_properties=["name", "content", "meta_data"],
                include_vector=True,
                filters=filter_expr,
            )

            search_results: List[Document] = self.get_search_results(response)

            if self.reranker:
                search_results = self.reranker.rerank(query=query, documents=search_results)

            log_info(f"Found {len(search_results)} documents")

            return search_results

        except Exception as e:
            logger.error(f"Error searching for documents: {e}")
            return []

        finally:
            self.get_client().close()

    async def async_vector_search(
        self, query: str, limit: int = 5, filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """
        Perform a vector search in Weaviate asynchronously.

        Args:
            query (str): The search query.
            limit (int): Maximum number of results to return.

        Returns:
            List[Document]: List of matching documents.
        """
        query_embedding = self.embedder.get_embedding(query)
        if query_embedding is None:
            logger.error(f"Error getting embedding for query: {query}")
            return []

        search_results = []
        client = await self.get_async_client()
        try:
            collection = client.collections.get(self.collection)
            filter_expr = self._build_filter_expression(filters)

            response = await collection.query.near_vector(
                near_vector=query_embedding,
                limit=limit,
                return_properties=["name", "content", "meta_data"],
                include_vector=True,
                filters=filter_expr,
            )

            search_results = self.get_search_results(response)

            if self.reranker:
                search_results = self.reranker.rerank(query=query, documents=search_results)

            log_info(f"Found {len(search_results)} documents")

            await client.close()
            return search_results

        except Exception as e:
            logger.error(f"Error searching for documents: {e}")
            return []

    def keyword_search(self, query: str, limit: int = 5, filters: Optional[Dict[str, Any]] = None) -> List[Document]:
        try:
            collection = self.get_client().collections.get(self.collection)
            filter_expr = self._build_filter_expression(filters)

            response = collection.query.bm25(
                query=query,
                query_properties=["content"],
                limit=limit,
                return_properties=["name", "content", "meta_data"],
                include_vector=True,
                filters=filter_expr,
            )

            search_results: List[Document] = self.get_search_results(response)

            if self.reranker:
                search_results = self.reranker.rerank(query=query, documents=search_results)

            log_info(f"Found {len(search_results)} documents")

            return search_results

        except Exception as e:
            logger.error(f"Error searching for documents: {e}")
            return []

        finally:
            self.get_client().close()

    async def async_keyword_search(
        self, query: str, limit: int = 5, filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """
        Perform a keyword search in Weaviate asynchronously.

        Args:
            query (str): The search query.
            limit (int): Maximum number of results to return.

        Returns:
            List[Document]: List of matching documents.
        """
        search_results = []
        client = await self.get_async_client()
        try:
            collection = client.collections.get(self.collection)

            filter_expr = self._build_filter_expression(filters)
            response = await collection.query.bm25(
                query=query,
                query_properties=["content"],
                limit=limit,
                return_properties=["name", "content", "meta_data"],
                include_vector=True,
                filters=filter_expr,
            )

            search_results = self.get_search_results(response)

            if self.reranker:
                search_results = self.reranker.rerank(query=query, documents=search_results)

            log_info(f"Found {len(search_results)} documents")

            await client.close()
            return search_results

        except Exception as e:
            logger.error(f"Error searching for documents: {e}")
            return []

    def hybrid_search(self, query: str, limit: int = 5, filters: Optional[Dict[str, Any]] = None) -> List[Document]:
        try:
            query_embedding = self.embedder.get_embedding(query)
            if query_embedding is None:
                logger.error(f"Error getting embedding for query: {query}")
                return []

            collection = self.get_client().collections.get(self.collection)
            filter_expr = self._build_filter_expression(filters)

            response = collection.query.hybrid(
                query=query,
                vector=query_embedding,
                limit=limit,
                return_properties=["name", "content", "meta_data"],
                include_vector=True,
                query_properties=["content"],
                alpha=self.hybrid_search_alpha,
                filters=filter_expr,
            )

            search_results: List[Document] = self.get_search_results(response)

            if self.reranker:
                search_results = self.reranker.rerank(query=query, documents=search_results)

            log_info(f"Found {len(search_results)} documents")

            return search_results

        except Exception as e:
            logger.error(f"Error searching for documents: {e}")
            return []

        finally:
            self.get_client().close()

    async def async_hybrid_search(
        self, query: str, limit: int = 5, filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """
        Perform a hybrid search combining vector and keyword search in Weaviate asynchronously.

        Args:
            query (str): The keyword query.
            limit (int): Maximum number of results to return.

        Returns:
            List[Document]: List of matching documents.
        """
        query_embedding = self.embedder.get_embedding(query)
        if query_embedding is None:
            logger.error(f"Error getting embedding for query: {query}")
            return []

        search_results = []
        client = await self.get_async_client()
        try:
            collection = client.collections.get(self.collection)

            filter_expr = self._build_filter_expression(filters)
            response = await collection.query.hybrid(
                query=query,
                vector=query_embedding,
                limit=limit,
                return_properties=["name", "content", "meta_data"],
                include_vector=True,
                query_properties=["content"],
                alpha=self.hybrid_search_alpha,
                filters=filter_expr,
            )

            search_results = self.get_search_results(response)

            if self.reranker:
                search_results = self.reranker.rerank(query=query, documents=search_results)

            log_info(f"Found {len(search_results)} documents")

            await client.close()
            return search_results

        except Exception as e:
            logger.error(f"Error searching for documents: {e}")
            return []

    def exists(self) -> bool:
        """Check if the collection exists in Weaviate."""
        return self.get_client().collections.exists(self.collection)

    async def async_exists(self) -> bool:
        """Check if the collection exists in Weaviate asynchronously."""
        client = await self.get_async_client()
        try:
            return await client.collections.exists(self.collection)
        finally:
            await client.close()

    def drop(self) -> None:
        """Delete the Weaviate collection."""
        if self.exists():
            log_debug(f"Deleting collection '{self.collection}' from Weaviate.")
            self.get_client().collections.delete(self.collection)

    async def async_drop(self) -> None:
        """Delete the Weaviate collection asynchronously."""
        if await self.async_exists():
            log_debug(f"Deleting collection '{self.collection}' from Weaviate asynchronously.")
            client = await self.get_async_client()
            try:
                await client.collections.delete(self.collection)
            finally:
                await client.close()

    def optimize(self) -> None:
        """Optimize the vector database (e.g., rebuild indexes)."""
        pass

    def delete(self) -> bool:
        """Delete all records from the database."""
        self.drop()
        return True

    def get_vector_index_config(self, index_type: VectorIndex, distance_metric: Distance):
        """
        Returns the appropriate vector index configuration with the specified distance metric.

        Args:
            index_type (VectorIndex): Type of vector index (HNSW, FLAT, DYNAMIC).
            distance_metric (Distance): Distance metric (COSINE, DOT, etc).

        Returns:
            Configure.VectorIndex: The configured vector index instance.
        """
        # Get the Weaviate distance metric
        distance = getattr(VectorDistances, distance_metric.name)

        # Define vector index configurations based on enum value
        configs = {
            VectorIndex.HNSW: Configure.VectorIndex.hnsw(distance_metric=distance),
            VectorIndex.FLAT: Configure.VectorIndex.flat(distance_metric=distance),
            VectorIndex.DYNAMIC: Configure.VectorIndex.dynamic(distance_metric=distance),
        }

        return configs[index_type]

    def get_search_results(self, response: Any) -> List[Document]:
        """
        Create search results from the Weaviate response.

        Args:
            response (Any): The Weaviate response object.

        Returns:
            List[Document]: List of matching documents.
        """
        search_results: List[Document] = []
        for obj in response.objects:
            properties = obj.properties
            meta_data = json.loads(properties["meta_data"]) if properties.get("meta_data") else None
            embedding = obj.vector["default"] if isinstance(obj.vector, dict) else obj.vector

            search_results.append(
                Document(
                    name=properties["name"],
                    meta_data=meta_data if meta_data else {},
                    content=properties["content"],
                    embedder=self.embedder,
                    embedding=embedding,
                    usage=None,
                )
            )

        return search_results

    def upsert_available(self) -> bool:
        """Indicate that upsert functionality is available."""
        return True

    def _build_filter_expression(self, filters: Optional[Dict[str, Any]]) -> Optional[Filter]:
        """
        Build a filter expression for Weaviate queries.

        Args:
            filters (Optional[Dict[str, Any]]): Dictionary of filters to apply.

        Returns:
            Optional[Filter]: The constructed filter expression, or None if no filters provided.
        """
        if not filters:
            return None

        try:
            # Create a filter for each key-value pair
            filter_conditions = []
            for key, value in filters.items():
                # Create a pattern to match in the JSON string
                if isinstance(value, (list, tuple)):
                    # For list values
                    pattern = f'"{key}": {json.dumps(value)}'
                else:
                    # For single values
                    pattern = f'"{key}": "{value}"'

                # Add the filter condition using like operator
                filter_conditions.append(Filter.by_property("meta_data").like(f"*{pattern}*"))

            # If we have multiple conditions, combine them
            if len(filter_conditions) > 1:
                # Use the first condition as base and chain the rest
                filter_expr = filter_conditions[0]
                for condition in filter_conditions[1:]:
                    filter_expr = filter_expr & condition
                return filter_expr
            elif filter_conditions:
                return filter_conditions[0]

        except Exception as e:
            logger.error(f"Error building filter expression: {e}")
            return None

        return None
