import unittest
import uuid
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, WorkflowExecutionModel, WorkflowModel
from src.engine.orchestrator import evaluate_condition
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from typing import TypedDict


class TestConditionalRouting(unittest.TestCase):
    def test_evaluate_condition_keywords(self):
        # Empty / None
        self.assertTrue(evaluate_condition("", {}))
        self.assertTrue(evaluate_condition(None, {}))

        # Success / Completed
        self.assertTrue(evaluate_condition("success", {"status": "completed", "error": None}))
        self.assertTrue(evaluate_condition("completed", {"status": "success", "error": None}))
        self.assertFalse(evaluate_condition("success", {"status": "failed", "error": "error msg"}))
        self.assertFalse(evaluate_condition("success", {"status": "completed", "error": "something broke"}))

        # Failure / Failed
        self.assertTrue(evaluate_condition("failed", {"status": "failed"}))
        self.assertTrue(evaluate_condition("failure", {"status": "error"}))
        self.assertTrue(evaluate_condition("error", {"status": "completed", "error": "runtime issue"}))
        self.assertFalse(evaluate_condition("failed", {"status": "completed", "error": None}))

        # Approval
        self.assertTrue(evaluate_condition("approved", {"approval_status": "approved"}))
        self.assertFalse(evaluate_condition("approved", {"approval_status": "pending"}))
        self.assertTrue(evaluate_condition("rejected", {"approval_status": "rejected"}))

    def test_evaluate_condition_expressions(self):
        state = {
            "status": "completed",
            "error": None,
            "artifacts": {"doc_count": 5},
            "messages": ["msg1", "msg2"],
        }
        self.assertTrue(evaluate_condition("status == 'completed'", state))
        self.assertFalse(evaluate_condition("status == 'failed'", state))
        self.assertTrue(evaluate_condition("artifacts.get('doc_count') == 5", state))
        self.assertTrue(evaluate_condition("len(messages) == 2", state))
        self.assertFalse(evaluate_condition("non_existent_var == 1", state))

    def test_conditional_graph_branching(self):
        class State(TypedDict):
            status: str
            path_taken: str

        def node_start(s: State):
            return {"status": "completed", "path_taken": "start"}

        def node_success(s: State):
            return {"status": "completed", "path_taken": "success_branch"}

        def node_failure(s: State):
            return {"status": "failed", "path_taken": "failure_branch"}

        builder = StateGraph(State)
        builder.add_node("start", node_start)
        builder.add_node("on_success", node_success)
        builder.add_node("on_failure", node_failure)

        builder.add_edge(START, "start")

        def router(s: State):
            if evaluate_condition("success", s):
                return "on_success"
            elif evaluate_condition("failed", s):
                return "on_failure"
            return END

        builder.add_conditional_edges(
            "start",
            router,
            {"on_success": "on_success", "on_failure": "on_failure", END: END},
        )
        builder.add_edge("on_success", END)
        builder.add_edge("on_failure", END)

        graph = builder.compile()
        events = list(graph.stream({"status": "init", "path_taken": ""}))
        final_state = events[-1]
        self.assertIn("on_success", final_state)
        self.assertEqual(final_state["on_success"]["path_taken"], "success_branch")


class TestTerminalStatusInference(unittest.TestCase):
    """A graph that drains to END after a failed node is not a success.

    Without this check a run whose publish step threw still reports
    `completed`, which makes the high-level status view lie to the user.
    """

    def _drain(self, node_return):
        class State(TypedDict):
            status: str
            error: str

        builder = StateGraph(State)
        builder.add_node("work", lambda s: node_return)
        builder.add_edge(START, "work")
        builder.add_edge("work", END)
        graph = builder.compile(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "drain"}}
        list(graph.stream({"status": "init", "error": ""}, cfg))
        return graph, graph.get_state(cfg).values

    def test_drained_graph_reports_its_own_outcome(self):
        from src.api.routes.workflows import _terminal_status

        self.assertEqual(
            _terminal_status({"status": "completed", "error": None}),
            ("completed", None),
        )

        self.assertEqual(
            _terminal_status({"status": "failed", "error": "push denied"}),
            ("failed", "push denied"),
        )

        # An error with a stale success status still wins — the error is truth.
        self.assertEqual(
            _terminal_status({"status": "completed", "error": "boom"}),
            ("failed", "boom"),
        )

    def test_a_failed_node_really_does_drain_to_end(self):
        """Guards the assumption behind the check: the graph ends normally even
        when the node failed, which is exactly why END alone is not proof."""
        _, final = self._drain({"status": "failed", "error": "push denied"})
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["error"], "push denied")


