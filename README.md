# Okta-Protected A2A / A2UI Agent on Vertex AI Agent Engine

This repository implements a secure, Okta-authenticated **Agent-to-Agent (A2A)** and **Agent-Driven User Interface (A2UI)** time retrieval agent, refactored to deploy natively on **Vertex AI Agent Engine** and integrate with **Gemini Enterprise**.

It solves the "Token Gap"—illustrating exactly how Gemini Enterprise-provisioned third-party OAuth tokens are intercepted inside the container's request context, persistent session cached, and delivered dynamically to tools using the Google Agent Development Kit (ADK).

---

## 🗺️ Project Structure & Blueprint

*   [`agent.py`](agent.py): Hages local geocoding and timezone tools, the debug auth session tool, and the `OktaTimeAgent` class.
*   [`agent_executor.py`](agent_executor.py): Implements `AdkAgentToA2AExecutor` which parses Bearer tokens from request context headers and structures flat Part messages.
*   [`deploy_ae.py`](deploy_ae.py): Automated script for Reasoning Engine SDK deployment and assistant registration.
*   [`test_okta_a2a.py`](test_okta_a2a.py): Comprehensive local mock unit testing suite.
*   [`okta_a2a_integration_guide.md`](okta_a2a_integration_guide.md): Detailed step-by-step integration, configuration, and architectural guide.
*   [`.env.example`](.env.example): Environment variables layout for local configurations.

---

## 🛡️ Verified Local Testing

We have verified the entire token propagation, storage, and tool context injection loop with an isolated local unit test suite.

To set up dependencies and run the tests:

```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install framework and tool dependencies
pip install -r <(echo "
a2a-sdk>=1.0.3
google-adk[extensions]>=2.0.0
pytz
geopy
timezonefinder
jsonschema
")

# 3. Run the automated unit tests
python3 -m unittest test_okta_a2a.py
```

All 4 tests pass successfully:
*   `test_token_extraction_from_headers` (Bearer token parsing from headers).
*   `test_token_extraction_from_metadata_fallback` (Access token parsing fallback).
*   `test_tool_retrieves_token_correctly` (ToolContext state injection).
*   `test_debug_auth_info_tool` (Metadata JSON representation).

---

## 🚀 Deploying & Registering to the Cloud

For complete step-by-step instructions on how to:
1.  Configure allowed Redirect URIs in Okta.
2.  Construct authorizationUri templates.
3.  Create Discovery Engine Authorization Resources.
4.  Deploy to Agent Engine and register automatically.

Please refer to the comprehensive [**Okta A2A Integration and Architecture Guide**](okta_a2a_integration_guide.md).
