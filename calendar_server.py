# calendar_server.py
# This is the main file for your web service. It contains the server logic
# that will handle requests from ElevenLabs and interact with Google Calendar.

import os
import json
from flask import Flask, request, jsonify
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Define the scopes required to interact with Google Calendar.
# 'calendar.events' allows the app to manage (create, update, delete) events.
SCOPES = ['https://www.googleapis.com/auth/calendar.events']

app = Flask(__name__)

# Function to get Google Calendar service.
def get_calendar_service():
    """
    Retrieves the Google Calendar service object using credentials stored
    in a JSON file. This is the secure way to authenticate.
    """
    creds = None
    # The file token.json stores the user's access and refresh tokens.
    # We will get this after the first run and store it.
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # If there are no valid credentials, let the user authenticate.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Load credentials from environment variables, as recommended for Render.
            try:
                creds_info = json.loads(os.environ.get('GOOGLE_CALENDAR_CREDENTIALS'))
                flow = InstalledAppFlow.from_client_config(creds_info, SCOPES)
                creds = flow.run_local_server(port=0)
            except json.JSONDecodeError:
                return jsonify({"error": "Invalid GOOGLE_CALENDAR_CREDENTIALS environment variable."}), 400
            except Exception as e:
                return jsonify({"error": f"Authentication failed: {e}"}), 500
        
        # Save the credentials for the next run.
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    service = build('calendar', 'v3', credentials=creds)
    return service

# Main endpoint to handle scheduling requests from ElevenLabs.
@app.route('/schedule-appointment', methods=['POST'])
def schedule_appointment():
    """
    An API endpoint that takes appointment details and creates a new
    event in the user's Google Calendar.
    """
    try:
        # Parse the JSON payload from the request.
        data = request.json
        summary = data.get('summary', 'New AI Agent Consultation')
        start_time = data.get('start_time')
        end_time = data.get('end_time')
        calendar_id = 'primary' # You can make this configurable if needed.

        # Basic validation of the incoming data.
        if not start_time or not end_time:
            return jsonify({"error": "Missing start_time or end_time."}), 400

        service = get_calendar_service()

        # Build the event body for the Google Calendar API.
        event = {
            'summary': summary,
            'location': 'Client Call',
            'description': 'Scheduled by AdiuvansAI Agent.',
            'start': {
                'dateTime': start_time,
                'timeZone': 'America/New_York', # Set to your desired timezone.
            },
            'end': {
                'dateTime': end_time,
                'timeZone': 'America/New_York',
            },
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},
                    {'method': 'popup', 'minutes': 10},
                ],
            },
        }

        # Call the Google Calendar API to insert the event.
        event = service.events().insert(calendarId=calendar_id, body=event).execute()
        
        # Return a success message with details of the created event.
        return jsonify({
            "status": "success",
            "message": "Appointment scheduled successfully.",
            "event_link": event.get('htmlLink')
        }), 200

    except HttpError as e:
        return jsonify({"error": f"Google Calendar API Error: {e.content.decode()}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # When running locally, you'll need to handle the port.
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

```
# requirements.txt
# This file lists all the Python libraries that Render needs to install
# for your application to run correctly.

Flask==2.2.2
google-api-python-client
google-auth
google-auth-oauthlib
```
# render.yaml
# This is a configuration file that tells Render how to deploy your service.
# Render automatically detects this file and configures your app accordingly.

services:
- type: web
  name: adiuvansai-mcp-server
  env: python
  buildCommand: "pip install -r requirements.txt"
  startCommand: "gunicorn calendar_server:app"
  envVars:
  - key: GOOGLE_CALENDAR_CREDENTIALS
    sync: false
```
# credentials.json
# You DO NOT push this file to Git. Instead, you'll copy its contents and
# paste them into Render as a secure environment variable.

# Copy the entire content of your credentials.json file here.
# For example:
# {
#   "web": {
#     "client_id": "...",
#     "project_id": "...",
#     "auth_uri": "...",
#     ...
#   }
