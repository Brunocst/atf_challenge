# Production note — NovaMart copilot on Google's enterprise stack

**Recommendation: hybrid.** Use **Gemini Enterprise** as the employee-facing front
door, and a **custom ADK agent on Agent Runtime** for the two things the packaged
assistant does not express well: live order lookup and the escalation workflow.
Either/or is the wrong framing — the enterprise product covers identity, connectors
and governance for free, and the domain logic is NovaMart's own.

## 1. Gemini Enterprise versus a custom build

Gemini Enterprise already provides what the prototype fakes: 100+ connectors
(Google Drive, Confluence, SharePoint, Jira, ServiceNow), per-user permission
enforcement, audit trails, and per-project budget caps. It indexes sources **in
place** rather than copying documents into a side index, and it runs under each
employee's existing permissions instead of a shared service account. That is the
cheapest path to the "policy search" half of the problem, and it removes an entire
class of access-control bugs.

What it does not do is NovaMart's domain logic. So the split is:

- **Policies** — sync from Drive or a connector into an **Agent Search data store**.
  Do not rebuild a side index the prototype owns; that is how policy drift happens.
- **The copilot agent** — register the ADK agent explicitly and give it its own tools:
  `VertexAiSearchTool` for policy retrieval, plus the order API. **A custom agent added
  to a Gemini Enterprise app does not inherit the app's connectors** — this is the
  detail most builds get wrong, and it means the agent needs its own retrieval binding.
- **Orders** — stay in the order system, fetched live under the employee's identity.
  Never embedded, never indexed.

Note the prototype's hand-rolled retrieval becomes unnecessary here: `VertexAiSearchTool`
returns grounded responses with `groundingMetadata` — `groundingChunks`,
`groundingSupports`, and `retrievalQueries` — giving sentence-level citations natively.
That is exactly the payload the escalation packet needs.

## 2. Connecting the real data

Documents: connector → Agent Search data store, refreshed on change so a policy edit is
live in minutes. Orders: expose the order service through an authenticated internal
service (Apigee, or Cloud Run behind IAP) rather than direct database access, and scope
every lookup to the employee's store so an associate at S-118 cannot read S-204.
Reuse the prototype's contract deliberately — the order tool returns **facts plus a
computed age, never an eligibility verdict** — so a policy change remains a document
change and never a service release.

## 3. Security and governance

- **Workforce SSO**, with permissions enforced at retrieval time (ACL-aware) so a
  result a user cannot open is never returned.
- **No PII in prompts or logs.** The prototype already keeps `store_id` and `order_id`
  and no customer identifiers; keep that rule and log the order id only.
- **VPC Service Controls**, **CMEK**, and EU data residency if required.
- **Model Armor** for prompt injection and data leakage. This matters more than usual
  here: free-text order notes are attacker-controlled, and the prototype includes an
  injection payload in a note precisely because that is the realistic vector. Tool
  output must be treated as data, never as instructions.
- **Agent Identity** for audit; escalation packets retained with a stated TTL.
- **Tiering.** The free tier may use prompts to improve Google's products; the paid
  enterprise tier does not. That distinction should be confirmed in writing before any
  real customer data touches this.

## 4. Evaluating and monitoring over time

- **Promote the golden set to a CI gate — but not as it stands.** Re-run it on every
  prompt, model, or policy-doc change; the runner is deterministic and exits non-zero on
  failure, which is what makes it usable as a gate at all. The measurement around it is
  not deterministic: two full samples of the *same* configuration scored 12/18 and 13/18,
  with one case flipping either way. A naive gate would therefore fail builds on noise and
  teach the team to ignore it. Pin `temperature=0`, run N repeats, and block on the
  **stable** failure set — the handful of cases that failed in every sample — while the
  noisy ones are reported but non-blocking until they have enough samples to be trusted.
- **Agent Simulation before release → Agent Observability after.** Sample real
  escalations for human review weekly. Because the hand-off is asynchronous, also track
  time-to-first-human-response and how many packets are resolved without being reopened:
  a queue that never closes is a black hole with good UX.
- **Watch four numbers**: citation rate, escalate rate, tool error rate, and token
  cost/latency per turn (ADK reports these as efficiency criteria with no extra model
  calls). Set a budget cap per project.
- **Two alerts that matter, and they point in opposite directions.** Repeat escalations
  are **doc gaps** — feed them into the doc-gap report and write the missing policy.
  An **escalate-rate collapse** is the dangerous one: it usually means the model started
  answering outside the corpus, which looks like an improvement on a dashboard.

## 5. Rollout

Pilot in a few stores, then measure the only number that matters: **ops ticket
deflection**. Answers served is a vanity metric — the business problem is the load on
the operations team, so success is questions that no longer reach them.
