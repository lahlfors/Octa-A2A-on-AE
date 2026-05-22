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

import json
import logging
import os
from a2a import types
from a2a import utils
from a2a.server import agent_execution
from a2a.server import events
from a2a.server import tasks
from a2a.utils import errors as a2a_errors
import a2ui_schema
import agent
from google.adk import runners
from google.adk.artifacts import in_memory_artifact_service
from google.adk.memory import in_memory_memory_service
from google.adk.sessions import in_memory_session_service
from google.genai import types as genai_types
import jsonschema

logger = logging.getLogger(__name__)


class AdkAgentToA2AExecutor(agent_execution.AgentExecutor):
  """An agent executor for ADK agents that extracts Okta OAuth tokens and handles A2UI rendering."""

  _runner: runners.Runner

  def __init__(self):
    # Prepare A2UI schema validator
    try:
      single_message_schema = json.loads(a2ui_schema.A2UI_SCHEMA)
      self.a2ui_schema_object = {
          "type": "array",
          "items": single_message_schema,
      }
      logger.info("[DEBUG] A2UI_SCHEMA successfully loaded.")
    except Exception as e:  # pylint: disable=broad-except
      logger.error("[DEBUG] Failed to parse A2UI_SCHEMA: %s", e)
      self.a2ui_schema_object = None

    self._agent = agent.OktaTimeAgent()
    self._runner = runners.Runner(
        app_name=self._agent.name,
        agent=self._agent,
        session_service=in_memory_session_service.InMemorySessionService(),
        artifact_service=in_memory_artifact_service.InMemoryArtifactService(),
        memory_service=in_memory_memory_service.InMemoryMemoryService(),
    )
    self._user_id = "remote_agent"

  async def execute(
      self,
      context: agent_execution.RequestContext,
      event_queue: events.EventQueue,
  ) -> None:
    query = context.get_user_input()
    task = context.current_task
    logger.info("[DEBUG] Query: %s", query)

    if not task:
      if not context.message:
        return

      task = utils.new_task(context.message)
      await event_queue.enqueue_event(task)

    updater = tasks.TaskUpdater(event_queue, task.id, task.context_id)
    session_id = task.context_id

    session = await self._runner.session_service.get_session(
        app_name=self._agent.name,
        user_id=self._user_id,
        session_id=session_id,
    )
    if session is None:
      session = await self._runner.session_service.create_session(
          app_name=self._agent.name,
          user_id=self._user_id,
          state={},
          session_id=session_id,
      )

    # --- Extract Okta OAuth 2.0 Token ---
    headers = context.call_context.state.get("headers", {})
    auth_header = headers.get("Authorization") or headers.get("authorization")
    token = None
    
    if auth_header and auth_header.startswith("Bearer "):
      token = auth_header[len("Bearer "):]
      
    if not token:
      auth_obj = context.call_context.state.get("auth")
      if auth_obj and isinstance(auth_obj, str):
        token = auth_obj
        
    if not token:
      token = context.metadata.get("access_token")

    if token:
      masked_token = token[:6] + "..." + token[-6:] if len(token) > 12 else "***"
      logger.info(f"[DEBUG] Okta OAuth Token successfully extracted: {masked_token}")
      session.state["auth_token"] = token
      session.state["headers"] = headers
      
      # Safely update session service storage to bypass copy restrictions
      try:
        session_service = self._runner.session_service
        if hasattr(session_service, "sessions"):
          app_name = self._agent.name
          storage_sessions = session_service.sessions.setdefault(app_name, {}).setdefault(self._user_id, {})
          if session.id in storage_sessions:
            storage_sessions[session.id].state["auth_token"] = token
            storage_sessions[session.id].state["headers"] = headers
            logger.info("[DEBUG] Okta token successfully persisted in session storage.")
      except Exception as e:
        logger.warning(f"[DEBUG] Failed to update storage session directly: {e}")
    else:
      logger.warning("[DEBUG] No Okta OAuth Token or Bearer credentials found in request context.")
      session.state["auth_token"] = None
      session.state["headers"] = headers

    current_query_text = query
    max_retries = 1
    attempt = 0

    # Working status
    await updater.start_work()

    while attempt <= max_retries:
      attempt += 1
      content = genai_types.Content(
          role="user", parts=[{"text": current_query_text}]
      )

      final_response_content = None

      logger.info("[DEBUG] attempt: %s", attempt)

      try:
        async for event in self._runner.run_async(
            user_id=self._user_id, session_id=session.id, new_message=content
        ):
          if event.is_final_response():
            if (
                event.content
                and event.content.parts
                and event.content.parts[0].text
            ):
              final_response_content = "\n".join(
                  [p.text for p in event.content.parts if p.text]
              )
              logger.info(
                  "[DEBUG] Final response content: %s", final_response_content
              )

      except Exception as e:  # pylint: disable=broad-except
        await updater.failed(
            message=utils.new_agent_text_message(
                f"Task failed with error: {str(e)}"
            )
        )
        return

      if final_response_content is None:
        if attempt <= max_retries:
          current_query_text = "I received no response. Please try again."
          continue
        else:
          await updater.failed(
              message=utils.new_agent_text_message("No response generated.")
          )
          return

      is_valid = False
      error_message = ""
      json_string_cleaned = None
      text_part = final_response_content

      if "---a2ui_JSON---" in final_response_content:
        try:
          text_part, json_string = final_response_content.split(
              "---a2ui_JSON---", 1
          )
          json_string_cleaned = (
              json_string.strip().lstrip("```json").rstrip("```").strip()
          )

          if not json_string_cleaned:
            json_string_cleaned = "[]"

          parsed_json = json.loads(json_string_cleaned)
          logger.info("[DEBUG] Parsed A2UI JSON: %s", parsed_json)
          if self.a2ui_schema_object:
            jsonschema.validate(
                instance=parsed_json, schema=self.a2ui_schema_object
            )
          is_valid = True
        except Exception as e:  # pylint: disable=broad-except
          error_message = f"A2UI Validation failed: {str(e)}"
      else:
        # Pure conversational text response is always valid
        is_valid = True

      if is_valid:
        parts = []
        if text_part.strip():
          parts.append(types.Part(text=text_part.strip()))

        if json_string_cleaned:
          logger.info("[DEBUG] UI JSON: %s", json_string_cleaned)
          json_data = json.loads(json_string_cleaned)
          from google.protobuf.json_format import ParseDict
          if isinstance(json_data, list):
            for message in json_data:
              p = types.Part()
              ParseDict({
                  "data": message,
                  "metadata": {"mimeType": "application/json+a2ui"}
              }, p)
              parts.append(p)
          else:
            p = types.Part()
            ParseDict({
                "data": json_data,
                "metadata": {"mimeType": "application/json+a2ui"}
            }, p)
            parts.append(p)
        
        logger.info("[DEBUG] Final parts to yield: %s", parts)
        await updater.add_artifact(parts, name="response")
        await updater.complete()
        return

      else:
        if attempt <= max_retries:
          current_query_text = (
              f"Your previous response was invalid. {error_message} You MUST"
              " generate a valid response that strictly follows the A2UI JSON"
              f" SCHEMA. Please retry the original request: '{query}'"
          )
          logger.warning(
              "[DEBUG] Retrying due to validation error: %s", error_message
          )
          continue
        else:
          await updater.add_artifact(
              [
                  types.Part(
                      text=(
                          "I encountered an error generating the UI:"
                          f" {error_message}. Here is the raw response:"
                          f" {final_response_content}"
                      )
                  )
              ],
              name="error_response",
          )
          await updater.complete()
          return

  async def cancel(
      self,
      context: agent_execution.RequestContext,
      event_queue: events.EventQueue,
  ) -> None:
    raise a2a_errors.ServerError(error=types.UnsupportedOperationError())
