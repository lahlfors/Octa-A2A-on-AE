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

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from agent import get_current_time, get_session_auth_info
from agent_executor import AdkAgentToA2AExecutor
from a2a import types
from a2a.server import agent_execution, events
from google.adk.tools import ToolContext

class MockCallContext:
    """Mock of ServerCallContext."""
    def __init__(self, headers, auth=None):
        self.tenant = "test-tenant"
        self.requested_extensions = set()
        self.state = {
            "headers": headers,
            "auth": auth
        }

class MockRequestContext(agent_execution.RequestContext):
    """Mock of RequestContext."""
    def __init__(self, headers, auth=None, metadata=None):
        self._call_context = MockCallContext(headers, auth)
        self._params = MagicMock()
        self._params.message = MagicMock()
        self._params.message.task_id = "test-task-123"
        self._params.message.context_id = "test-context-456"
        self._params.message.parts = [MagicMock()]
        self._params.metadata = metadata or {}
        
        self._task_id = "test-task-123"
        self._context_id = "test-context-456"
        
        # Configure MagicMock task to return correct string values
        self._current_task = MagicMock()
        self._current_task.id = "test-task-123"
        self._current_task.context_id = "test-context-456"
        self._related_tasks = []

    @property
    def call_context(self):
        return self._call_context

    @property
    def metadata(self):
        return self._params.metadata

    def get_user_input(self, delimiter='\n'):
        return "What time is it in London?"

class MockEvent:
    """Mock of ADK Runner RunEvent."""
    def __init__(self, text):
        self.content = MagicMock()
        part = MagicMock()
        part.text = text
        self.content.parts = [part]
        
    def is_final_response(self):
        return True

class MockEventQueue(events.EventQueue):
    """Mock EventQueue to capture events."""
    def __init__(self):
        self.events = []
        
    async def enqueue_event(self, event):
        self.events.append(event)

class TestOktaA2AIntegration(unittest.IsolatedAsyncioTestCase):
    """Test suite to verify the Okta token extraction, storage, and propagation loop."""

    async def test_token_extraction_from_headers(self):
        """Verifies the custom executor successfully extracts a Bearer token from call context headers."""
        # 1. Prepare mock request context with standard Authorization Bearer header
        headers = {
            "Authorization": "Bearer mock_okta_token_abc123",
            "Content-Type": "application/json"
        }
        mock_context = MockRequestContext(headers=headers)
        mock_queue = MockEventQueue()

        # 2. Instantiate AdkAgentToA2AExecutor
        executor = AdkAgentToA2AExecutor()

        # 3. Mock Runner.run_async to prevent live Gemini/LLM API calls
        async def mock_run_async(*args, **kwargs):
            yield MockEvent("The current time in London is 08:30 PM.")
            
        executor._runner.run_async = mock_run_async

        # 4. Execute
        await executor.execute(mock_context, mock_queue)

        # 5. Retrieve the cached session from the runner's session service
        session = await executor._runner.session_service.get_session(
            app_name=executor._agent.name,
            user_id=executor._user_id,
            session_id=mock_context.context_id
        )

        # 6. Assertions
        self.assertIsNotNone(session, "Session should have been created.")
        self.assertEqual(
            session.state.get("auth_token"),
            "mock_okta_token_abc123",
            "Bearer token should be extracted and persisted in the session state."
        )
        self.assertEqual(
            session.state.get("headers"),
            headers,
            "HTTP Headers should be stored in session state."
        )

    async def test_token_extraction_from_custom_bypass_header(self):
        """Verifies the executor prioritizes custom X-App-Token headers to avoid Google Cloud IAM conflicts."""
        headers = {
            "X-App-Token": "Bearer custom_okta_token_xyz",
            "Content-Type": "application/json"
        }
        mock_context = MockRequestContext(headers=headers)
        mock_queue = MockEventQueue()

        executor = AdkAgentToA2AExecutor()
        async def mock_run_async(*args, **kwargs):
            yield MockEvent("Response.")
        executor._runner.run_async = mock_run_async

        await executor.execute(mock_context, mock_queue)

        session = await executor._runner.session_service.get_session(
            app_name=executor._agent.name,
            user_id=executor._user_id,
            session_id=mock_context.context_id
        )

        self.assertEqual(
            session.state.get("auth_token"),
            "custom_okta_token_xyz",
            "Should extract token from the custom X-App-Token bypass header."
        )

    async def test_token_extraction_from_metadata_fallback(self):
        """Verifies the executor falls back to metadata access_token when headers are missing."""
        mock_context = MockRequestContext(headers={}, metadata={"access_token": "fallback_token_789"})
        mock_queue = MockEventQueue()

        executor = AdkAgentToA2AExecutor()
        async def mock_run_async(*args, **kwargs):
            yield MockEvent("Response.")
        executor._runner.run_async = mock_run_async

        await executor.execute(mock_context, mock_queue)

        session = await executor._runner.session_service.get_session(
            app_name=executor._agent.name,
            user_id=executor._user_id,
            session_id=mock_context.context_id
        )

        self.assertEqual(session.state.get("auth_token"), "fallback_token_789")

    def test_tool_retrieves_token_correctly(self):
        """Verifies the get_current_time tool extracts the auth_token from ToolContext state."""
        # 1. Build mock ToolContext simulating ADK runtime injection
        mock_tool_context = MagicMock(spec=ToolContext)
        mock_tool_context.state = {
            "auth_token": "test_okta_token_value"
        }

        # 2. Call the geopy time tool
        result = get_current_time("Paris", mock_tool_context)

        # 3. Assertions (verify tool runs successfully and handles coordinate lookup)
        self.assertIn("Paris", result)
        self.assertIn("Date", result)
        self.assertNotIn("Error", result, f"Tool failed with: {result}")

    def test_debug_auth_info_tool(self):
        """Verifies the get_session_auth_info debug tool reports complete auth structures."""
        mock_tool_context = MagicMock(spec=ToolContext)
        mock_tool_context.state = {
            "auth_token": "test_secret_key_456",
            "headers": {
                "Authorization": "Bearer test_secret_key_456",
                "User-Agent": "Mozilla/5.0"
            }
        }

        # Call debug tool
        result_str = get_session_auth_info(mock_tool_context)
        result = json.loads(result_str)

        # Assertions
        self.assertEqual(result["auth_token_in_state"], "test_secret_key_456")
        self.assertEqual(result["authorization_header"], "Bearer test_secret_key_456")
        self.assertIn("User-Agent", result["headers_keys"])

if __name__ == "__main__":
    unittest.main()
