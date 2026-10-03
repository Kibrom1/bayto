import asyncio
import httpx
from mcp.server.fastmcp import FastMCP

# The orchestrator API is typically hosted on the host machine from the sandbox's perspective
ORCHESTRATOR_URL = "http://host.docker.internal:8000"

mcp = FastMCP("Bayto")

@mcp.tool()
async def get_task_context() -> str:
    """
    Retrieve the current task brief, success criteria, and session status.
    Useful for agents to align their contributions with the primary goal.
    """
    # In a real SBX deployment, the session_id would be passed via request context or env
    # For this implementation, we look for it in the environment (set by the SBX gateway)
    import os
    session_id = os.environ.get("BAYTO_SESSION_ID")
    if not session_id:
        return "Error: BAYTO_SESSION_ID not found in environment. Cannot retrieve context."

    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(f"{ORCHESTRATOR_URL}/sessions/{session_id}")
            res.raise_for_status()
            data = res.json()["session"]
            # We might need to fetch the task detail separately if not in session detail
            task_res = await client.get(f"{ORCHESTRATOR_URL}/tasks/{data['task_id']}")
            task_res.raise_for_status()
            task = task_res.json()["task"]

            return (
                f"Task: {task['title']}\n"
                f"Brief: {task['brief']}\n"
                f"Success Criteria: {task['success_criteria']}\n"
                f"Current Status: {data['status']}\n"
                f"Current Round: {data['round']}"
            )
        except Exception as e:
            return f"Error retrieving task context: {str(e)}"

@mcp.tool()
async def request_human(message: str) -> str:
    """
    Request a human intervention or a specific answer from the human overseer.
    This will send an interjection to the session.
    """
    import os
    session_id = os.environ.get("BAYTO_SESSION_ID")
    if not session_id:
        return "Error: BAYTO_SESSION_ID not found in environment."

    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(
                f"{ORCHESTRATOR_URL}/sessions/{session_id}/interject",
                json={"body": f"[Agent Request]: {message}", "to": ["human"]}
            )
            res.raise_for_status()
            return "Request sent to human successfully."
        except Exception as e:
            return f"Error requesting human: {str(e)}"

@mcp.tool()
async def cite_source(source_url: str, quote: str, explanation: str) -> str:
    """
    Cite an external source to support a claim.
    This records the citation in the agent's response meta.
    """
    # Citations are typically handled by the agent's own response envelope.
    # This tool serves as a formal way to structure the citation for the moderator.
    return f"Citation recorded: Source {source_url} - '{quote}' ({explanation})"

if __name__ == "__main__":
    mcp.run()
