# calendar_server.py
# This is the main file for your web service. It contains the server logic
# that will handle requests from ElevenLabs and interact with Google Calendar.

import os
import json
from flask import Flask, request, jsonify
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Define the scopes required to interact with Google Calendar.
# 'calendar.events' allows the app to manage (create, update, delete) events.
SCOPES = ['https://www.googleapis.com/auth/calendar.events']

app = Flask(__name__)

# Function to get Google Calendar service.
def get_calendar_service():
    """
    Retrieves the Google Calendar service object using credentials and token
    from environment variables for secure, production-ready authentication.
    """
    creds = None
    try:
        # Load the credentials from the GOOGLE_CALENDAR_CREDENTIALS env variable
        creds_info = json.loads(os.environ.get('GOOGLE_CALENDAR_CREDENTIALS'))
        # Load the token from the GOOGLE_CALENDAR_TOKEN env variable
        token_info = json.loads(os.environ.get('GOOGLE_CALENDAR_TOKEN'))
        
        # Build a credentials object from the loaded token info.
        creds = Credentials.from_authorized_user_info(info=token_info, scopes=SCOPES)
        
        # If the token has expired, refresh it.
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
    except (json.JSONDecodeError, TypeError) as e:
        return jsonify({"error": f"Failed to load credentials or token from environment variables: {e}"}), 500
    except Exception as e:
        return jsonify({"error": f"Authentication failed: {e}"}), 500
    
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
        if not data:
            return jsonify({"error": "No JSON payload received."}), 400
        
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
    # When running on Render, the port is provided as an environment variable.
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
