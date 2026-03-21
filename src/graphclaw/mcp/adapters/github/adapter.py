"""graphclaw.mcp.adapters.github.adapter — GitHub MCP adapter.

Description
-----------
Pre-built adapter for GitHub REST API integration via the MCP protocol.
Declares tools for listing issues, fetching pull requests, creating issues,
and adding comments, using HTTP transport with a Personal Access Token.

Design Patterns
---------------
- ClassVar manifest: ``TOOLS`` is a class-level list of MCP tool descriptors
  that the MCP executor uses to discover available operations without
  instantiating the adapter.

Public API
----------
- GitHubMCPAdapter: Pre-built adapter for the GitHub REST API.

Dependencies
------------
- graphclaw.mcp.adapters.base: MCPAdapterABC (stdlib-like ABC layer).
"""

from __future__ import annotations

from typing import ClassVar

from graphclaw.mcp.adapters.base import MCPAdapterABC


class GitHubMCPAdapter(MCPAdapterABC):
    """Pre-built MCP adapter for the GitHub REST API.

    Connects via HTTP transport.  Credentials (Personal Access Token) must be
    stored in the SecretsManager under the path described in
    ``get_install_instructions``.

    Class Variables
    ---------------
    TOOLS:
        MCP tool descriptors for list_issues, get_pull_request,
        create_issue, and add_comment.
    """

    server_name: ClassVar[str] = "github"
    default_transport: ClassVar[str] = "http"
    default_trust_read: ClassVar[str] = "AUTO"
    default_trust_write: ClassVar[str] = "GATED"
    default_scope: ClassVar[list[str]] = [
        "read_issues",
        "create_issue",
        "read_pr",
        "add_comment",
        "read_file",
        "list_repos",
    ]

    TOOLS: ClassVar[list[dict]] = [
        {
            "name": "list_issues",
            "description": "List issues for a repository",
            "input_schema": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "state": {
                        "type": "string",
                        "enum": ["open", "closed", "all"],
                        "default": "open",
                    },
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["owner", "repo"],
            },
        },
        {
            "name": "get_pull_request",
            "description": "Get a specific pull request",
            "input_schema": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "pull_number": {"type": "integer"},
                },
                "required": ["owner", "repo", "pull_number"],
            },
        },
        {
            "name": "create_issue",
            "description": "Create a new GitHub issue",
            "input_schema": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["owner", "repo", "title"],
            },
        },
        {
            "name": "add_comment",
            "description": "Add a comment to an issue or PR",
            "input_schema": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "issue_number": {"type": "integer"},
                    "body": {"type": "string"},
                },
                "required": ["owner", "repo", "issue_number", "body"],
            },
        },
    ]

    @classmethod
    def get_install_instructions(cls) -> str:
        """Return setup instructions for the GitHub MCP server.

        Returns
        -------
        str:
            Step-by-step instructions for creating a Personal Access Token
            and storing it in Secrets Manager.
        """
        return (
            "GitHub MCP Server setup:\n"
            "1. Create a GitHub Personal Access Token with required scopes"
            " (repo, issues)\n"
            "2. Store token in Secrets Manager under"
            " /workgraph/USER-{id}/mcp/{server_id}\n"
            "3. The server connects via HTTP transport using the GitHub REST API v3"
        )
