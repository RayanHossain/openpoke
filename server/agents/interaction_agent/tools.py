"""Tool definitions for interaction agent."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Optional

from ...logging_config import logger
from ...services.conversation import get_conversation_log
from ...services.execution import get_execution_agent_logs
from ...services.execution.agent_registry import get_agent_registry
from ...services.execution.keys import extract_from_text
from ...services.execution.routing_cards import MAX_CARDS, render_card
from ..execution_agent.batch_manager import ExecutionBatchManager


@dataclass
class ToolResult:
    """Standardized payload returned by interaction-agent tools."""

    success: bool
    payload: Any = None
    user_message: Optional[str] = None
    recorded_reply: bool = False

# Tool schemas for OpenRouter
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "send_message_to_agent",
            "description": "Deliver instructions to an EXISTING execution agent, chosen by its agent_id from the agent cards. Never creates an agent; an unknown agent_id returns an error and does nothing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "ID of an existing agent exactly as shown on its card (e.g. 'ea_17').",
                    },
                    "instructions": {"type": "string", "description": "Instructions for the agent to execute."},
                },
                "required": ["agent_id", "instructions"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_agent",
            "description": "Start a NEW execution agent for work no existing card owns. First call with confirm=false: it creates nothing and returns any agents that already own the people, threads, drafts or triggers in the request. If one of them fits, use send_message_to_agent instead; otherwise call again with confirm=true to create the agent and dispatch the instructions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Short human-readable label for the new agent (display only; e.g. 'Alice lunch').",
                    },
                    "instructions": {"type": "string", "description": "Instructions for the new agent to execute."},
                    "confirm": {
                        "type": "boolean",
                        "description": "false (default): check for existing owners, create nothing. true: create and dispatch.",
                    },
                },
                "required": ["label", "instructions"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message_to_user",
            "description": "Deliver a natural-language response directly to the user. Use this for updates, confirmations, or any assistant response the user should see immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Plain-text message that will be shown to the user and recorded in the conversation log.",
                    },
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_draft",
            "description": "Record an email draft so the user can review the exact text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email for the draft.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject for the draft.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body content (plain text).",
                    },
                },
                "required": ["to", "subject", "body"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait silently when a message is already in conversation history to avoid duplicating responses. Adds a <wait> log entry that is not visible to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of why waiting (e.g., 'Message already sent', 'Draft already created').",
                    },
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]

_EXECUTION_BATCH_MANAGER = ExecutionBatchManager()


def _dispatch(agent_id: str, label: str, instructions: str) -> None:
    """Record the request in the agent's log and run it in the background."""
    get_execution_agent_logs().record_request(agent_id, instructions)

    async def _execute_async() -> None:
        try:
            result = await _EXECUTION_BATCH_MANAGER.execute_agent(label, instructions, agent_id=agent_id)
            status = "SUCCESS" if result.success else "FAILED"
            logger.info(f"Agent {agent_id} ('{label}') completed: {status}")
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(f"Agent {agent_id} ('{label}') failed: {str(exc)}")

    asyncio.get_running_loop().create_task(_execute_async())


def _has_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        logger.error("No running event loop available for async execution")
        return False


# Dispatch instructions to an existing execution agent by its registry ID
def send_message_to_agent(agent_id: str, instructions: str) -> ToolResult:
    """Send instructions to an existing execution agent. Never creates one."""
    record = get_agent_registry().get(agent_id) if isinstance(agent_id, str) else None
    if record is None:
        logger.info(f"Rejected dispatch to unknown agent_id {agent_id!r}")
        return ToolResult(
            success=False,
            payload={
                "error": (
                    f"Unknown agent_id {agent_id!r}. Use an agent_id exactly as shown on a card, "
                    "or create_agent if no existing agent owns this work."
                )
            },
        )
    if not _has_event_loop():
        return ToolResult(success=False, payload={"error": "No event loop available"})

    _dispatch(record.agent_id, record.label, instructions)
    logger.info(f"Dispatched to agent {record.agent_id} ('{record.label}')")
    return ToolResult(
        success=True,
        payload={
            "status": "submitted",
            "agent_id": record.agent_id,
            "label": record.label,
            "new_agent_created": False,
        },
    )


