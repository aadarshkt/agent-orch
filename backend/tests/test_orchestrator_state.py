"""
Test that a failing node preserves upstream state (no dropped messages/artifacts).
"""
import unittest
from unittest import mock

from src.engine.orchestrator import make_node_func


class _FailingExecutor:
    async def execute(self, node_config, state, context):
        raise RuntimeError("boom")


class TestNodeStatePreservation(unittest.TestCase):
    def test_failure_preserves_upstream_state(self):
        func = make_node_func(
            {"id": "n1"},
            {"name": "n1", "node_type_key": "cli_agent", "params": {}},
            {"executor_key": "cli_agent"},
            None,
        )
        prior = {
            "messages": [{"role": "user", "content": "hi"}],
            "artifacts": {"upstream_output": "x"},
            "status": "completed",
        }

        with mock.patch(
            "src.engine.orchestrator.get_executor", return_value=_FailingExecutor()
        ):
            result = func(prior)

        self.assertEqual(result["messages"], prior["messages"])
        self.assertEqual(result["artifacts"], prior["artifacts"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "boom")
        self.assertEqual(result["current_step"], "n1")


if __name__ == "__main__":
    unittest.main()
