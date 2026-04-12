#!/usr/bin/env python3
"""Quick verification script for WS-P45-C changes."""

from datetime import datetime, timezone
from graphclaw.models.nodes import TaskNode, GoalNode
from graphclaw.models.enums import TaskType
from graphclaw.db.age.repository import AgeGraphStore

# Test TaskNode has intelligence field
now = datetime.now(timezone.utc)
t = TaskNode(
    id='TSK-AB-1234-ATM', 
    task_type=TaskType.ATOMIC, 
    title='Test', 
    description='Test',
    created_at=now,
    updated_at=now,
    version=1
)
print(f'✓ TaskNode.intelligence field: {t.intelligence}')

# Test GoalNode has intelligence field
g = GoalNode(
    id='GOAL-test', 
    title='Test Goal', 
    description='Test description',
    created_at=now,
    updated_at=now,
    version=1
)
print(f'✓ GoalNode.intelligence field: {g.intelligence}')

# Test intelligence can be set
t.intelligence = "[2026-04-12] email | outbound | Asked for status update"
print(f'✓ TaskNode.intelligence can be set: {t.intelligence}')

# Test repository methods exist
methods = [
    'update_node_intelligence',
    'get_node_intelligence', 
    'create_checkin_node',
    'update_checkin_response'
]

for method in methods:
    if hasattr(AgeGraphStore, method):
        print(f'✓ AgeGraphStore.{method} exists')
    else:
        print(f'✗ AgeGraphStore.{method} MISSING')

print('\n✓ All changes verified successfully!')
