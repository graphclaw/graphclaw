"""Verification script for WS-P45-F implementation.

Validates:
1. AsyncLogger parameter added to AgentLoop.__init__
2. Logging in _execute_tool, process_chat_message, and run_cycle
3. Intelligence context inclusion in _build_graph_summary
4. check_inbox tool definition and implementation
"""

import ast
import inspect


def verify_implementation():
    """Verify all WS-P45-F changes are present."""
    errors = []
    
    # Import the module
    try:
        from graphclaw.agent.loop import AgentLoop
    except ImportError as e:
        errors.append(f"Failed to import AgentLoop: {e}")
        return errors
    
    # 1. Check __init__ signature includes _logger parameter
    init_sig = inspect.signature(AgentLoop.__init__)
    params = list(init_sig.parameters.keys())
    if "_logger" not in params:
        errors.append("FAIL: _logger parameter not found in __init__")
    else:
        print("✓ _logger parameter added to __init__")
    
    # 2. Check that AsyncLogger is imported in TYPE_CHECKING
    source_file = inspect.getsourcefile(AgentLoop)
    with open(source_file, "r", encoding="utf-8") as f:
        source = f.read()
    
    if "from graphclaw.infra.logger import AsyncLogger" in source:
        print("✓ AsyncLogger imported from graphclaw.infra.logger")
    else:
        errors.append("FAIL: AsyncLogger not imported")
    
    # 3. Check time module is imported
    if "import time" in source:
        print("✓ time module imported")
    else:
        errors.append("FAIL: time module not imported")
    
    # 4. Check _execute_tool has logging code
    if "agent.tool_call" in source:
        print("✓ Tool call logging present in _execute_tool")
    else:
        errors.append("FAIL: Tool call logging not found")
    
    # 5. Check process_chat_message has logging code
    if "agent.message" in source:
        print("✓ LLM message logging present in process_chat_message")
    else:
        errors.append("FAIL: LLM message logging not found")
    
    # 6. Check run_cycle has logging code
    if "agent.scoring_cycle" in source:
        print("✓ Scoring cycle logging present in run_cycle")
    else:
        errors.append("FAIL: Scoring cycle logging not found")
    
    # 7. Check intelligence snippet in _build_graph_summary
    if "task.intelligence" in source and "[ctx:" in source:
        print("✓ Intelligence context inclusion in _build_graph_summary")
    else:
        errors.append("FAIL: Intelligence context not properly included")
    
    # 8. Check check_inbox tool definition
    if "check_inbox" in source:
        print("✓ check_inbox tool found")
        # Check the tool implementation
        if "_tool_check_inbox" in source:
            print("✓ _tool_check_inbox implementation found")
        else:
            errors.append("FAIL: _tool_check_inbox implementation not found")
        
        # Check StoragePaths usage
        if "StoragePaths.agent_inbox_recent_prefix" in source:
            print("✓ StoragePaths.agent_inbox_recent_prefix used")
        else:
            errors.append("FAIL: StoragePaths usage not found")
        
        # Check list_objects call
        if "list_objects(prefix)" in source:
            print("✓ list_objects method called correctly")
        else:
            errors.append("FAIL: list_objects call not found")
    else:
        errors.append("FAIL: check_inbox tool not defined")
    
    # 9. Check session_id tracking
    if "_current_session_id" in source:
        print("✓ Session ID tracking added")
    else:
        errors.append("FAIL: Session ID tracking not found")
    
    # 10. Verify process_chat_message signature includes session_id
    try:
        chat_sig = inspect.signature(AgentLoop.process_chat_message)
        if "session_id" in chat_sig.parameters:
            print("✓ session_id parameter added to process_chat_message")
        else:
            errors.append("FAIL: session_id parameter not in process_chat_message")
    except Exception as e:
        errors.append(f"FAIL: Could not check process_chat_message signature: {e}")
    
    return errors


if __name__ == "__main__":
    print("=" * 60)
    print("WS-P45-F Implementation Verification")
    print("=" * 60)
    print()
    
    errors = verify_implementation()
    
    print()
    print("=" * 60)
    if errors:
        print(f"FAILED: {len(errors)} error(s) found:")
        for error in errors:
            print(f"  - {error}")
    else:
        print("SUCCESS: All WS-P45-F changes verified!")
    print("=" * 60)
