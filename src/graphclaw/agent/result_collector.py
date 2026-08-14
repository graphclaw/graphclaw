# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.agent.result_collector — Background service for collecting skill results.

Description
-----------
``ResultCollector`` polls the ``WorkerPool`` for completed ``SkillResult``
objects, updates corresponding ``TaskNode`` state and intelligence in the
graph, writes result summaries to agent memory, and logs decisions.

Also handles ``AgentTaskCompletedEvent`` objects from the ``AGENT_UPDATES``
queue via ``process_agent_result()``, applying the same graph + memory +
decisions-log updates as for skill results.

Design Patterns
---------------
- Polling loop: Runs as an ``asyncio.Task`` periodically checking worker statuses.
- Dependency Injection: ``GraphStore``, ``WorkerPool``, ``StorageClient`` injected.

Public API
----------
- ResultCollector: Background result collection service.
- ResultCollector.start: Begin the polling loop.
- ResultCollector.stop: Cancel the polling loop.
- ResultCollector.process_agent_result: Process a completed sub-agent result event.

Dependencies
------------
- graphclaw.db.base: GraphStore.
- graphclaw.skills.worker: WorkerPool.
- graphclaw.infra.storage: StorageClient, StoragePaths.
- graphclaw.agent.sub_agent_runner: AgentUpdateEvent (TYPE_CHECKING).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graphclaw.agent.sub_agent_runner import AgentUpdateEvent
    from graphclaw.db.base import GraphStore
    from graphclaw.infra.storage import StorageClient
    from graphclaw.skills.worker import WorkerPool

logger = logging.getLogger(__name__)


