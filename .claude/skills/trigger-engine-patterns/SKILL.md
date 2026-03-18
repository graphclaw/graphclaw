---
name: trigger-engine-patterns
description: >
  Trigger engine patterns for GraphClaw — time-based, event-based, inbound, and
  on-demand triggers, follow-up timing model, daily briefing generation, and
  interrupt threshold logic. Use when implementing trigger scheduling, briefing
  generation, follow-up timing, or event dispatching. Triggers on: "trigger engine",
  "daily briefing", "follow-up timing", "scheduled trigger", "interrupt threshold",
  "briefing generation".
---

# Trigger Engine (PRD Sections 10, 12)

## Trigger Types

| Type | Source | Example |
|------|--------|---------|
| **Time-based** | Scheduler (cron) | Daily briefing at 8am, recurring task spawn |
| **Event-based** | Storage/broker event | status.md write → completion signal |
| **Inbound** | Channel gateway | Email received → inbound update protocol |
| **On-demand** | CLI / API | User invokes `graphclaw agent run` |

## Trigger Engine Loop

```python
class TriggerEngine:
    async def run(self):
        """Main loop: check scheduled triggers, process broker events."""
        asyncio.create_task(self._scheduled_trigger_loop())
        asyncio.create_task(self._event_consumer_loop())

    async def _scheduled_trigger_loop(self):
        while True:
            now = utcnow()
            due_triggers = await self._get_due_triggers(now)
            for trigger in due_triggers:
                await self._dispatch(trigger)
            await asyncio.sleep(60)  # check every minute

    async def _event_consumer_loop(self):
        async for message in self._broker.consume("trigger_events"):
            trigger = TriggerEvent.model_validate_json(message)
            await self._dispatch(trigger)
```

## Follow-up Timing Model (PRD Section 10)

```python
def compute_followup_timing(
    base_cadence: float,        # from UserNode preferences (days)
    complexity_factor: float,   # task type + estimated_effort
    reliability_score: float,   # ResourceNode reliability (0-1)
    recency_bonus: float,       # recent on-time delivery bonus
) -> float:
    """Returns days until next follow-up."""
    return (
        base_cadence
        * complexity_factor
        * (1.0 / max(reliability_score, 0.1))  # less reliable → more frequent
        * (1.0 - recency_bonus * 0.2)           # recent delivery → slightly less frequent
    )
```

## Daily Briefing Structure (PRD Section 12)

```markdown
## CRITICAL (max 3)
{items where score > interrupt_threshold, sorted by priority}
"Your decision needed today"

## INFERENCES TO CONFIRM
{follow-up timings, goal/constraint inference, resource risk signals}

## COMPLETED SINCE LAST BRIEFING
{task completions, milestone progress, proactive updates}

## AHEAD OF THE CURVE (awareness only)
{approaching urgency, critical path float warnings}

## DEFERRED ITEMS CHECK
{previously snoozed: "Still want to defer?"}
```

### Generation Rules
- Max 3 CRITICAL items — rest handled autonomously (stated explicitly)
- Cognitive load limit enforced via top-N filtering
- Interrupt threshold: `UserNode.interrupt_threshold` (default 0.8)
- Items below threshold accumulate for next briefing window
- Only genuine urgency (score > 0.95) breaks through mid-day

## Briefing Generation Pipeline

```python
async def generate_daily_briefing(user_id: str, repo: GraphRepository) -> str:
    # 1. Run scoring cycle → ranked action queue
    queue = await agent_loop.run_cycle()

    # 2. Partition by section
    critical = [e for e in queue if e.final_score > user.interrupt_threshold][:3]
    inferences = await get_pending_inferences(user_id)
    completed = await get_completed_since(user_id, last_briefing_at)
    upcoming = [e for e in queue if 0.5 < e.final_score <= user.interrupt_threshold][:5]
    deferred = await get_snoozed_items(user_id)

    # 3. Format via LLM (briefing_style: concise|detailed)
    return await format_briefing(user.briefing_style, critical, inferences, ...)
```
