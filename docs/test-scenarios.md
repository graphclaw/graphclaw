# Test Scenarios

## Scenario 1: Getting the user onboarded
- The user is added into the system and signed in
- user configures the channels he needs as a preferred mode of communication and chooses to integrate email id and telegram as his channels of communication. use the telegram and email provided for testing the scenario
- configures the main orchestrating agent by defining the persona, heartbeat and other details about the orchestrating agent. He names the orchesrtating agent as betty.
- betty once configured is scheduled to provide briefing in morning , afternoon, evening 
- betty reaches out to the user for welcoming and discussing the set of tasks to be configured.


## Scenario 2: User assigning the task of following up with another user
- betty needs to setup the task based on the inputs from the user for setting up a followup with soni - <contact-email@example.com> to check when is she ready for assessment.
- betty will also ensure that when reaching out to the new user also invites the user to join the graphclaw platform. 
- This is part of the original design where it is not only working on following up with the user but also doing soft outreach campaign to expand the network. this is to be included as part of the memory , context, persona, soul , heartbeat of the agent.

## Scenario 3: User assigns the task of managing a project.
- user is talking to betty during he briefing to discuss about a project and betty has been assigned a project to manage birthday party for his son. betty needs to plan for the birthday show the plan to user get approaval on the tasks and then sets up the tasks in the graph to execute on the plan. This will involve performing work breakdown , finding the list of agents to which the tasks can be assigned getting these agents created and the necessary skills added. example - drafting the birthday invitation, sending emails etc.

## Scenario 4: User assigns the goal of finding leads for his growing podcast
- betty has been given the goal of finding leads for user's podcast channel whom he can interview. This involves working on evaluatinig the profiles, finding the right person in user's linkedin network and then crafting right invitation request for interview. 
- Reviewing the emails and requests drafted and then sending, managing , following up. Working on the plan and then creating the necessary actions, tasks, etc to execute on this goal. 

---

## Intelligence Layer — Test Scenarios (Phase 4.5)

### Scenario 5: Inbound email reply is matched to the correct task node (Tier 1 — threading)

**Preconditions:** Betty has sent an outbound email to Soni (<contact-email@example.com>) in the context of task `TSK-AG-13860-DEL` (follow-up task). The original message ID and checkin node are stored in Redis and the graph.

**Steps:**
1. Soni replies to Betty's email with "Hi, I've uploaded the report to the shared folder. Please review."
2. The email poller picks it up within 30 seconds.
3. Verify `in_reply_to` header is present and matches the stored checkin message ID.

**Expected outcomes:**
- Gateway logs show `agent.inbound_processed` with `matched_by=THREAD`, `task_id=TSK-AG-13860-DEL`
- `TSK-AG-13860-DEL` node `intelligence` field updated with entry: `[{today}] email | inbound | Soni confirmed upload…`
- `CheckinNode.inbound_response` updated with Soni's reply body
- MinIO `inbox/recent/` has a compact entry; `inbox/archive/` has full email
- Betty proactively tells the user about Soni's reply in the next briefing or chat turn


### Scenario 6: Fresh Telegram message matched via vector search (Tier 3 — semantic)

**Preconditions:** Task `TSK-AG-13860-DEL` exists in the graph with embeddings computed. No prior reply-chain from this Telegram user.

**Steps:**
1. Send a Telegram message to the bot: "hey, just wanted to let you know the bangalore slides are ready for review"
2. The Telegram adapter picks it up and publishes to `INBOUND_MESSAGES`.

**Expected outcomes:**
- `TaskResolver` runs vector search; similarity score ≥ 0.70 against the relevant task
- `agent.intelligence_update` log event written under `{user_id}/logs/inbound/`
- Task node `intelligence` updated: `[{today}] telegram | inbound | User confirmed bangalore slides ready for review`
- Betty's `working/context.md` NOT updated (task-specific content routes to node, not general memory)
- `inbox/recent/` compact entry present with `channel=telegram`


### Scenario 7: Unmatched inbound — Betty asks user for direction

**Preconditions:** A fresh email arrives from an address that IS a known contact (exists as a `ResourceNode` in the graph) but contains no task ID and body does not match any task by vector search (similarity < 0.40).

**Steps:**
1. Send email to <gateway-inbox@example.com> with subject "Planning meeting next week?" and body that has no connection to any active task.

**Expected outcomes:**
- All three resolution tiers return no match
- Sender email is found in graph as a known `ResourceNode`
- Betty sends the user a message (via their preferred channel): "I received a message from [sender] that I couldn't match to any task. It says: '[50-word summary]'. What should I do with it?"
- `inbox/recent/` entry exists with `task_id_matched=null`, `signal=UNMATCHED`
- No task node intelligence field is updated
- No log entry at ERROR level — this is expected behavior, not an error


### Scenario 8: Outbound intelligence log — full round-trip recorded on node

**Preconditions:** Task `TSK-AG-13860-DEL` exists. Betty sends an outbound email to Soni via `check_inbox` + chat interaction.

**Steps:**
1. Run `graphclaw agent chat "send soni a reminder about the deliverable"` with task context in scope.
2. Betty sends email to `<contact-email@example.com>`.
3. Wait for Soni to reply (use test email account to reply manually or simulate).
4. Check graph after both events.

**Expected outcomes:**
- After outbound: `TSK-AG-13860-DEL.intelligence` contains `[{today}] email | outbound | Sent "Reminder:…" to <contact-email@example.com>`
- `CheckinNode` created in graph with `outbound_message` set, `inbound_response = null`, `REFERS_TO` edge to task
- After inbound reply: `CheckinNode.inbound_response` populated
- `TSK-AG-13860-DEL.intelligence` now has both outbound and inbound lines — full round-trip visible
- Betty can answer "what's the latest communication on TSK-AG-13860-DEL?" using graph intelligence alone


### Scenario 9: Log sink — structured JSONL written to MinIO, no PII in logs

**Preconditions:** Docker compose running with gateway + MinIO. `GRAPHCLAW_USER_ID` set.

**Steps:**
1. Run `graphclaw agent chat "summarize my top 3 tasks"` — triggers `agent.tool_call` and `agent.message` log events.
2. Send a test inbound email — triggers `agent.inbound_processed` and `agent.intelligence_update`.
3. Check MinIO.

**Expected outcomes:**
- MinIO bucket `graphclaw`: `{user_id}/logs/agent/{today}/` contains at least one `.jsonl` file
- Each line is valid JSON with `timestamp`, `level`, `service`, `event_type`, `session_id`
- `agent.tool_call` entries contain `tool_name` and `latency_ms` but NOT `args` body content
- `agent.message` entries contain `input_tokens`, `output_tokens`, `latency_ms` but NOT message content
- `agent.inbound_processed` entries contain `message_id`, `channel`, `task_id`, `signal` but NOT email body or subject
- No entry anywhere contains raw email body text, personal email addresses in cleartext, or message content
- `_system/logs/gateway/{today}/` contains gateway startup and channel events with no user-specific data