import os
import discord
import requests
import json # Import json for parsing LLM tool calls

# --- API Keys and URLs (Set as Environment Variables on Render) ---
# Discord Bot Token (from Discord Developer Portal)
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
# Your Flask Webhook URL (e.g., https://pricelookupwebhook.onrender.com/market_data)
FLASK_WEBHOOK_URL = os.environ.get('FLASK_WEBHOOK_URL')
# Google API Key for Gemini (ensure this is set for LLM calls)
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')


# --- Discord Bot Setup ---
# Define Discord intents (crucial for message content)
intents = discord.Intents.default()
intents.message_content = True # Enable message content intent
intents.members = True       # Enable server members intent (if needed for user info)
intents.presences = True     # Enable presence intent (if needed)

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    """Event that fires when the bot successfully connects to Discord."""
    print(f'Logged in as {client.user} (ID: {client.user.id})')
    print('------')

@client.event
async def on_message(message):
    """Event that fires when a message is sent in a channel the bot can see."""
    # Ignore messages from the bot itself to prevent infinite loops
    if message.author == client.user:
        return

    user_query = message.content.strip()
    print(f"Received message: '{user_query}' from {message.author}")

    # Initialize chat history for the LLM
    chat_history = []
    chat_history.append({"role": "user", "parts": [{"text": user_query}]})

    response_text_for_discord = "I'm currently unavailable. Please try again later."

    try:
        # --- Define the market_data tool for the LLM ---
        # This tells the LLM about your Flask webhook and its parameters
        tools = [
            {
                "functionDeclarations": [
                    {
                        "name": "get_market_data",
                        "description": "Fetches live price, historical data, or technical analysis indicators for a given symbol, or market news for a query. Use this tool to get specific market data points for analysis.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "symbol": {
                                    "type": "string",
                                    "description": "Ticker symbol (e.g., 'BTC/USD', 'AAPL') for price/TA. Required for 'live', 'historical', 'indicator' data types."
                                },
                                "data_type": {
                                    "type": "string",
                                    "enum": ["live", "historical", "indicator", "news"],
                                    "description": "Type of data to fetch: 'live', 'historical', 'indicator', or 'news'. Defaults to 'live'."
                                },
                                "interval": {
                                    "type": "string",
                                    "description": "Time interval (e.g., '1min', '1day'). Required for 'historical' or 'indicator' data. Defaults to '1day'."
                                },
                                "outputsize": {
                                    "type": "string",
                                    "description": "Number of data points to retrieve. Defaults to '50' for historical, adjusted for indicator. Should be a whole number string."
                                },
                                "indicator": {
                                    "type": "string",
                                    "enum": ["SMA", "EMA", "RSI", "MACD", "BBANDS", "STOCHRSI"],
                                    "description": "Name of the technical indicator (e.g., 'SMA', 'EMA', 'RSI', 'MACD', 'BBANDS', 'STOCHRSI'). Required if 'data_type' is 'indicator'."
                                },
                                "indicator_period": {
                                    "type": "string",
                                    "description": "Period for the indicator (e.g., '14', '20', '50'). Required if 'indicator' is specified. Should be a whole number string."
                                },
                                "news_query": {
                                    "type": "string",
                                    "description": "Keywords for news search. Required if 'data_type' is 'news'."
                                },
                                "from_date": {
                                    "type": "string",
                                    "description": "Start date for news (YYYY-MM-DD). Defaults to 7 days ago."
                                },
                                "sort_by": {
                                    "type": "string",
                                    "enum": ["relevancy", "popularity", "publishedAt"],
                                    "description": "How to sort news ('relevancy', 'popularity', 'publishedAt'). Defaults to 'publishedAt'."
                                },
                                "news_language": {
                                    "type": "string",
                                    "description": "Language of news (e.g., 'en'). Defaults to 'en'."
                                }
                            },
                            "required": [] # LLM will infer required based on data_type
                        }
                    }
                ]
            }
        ]

        # --- First LLM Call: Get LLM's initial response or tool call ---
        llm_payload_first_turn = {
            "contents": chat_history,
            "tools": tools,
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
        }

        llm_api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GOOGLE_API_KEY}"
        llm_response_first_turn = requests.post(llm_api_url, headers={'Content-Type': 'application/json'}, json=llm_payload_first_turn)
        llm_response_first_turn.raise_for_status()
        llm_data_first_turn = llm_response_first_turn.json()

        # Check for LLM candidates and content from the first turn
        if llm_data_first_turn and llm_data_first_turn.get('candidates'):
            candidate_first_turn = llm_data_first_turn['candidates'][0]
            if candidate_first_turn.get('content') and candidate_first_turn['content'].get('parts'):
                parts_first_turn = candidate_first_turn['content']['parts']

                # --- If LLM requests a tool call ---
                if parts_first_turn[0].get('functionCall'):
                    function_call = parts_first_turn[0]['functionCall']
                    function_name = function_call['name']
                    function_args = function_call['args']

                    if function_name == "get_market_data":
                        if FLASK_WEBHOOK_URL:
                            print(f"LLM requested tool call: get_market_data with args: {function_args}")
                            
                            # Add the LLM's function call to chat history
                            chat_history.append({"role": "model", "parts": [{"functionCall": function_call}]})

                            # Make the request to your Flask webhook (the actual tool execution)
                            webhook_response = requests.get(FLASK_WEBHOOK_URL, params=function_args)
                            webhook_response.raise_for_status()
                            tool_output_data = webhook_response.json()
                            tool_output_text = tool_output_data.get('text', 'No specific response from market data agent.')
                            print(f"Tool execution output: {tool_output_text}")

                            # Add the tool output to chat history for the LLM to see
                            chat_history.append({"role": "function", "parts": [{"functionResponse": {"name": function_name, "response": {"text": tool_output_text}}}]})

                            # --- Second LLM Call: Generate response based on tool output ---
                            llm_payload_second_turn = {
                                "contents": chat_history, # Updated chat history with tool call and output
                                "tools": tools, # Still provide tools just in case
                                "safetySettings": [
                                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                                ]
                            }
                            llm_response_second_turn = requests.post(llm_api_url, headers={'Content-Type': 'application/json'}, json=llm_payload_second_turn)
                            llm_response_second_turn.raise_for_status()
                            llm_data_second_turn = llm_response_second_turn.json()

                            if llm_data_second_turn and llm_data_second_turn.get('candidates'):
                                candidate_second_turn = llm_data_second_turn['candidates'][0]
                                if candidate_second_turn.get('content') and candidate_second_turn['content'].get('parts'):
                                    response_text_for_discord = candidate_second_turn['content']['parts'][0].get('text', 'No conversational response from AI.')
                                else:
                                    response_text_for_discord = "AI did not provide a conversational response after tool execution."
                            else:
                                response_text_for_discord = "Could not get a valid second response from the AI."

                        else:
                            response_text_for_discord = "Error: Flask webhook URL is not configured."
                    else:
                        response_text_for_discord = "LLM requested an unknown function."

                # --- If LLM generated a direct text response (no tool call needed) ---
                elif parts_first_turn[0].get('text'):
                    response_text_for_discord = parts_first_turn[0]['text']
                else:
                    response_text_for_discord = "LLM response format not recognized in the first turn."
            else:
                response_text_for_discord = "LLM did not provide content in its first turn response."
        else:
            response_text_for_discord = "Could not get a valid response from the AI. Please try again."
            if llm_data_first_turn.get('promptFeedback') and llm_data_first_turn['promptFeedback'].get('blockReason'):
                response_text_for_discord += f" (Blocked: {llm_data_first_turn['promptFeedback']['blockReason']})"


    except requests.exceptions.RequestException as e:
        print(f"Error connecting to LLM or Flask webhook: {e}")
        response_text_for_discord = "I'm having trouble connecting to my AI brain or data service. Please try again later."
    except Exception as e:
        print(f"An unexpected error occurred in bot logic: {e}")
        response_text_for_discord = "An unexpected error occurred while processing your request. My apologies."

    # Send the final response back to the Discord channel
    await message.channel.send(response_text_for_discord)

# Run the bot
if __name__ == '__main__':
    if not DISCORD_BOT_TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
    elif not FLASK_WEBHOOK_URL:
        print("Error: FLASK_WEBHOOK_URL environment variable not set.")
    elif not GOOGLE_API_KEY:
        print("Error: GOOGLE_API_KEY environment variable not set.")
    else:
        client.run(DISCORD_BOT_TOKEN)
