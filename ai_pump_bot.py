import os
import discord
import requests
import json
import re # Import regex for parsing indicator values

# --- API Keys and URLs (Set as Environment Variables on Render) ---
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
FLASK_WEBHOOK_URL = os.environ.get('FLASK_WEBHOOK_URL')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

client = discord.Client(intents=intents)

# --- Conversation Memory (In-memory, volatile on bot restart) ---
# Stores chat history for each user/channel
conversation_histories = {} # Format: {user_id: [{"role": "user/model/function", "parts": [...]}, ...]}

@client.event
async def on_ready():
    """Event that fires when the bot successfully connects to Discord."""
    print(f'Logged in as {client.user} (ID: {client.user.id})')
    print('------')

@client.event
async def on_message(message):
    """Event that fires when a message is sent in a channel the bot can see."""
    if message.author == client.user:
        return

    user_id = str(message.author.id)
    user_query = message.content.strip()
    print(f"Received message: '{user_query}' from {message.author} (ID: {user_id})")

    # Get or initialize chat history for this user
    if user_id not in conversation_histories:
        conversation_histories[user_id] = []
    
    # Add current user query to history
    conversation_histories[user_id].append({"role": "user", "parts": [{"text": user_query}]})
    
    # Use the current chat history for the LLM interaction
    current_chat_history = conversation_histories[user_id]

    response_text_for_discord = "I'm currently unavailable. Please try again later."

    try:
        # --- Define the market_data tool for the LLM ---
        tools = [
            {
                "functionDeclarations": [
                    {
                        "name": "get_market_data",
                        "description": "Fetches live price, historical data, or technical analysis indicators for a given symbol, or market news for a query. Use this tool to get specific market data points.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "symbol": { "type": "string", "description": "Ticker symbol (e.g., 'BTC/USD', 'AAPL')." },
                                "data_type": { "type": "string", "enum": ["live", "historical", "indicator", "news"], "description": "Type of data to fetch." },
                                "interval": { "type": "string", "description": "Time interval (e.g., '1min', '1day')." },
                                "outputsize": { "type": "string", "description": "Number of data points." },
                                "indicator": { "type": "string", "enum": ["SMA", "EMA", "RSI", "MACD", "BBANDS", "STOCHRSI"], "description": "Name of the technical indicator." },
                                "indicator_period": { "type": "string", "description": "Period for the indicator." },
                                "news_query": { "type": "string", "description": "Keywords for news search." },
                                "from_date": { "type": "string", "description": "Start date for news (YYYY-MM-DD)." },
                                "sort_by": { "type": "string", "enum": ["relevancy", "popularity", "publishedAt"], "description": "How to sort news." },
                                "news_language": { "type": "string", "description": "Language of news." }
                            },
                            "required": []
                        }
                    }
                ]
            }
        ]

        # --- Attempt to parse for direct indicator analysis: <symbol> <indicator> ---
        parsed_indicator_command = None
        match = re.match(r'^([a-zA-Z0-9\/]+)\s+(rsi|macd|bbands|stochrsi)\s*$', user_query.lower())
        if match:
            symbol_for_direct_analysis = match.group(1).upper()
            indicator_name_for_direct_analysis = match.group(2).upper()
            parsed_indicator_command = True

            # Fetch and Analyze Single Indicator Locally
            indicator_period = '14' # Default period for general analysis
            if indicator_name_for_direct_analysis == 'MACD':
                indicator_period = '0' # MACD doesn't typically use a single 'period' param in this context

            analysis_params = {
                'data_type': 'indicator',
                'symbol': symbol_for_direct_analysis,
                'indicator': indicator_name_for_direct_analysis,
                'indicator_period': indicator_period,
                'interval': '1day', # Consistent interval for analysis
                'outputsize': '300' # Ensure enough data
            }

            current_price_val = None
            if indicator_name_for_direct_analysis == 'BBANDS':
                try:
                    live_price_params = {'data_type': 'live', 'symbol': symbol_for_direct_analysis}
                    live_price_response = requests.get(FLASK_WEBHOOK_URL, params=live_price_params)
                    live_price_response.raise_for_status()
                    live_price_data = live_price_response.json()
                    price_text = live_price_data.get('text', '')
                    price_match = re.search(r'\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)', price_text)
                    if price_match:
                        current_price_val = float(price_match.group(1).replace(',', ''))
                except Exception as e:
                    print(f"Error fetching live price for BBANDS: {e}")

            try:
                print(f"Fetching {indicator_name_for_direct_analysis} for {symbol_for_direct_analysis}...")
                webhook_response = requests.get(FLASK_WEBHOOK_URL, params=analysis_params)
                webhook_response.raise_for_status()
                indicator_data_json = webhook_response.json()
                indicator_text = indicator_data_json.get('text', f"{indicator_name_for_direct_analysis} data N/A")
                
                assessment = "Neutral"
                # Extract value and perform assessment
                if "The" in indicator_text and "is" in indicator_text:
                    if indicator_name_for_direct_analysis == 'RSI':
                        try:
                            val_str = indicator_text.split(' is ')[-1].strip()
                            val = float(re.sub(r'[^\d.]', '', val_str))
                            if val > 70: assessment = "Bearish"
                            elif val < 30: assessment = "Bullish"
                        except ValueError: pass
                    elif indicator_name_for_direct_analysis == 'MACD':
                        if "MACD_Line:" in indicator_text and "Signal_Line:" in indicator_text:
                            try:
                                macd_line_str = indicator_text.split('MACD_Line: ')[1].split('. ')[0].strip()
                                signal_line_str = indicator_text.split('Signal_Line: ')[1].split('. ')[0].strip()
                                macd_line_val = float(re.sub(r'[^\d.-]', '', macd_line_str))
                                signal_line_val = float(re.sub(r'[^\d.-]', '', signal_line_str))
                                if macd_line_val > signal_line_val: assessment = "Bullish"
                                elif macd_line_val < signal_line_val: assessment = "Bearish"
                            except (ValueError, IndexError): pass
                    elif indicator_name_for_direct_analysis == 'BBANDS' and current_price_val is not None:
                        if "Upper_Band:" in indicator_text and "Lower_Band:" in indicator_text:
                            try:
                                upper_band_str = indicator_text.split('Upper_Band: ')[1].split('. ')[0].strip()
                                lower_band_str = indicator_text.split('Lower_Band: ')[1].split('. ')[0].strip()
                                upper_band = float(re.sub(r'[^\d.]', '', upper_band_str))
                                lower_band = float(re.sub(r'[^\d.]', '', lower_band_str))
                                if current_price_val > upper_band: assessment = "Bearish"
                                elif current_price_val < lower_band: assessment = "Bullish"
                                else: assessment = "Neutral"
                            except (ValueError, IndexError): pass
                    elif indicator_name_for_direct_analysis == 'STOCHRSI':
                        if "StochRSI_K:" in indicator_text and "StochRSI_D:" in indicator_text:
                            try:
                                stochrsi_k_str = indicator_text.split('StochRSI_K: ')[1].split('. ')[0].strip()
                                stochrsi_d_str = indicator_text.split('StochRSI_D: ')[1].split('. ')[0].strip()
                                stochrsi_k_val = float(re.sub(r'[^\d.]', '', stochrsi_k_str))
                                stochrsi_d_val = float(re.sub(r'[^\d.]', '', stochrsi_d_str))
                                if stochrsi_k_val > 80: assessment = "Bearish"
                                elif stochrsi_k_val < 20: assessment = "Bullish"
                                elif stochrsi_k_val > stochrsi_d_val: assessment = "Bullish"
                                elif stochrsi_k_val < stochrsi_d_val: assessment = "Bearish"
                            except (ValueError, IndexError): pass

                response_text_for_discord = f"For {symbol_for_direct_analysis}, {indicator_name_for_direct_analysis} is: **{assessment}**."
                
            except requests.exceptions.RequestException as e:
                response_text_for_discord = f"I'm having trouble retrieving data for {symbol_for_direct_analysis} {indicator_name_for_direct_analysis}. Error: {e}"
            except Exception as e:
                response_text_for_discord = f"An unexpected error occurred while processing {indicator_name_for_direct_analysis} for {symbol_for_direct_analysis}. Error: {e}"
        
        # --- If not a direct indicator command, proceed with LLM interaction ---
        if not parsed_indicator_command:
            llm_payload = {
                "contents": current_chat_history, # Use the full history
                "tools": tools,
                "safetySettings": [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                ]
            }

            llm_api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GOOGLE_API_KEY}"
            
            try:
                llm_response = requests.post(llm_api_url, headers={'Content-Type': 'application/json'}, json=llm_payload)
                llm_response.raise_for_status()
                llm_data = llm_response.json()
            except requests.exceptions.RequestException as e:
                print(f"Error connecting to Gemini LLM: {e}")
                response_text_for_discord = f"I'm having trouble connecting to my AI brain. Please check the GOOGLE_API_KEY and try again later. Error: {e}"
                await message.channel.send(response_text_for_discord)
                return

            if llm_data and llm_data.get('candidates'):
                candidate = llm_data['candidates'][0]
                if candidate.get('content') and candidate['content'].get('parts'):
                    parts = candidate['content']['parts']

                    if parts[0].get('functionCall'):
                        function_call = parts[0]['functionCall']
                        function_name = function_call['name']
                        function_args = function_call['args']

                        if function_name == "get_market_data":
                            if FLASK_WEBHOOK_URL:
                                print(f"LLM requested tool call: get_market_data with args: {function_args}")
                                current_chat_history.append({"role": "model", "parts": [{"functionCall": function_call}]})

                                try:
                                    webhook_response = requests.get(FLASK_WEBHOOK_URL, params=function_args)
                                    webhook_response.raise_for_status()
                                    tool_output_data = webhook_response.json()
                                    tool_output_text = tool_output_data.get('text', 'No specific response from market data agent.')
                                    print(f"Tool execution output: {tool_output_text}")
                                except requests.exceptions.RequestException as e:
                                    print(f"Error connecting to Flask Webhook: {e}")
                                    response_text_for_discord = f"I'm having trouble connecting to my data service webhook. Please ensure the webhook URL is correct and the service is running. Error: {e}"
                                    await message.channel.send(response_text_for_discord)
                                    return
                                
                                current_chat_history.append({"role": "function", "parts": [{"functionResponse": {"name": function_name, "response": {"text": tool_output_text}}}]})

                                # Second LLM call to get conversational response after tool execution
                                llm_payload_second_turn = {
                                    "contents": current_chat_history,
                                    "tools": tools,
                                    "safetySettings": [
                                        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                                        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                                        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                                        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                                    ]
                                }
                                try:
                                    llm_response_second_turn = requests.post(llm_api_url, headers={'Content-Type': 'application/json'}, json=llm_payload_second_turn)
                                    llm_response_second_turn.raise_for_status()
                                    llm_data_second_turn = llm_response_second_turn.json()
                                except requests.exceptions.RequestException as e:
                                    print(f"Error connecting to Gemini LLM (second turn after tool): {e}")
                                    response_text_for_discord = f"I received the data, but I'm having trouble processing it with my AI brain. Please try again later. Error: {e}"
                                    await message.channel.send(response_text_for_discord)
                                    return

                                if llm_data_second_turn and llm_data_second_turn.get('candidates'):
                                    final_candidate = llm_data_second_turn['candidates'][0]
                                    if final_candidate.get('content') and final_candidate['content'].get('parts'):
                                        response_text_for_discord = final_candidate['content']['parts'][0].get('text', 'No conversational response from AI.')
                                    else:
                                        response_text_for_discord = "AI did not provide a conversational response after tool execution."
                                else:
                                    response_text_for_discord = "Could not get a valid second response from the AI."
                            else:
                                response_text_for_discord = "Error: Flask webhook URL is not configured."
                        else:
                            response_text_for_discord = "LLM requested an unknown function."
                    elif parts[0].get('text'):
                        response_text_for_discord = parts[0]['text']
                    else:
                        response_text_for_discord = "LLM response format not recognized."
                else:
                    response_text_for_discord = "LLM did not provide content in its response."
            else:
                response_text_for_discord = "Could not get a valid response from the AI. Please try again."
                if llm_data.get('promptFeedback') and llm_data['promptFeedback'].get('blockReason'):
                    response_text_for_discord += f" (Blocked: {llm_data['promptFeedback']['blockReason']})"
            
            # Add LLM's response to history
            current_chat_history.append({"role": "model", "parts": [{"text": response_text_for_discord}]})


    except requests.exceptions.RequestException as e:
        print(f"General Request Error: {e}")
        response_text_for_discord = f"An unexpected connection error occurred. Please check network connectivity or API URLs. Error: {e}"
    except Exception as e:
        print(f"An unexpected error occurred in bot logic: {e}")
        response_text_for_discord = f"An unexpected error occurred while processing your request. My apologies. Error: {e}"

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
