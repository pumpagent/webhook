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

        # --- Initial LLM Call: Determine if a tool call is needed or direct response ---
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
        
        try:
            llm_response_first_turn = requests.post(llm_api_url, headers={'Content-Type': 'application/json'}, json=llm_payload_first_turn)
            llm_response_first_turn.raise_for_status()
            llm_data_first_turn = llm_response_first_turn.json()
        except requests.exceptions.RequestException as e:
            print(f"Error connecting to Gemini LLM (first turn): {e}")
            response_text_for_discord = f"I'm having trouble connecting to my AI brain. Please check the GOOGLE_API_KEY and try again later. Error: {e}"
            await message.channel.send(response_text_for_discord)
            return # Exit early if LLM connection fails

        # Check for LLM candidates and content from the first turn
        if llm_data_first_turn and llm_data_first_turn.get('candidates'):
            candidate_first_turn = llm_data_first_turn['candidates'][0]
            if candidate_first_turn.get('content') and candidate_first_turn['content'].get('parts'):
                parts_first_turn = candidate_first_turn['content']['parts']

                # --- If LLM requests a tool call (e.g., for specific data) ---
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
                                return # Exit early if webhook connection fails
                            
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
                            try:
                                llm_response_second_turn = requests.post(llm_api_url, headers={'Content-Type': 'application/json'}, json=llm_payload_second_turn)
                                llm_response_second_turn.raise_for_status()
                                llm_data_second_turn = llm_response_second_turn.json()
                            except requests.exceptions.RequestException as e:
                                print(f"Error connecting to Gemini LLM (second turn after tool): {e}")
                                response_text_for_discord = f"I received the data, but I'm having trouble processing it with my AI brain. Please try again later. Error: {e}"
                                await message.channel.send(response_text_for_discord)
                                return # Exit early

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

                # --- Handle specific "analyze" command for comprehensive analysis ---
                elif user_query.lower().startswith("analyze "):
                    symbol_for_analysis = user_query.lower().replace("analyze ", "").strip().upper()
                    if not symbol_for_analysis:
                        response_text_for_discord = "Please specify a symbol to analyze (e.g., 'analyze BTC/USD')."
                    else:
                        analysis_results = []
                        
                        # --- Fetch Live Price for BBANDS Context ---
                        current_price_str = "N/A"
                        try:
                            live_price_params = {'data_type': 'live', 'symbol': symbol_for_analysis}
                            live_price_response = requests.get(FLASK_WEBHOOK_URL, params=live_price_params)
                            live_price_response.raise_for_status()
                            live_price_data = live_price_response.json()
                            # Extract price from the text, e.g., "The current price of BTC/USD is $65,000.00."
                            price_text = live_price_data.get('text', '')
                            import re
                            match = re.search(r'\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)', price_text)
                            if match:
                                current_price_str = match.group(1).replace(',', '')
                                print(f"Current price for {symbol_for_analysis}: {current_price_str}")
                            else:
                                print(f"Could not parse current price from: {price_text}")

                        except requests.exceptions.RequestException as e:
                            print(f"Error fetching live price for BBANDS analysis: {e}")
                            current_price_str = "Error fetching live price"
                        except Exception as e:
                            print(f"Unexpected error parsing live price: {e}")
                            current_price_str = "Error parsing live price"


                        # --- Fetch and Analyze Indicators ---
                        indicators_to_fetch = {
                            'RSI': {'period': '14'},
                            'MACD': {'period': '0'}, # Period is often fixed for MACD
                            'BBANDS': {'period': '20'}, # Common BBANDS period
                            'STOCHRSI': {'period': '14'}
                        }
                        
                        for indicator_name, params in indicators_to_fetch.items():
                            indicator_period = params['period']

                            analysis_params = {
                                'data_type': 'indicator',
                                'symbol': symbol_for_analysis,
                                'indicator': indicator_name,
                                'indicator_period': indicator_period,
                                'interval': '1day', # Consistent interval
                                'outputsize': '300' # Ensure enough data
                            }

                            try:
                                print(f"Fetching {indicator_name} for {symbol_for_analysis}...")
                                webhook_response = requests.get(FLASK_WEBHOOK_URL, params=analysis_params)
                                webhook_response.raise_for_status()
                                indicator_data = webhook_response.json()
                                indicator_text = indicator_data.get('text', f"{indicator_name} data N/A")
                                
                                # --- Local Bullish/Bearish Assessment ---
                                assessment = "Neutral"
                                if "The" in indicator_text and "is" in indicator_text: # Basic check for valid data
                                    if indicator_name == 'RSI':
                                        try:
                                            rsi_val = float(indicator_text.split(' is ')[-1].replace('.', '')) # Remove comma for float conversion
                                            if rsi_val > 70: assessment = "Bearish" # Overbought
                                            elif rsi_val < 30: assessment = "Bullish" # Oversold
                                        except ValueError: pass
                                    elif indicator_name == 'MACD':
                                        if "MACD_Line:" in indicator_text and "Signal_Line:" in indicator_text:
                                            try:
                                                macd_line_val = float(indicator_text.split('MACD_Line: ')[1].split('.')[0].replace(',', ''))
                                                signal_line_val = float(indicator_text.split('Signal_Line: ')[1].split('.')[0].replace(',', ''))
                                                if macd_line_val > signal_line_val: assessment = "Bullish"
                                                elif macd_line_val < signal_line_val: assessment = "Bearish"
                                            except (ValueError, IndexError): pass
                                    elif indicator_name == 'BBANDS' and current_price_str != "N/A" and "Error" not in current_price_str:
                                        if "Upper_Band:" in indicator_text and "Lower_Band:" in indicator_text:
                                            try:
                                                upper_band = float(indicator_text.split('Upper_Band: ')[1].split('.')[0].replace(',', ''))
                                                lower_band = float(indicator_text.split('Lower_Band: ')[1].split('.')[0].replace(',', ''))
                                                current_price_val = float(current_price_str)
                                                
                                                if current_price_val > upper_band: assessment = "Bearish" # Price above upper band
                                                elif current_price_val < lower_band: assessment = "Bullish" # Price below lower band
                                                else: assessment = "Neutral" # Price within bands
                                            except (ValueError, IndexError): pass
                                    elif indicator_name == 'STOCHRSI':
                                        if "StochRSI_K:" in indicator_text and "StochRSI_D:" in indicator_text:
                                            try:
                                                stochrsi_k_val = float(indicator_text.split('StochRSI_K: ')[1].split('.')[0].replace(',', ''))
                                                stochrsi_d_val = float(indicator_text.split('StochRSI_D: ')[1].split('.')[0].replace(',', ''))
                                                
                                                if stochrsi_k_val > 80: assessment = "Bearish" # Overbought
                                                elif stochrsi_k_val < 20: assessment = "Bullish" # Oversold
                                                elif stochrsi_k_val > stochrsi_d_val: assessment = "Bullish" # K crossing above D
                                                elif stochrsi_k_val < stochrsi_d_val: assessment = "Bearish" # K crossing below D
                                            except (ValueError, IndexError): pass
                                
                                analysis_results.append(f"{indicator_name}: {assessment}")
                            except requests.exceptions.RequestException as e:
                                analysis_results.append(f"{indicator_name}: Data Missing (Error: {e})")
                                print(f"Error fetching {indicator_name}: {e}")
                            except Exception as e:
                                analysis_results.append(f"{indicator_name}: Data Missing (Unexpected Error: {e})")
                                print(f"Unexpected error fetching {indicator_name}: {e}")

                        # Combine all collected and analyzed data into a single string for the LLM
                        combined_analysis_context = "\n".join(analysis_results)
                        
                        # --- Truncate combined_analysis_context if too long ---
                        MAX_LLM_CONTEXT_LENGTH = 500 # Characters - a conservative limit
                        if len(combined_analysis_context) > MAX_LLM_CONTEXT_LENGTH:
                            original_len = len(combined_analysis_context)
                            combined_analysis_context = combined_analysis_context[:MAX_LLM_CONTEXT_LENGTH] + "\n... (analysis data truncated)"
                            print(f"Combined analysis context truncated from {original_len} to {len(combined_analysis_context)} characters.")

                        # --- Log the context sent to LLM for debugging ---
                        print(f"Context sent to LLM for final analysis:\n{combined_analysis_context}")

                        # Add the combined tool output to chat history for the LLM to see
                        chat_history.append({"role": "function", "parts": [{"functionResponse": {"name": "get_market_data", "response": {"text": combined_analysis_context}}}]})
                        
                        # Add a system instruction to guide the LLM's analysis
                        system_instruction = (
                            "Based on the following technical indicator assessments for "
                            f"{symbol_for_analysis}, summarize the overall sentiment (Bullish, Bearish, or Mixed). "
                            "Present the assessment for each indicator clearly, then give a concise overall summary. "
                            "Example: 'RSI: Bullish, MACD: Bearish. Overall: Mixed sentiment.'"
                            "If data is missing for an indicator, state 'N/A' or 'Data Missing'."
                        )
                        chat_history.insert(0, {"role": "system", "parts": [{"text": system_instruction}]})


                        # --- Final LLM Call: Generate analysis based on all collected data ---
                        llm_payload_final_turn = {
                            "contents": chat_history, # Updated chat history with all tool outputs
                            "safetySettings": [
                                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                            ]
                        }
                        try:
                            llm_response_final_turn = requests.post(llm_api_url, headers={'Content-Type': 'application/json'}, json=llm_payload_final_turn)
                            llm_response_final_turn.raise_for_status()
                            llm_data_final_turn = llm_response_final_turn.json()
                        except requests.exceptions.RequestException as e:
                            print(f"Error connecting to Gemini LLM (final turn after analysis data): {e}")
                            response_text_for_discord = f"I collected the analysis data, but I'm having trouble generating a full analysis with my AI brain. Please try again later. Error: {e}"
                            await message.channel.send(response_text_for_discord)
                            return # Exit early

                        if llm_data_final_turn and llm_data_final_turn.get('candidates'):
                            candidate_final_turn = llm_data_final_turn['candidates'][0]
                            if candidate_final_turn.get('content') and candidate_final_turn['content'].get('parts'):
                                response_text_for_discord = candidate_final_turn['content']['parts'][0].get('text', 'No analysis generated by AI.')
                            else:
                                # LLM responded but no text content (e.g., safety block, empty response)
                                print(f"LLM final turn: No text content in response. Full response: {llm_data_final_turn}")
                                block_reason = llm_data_final_turn.get('promptFeedback', {}).get('blockReason', 'unknown')
                                response_text_for_discord = f"AI could not generate a full analysis. This might be due to content policy. Block reason: {block_reason}. Please try rephrasing or a different symbol."
                        else:
                            # LLM response was empty or malformed
                            print(f"LLM final turn: No candidates in response. Full response: {llm_data_final_turn}")
                            response_text_for_discord = "Could not get a valid analysis from the AI. The response was empty or malformed. Please try again."

                # --- If LLM generated a direct text response (no tool call needed and not an "analyze" command) ---
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
        # This catch-all is for unexpected request errors outside of specific try blocks
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
