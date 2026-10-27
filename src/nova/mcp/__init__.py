"""MCP host integration for Nova.

Nova acts as an MCP *host*: it launches external MCP servers (stdio
transport), lists their tools and exposes them through the standard
ToolRegistry so the LLM can call them like any native tool.
"""
