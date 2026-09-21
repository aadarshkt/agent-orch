# Taste
- Prefers tightly condensed, summary-style output (e.g., one-to-two-line bullets) rather than long, verbose prose. Confidence: 0.95
- Prefers configuration-as-data (named presets/registries, e.g. YAML runtimes) so new tools/agents can be added quickly via data changes without code, seed, or frontend edits. Confidence: 0.8
- Separates per-run inputs (provided via frontend/dynamic forms, e.g. GitLab URLs, branch, prompt) from preconfigured server-side environment (Docker image, binary, skills, packages). Confidence: 0.85
- Prefers a single, self-contained YAML file as the source of truth per workflow, defining complete dependencies (runtimes, agents, workflow graph) in one place. Confidence: 0.9
- Wants a one-to-one mapping between YAML config and UI components so forms render automatically from the schema. Confidence: 0.7
- Draws the code/data boundary at the executor class: it defines the agent type's shape (input_schema fields), while individual agent instances in YAML only supply values. Adding an instance is a data change; adding a new field is a one-time schema edit. Confidence: 0.7
- Wants explicit, verifiable inter-node artifact/dependency handoffs (e.g. a downstream node referencing the prior node's commit hash via artifacts, or a declared depends_on/artifact_ref) rather than relying only on implicit execution ordering. Confidence: 0.7
- Prefers to build conceptual understanding of a design/architecture first (mental models, "how does this work", step-by-step explanations) before committing to implementation, rather than jumping straight to a solution. Confidence: 0.7
- Wants end-to-end, step-by-step documentation that covers the complete lifecycle of a feature (create, register, use across UI/YAML, migration/seeding) rather than partial or concept-only write-ups. Confidence: 0.7
- Prefers explicit, ordered/phased task lists that break remaining work into a clear sequence rather than a flat or unprioritized set. Confidence: 0.7
- Prefers happy-path-first scaffolding: defer validation and hardening (use placeholder/stub entries for now) until the core flow works. Confidence: 0.7
- Treats Docker image packaging (build/push of a CLI binary + runtime + packages) as part of CLI agent onboarding, not just defining runtime presets. Confidence: 0.6
 Prefers the assistant to proactively generate additional open design questions/edge cases ("create more such questions") rather than only answering the question asked. Confidence: 0.7
