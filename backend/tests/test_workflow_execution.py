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


if __name__ == "__main__":
    unittest.main()

