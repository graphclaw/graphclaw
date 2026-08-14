# Email Drafting Workflow Test - Sub-Agent Output Fix

## Test Scenario Summary

**Date**: 2026-08-14  
**Test**: End-to-end email drafting delegation workflow  
**Status**: Fix implemented, awaiting live test with API credits

---

## What We Tested

### 1. Setup (Completed ✅)
- User (Abhishek) logged in to GraphClaw Cockpit
- Orchestrator agent: **Ella** (onboarded)
- Created sub-agent: **email-drafter**
- Created skill: **draft_email**
- Goal created: Email Drafting Automation (GOAL-68cf8f42)

### 2. Test Request (Submitted ✅)
User sent message to Ella:
```
"Ella, please draft an email to Sarah about our upcoming project 
kickoff meeting. Keep it professional and mention we need her 
agenda items by tomorrow. The meeting is scheduled for Friday at 2 PM."
```

### 3. Expected Flow with Fix
```
┌─────────────────────────────────────────────────────────────┐
│ 1. User Request → Ella (Main Orchestrator)                  │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Ella uses delegate_to_agent tool                         │
│    - Creates task TSK-xxx                                   │
│    - Publishes AgentJobEvent to AGENT_JOBS queue            │
│    - Task state → IN_PROGRESS                               │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. SubAgentRunner picks up job                              │
│    - Agent: email-drafter                                   │
│    - Reads delegation context from storage                  │
│    - Emits AgentUpdateEvent(STARTED)                        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. LLM Loop (up to 15 iterations)                           │
│    Tool calls available:                                    │
│    - invoke_skill                                           │
│    - call_mcp_tool                                          │
│    - update_working_memory                                  │
│    - read_memory                                            │
│    - save_output ⭐ NEW FIX                                 │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. Agent Drafts Email                                       │
│    - Analyzes context                                       │
│    - Composes professional email                            │
│    - Calls save_output tool ⭐                              │
│      filename: "email-draft.md"                             │
│      content: "<email draft text>"                          │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. save_output Tool Handler                                 │
│    - Sanitizes filename                                     │
│    - Writes to storage path:                                │
│      {user_id}/agents/email-drafter/output/email-draft.md   │
│    - Returns success: {ok: true, filename: "...", ...}      │
│    - Emits audit event                                      │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 7. LLM Loop Completes                                       │
│    - No more tool calls needed                              │
│    - AgentUpdateEvent(COMPLETED) → AGENT_UPDATES            │
│    - status: "COMPLETED"                                    │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 8. ResultCollector.process_agent_result()                   │
│    - Receives AgentUpdateEvent                              │
│    - Reads output file:                                     │
│      {user_id}/agents/email-drafter/output/email-draft.md   │
│    - Includes draft content in task intelligence            │
│    - Task state → NEEDS_REVIEW                              │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 9. Orchestrator Receives Draft ✅                           │
│    - Reads task intelligence field                          │
│    - Returns draft to user in chat                          │
│    - User can review, edit, approve                         │
└─────────────────────────────────────────────────────────────┘
```

---

## The Fix: save_output Tool

### Before the Fix ❌
- Sub-agent had NO way to save deliverables
- Email draft existed only in conversation history
- ResultCollector tried to read non-existent output files
- Task updated to NEEDS_REVIEW but with no draft content
- Orchestrator couldn't retrieve the draft

### After the Fix ✅

**New Tool Definition:**
```python
_TD(
    name="save_output",
    description=(
        "Save your final work product or deliverable to an output file. "
        "Use this when you've completed your task and need to return results "
        "to the orchestrator (e.g., drafted email, analysis report, summary)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["filename", "content"],
    },
)
```

**Tool Handler:**
```python
async def _tool_save_output(self, args, job):
    filename = sanitize(args["filename"])  # Path traversal protection
    content = args["content"]
    
    path = f"{StoragePaths.agent_root(job.user_id, job.agent_id)}output/{filename}"
    await self._storage.write(path, content.encode("utf-8"))
    
    return {
        "ok": True,
        "filename": filename,
        "path": path,
        "size_bytes": len(content.encode("utf-8"))
    }
```

**System Prompt Update:**
```
## Deliverables
When you complete your task and produce a final deliverable (email draft, 
report, summary, etc.), use the `save_output` tool to save it. Examples:
  - Email drafts: save_output(filename='email-draft.md', content='...')
  - Analysis reports: save_output(filename='report.md', content='...')
  - Summaries: save_output(filename='summary.txt', content='...')
```

---

## Test Blocker

**Issue**: Anthropic API credit balance too low  
**Error**: `Error code: 400 - 'invalid_request_error', 'message': 'Your credit balance is too low...`

**Available Alternative**: Ollama is running locally (v0.32.5)
- Configured model: `ollama/llama3.2`
- API endpoint: `http://localhost:11434`
- To use: Update orchestrator LLM provider to use Ollama instead of Anthropic

---

## Verification Points

When live test completes, verify:

1. ✅ Sub-agent receives delegation
2. ✅ Sub-agent has `save_output` in available tools
3. ✅ LLM calls `save_output` after drafting email
4. ✅ File written to `{user_id}/agents/email-drafter/output/email-draft.md`
5. ✅ ResultCollector reads the file successfully
6. ✅ Task intelligence contains draft content
7. ✅ Orchestrator returns draft to user in chat

---

## Code Changes

**File**: `src/graphclaw/agent/sub_agent_runner.py`  
**Commit**: `ee3c8fb`  
**Changes**:
- Added `save_output` tool to `_build_tools()` (line ~691)
- Implemented `_tool_save_output()` handler (line ~1003)
- Updated `_dispatch_tool()` to route save_output calls (line ~730)
- Enhanced system prompt with deliverables guidance (line ~437)

---

## Next Steps

1. **Option A**: Add Anthropic API credits and retry
2. **Option B**: Switch orchestrator to use Ollama (local, free)
3. **Option C**: Mock test with direct API calls to sub-agent runner

The fix is implemented and ready. Once API access is restored, the full 
workflow will complete end-to-end with the orchestrator receiving the 
email draft from the sub-agent.