# Create a new execution agent (two-phase: check owners, then confirm)
def create_agent(label: str, instructions: str, confirm: bool = False) -> ToolResult:
    """confirm=False: report existing owners, create nothing. confirm=True: create and dispatch."""
    clean_label = (label or "").strip() if isinstance(label, str) else ""
    if not clean_label:
        return ToolResult(success=False, payload={"error": "label must be a non-empty string"})

    if confirm is not True:
        owners = get_agent_registry().agents_for_keys(extract_from_text(f"{clean_label}\n{instructions}"))
        return ToolResult(
            success=True,
            payload={
                "status": "confirmation_required",
                "existing_owners": [render_card(agent_id) for agent_id in owners[:MAX_CARDS]],
                "next_step": (
                    "If an existing owner fits, use send_message_to_agent with its agent_id. "
                    "Otherwise call create_agent again with confirm=true."
                ),
            },
        )

    if not _has_event_loop():
        return ToolResult(success=False, payload={"error": "No event loop available"})

    record = get_agent_registry().create_agent(clean_label)
    _dispatch(record.agent_id, record.label, instructions)
    logger.info(f"Created agent {record.agent_id} ('{record.label}')")
    return ToolResult(
        success=True,
        payload={
            "status": "submitted",
            "agent_id": record.agent_id,
            "label": record.label,
            "new_agent_created": True,
        },
    )


# Send immediate message to user and record in conversation history
def send_message_to_user(message: str) -> ToolResult:
    """Record a user-visible reply in the conversation log."""
    log = get_conversation_log()
    log.record_reply(message)

    return ToolResult(
        success=True,
        payload={"status": "delivered"},
        user_message=message,
        recorded_reply=True,
    )


# Format and record email draft for user review
def send_draft(
    to: str,
    subject: str,
    body: str,
) -> ToolResult:
    """Record a draft update in the conversation log for the interaction agent."""
    log = get_conversation_log()

    message = f"To: {to}\nSubject: {subject}\n\n{body}"

    log.record_reply(message)
    logger.info(f"Draft recorded for: {to}")

    return ToolResult(
        success=True,
        payload={
            "status": "draft_recorded",
            "to": to,
            "subject": subject,
        },
        recorded_reply=True,
    )


# Record silent wait state to avoid duplicate responses
def wait(reason: str) -> ToolResult:
    """Wait silently and add a wait log entry that is not visible to the user."""
    log = get_conversation_log()
    
    # Record a dedicated wait entry so the UI knows to ignore it
    log.record_wait(reason)
    

    return ToolResult(
        success=True,
        payload={
            "status": "waiting",
            "reason": reason,
        },
        recorded_reply=True,
    )


# Return predefined tool schemas for LLM function calling
def get_tool_schemas():
    """Return OpenAI-compatible tool schemas."""
    return TOOL_SCHEMAS


# Route tool calls to appropriate handlers with argument validation and error handling
def handle_tool_call(name: str, arguments: Any) -> ToolResult:
    """Handle tool calls from interaction agent."""
    try:
        if isinstance(arguments, str):
            args = json.loads(arguments) if arguments.strip() else {}
        elif isinstance(arguments, dict):
            args = arguments
        else:
            return ToolResult(success=False, payload={"error": "Invalid arguments format"})

        if name == "send_message_to_agent":
            return send_message_to_agent(**args)
        if name == "create_agent":
            return create_agent(**args)
        if name == "send_message_to_user":
            return send_message_to_user(**args)
        if name == "send_draft":
            return send_draft(**args)
        if name == "wait":
            return wait(**args)

        logger.warning("unexpected tool", extra={"tool": name})
        return ToolResult(success=False, payload={"error": f"Unknown tool: {name}"})
    except json.JSONDecodeError:
        return ToolResult(success=False, payload={"error": "Invalid JSON"})
    except TypeError as exc:
        return ToolResult(success=False, payload={"error": f"Missing required arguments: {exc}"})
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("tool call failed", extra={"tool": name, "error": str(exc)})
        return ToolResult(success=False, payload={"error": "Failed to execute"})
