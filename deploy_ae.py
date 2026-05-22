# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import json
import sys
import requests
from dotenv import load_dotenv
import google.cloud.aiplatform as aiplatform
import vertexai
from vertexai.preview.reasoning_engines import A2aAgent
from a2a.types import AgentSkill
from vertexai.preview.reasoning_engines.templates.a2a import create_agent_card
from google.genai import types as genai_types
from google.auth import default
from google.auth.transport.requests import Request

def get_bearer_token():
    credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(Request())
    return credentials.token

def main():
    # Load environment configuration
    if os.path.exists(".env"):
        load_dotenv()
    else:
        print("⚠️ .env file not found. Loading from system environment variables.")

    project_id = os.environ.get("PROJECT_ID")
    location = os.environ.get("LOCATION", "us-central1")
    storage = os.environ.get("STORAGE_BUCKET")
    existing_engine_id = os.environ.get("EXISTING_ENGINE_ID")
    app_id = os.environ.get("GEMINI_ENTERPRISE_APP_ID")
    agent_auth = os.environ.get("AGENT_AUTHORIZATION")

    if not project_id or not storage:
        print("❌ Error: PROJECT_ID and STORAGE_BUCKET must be configured.")
        sys.exit(1)

    print(f"Initializing Vertex AI in {project_id} (location: {location}, bucket: {storage})...")
    # 1. Force explicit project ID initialization to bypass permission lookup race (Stratagem 359)
    aiplatform.init(project=project_id)
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    
    vertexai.init(project=project_id, location=location, staging_bucket=storage)
    client = vertexai.Client(project=project_id, location=location)

    # 2. Define Agent Skill & Create Card
    print("Defining Agent Skill and Card...")
    agent_skill = AgentSkill(
        id="okta_time_retrieval",
        name="Time Retrieval Tool",
        description="Retrieves the current time globally for any city and handles session inspection.",
        tags=["time", "clock", "session", "auth"],
        examples=["What time is it in London?", "Inspect my session auth details"],
    )

    agent_card = create_agent_card(
        agent_name="Okta Protected Time Agent",
        description="A secure, Okta-authenticated agent built to showcase GE-managed 3P OAuth.",
        skills=[agent_skill],
    )

    # 3. Instantiate A2aAgent with Custom Executor
    print("Wrapping ADK Agent with custom executor...")
    import agent_executor
    a2a_agent = A2aAgent(
        agent_card=agent_card,
        agent_executor_builder=agent_executor.AdkAgentToA2AExecutor,
    )

    # 4. Define Container Configurations
    config = {
        "display_name": "Okta Time Agent",
        "agent_framework": "google-adk",
        "staging_bucket": storage,
        "requirements": [
            "google-adk>=2.0.0",
            "google-cloud-aiplatform[agent_engines,adk]>=1.143.0",
            "a2a-sdk>=1.0.3",
            "pydantic==2.13.4",
            "pytz",
            "geopy",
            "timezonefinder",
            "jsonschema"
        ],
        "env_vars": {
            "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "true",
        },
        "extra_packages": [
            "agent_executor.py",
            "agent.py",
            "a2ui_schema.py"
        ]
    }

    # 5. Deploy to Agent Engine (create or update)
    if existing_engine_id:
        engine_name = f"projects/{project_id}/locations/{location}/reasoningEngines/{existing_engine_id}"
        print(f"Applying in-place update to: {engine_name}...")
        remote_agent = client.agent_engines.update(name=engine_name, agent=a2a_agent, config=config)
    else:
        print("Spinning up a fresh create instance on Agent Engine...")
        remote_agent = client.agent_engines.create(agent=a2a_agent, config=config)
        
    print(f"✓ Agent successfully deployed: {remote_agent.name}")

    # 6. Register the Agent with Gemini Enterprise (if app_id configured)
    if app_id and agent_auth:
        print(f"Starting registration with Gemini Enterprise App ID: {app_id}...")
        remote_engine_resource = remote_agent.api_resource.name
        
        # Fetch the actual agent card from deployed Agent Engine endpoint
        print("Fetching Agent Card from endpoint...")
        a2a_endpoint = f"https://{location}-aiplatform.googleapis.com/v1beta1/{remote_engine_resource}/a2a/v1/card"
        headers = {"Authorization": f"Bearer {get_bearer_token()}"}
        response = requests.get(a2a_endpoint, headers=headers)
        response.raise_for_status()
        a2ui_agent_card = response.json()

        # Register via Discovery Engine Assistants API
        print("Making Assistant Agent Registration POST request...")
        api_endpoint = (
            f"https://discoveryengine.googleapis.com/v1alpha/projects/{project_id}/"
            f"locations/global/collections/default_collection/engines/{app_id}/"
            "assistants/default_assistant/agents"
        )

        payload = {
            "name": "okta_time_agent",
            "displayName": "Okta A2A Time Agent",
            "description": "Time agent authenticated via Okta managed OAuth flow.",
            "a2aAgentDefinition": {"jsonAgentCard": json.dumps(a2ui_agent_card)},
            "authorizationConfig": {
                "agentAuthorization": agent_auth
            }
        }

        response = requests.post(api_endpoint, headers=headers, json=payload)
        if response.status_code in [200, 201]:
            print("✓ Agent successfully registered with Gemini Enterprise Assistant.")
        else:
            print(f"❌ Failed to register agent: {response.status_code} - {response.text}")
    else:
        print("⚠️ Skipping Gemini Enterprise registration (GEMINI_ENTERPRISE_APP_ID or AGENT_AUTHORIZATION not set).")

if __name__ == "__main__":
    main()