from sqlalchemy.pool import StaticPool
import src.db.session


class TestWorkflowExecutionAndResume(unittest.TestCase):
    def setUp(self):
        # Create SQLite in-memory database with StaticPool for multi-threaded testing
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.db = self.SessionLocal()
        self.orig_session_local = src.db.session.SessionLocal
        src.db.session.SessionLocal = self.SessionLocal

    def tearDown(self):
        src.db.session.SessionLocal = self.orig_session_local
        self.db.close()

    def test_workflow_execution_model_crud(self):
        thread_id = str(uuid.uuid4())
        workflow_id = str(uuid.uuid4())

        # 1. Create execution record
        execution = WorkflowExecutionModel(
            id=str(uuid.uuid4()),
            thread_id=thread_id,
            workflow_id=workflow_id,
            status="running",
            current_step="start",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(execution)
        self.db.commit()

        # 2. Query execution
        rec = self.db.query(WorkflowExecutionModel).filter_by(thread_id=thread_id).first()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, "running")
        self.assertEqual(rec.current_step, "start")

        # 3. Update execution when paused
        rec.status = "paused"
        rec.current_step = "step_approval"
        self.db.commit()

        rec2 = self.db.query(WorkflowExecutionModel).filter_by(thread_id=thread_id).first()
        self.assertEqual(rec2.status, "paused")
        self.assertEqual(rec2.current_step, "step_approval")

        # 4. Update execution when completed
        rec2.status = "completed"
        rec2.completed_at = datetime.utcnow()
        self.db.commit()

        rec3 = self.db.query(WorkflowExecutionModel).filter_by(thread_id=thread_id).first()
        self.assertEqual(rec3.status, "completed")
        self.assertIsNotNone(rec3.completed_at)

    def test_langgraph_hitl_pause_and_resume(self):
        class StepState(TypedDict):
            counter: int
            approval_status: str

        def step_1(state: StepState):
            return {"counter": state["counter"] + 1}

        def step_2(state: StepState):
            return {"counter": state["counter"] + 10}

        builder = StateGraph(StepState)
        builder.add_node("step_1", step_1)
        builder.add_node("step_2", step_2)
        builder.add_edge(START, "step_1")
        builder.add_edge("step_1", "step_2")
        builder.add_edge("step_2", END)

        checkpointer = MemorySaver()
        # Pause before step_2
        graph = builder.compile(checkpointer=checkpointer, interrupt_before=["step_2"])

        config = {"configurable": {"thread_id": "thread-test-hitl"}}

        # Initial run: should pause before step_2
        init_events = list(graph.stream({"counter": 0, "approval_status": "pending"}, config))
        state_after_init = graph.get_state(config)
        self.assertEqual(state_after_init.next, ("step_2",))
        self.assertEqual(state_after_init.values["counter"], 1)

        # Resume run: simulate human approval
        graph.update_state(config, {"approval_status": "approved"})
        resume_events = list(graph.stream(None, config))
        state_after_resume = graph.get_state(config)
        self.assertEqual(state_after_resume.next, ())
        self.assertEqual(state_after_resume.values["counter"], 11)
        self.assertEqual(state_after_resume.values["approval_status"], "approved")

    def test_api_endpoints_execute_status_resume(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.routes import workflows
        from src.db.session import get_db

        app = FastAPI()
        app.include_router(workflows.router, prefix="/workflows")
        app.state.checkpointer = MemorySaver()

        # Override DB dependency
        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db

        from src.db.models import NodeTypeModel, AgentModel

        nt = NodeTypeModel(
            id=str(uuid.uuid4()),
            type_key="cloud_agent",
            display_name="Cloud Agent",
            executor_key="cloud_agent",
            config_schema={},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(nt)

        agent = AgentModel(
            id="a1",
            name="Test Agent",
            node_type_key="cloud_agent",
            params={},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(agent)

        # Seed workflow
        w_id = str(uuid.uuid4())
        workflow = WorkflowModel(
            id=w_id,
            name="Test API Workflow",
            description="Testing API routes",
            hitl_enabled=True,
            nodes=[{"id": "step_1", "agent_id": "a1", "requires_approval": False}],
            edges=[],
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(workflow)
        self.db.commit()

        client = TestClient(app)

        # 1. Execute
        exec_res = client.post(f"/workflows/{w_id}/execute")
        self.assertEqual(exec_res.status_code, 200)
        data = exec_res.json()
        self.assertEqual(data["status"], "started")
        thread_id = data["thread_id"]

        # 2. Get status
        status_res = client.get(f"/workflows/{thread_id}/status")
        self.assertEqual(status_res.status_code, 200)
        status_data = status_res.json()
        self.assertEqual(status_data["thread_id"], thread_id)
        self.assertEqual(status_data["workflow_id"], w_id)
        self.assertIn(status_data["status"], ("running", "completed", "paused"))

        # 3. Simulate paused state in DB to test resume endpoint
        rec = self.db.query(WorkflowExecutionModel).filter_by(thread_id=thread_id).first()
        rec.status = "paused"
        self.db.commit()

        resume_res = client.post(f"/workflows/{thread_id}/resume")
        self.assertEqual(resume_res.status_code, 200)
        self.assertEqual(resume_res.json()["status"], "resumed")

        # 4. List executions
        list_res = client.get(f"/workflows/{w_id}/executions")
        self.assertEqual(list_res.status_code, 200)
        executions = list_res.json()
        self.assertGreaterEqual(len(executions), 1)
        self.assertEqual(executions[0]["thread_id"], thread_id)

        # 5. Persisted run state (read from the checkpointer)
        state_res = client.get(f"/workflows/{thread_id}/state")
        self.assertEqual(state_res.status_code, 200)
        state_data = state_res.json()
        self.assertEqual(state_data["thread_id"], thread_id)
        self.assertEqual(state_data["workflow_id"], w_id)
        self.assertIn("step_1", state_data["nodes"])
        self.assertIn("output", state_data["nodes"]["step_1"])
        self.assertIn("next", state_data)

        # 6. Unknown thread -> 404
        missing_res = client.get("/workflows/does-not-exist/state")
        self.assertEqual(missing_res.status_code, 404)


class TestRunStateSummary(unittest.TestCase):
    """The checkpointer snapshot flattens into per-node outputs + artifacts."""

    def _config(self):
        from src.config.schema import WorkflowConfig, NodeConfig

        return WorkflowConfig(
            name="wf",
            nodes=[
                NodeConfig(id="n1", agent_id="a1"),
                NodeConfig(id="n2", agent_id="a1"),
            ],
            edges=[],
        )

    def test_flattens_node_artifacts(self):
        from src.api.routes.workflows import _summarize_run_state

        values = {
            "status": "completed",
            "approval_status": "approved",
            "artifacts": {
                "n1_output": "hello from n1",
                "n1_artifacts": ["/ws/n1/ai_review/report.md"],
                "n1_exit_code": 0,
                "n1_workspace": "/ws/n1",
                "n1_commit": "Pushed to o/r@main",
                "n1_commit_url": "https://github.com/o/r/commit/abc123",
            },
        }
        summary = _summarize_run_state(self._config(), values, ("n3",))

        self.assertEqual(summary["graph_status"], "completed")
        self.assertEqual(summary["approval_status"], "approved")
        self.assertEqual(summary["next"], ["n3"])

        n1 = summary["nodes"]["n1"]
        self.assertEqual(n1["output"], "hello from n1")
        self.assertEqual(n1["artifacts"], ["/ws/n1/ai_review/report.md"])
        self.assertEqual(n1["exit_code"], 0)
        self.assertEqual(n1["commit"], "Pushed to o/r@main")
        self.assertEqual(n1["commit_url"], "https://github.com/o/r/commit/abc123")

    def test_missing_node_artifacts_are_empty(self):
        from src.api.routes.workflows import _summarize_run_state

        summary = _summarize_run_state(self._config(), {}, ())

        self.assertEqual(summary["next"], [])
        self.assertIsNone(summary["nodes"]["n1"]["output"])
        self.assertEqual(summary["nodes"]["n1"]["artifacts"], [])

    def test_step_summary_reports_progress(self):
        """The high-level view answers "where is this run" from what the
        checkpoint holds, so it survives a reload and needs no event replay."""
        from src.api.routes.workflows import _summarize_run_state

        values = {
            "artifacts": {
                "n1_output": "done",
                "n1_exit_code": 0,
                "n1_artifacts": ["/ws/a.md"],
                "n1_workspace": "/ws/n1",
            }
        }
        summary = _summarize_run_state(self._config(), values, ("n2",), run_status="paused")
        steps = {s["id"]: s for s in summary["steps"]}

        self.assertEqual([s["id"] for s in summary["steps"]], ["n1", "n2"])
        self.assertEqual(steps["n1"]["status"], "completed")
        self.assertEqual(steps["n1"]["exit_code"], 0)
        self.assertEqual(steps["n1"]["artifact_count"], 1)
        # n2 is where the gate is: the run is waiting on it, and it has not run.
        self.assertEqual(steps["n2"]["status"], "waiting")
        self.assertIsNone(steps["n2"]["exit_code"])

    def test_step_summary_detects_run_without_any_artifacts(self):
        """A cloud_agent node stamps no artifacts at all, so an artifact scan
        alone reports a finished step as still pending. `completed_steps`, which
        the dispatcher writes for every node, is the authoritative signal."""
        from src.api.routes.workflows import _summarize_run_state

        values = {"completed_steps": ["n1"], "artifacts": {}}
        steps = {
            s["id"]: s["status"]
            for s in _summarize_run_state(
                self._config(), values, ("n2",), run_status="paused"
            )["steps"]
        }
        self.assertEqual(steps["n1"], "completed")
        self.assertEqual(steps["n2"], "waiting")

    def test_step_summary_falls_back_to_artifacts(self):
        """Checkpoints written before completed_steps existed must still render
        sensibly, so an artifact the node stamped still counts as having run."""
        from src.api.routes.workflows import _summarize_run_state

        values = {"artifacts": {"n1_commit": "Pushed to o/r@abc123"}}
        steps = {
            s["id"]: s["status"]
            for s in _summarize_run_state(
                self._config(), values, ("n2",), run_status="running"
            )["steps"]
        }
        self.assertEqual(steps["n1"], "completed")
        self.assertEqual(steps["n2"], "running")

    def test_dispatcher_records_every_finished_node(self):
        """The dispatcher is the only chokepoint every node passes through, so
        it is what makes progress knowable regardless of executor."""
        from src.engine.orchestrator import make_node_func
        from src.registry.base_executor import ExecutionContext

        seen = []

        class Recorder:
            async def execute(self, node_config, state, context):
                seen.append(node_config["id"])
                # An executor that records nothing of its own.
                return {**state, "status": "completed"}

        import src.engine.orchestrator as orch

        original = orch.get_executor
        orch.get_executor = lambda key: Recorder()
        try:
            func = make_node_func(
                {"id": "n1", "agent_id": "a1"},
                {"name": "A", "node_type_key": "cloud_agent", "params": {}},
                {"executor_key": "cloud_agent"},
                ExecutionContext(thread_id="t", event_bus=None, checkpointer=None),
            )
            out = func({"messages": [], "artifacts": {}})
        finally:
            orch.get_executor = original

        self.assertEqual(seen, ["n1"])
        self.assertEqual(out["completed_steps"], ["n1"])

    def test_dispatcher_appends_without_duplicating(self):
        from src.engine.orchestrator import make_node_func
        from src.registry.base_executor import ExecutionContext

        class Recorder:
            async def execute(self, node_config, state, context):
                return {**state, "completed_steps": ["n1"]}

        import src.engine.orchestrator as orch

        original = orch.get_executor
        orch.get_executor = lambda key: Recorder()
        try:
            func = make_node_func(
                {"id": "n1", "agent_id": "a1"},
                {"name": "A", "node_type_key": "cloud_agent", "params": {}},
                {"executor_key": "cloud_agent"},
                ExecutionContext(thread_id="t", event_bus=None, checkpointer=None),
            )
            out = func({"messages": [], "artifacts": {}, "completed_steps": ["n1"]})
        finally:
            orch.get_executor = original

        self.assertEqual(out["completed_steps"], ["n1"])

    def test_step_summary_marks_failures_and_pending(self):
        from src.api.routes.workflows import _summarize_run_state

        values = {"artifacts": {"n1_exit_code": 2, "n1_output": "boom"}}
        steps = {
            s["id"]: s["status"]
            for s in _summarize_run_state(
                self._config(), values, ("n2",), run_status="running"
            )["steps"]
        }
        self.assertEqual(steps["n1"], "failed")
        self.assertEqual(steps["n2"], "running")

        # A node the graph has not reached and is not waiting on is pending.
        empty = _summarize_run_state(self._config(), {}, ("n1",), run_status="running")["steps"]
        self.assertEqual({s["id"]: s["status"] for s in empty}, {"n1": "running", "n2": "pending"})

    def test_step_summary_marks_everything_pending_without_artifacts(self):
        from src.api.routes.workflows import _summarize_run_state

        steps = _summarize_run_state(self._config(), {}, (), run_status="paused")["steps"]
        self.assertEqual({s["status"] for s in steps}, {"pending"})

    def test_step_summary_marks_the_failing_node(self):
        """A node that throws records no artifacts, but the graph names it in
        `current_step`. Without this the failed node reads as 'pending' and the
        timeline points at the wrong step."""
        from src.api.routes.workflows import _summarize_run_state

        values = {
            "error": "No workspace found for nodes 'review'",
            "current_step": "n2",
            "artifacts": {"n1_exit_code": 0, "n1_output": "ok"},
        }
        steps = {
            s["id"]: s["status"]
            for s in _summarize_run_state(
                self._config(), values, (), run_status="failed"
            )["steps"]
        }
        self.assertEqual(steps["n1"], "completed")
        self.assertEqual(steps["n2"], "failed")

    def test_pending_steps_hidden_for_terminal_runs(self):
        """A rejected run must not keep advertising a pending node under `next`
        — otherwise /state reports a step that will never run and the UI shows an
        approval gate for a run that is already over. The graph itself is left
        untouched, so `next` is suppressed at the summary layer."""
        from src.api.routes.workflows import _summarize_run_state

        self.assertEqual(
            _summarize_run_state(self._config(), {}, ("n2",), run_status="rejected")["next"],
            [],
        )
        self.assertEqual(
            _summarize_run_state(self._config(), {}, ("n2",), run_status="failed")["next"],
            [],
        )
        self.assertEqual(
            _summarize_run_state(self._config(), {}, ("n2",), run_status="completed")["next"],
            [],
        )

    def test_pending_steps_kept_while_run_is_live(self):
        from src.api.routes.workflows import _summarize_run_state

        for status in ("running", "paused"):
            self.assertEqual(
                _summarize_run_state(self._config(), {}, ("n2",), run_status=status)["next"],
                ["n2"],
                msg=status,
            )

    def test_review_decision_is_surfaced(self):
        """The verdict must be readable from persisted state, not just the event
        stream — otherwise a reload loses the record of who signed off."""
        from src.api.routes.workflows import _summarize_run_state

        summary = _summarize_run_state(
            self._config(),
            {
                "approval_status": "rejected",
                "approval_reviewer": "ada",
                "approval_comment": "output was truncated",
            },
            ("n2",),
        )

        self.assertEqual(summary["approval_status"], "rejected")
        self.assertEqual(summary["approval_reviewer"], "ada")
        self.assertEqual(summary["approval_comment"], "output was truncated")
        self.assertEqual(summary["next"], ["n2"])


class TestReviewGate(unittest.TestCase):
    """The approval gate is driven by persisted state, and reject is a real
    terminal outcome rather than an unreachable branch."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.db = self.SessionLocal()
        self.orig_session_local = src.db.session.SessionLocal
        src.db.session.SessionLocal = self.SessionLocal

        from src.db.models import NodeTypeModel, AgentModel

        self.db.add(
            NodeTypeModel(
                id=str(uuid.uuid4()),
                type_key="cloud_agent",
                display_name="Cloud Agent",
                executor_key="cloud_agent",
                config_schema={},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        self.db.add(
            AgentModel(
                id="a1",
                name="Test Agent",
                node_type_key="cloud_agent",
                params={},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        self.workflow_id = str(uuid.uuid4())
        self.db.add(
            WorkflowModel(
                id=self.workflow_id,
                name="Review Gate Workflow",
                description="",
                hitl_enabled=True,
                nodes=[{"id": "step_1", "agent_id": "a1", "requires_approval": False}],
                edges=[],
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        self.db.commit()

    def tearDown(self):
        src.db.session.SessionLocal = self.orig_session_local
        self.db.close()

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.routes import workflows
        from src.db.session import get_db

        app = FastAPI()
        app.include_router(workflows.router, prefix="/workflows")
        app.state.checkpointer = MemorySaver()

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        return TestClient(app)

    def _execution(self, status):
        thread_id = str(uuid.uuid4())
        self.db.add(
            WorkflowExecutionModel(
                id=str(uuid.uuid4()),
                thread_id=thread_id,
                workflow_id=self.workflow_id,
                status=status,
                current_step="step_1",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        self.db.commit()
        return thread_id

    def test_reject_marks_run_terminal_and_records_who(self):
        thread_id = self._execution("paused")
        client = self._client()

        res = client.post(
            f"/workflows/{thread_id}/review",
            json={
                "decision": "reject",
                "comment": "the diff is too large to ship",
                "reviewer": "ada",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["decision"], "rejected")

        rec = self.db.query(WorkflowExecutionModel).filter_by(thread_id=thread_id).first()
        self.assertEqual(rec.status, "rejected")
        self.assertIsNotNone(rec.completed_at)
        self.assertIn("too large", rec.error)

    def test_approve_resumes_the_run(self):
        thread_id = self._execution("paused")
        client = self._client()

        res = client.post(
            f"/workflows/{thread_id}/review",
            json={"decision": "approve", "reviewer": "grace"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["decision"], "approved")

    def test_review_requires_a_paused_run(self):
        """The gate is derived from the execution row, so a run that is merely
        running must not be reviewable."""
        for status in ("running", "completed", "failed"):
            thread_id = self._execution(status)
            client = self._client()
            res = client.post(
                f"/workflows/{thread_id}/review", json={"decision": "approve"}
            )
            self.assertEqual(res.status_code, 400, msg=f"status={status}")

    def test_second_review_is_refused(self):
        """A double-clicked Approve must not queue the graph twice.

        Under TestClient the background task completes before the response is
        returned, so the second call is rejected by the `paused` precondition.
        A live server has a narrower window where both calls pass that check,
        which is what _claim_paused_execution closes (tested below).
        """
        thread_id = self._execution("paused")
        client = self._client()

        first = client.post(f"/workflows/{thread_id}/review", json={"decision": "approve"})
        self.assertEqual(first.status_code, 200)

        second = client.post(f"/workflows/{thread_id}/review", json={"decision": "approve"})
        self.assertEqual(second.status_code, 400)
        self.assertIn("not waiting for approval", second.json()["detail"])

    def test_claim_is_one_shot(self):
        """The claim is the arbiter for the concurrent case: exactly one caller
        can win, no matter how many raced past the precondition check."""
        from src.api.routes.workflows import _claim_paused_execution

        thread_id = self._execution("paused")

        self.assertTrue(_claim_paused_execution(thread_id))
        self.assertFalse(_claim_paused_execution(thread_id))
        self.assertFalse(_claim_paused_execution(thread_id))

        rec = self.db.query(WorkflowExecutionModel).filter_by(thread_id=thread_id).first()
        self.assertEqual(rec.status, "running")

    def test_claim_ignores_runs_that_are_not_paused(self):
        from src.api.routes.workflows import _claim_paused_execution

        self.assertFalse(_claim_paused_execution(str(uuid.uuid4())))

    def test_resume_endpoint_still_approves(self):
        """The original approve-only endpoint is an alias and must keep working."""
        thread_id = self._execution("paused")
        client = self._client()

        res = client.post(f"/workflows/{thread_id}/resume")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["decision"], "approved")

    def test_rejected_is_reachable_from_state(self):
        """evaluate_condition has always supported 'rejected'; a reject must be
        able to produce it, otherwise the branch is dead code."""
        self.assertTrue(
            evaluate_condition("rejected", {"approval_status": "rejected"})
        )


class TestEdgeKeyNormalization(unittest.TestCase):
    """Regression: imported YAML edges were stored as {'from','to'} while the
    resolver read 'from_node'/'to_node'. The KeyError escaped the HTTPException
    handler, so the run stayed 'running' forever with no error."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.db = self.SessionLocal()
        self.orig_session_local = src.db.session.SessionLocal
        src.db.session.SessionLocal = self.SessionLocal

        from src.db.models import NodeTypeModel, AgentModel

        self.db.add(NodeTypeModel(
            id=str(uuid.uuid4()), type_key="cli_agent", display_name="CLI Agent",
            executor_key="cli_agent", config_schema={},
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))
        self.db.add(AgentModel(
            id="a1", name="edge-agent", node_type_key="cli_agent",
            params={"runtime": "test-cli"},
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))
        self.db.commit()

    def tearDown(self):
        src.db.session.SessionLocal = self.orig_session_local
        self.db.close()

    def _workflow(self, edges):
        wf = WorkflowModel(
            id=str(uuid.uuid4()),
            name=f"wf-{uuid.uuid4()}",
            description=None,
            hitl_enabled=False,
            nodes=[
                {"id": "n1", "agent_id": "a1", "requires_approval": False},
                {"id": "n2", "agent_id": "a1", "requires_approval": False},
            ],
            edges=edges,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(wf)
        self.db.commit()
        return wf

    def test_resolve_accepts_canonical_and_legacy_edge_keys(self):
        from src.api.routes.workflows import _resolve_workflow

        shapes = [
            [{"from_node": "n1", "to_node": "n2", "condition": None}],
            [{"from": "n1", "to": "n2", "condition": None}],
        ]
        for edges in shapes:
            wf = self._workflow(edges)
            config, _, _ = _resolve_workflow(wf.id, self.db)
            self.assertEqual(config.edges[0].from_node, "n1")
            self.assertEqual(config.edges[0].to_node, "n2")

    def test_import_writes_canonical_edge_keys(self):
        from src.api.routes.workflows import _import_workflow_spec, _resolve_workflow
        from src.config.schema import (
            AgentDef, EdgeConfig, RuntimeConfig, WorkflowGraphSpec,
            WorkflowNodeSpec, WorkflowSpec,
        )
        from src.registry.runtime_registry import register_runtimes

        register_runtimes([RuntimeConfig(name="test-cli", kind="cli", image="img")])

        spec = WorkflowSpec(
            runtimes=[],
            agents={"n1": AgentDef(type="cli_agent", runtime="test-cli", inputs={})},
            workflow=WorkflowGraphSpec(
                name=f"imported-{uuid.uuid4()}",
                hitl_enabled=False,
                nodes=[
                    WorkflowNodeSpec(id="n1", agent="n1"),
                    WorkflowNodeSpec(id="n2", agent="n1"),
                ],
                edges=[EdgeConfig(**{"from": "n1", "to": "n2"})],
            ),
        )
        wf = _import_workflow_spec(spec, self.db)

        self.assertEqual(wf.edges[0]["from_node"], "n1")
        self.assertEqual(wf.edges[0]["to_node"], "n2")

        config, _, _ = _resolve_workflow(wf.id, self.db)
        self.assertEqual(config.edges[0].from_node, "n1")
        self.assertEqual(config.edges[0].to_node, "n2")


if __name__ == "__main__":
    unittest.main()