class ResultCollector:
    """Polls the WorkerPool for completed skill results and updates the graph.

    Parameters
    ----------
    graph_repo:
        GraphStore instance for updating task nodes.
    worker_pool:
        WorkerPool to poll for completed results.
    storage_client:
        Optional StorageClient for writing results to agent memory.
    user_id:
        The user ID for storage path construction.
    agent_id:
        The agent ID for storage path construction (default ``"main"``).
    poll_interval:
        Seconds between polling cycles (default 5).
    """

    def __init__(
        self,
        graph_repo: GraphStore,
        worker_pool: WorkerPool,
        storage_client: StorageClient | None = None,
        user_id: str = "",
        agent_id: str = "main",
        poll_interval: float = 5.0,
    ) -> None:
        self._repo = graph_repo
        self._pool = worker_pool
        self._storage = storage_client
        self._user_id = user_id
        self._agent_id = agent_id
        self._poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._processed_jobs: set[str] = set()

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("ResultCollector: started polling (interval=%.1fs)", self._poll_interval)

    async def stop(self) -> None:
        """Cancel the polling loop."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("ResultCollector: stopped")

    async def _poll_loop(self) -> None:
        """Periodically check worker statuses for completed jobs."""
        while True:
            try:
                await self._check_completed()
            except Exception as exc:  # noqa: BLE001
                logger.warning("ResultCollector: poll error: %s", exc)
            await asyncio.sleep(self._poll_interval)

    async def _check_completed(self) -> None:
        """Inspect worker statuses and process any newly completed results."""
        statuses = self._pool.get_worker_statuses()
        for ws in statuses:
            if ws.state.value in ("COMPLETED", "FAILED", "TIMED_OUT"):
                job_id = ws.current_job_id
                if job_id and job_id not in self._processed_jobs:
                    self._processed_jobs.add(job_id)
                    logger.info(
                        "ResultCollector: worker %s finished job %s (state=%s)",
                        ws.worker_id,
                        job_id,
                        ws.state.value,
                    )

    async def process_result(
        self,
        task_id: str,
        skill_name: str,
        status: str,
        output: str,
        error: str = "",
    ) -> None:
        """Update a task node with a skill result and write to agent memory.

        This method is called directly by the AgentLoop after synchronous
        skill invocation, providing an alternative to polling.

        Parameters
        ----------
        task_id:
            The task node to update.
        skill_name:
            Name of the skill that produced the result.
        status:
            ``COMPLETED``, ``FAILED``, or ``TIMEOUT``.
        output:
            The skill output text.
        error:
            Error message if the skill failed.
        """
        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc)

        # Update task intelligence and state
        updates: dict = {"updated_at": now.isoformat()}
        if status == "COMPLETED":
            updates["intelligence"] = output[:2000]
            updates["state"] = "NEEDS_REVIEW"
        elif status in ("FAILED", "TIMEOUT"):
            updates["intelligence"] = f"Skill '{skill_name}' {status.lower()}: {error}"
            updates["state"] = "BLOCKED"

        try:
            await self._repo.update_node(task_id, updates)
        except Exception as exc:
            logger.warning("ResultCollector: could not update task %s: %s", task_id, exc)

        # Write to agent memory
        if self._storage and self._user_id:
            from graphclaw.infra.storage import StoragePaths

            context_path = StoragePaths.agent_memory_working(self._user_id, self._agent_id)
            memory_entry = (
                f"\n## Skill Result: {skill_name}\n"
                f"- **Task:** {task_id}\n"
                f"- **Status:** {status}\n"
                f"- **Completed at:** {now.isoformat()}\n"
            )
            if output:
                memory_entry += f"- **Output preview:** {output[:500]}\n"
            if error:
                memory_entry += f"- **Error:** {error}\n"

            try:
                existing = b""
                try:
                    existing = await self._storage.read(context_path)
                except Exception:
                    pass
                updated = existing.decode(errors="replace") + memory_entry
                await self._storage.write(context_path, updated.encode())
            except Exception as exc:
                logger.debug("ResultCollector: could not write memory: %s", exc)

        # Write to decisions log
        if self._storage and self._user_id:
            log_path = f"{StoragePaths.agent_root(self._user_id, self._agent_id)}log/decisions.md"
            log_entry = (
                f"\n### {now.isoformat()} — Skill execution: {skill_name}\n"
                f"- Task: {task_id}\n"
                f"- Status: {status}\n"
                f"- Action: {'Updated task to NEEDS_REVIEW' if status == 'COMPLETED' else 'Marked task BLOCKED'}\n"
            )
            try:
                existing_log = b""
                try:
                    existing_log = await self._storage.read(log_path)
                except Exception:
                    pass
                await self._storage.write(
                    log_path, (existing_log.decode(errors="replace") + log_entry).encode()
                )
            except Exception:
                pass

    async def process_agent_result(self, event: AgentUpdateEvent) -> None:
        """Update a task node with a completed sub-agent result.

        Called by ``AgentEventConsumer._handle_agent_completed()`` on each
        ``AgentTaskCompletedEvent`` received from the ``AGENT_UPDATES`` queue.

        Applies the same graph + agent-memory + decisions-log updates as
        ``process_result()`` but sourced from a sub-agent event rather than a
        skill invocation.

        Parameters
        ----------
        event:
            ``AgentUpdateEvent`` with ``event_type == COMPLETED``.
        """
        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc)
        task_id = event.task_id
        agent_id = event.agent_id
        result_summary = event.message or ""
        status = event.status or "COMPLETED"

        # Determine new task state
        new_state = "NEEDS_REVIEW" if status == "COMPLETED" else "BLOCKED"

        # Update task node
        updates: dict = {
            "updated_at": now.isoformat(),
            "state": new_state,
        }
        
        # Try to read agent output files and include in intelligence
        agent_output_content = ""
        if self._storage and status == "COMPLETED":
            # Resolve user_id from session_id (format: ses-{user_id}-{timestamp})
            output_user_id = event.session_id.split("-")[1] if "-" in event.session_id else ""
            if output_user_id:
                from graphclaw.infra.storage import StoragePaths
                
                # Try common output file paths for sub-agents
                output_paths = [
                    f"{StoragePaths.agent_root(output_user_id, agent_id)}output/email-draft.md",
                    f"{StoragePaths.agent_root(output_user_id, agent_id)}output/result.md",
                    f"{StoragePaths.agent_root(output_user_id, agent_id)}output/summary.md",
                ]
                
                for output_path in output_paths:
                    try:
                        content_bytes = await self._storage.read(output_path)
                        agent_output_content = content_bytes.decode(errors="replace").strip()
                        if agent_output_content:
                            logger.info(
                                "ResultCollector: read agent output from %s (%d bytes)",
                                output_path,
                                len(content_bytes),
                            )
                            break
                    except Exception:
                        continue
        
        # Combine result summary with agent output
        intelligence_text = result_summary
        if agent_output_content:
            intelligence_text = f"{result_summary}\n\n---\n\n{agent_output_content}"
        
        if intelligence_text:
            updates["intelligence"] = intelligence_text[:2000]

        try:
            await self._repo.update_node(task_id, updates)
            logger.info(
                "ResultCollector: task %s updated → %s after agent %s completed",
                task_id,
                new_state,
                agent_id,
            )
        except Exception as exc:
            logger.warning("ResultCollector: could not update task %s: %s", task_id, exc)

        # Resolve storage user_id: prefer event session_id prefix for correlation
        storage_user_id = self._user_id
        storage_agent_id = self._agent_id

        # Write to agent memory
        if self._storage and storage_user_id:
            from graphclaw.infra.storage import StoragePaths

            context_path = StoragePaths.agent_memory_working(storage_user_id, storage_agent_id)
            memory_entry = (
                f"\n## Sub-Agent Result: {agent_id}\n"
                f"- **Task:** {task_id}\n"
                f"- **Status:** {status}\n"
                f"- **Completed at:** {now.isoformat()}\n"
            )
            if event.batch_id:
                memory_entry += f"- **Batch:** {event.batch_id}\n"
            if result_summary:
                memory_entry += f"- **Summary:** {result_summary[:500]}\n"

            try:
                existing = b""
                try:
                    existing = await self._storage.read(context_path)
                except Exception:
                    pass
                await self._storage.write(
                    context_path, (existing.decode(errors="replace") + memory_entry).encode()
                )
            except Exception as exc:
                logger.debug("ResultCollector: could not write agent memory: %s", exc)

        # Write to decisions log
        if self._storage and storage_user_id:
            from graphclaw.infra.storage import StoragePaths

            log_path = (
                f"{StoragePaths.agent_root(storage_user_id, storage_agent_id)}log/decisions.md"
            )
            log_entry = (
                f"\n### {now.isoformat()} — Sub-agent completed: {agent_id}\n"
                f"- Task: {task_id}\n"
                f"- Status: {status}\n"
                f"- Action: Updated task to {new_state}\n"
            )
            if event.batch_id:
                log_entry += f"- Batch: {event.batch_id}\n"
            try:
                existing_log = b""
                try:
                    existing_log = await self._storage.read(log_path)
                except Exception:
                    pass
                await self._storage.write(
                    log_path,
                    (existing_log.decode(errors="replace") + log_entry).encode(),
                )
            except Exception:
                pass


__all__ = ["ResultCollector"]
