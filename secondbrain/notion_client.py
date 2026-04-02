"""
Notion API Client for SecondBrain

Provides a clean wrapper around the Notion API with:
- Automatic retry logic
- Consistent error handling
- Page content extraction
- Database queries
"""

import requests
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone

from secondbrain.config import settings
from secondbrain.logger import get_logger
from secondbrain.retry import retry_with_backoff

logger = get_logger(__name__)

# Base URLs
NOTION_API_BASE = "https://api.notion.com/v1"


class NotionClient:
    """
    Wrapper for Notion API operations.

    Usage:
        from secondbrain.notion_client import NotionClient

        client = NotionClient()
        pages = client.fetch_pages_to_sync("database-id")
        content = client.extract_page_content(page)
        client.update_status(page_id, "Processed")
    """

    def __init__(self, token: Optional[str] = None):
        """
        Initialize the Notion client.

        Args:
            token: Notion API token (defaults to settings.notion_token)
        """
        self.token = token or settings.notion_token
        if not self.token:
            raise ValueError("Notion token is required")

    @property
    def headers(self) -> Dict[str, str]:
        """Get headers for API requests."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": settings.notion_version,
            "Content-Type": "application/json"
        }

    @retry_with_backoff(
        max_retries=3,
        base_delay=1.0,
        retryable_exceptions=(requests.exceptions.RequestException, ConnectionError)
    )
    def _request(
        self,
        method: str,
        endpoint: str,
        json_data: Optional[Dict] = None,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Make an API request with retry logic.

        Args:
            method: HTTP method (GET, POST, PATCH)
            endpoint: API endpoint (e.g., "/databases/{id}/query")
            json_data: Request body
            timeout: Request timeout in seconds

        Returns:
            Response JSON as dict
        """
        url = f"{NOTION_API_BASE}{endpoint}"
        timeout = timeout or settings.notion_timeout

        response = requests.request(
            method=method,
            url=url,
            headers=self.headers,
            json=json_data,
            timeout=timeout
        )

        # Handle rate limiting
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 60))
            logger.warning(
                f"Rate limited by Notion API, retry after {retry_after}s",
                extra={"retry_after": retry_after}
            )
            raise requests.exceptions.RequestException(f"Rate limited: {retry_after}s")

        response.raise_for_status()
        return response.json()

    def query_database(
        self,
        database_id: str,
        filter_obj: Optional[Dict] = None,
        sorts: Optional[List[Dict]] = None,
        page_size: int = 100,
        raw_response: bool = False
    ):
        """
        Query a Notion database.

        Args:
            database_id: The database UUID
            filter_obj: Optional filter object (or full payload dict)
            sorts: Optional sort specifications
            page_size: Number of results per page
            raw_response: If True, return full API response (for pagination)

        Returns:
            List of page objects, or full response dict if raw_response=True
        """
        if not database_id:
            logger.error("Database ID is required")
            return {} if raw_response else []

        # Allow passing full payload directly via filter_obj
        if filter_obj and ("page_size" in filter_obj or "start_cursor" in filter_obj):
            payload = filter_obj
        else:
            payload: Dict[str, Any] = {"page_size": min(page_size, 100)}
            if filter_obj:
                payload["filter"] = filter_obj
            if sorts:
                payload["sorts"] = sorts

        try:
            result = self._request("POST", f"/databases/{database_id}/query", payload)

            if raw_response:
                return result

            pages = result.get("results", [])
            logger.info(
                f"Queried database",
                extra={"database_id": database_id[:8], "count": len(pages)}
            )
            return pages
        except Exception as e:
            logger.error(
                f"Failed to query database",
                extra={"database_id": database_id[:8], "error": str(e)}
            )
            return {} if raw_response else []

    def fetch_pages_to_sync(self, database_id: str) -> List[Dict]:
        """
        Fetch pages with 'New', 'Update Graph', or empty Processing Status.

        Args:
            database_id: The database UUID

        Returns:
            List of page objects needing sync
        """
        filter_obj = {
            "or": [
                {"property": "Processing Status", "select": {"equals": "New"}},
                {"property": "Processing Status", "select": {"equals": "Update Graph"}},
                {"property": "Processing Status", "select": {"is_empty": True}}
            ]
        }
        return self.query_database(database_id, filter_obj)

    def fetch_all_active_pages(self, database_id: str) -> List[Dict]:
        """
        Fetch all pages with status "Active" and their last_edited_time.

        Used by the reconciler to detect drift between Notion and Neo4j.
        Paginates through all results.

        Args:
            database_id: The database UUID

        Returns:
            List of dicts with 'page_id' and 'last_edited_time' (ISO string)
        """
        filter_obj = {
            "property": "Processing Status",
            "select": {"equals": "Active"}
        }

        active_pages = []
        has_more = True
        start_cursor = None

        while has_more:
            payload = {
                "page_size": 100,
                "filter": filter_obj,
            }
            if start_cursor:
                payload["start_cursor"] = start_cursor

            result = self.query_database(
                database_id, payload, raw_response=True
            )

            if not result or not isinstance(result, dict):
                break

            for page in result.get("results", []):
                active_pages.append({
                    "page_id": page["id"],
                    "last_edited_time": page.get("last_edited_time", ""),
                })

            has_more = result.get("has_more", False)
            start_cursor = result.get("next_cursor")

        logger.info(
            f"Fetched active pages for reconciliation",
            extra={"database_id": database_id[:8], "count": len(active_pages)}
        )
        return active_pages

    def fetch_pages_by_status(
        self,
        database_id: str,
        status_property: str,
        status_value: str
    ) -> List[Dict]:
        """
        Fetch pages with a specific status.

        Args:
            database_id: The database UUID
            status_property: Name of the status property
            status_value: Value to filter by

        Returns:
            List of matching page objects
        """
        filter_obj = {
            "property": status_property,
            "select": {"equals": status_value}
        }
        return self.query_database(database_id, filter_obj)

    def fetch_items_since(
        self,
        database_id: str,
        days: int = 7
    ) -> List[Dict]:
        """
        Fetch items created in the last N days.

        Args:
            database_id: The database UUID
            days: Number of days to look back

        Returns:
            List of page objects
        """
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        filter_obj = {
            "timestamp": "created_time",
            "created_time": {"on_or_after": since}
        }
        return self.query_database(database_id, filter_obj)

    def get_page_blocks(self, page_id: str) -> List[Dict]:
        """
        Get all blocks (content) from a page.

        Args:
            page_id: The page UUID

        Returns:
            List of block objects
        """
        try:
            result = self._request("GET", f"/blocks/{page_id}/children")
            return result.get("results", [])
        except Exception as e:
            logger.warning(
                f"Failed to get page blocks",
                extra={"page_id": page_id[:8], "error": str(e)}
            )
            return []

    def extract_page_content(self, page: Dict) -> Dict[str, Any]:
        """
        Extract text content from a page.

        Args:
            page: Page object from query

        Returns:
            Dict with page_id, text, title, current_status, notion_url, last_edited_time
        """
        page_id = page["id"]
        props = page.get("properties", {})

        # Extract title from various possible property names
        title = "Untitled"
        for prop_name in ["Title", "Name", "Task"]:
            title_prop = props.get(prop_name)
            if title_prop and title_prop.get("title"):
                title = "".join([t.get("plain_text", "") for t in title_prop["title"]])
                break

        # Get block content
        blocks = self.get_page_blocks(page_id)
        block_texts = []

        for block in blocks:
            block_type = block.get("type")
            if block_type in [
                "paragraph", "heading_1", "heading_2", "heading_3",
                "bulleted_list_item", "numbered_list_item"
            ]:
                rich_text = block.get(block_type, {}).get("rich_text", [])
                text = "".join([x.get("plain_text", "") for x in rich_text])
                if text.strip():
                    block_texts.append(text)

        full_text = f"{title}\n\n" + "\n".join(block_texts)

        # Get processing status
        status_prop = props.get("Processing Status")
        current_status = ""
        if status_prop and status_prop.get("select"):
            current_status = status_prop["select"].get("name", "")

        return {
            "page_id": page_id,
            "text": full_text.strip(),
            "title": title,
            "current_status": current_status,
            "notion_url": page.get("url", ""),
            "last_edited_time": page.get("last_edited_time", ""),
        }

    def update_page_property(
        self,
        page_id: str,
        property_name: str,
        property_value: Dict
    ) -> bool:
        """
        Update a single property on a page.

        Args:
            page_id: The page UUID
            property_name: Name of the property
            property_value: Property value object

        Returns:
            True if successful
        """
        try:
            self._request(
                "PATCH",
                f"/pages/{page_id}",
                {"properties": {property_name: property_value}}
            )
            return True
        except Exception as e:
            logger.warning(
                f"Failed to update page property",
                extra={"page_id": page_id[:8], "property": property_name, "error": str(e)}
            )
            return False

    def update_status(self, page_id: str, status: str) -> bool:
        """
        Update the Processing Status of a page.

        Args:
            page_id: The page UUID
            status: New status value

        Returns:
            True if successful
        """
        return self.update_page_property(
            page_id,
            "Processing Status",
            {"select": {"name": status}}
        )

    def create_page(
        self,
        database_id: str,
        properties: Dict[str, Any],
        children: Optional[List[Dict]] = None
    ) -> Optional[str]:
        """
        Create a new page in a database.

        Args:
            database_id: Target database UUID
            properties: Page properties
            children: Optional list of block objects for page body

        Returns:
            New page ID or None if failed
        """
        try:
            body = {
                "parent": {"database_id": database_id},
                "properties": properties
            }
            if children:
                body["children"] = children

            result = self._request("POST", "/pages", body)
            page_id = result.get("id")
            logger.info(
                f"Created page",
                extra={"database_id": database_id[:8], "page_id": page_id[:8] if page_id else None}
            )
            return page_id
        except Exception as e:
            logger.error(
                f"Failed to create page",
                extra={"database_id": database_id[:8], "error": str(e)}
            )
            return None

    def get_page(self, page_id: str) -> Optional[Dict]:
        """
        Retrieve a single page by ID.

        Args:
            page_id: The page UUID

        Returns:
            Page object or None
        """
        try:
            return self._request("GET", f"/pages/{page_id}")
        except Exception as e:
            logger.warning(
                f"Failed to get page",
                extra={"page_id": page_id[:8], "error": str(e)}
            )
            return None

    def append_blocks(self, page_id: str, children: List[Dict]) -> bool:
        """
        Append block children to a page.

        Args:
            page_id: The page UUID
            children: List of block objects

        Returns:
            True if successful
        """
        try:
            self._request(
                "PATCH",
                f"/blocks/{page_id}/children",
                {"children": children}
            )
            return True
        except Exception as e:
            logger.warning(
                f"Failed to append blocks",
                extra={"page_id": page_id[:8], "error": str(e)}
            )
            return False


# Helper functions for quick access (backwards compatibility)
def extract_title(page: Dict) -> str:
    """Extract title from a page object."""
    props = page.get("properties", {})
    for prop_name in ["Title", "Name", "Task"]:
        title_prop = props.get(prop_name)
        if title_prop and title_prop.get("title"):
            titles = title_prop["title"]
            if titles:
                return "".join([t.get("plain_text", "") for t in titles])
    return "Untitled"


# Singleton client for convenience
_client: Optional[NotionClient] = None


def get_client() -> NotionClient:
    """Get or create a singleton NotionClient instance."""
    global _client
    if _client is None:
        _client = NotionClient()
    return _client
