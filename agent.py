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
from typing import Any, Dict
import pytz
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
from datetime import datetime

from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import ToolContext

# Setup timezone finder and geolocator
geolocator = Nominatim(user_agent="okta_time_agent")
tf = TimezoneFinder()

def get_current_time(city: str, tool_context: ToolContext) -> str:
    """Call this tool to retrieve the current time for any city globally.
    
    Args:
        city: The name of the city (e.g., 'London', 'New York').
        tool_context: The context of the current tool call.
    """
    # Extract auth token from state if available
    auth_token = tool_context.state.get("auth_token")
    print(f"[DEBUG] get_current_time called. Auth token present: {auth_token is not None}")
    
    try:
        location = geolocator.geocode(city, language='en', timeout=10)
        if not location:
            return f"Error: Location '{city}' not found."

        timezone_str = tf.timezone_at(lng=location.longitude, lat=location.latitude)
        if not timezone_str:
            return f"Error: Timezone could not be determined for '{city}'."

        timezone = pytz.timezone(timezone_str)
        now = datetime.now(timezone)
        
        result = {
            "city": city,
            "formatted_address": location.address,
            "timezone": timezone_str,
            "time_24h": now.strftime("%H:%M"),
            "time_12h": now.strftime("%I:%M %p"),
            "date": now.strftime("%Y-%m-%d"),
            "utc_offset": now.strftime("%z")
        }
        return f"Current time in {city}: {now.strftime('%I:%M %p')} (Date: {now.strftime('%Y-%m-%d')}, Timezone: {timezone_str}). Full Details: {result}"
    except Exception as e:
        return f"Error calculating time for '{city}': {str(e)}"


def get_session_auth_info(tool_context: ToolContext) -> str:
    """Call this debug tool to inspect and retrieve all OAuth and request authentication metadata present in the current session."""
    auth_token = tool_context.state.get("auth_token")
    headers = tool_context.state.get("headers", {})
    
    result = {
        "auth_token_in_state": str(auth_token),
        "headers_keys": list(headers.keys()),
        "authorization_header": headers.get("Authorization") or headers.get("authorization"),
    }
    import json
    return json.dumps(result, indent=2)


class OktaTimeAgent(LlmAgent):
    """Okta-protected Time Agent using Google ADK."""
    
    def __init__(self):
        model_name = os.getenv("LITELLM_MODEL", "gemini/gemini-2.5-flash")
        instruction = (
            "You are a helpful assistant that can find the current time for any city globally.\n"
            "You also have access to a debug tool to check the current session's authentication information.\n"
            "Always use get_current_time to lookup the time.\n"
            "If the user asks about session details or authentication, use get_session_auth_info."
        )
        super().__init__(
            model=LiteLlm(model=model_name),
            name="okta_time_agent",
            description="Agent that retrieves the current time globally and shows authentication state.",
            instruction=instruction,
            tools=[get_current_time, get_session_auth_info]
        )
