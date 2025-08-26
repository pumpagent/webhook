# tools.py
# This file defines the tool functions that the MCP server exposes.
# It allows ElevenLabs to map the tool name to the correct function in your server.

from calendar_server import schedule_appointment

def get_tools():
  """
  Returns a dictionary of all the tool functions available on the server.
  The key of the dictionary is the tool name, and the value is the Python function.
  """
  return {
    "schedule_appointment": schedule_appointment
  }
