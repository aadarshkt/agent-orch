"""
Script Runner Executor.

Runs a Python script or shell command as a subprocess with configurable
timeout. Captures stdout/stderr and appends to workflow state.
"""
import asyncio
from typing import Any, Dict
from src.registry.base_executor import BaseExecutor, ExecutionContext
from src.registry.executor_registry import register_executor


@register_executor("script_runner")
class ScriptRunnerExecutor(BaseExecutor):
    """Runs a Python script or shell command as a subprocess."""

    async def execute(
        self,
        node_config: Dict[str, Any],
        state: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        node_id = node_config["id"]
        agent_name = node_config.get("agent_name", node_id)
        params = node_config.get("params", {})

        script_path = params.get("script_path", "")
        inline_script = params.get("inline_script", "")
        timeout = params.get("timeout", 60)

        if context.event_bus:
            source = script_path if script_path else "inline script"
            context.event_bus.publish_sync(
                context.thread_id,
                {
                    "event": "agent",
                    "data": f"[{agent_name}] Running {source} (timeout: {timeout}s)...",
                },
            )

        # --- Execute script ---
        stdout_output = ""
        stderr_output = ""

        try:
            if script_path:
                cmd = ["python", script_path]
            elif inline_script:
                cmd = ["python", "-c", inline_script]
            else:
                raise ValueError("Either 'script_path' or 'inline_script' must be provided.")

            # TODO: Add sandboxing / security measures for production
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
                stdout_output = stdout_bytes.decode("utf-8", errors="replace")
                stderr_output = stderr_bytes.decode("utf-8", errors="replace")
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                stderr_output = f"Script timed out after {timeout}s"

            return_code = process.returncode

        except Exception as e:
            return_code = -1
            stderr_output = str(e)

        if context.event_bus:
            status_msg = "completed" if return_code == 0 else f"failed (exit code: {return_code})"
            context.event_bus.publish_sync(
                context.thread_id,
                {
                    "event": "agent",
                    "data": f"[{agent_name}] Script {status_msg}.",
                },
            )

        # --- Update state ---
        messages = list(state.get("messages", []))
        messages.append(
            {
                "role": "system",
                "content": f"Script '{agent_name}' output:\n{stdout_output}" if stdout_output else f"Script '{agent_name}' completed.",
            }
        )

        artifacts = dict(state.get("artifacts", {}))
        artifacts[f"{node_id}_stdout"] = stdout_output
        if stderr_output:
            artifacts[f"{node_id}_stderr"] = stderr_output

        return {
            **state,
            "messages": messages,
            "artifacts": artifacts,
            "current_step": node_id,
            "status": "completed" if return_code == 0 else "failed",
            "error": stderr_output if return_code != 0 else None,
        }
