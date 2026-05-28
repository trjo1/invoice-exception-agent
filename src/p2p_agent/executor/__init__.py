"""Action executor — consumes approved HITL items and simulates downstream calls.

Mock today (logs what *would* have been done); the same interface will swap
in real SAP/Ariba/ServiceNow/Email backends once credentials land.
"""

from p2p_agent.executor.action_executor import ActionExecutor, ExecutorError

__all__ = ["ActionExecutor", "ExecutorError"]
