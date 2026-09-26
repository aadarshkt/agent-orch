-- Curate the demo dataset.
--
-- Renames placeholder workflows, agents, and node ids to professional,
-- demo-ready names, and replaces obviously non-production inputs (nonsense
-- prompts, invalid repo URLs, empty scripts) with sensible equivalents.
--
-- Idempotent: every statement is keyed on a stable row UUID, so re-running is
-- a no-op. Apply with:
--   docker exec -i agent-orch-postgres psql -U postgres -d postgres < backend/scripts/2026-09-26_curate_demo_data.sql

BEGIN;

-- ── Agents ────────────────────────────────────────────────────────────────────

-- cloud_agent: "my new agent"
UPDATE agents SET params = json_build_object(
  'system_prompt', 'Classify the incoming request and extract its functional requirements.',
  'gitlab_urls', json_build_array('https://gitlab.example.com/platform/product-specs.git'),
  'model', 'gemini-pro'
)::json
WHERE id = '6d233293-43b4-456e-b2aa-8fbcf67a836e';

UPDATE agents SET name = 'Requirements Classifier'
WHERE id = '6d233293-43b4-456e-b2aa-8fbcf67a836e';

-- mcp_agent: "my new mcp agent"
UPDATE agents SET params = json_build_object(
  'system_prompt', 'Generate UI design variants from the supplied requirements.',
  'mcp_server_name', 'figma',
  'action', 'create_designs'
)::json
WHERE id = 'db8a3dc3-9f91-4167-922f-a18995461d56';

UPDATE agents SET name = 'Design Variant Generator'
WHERE id = 'db8a3dc3-9f91-4167-922f-a18995461d56';

-- script_runner: "Thik hai script run kardo"
UPDATE agents SET params = json_build_object(
  'inline_script', 'print("Validating generated artifacts...")',
  'timeout', 60
)::json
WHERE id = '7c3a45aa-d4bd-4ea8-90a5-f5d5092cf6b3';

UPDATE agents SET name = 'Validation Script Runner'
WHERE id = '7c3a45aa-d4bd-4ea8-90a5-f5d5092cf6b3';

-- ── Workflows ─────────────────────────────────────────────────────────────────

-- "naya workflow" -> Feature Intake Pipeline
UPDATE workflows SET
  name = 'Feature Intake Pipeline',
  description = 'Classify an incoming feature request, generate design variants, then run automated validation.',
  nodes = '[
    {"id": "classify", "agent_id": "6d233293-43b4-456e-b2aa-8fbcf67a836e", "requires_approval": false},
    {"id": "generate", "agent_id": "db8a3dc3-9f91-4167-922f-a18995461d56", "requires_approval": false},
    {"id": "validate", "agent_id": "7c3a45aa-d4bd-4ea8-90a5-f5d5092cf6b3", "requires_approval": false}
  ]'::json,
  edges = '[
    {"from_node": "classify", "to_node": "generate", "condition": "success"},
    {"from_node": "generate", "to_node": "validate", "condition": null}
  ]'::json,
  updated_at = now()
WHERE id = 'b6ca06f2-8b7e-45b9-a808-114d4b08965f';

-- "new workflow" -> Design Review Pipeline
UPDATE workflows SET
  name = 'Design Review Pipeline',
  description = 'Analyse requirements, produce design variants behind an approval gate, then validate the output.',
  nodes = '[
    {"id": "classify", "agent_id": "6d233293-43b4-456e-b2aa-8fbcf67a836e", "requires_approval": false},
    {"id": "generate", "agent_id": "db8a3dc3-9f91-4167-922f-a18995461d56", "requires_approval": true},
    {"id": "validate", "agent_id": "7c3a45aa-d4bd-4ea8-90a5-f5d5092cf6b3", "requires_approval": false}
  ]'::json,
  edges = '[
    {"from_node": "classify", "to_node": "generate", "condition": null},
    {"from_node": "generate", "to_node": "validate", "condition": null}
  ]'::json,
  updated_at = now()
WHERE id = 'f951a531-42ee-4fae-a82c-5c6895c2b5bf';

-- "new workflow 1" -> Release Validation Pipeline
UPDATE workflows SET
  name = 'Release Validation Pipeline',
  description = 'Requirements analysis and design generation with an approval gate, followed by automated validation.',
  nodes = '[
    {"id": "classify", "agent_id": "6d233293-43b4-456e-b2aa-8fbcf67a836e", "requires_approval": false},
    {"id": "generate", "agent_id": "db8a3dc3-9f91-4167-922f-a18995461d56", "requires_approval": true},
    {"id": "validate", "agent_id": "7c3a45aa-d4bd-4ea8-90a5-f5d5092cf6b3", "requires_approval": false}
  ]'::json,
  edges = '[
    {"from_node": "classify", "to_node": "generate", "condition": null},
    {"from_node": "generate", "to_node": "validate", "condition": null}
  ]'::json,
  updated_at = now()
WHERE id = 'c218b42b-df7e-40e1-8bb9-3ece83dde1c9';

-- "CLI Four Node Chain" -> Multi-Stage CLI Pipeline (n1..n4 -> stage_1..stage_4)
UPDATE workflows SET
  name = 'Multi-Stage CLI Pipeline',
  description = 'Four chained CLI agent stages, then publish the run to GitHub.',
  nodes = '[
    {"id": "stage_1", "agent_id": "121cdcd8-8c0d-4466-b9ce-1ca6970829e2", "requires_approval": false},
    {"id": "stage_2", "agent_id": "54591f60-9f23-43bb-826d-143328bfe0c5", "requires_approval": false},
    {"id": "stage_3", "agent_id": "10cd7bfa-0acc-4ba6-9a3f-4674b3595d7b", "requires_approval": false},
    {"id": "stage_4", "agent_id": "d4a7303d-df06-40ea-bca1-0b0d5a1bcb46", "requires_approval": false},
    {"id": "publish", "agent_id": "d6d236d4-e2e6-431c-88ff-283d387526d5", "requires_approval": false}
  ]'::json,
  edges = '[
    {"from_node": "stage_1", "to_node": "stage_2", "condition": null},
    {"from_node": "stage_2", "to_node": "stage_3", "condition": null},
    {"from_node": "stage_3", "to_node": "stage_4", "condition": null},
    {"from_node": "stage_4", "to_node": "publish", "condition": null}
  ]'::json,
  updated_at = now()
WHERE id = '339b3dbc-4a3b-4145-8cd4-d3f3ce3b14b8';

-- "OpenRouter CLI Agent Test" -> LLM Reasoning Pipeline (node llm -> reasoning)
UPDATE workflows SET
  name = 'LLM Reasoning Pipeline',
  description = 'Single CLI agent stage that calls a hosted LLM through OpenRouter.',
  nodes = '[
    {"id": "reasoning", "agent_id": "fd5b6dca-3d7d-49b6-8070-674cbb71a230", "requires_approval": false}
  ]'::json,
  edges = '[]'::json,
  updated_at = now()
WHERE id = '825487dd-f9f1-4f5a-a639-a43897012437';

-- "CLI SDLC Chain" -> Software Delivery Pipeline
UPDATE workflows SET
  name = 'Software Delivery Pipeline',
  description = 'Requirements, implementation, unit tests, and code review, published as one commit.',
  updated_at = now()
WHERE id = '67ea0828-f73e-4777-91cb-380eeeafec7c';

COMMIT;
