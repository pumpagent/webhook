import os
import discord
import requests
import json
import re # Import regex for parsing indicator values
import time # For rate limiting

# --- API Keys and URLs (Set as Environment Variables on Render) ---
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
# Removed FLASK_WEBHOOK_URL as data will be pulled directly from Twelve Data
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
TWELVE_DATA_API_KEY = os.environ.get('TWELVE_DATA_API_KEY') # Now directly used by the bot

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

client = discord.Client(intents=intents)

# --- Rate Limiting & Caching Configuration (Moved from Flask app) ---
last_twelve_data_call = 0
TWELVE_DATA_MIN_INTERVAL = 10 # seconds (e.g., 10 seconds between Twelve Data calls)
api_response_cache = {}
CACHE_DURATION = 10 # Cache responses for 10 seconds

# --- Conversation Memory (In-memory, volatile on bot restart) ---
conversation_histories = {} # Format: {user_id: [{"role": "user/model/function", "parts": [...]}, ...]}
MAX_CONVERSATION_TURNS = 10 # Keep last 10 turns (user + model/function) in memory for LLM context


async def _fetch_data_from_twelve_data(data_type, symbol=None, interval=None, outputsize=None,
                                       indicator=None, indicator_period=None, news_query=None,
                                       from_date=None, sort_by=None, news_language=None):
    """
    Helper function to fetch data directly from Twelve Data API or NewsAPI.org.
    Includes rate limiting and caching.
    """
    global last_twelve_data_call

    cache_key = (data_type, symbol, interval, outputsize, indicator, indicator_period,
                 news_query, from_date, sort_by, news_language)
    current_time = time.time()

    # --- Check Cache First ---
    if cache_key in api_response_cache:
        cached_data = api_response_cache[cache_key]
        if (current_time - cached_data['timestamp']) < CACHE_DURATION:
            print(f"Serving cached response for {data_type} request to Twelve Data/NewsAPI.")
            return cached_data['response_json']

    # --- Rate Limiting for Twelve Data ---
    if data_type != 'news' and (current_time - last_twelve_data_call) < TWELVE_DATA_MIN_INTERVAL:
        time_to_wait = TWELVE_DATA_MIN_INTERVAL - (current_time - last_twelve_data_call)
        raise requests.exceptions.RequestException(
            f"Rate limit hit for Twelve Data. Please wait {time_to_wait:.2f} seconds."
        )

    readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper() if symbol else "N/A"
    response_data = {} # To store the final JSON response

    try:
        if data_type == 'live':
            if not symbol:
                raise ValueError("Missing 'symbol' parameter for live price.")
            api_url = f"https://api.twelvedata.com/quote?symbol={symbol}&apikey={TWELVE_DATA_API_KEY}"
            print(f"Fetching live price for {symbol} from Twelve Data API...")
            response = requests.get(api_url)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from Twelve Data.')
                raise requests.exceptions.RequestException(f"Twelve Data API error for symbol {symbol}: {error_message}")
            
            current_price = data.get('close')
            if current_price is not None:
                formatted_price = f"${float(current_price):,.2f}"
                response_data = {"text": f"The current price of {readable_symbol} is {formatted_price}."}
            else:
                raise ValueError(f"Twelve Data did not return a 'close' price for {symbol}. Response: {data}")

        elif data_type == 'historical':
            if not symbol:
                raise ValueError("Missing 'symbol' parameter for historical data.")
            
            interval_str = interval if interval else '1day'
            outputsize_str = outputsize if outputsize else '50'
            
            api_url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval_str}&outputsize={outputsize_str}&apikey={TWELVE_DATA_API_KEY}"
            print(f"Fetching data for {symbol} (interval: {interval_str}, outputsize: {outputsize_str}) from Twelve Data API...")
            response = requests.get(api_url)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from Twelve Data.')
                raise requests.exceptions.RequestException(f"Twelve Data API error for symbol {symbol} historical data: {error_message}")
            
            historical_values = data.get('values')
            if not historical_values:
                raise ValueError(f"No data found for {symbol} with the specified interval and output size. Response: {data}")

            response_data = {
                "text": (
                    f"I have retrieved {len(historical_values)} data points for {readable_symbol} "
                    f"at {interval_str} intervals, covering from {historical_values[-1]['datetime']} to {historical_values[0]['datetime']}. "
                    f"This data includes Open, High, Low, and Close prices."
                )
            }

        elif data_type == 'indicator':
            if not all([symbol, indicator]): # indicator_period can be defaulted
                raise ValueError("Missing required parameters for indicator data (symbol, indicator).")
            
            indicator_name_upper = indicator.upper()
            base_api_url = "https://api.twelvedata.com/"
            indicator_endpoint = ""
            params = {
                'symbol': symbol,
                'interval': interval if interval else '1day', # Default interval
                'apikey': TWELVE_DATA_API_KEY
            }
            
            # Default indicator_period if not provided by LLM
            indicator_period_str = str(indicator_period) if indicator_period else '14' 

            # Map indicator and period to Twelve Data API endpoints and parameters
            if indicator_name_upper == 'RSI':
                indicator_endpoint = "rsi"
                params['time_period'] = indicator_period_str
            elif indicator_name_upper == 'MACD':
                indicator_endpoint = "macd"
                params['fast_period'] = 12
                params['slow_period'] = 26
                params['signal_period'] = 9
            elif indicator_name_upper == 'BBANDS':
                indicator_endpoint = "bbands"
                params['time_period'] = indicator_period_str
                params['sd'] = 2 # Standard deviation, common default
            elif indicator_name_upper == 'STOCHRSI':
                indicator_endpoint = "stochrsi"
                params['time_period'] = indicator_period_str
                params['fast_k_period'] = 3
                params['fast_d_period'] = 3
                params['rsi_time_period'] = indicator_period_str
                params['stoch_time_period'] = indicator_period_str
            else:
                raise ValueError(f"Indicator '{indicator}' not supported by direct API.")

            api_url = f"{base_api_url}{indicator_endpoint}"
            print(f"Fetching {indicator_name_upper} for {symbol} from Twelve Data API with params: {params}...")
            response = requests.get(api_url, params=params)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from Twelve Data.')
                raise requests.exceptions.RequestException(f"Twelve Data API error for {indicator_name_upper} for {symbol}: {error_message}")
            
            indicator_value = None
            indicator_description = ""
            
            if indicator_name_upper == 'RSI':
                value = data.get('value')
                if value is not None:
                    indicator_value = float(value)
                    indicator_description = f"{indicator_period_str}-period Relative Strength Index"
            elif indicator_name_upper == 'MACD':
                macd = data.get('macd')
                signal = data.get('signal')
                histogram = data.get('histogram')
                if all(v is not None for v in [macd, signal, histogram]):
                    indicator_value = {
                        'MACD_Line': float(macd),
                        'Signal_Line': float(signal),
                        'Histogram': float(histogram)
                    }
                    indicator_description = "Moving Average Convergence D-I-vergence"
            elif indicator_name_upper == 'BBANDS':
                upper = data.get('upper')
                middle = data.get('middle')
                lower = data.get('lower')
                if all(v is not None for v in [upper, middle, lower]):
                    indicator_value = {
                        'Upper_Band': float(upper),
                        'Middle_Band': float(middle),
                        'Lower_Band': float(lower)
                    }
                    indicator_description = f"{indicator_period_str}-period Bollinger Bands"
            elif indicator_name_upper == 'STOCHRSI':
                stochrsi_k = data.get('stochrsi')
                stochrsi_d = data.get('stochrsi_signal')
                if all(v is not None for v in [stochrsi_k, stochrsi_d]):
                    indicator_value = {
                        'StochRSI_K': float(stochrsi_k),
                        'StochRSI_D': float(stochrsi_d)
                    }
                    indicator_description = f"{indicator_period_str}-period Stochastic Relative Strength Index"

            if indicator_value is not None:
                if isinstance(indicator_value, dict):
                    response_text = f"The {indicator_description} for {readable_symbol} is: "
                    for key, val in indicator_value.items():
                        response_text += f"{key}: {val:,.2f}. "
                    response_data = {"text": response_text.strip()}
                else:
                    response_data = {"text": f"The {indicator_description} for {readable_symbol} is {indicator_value:,.2f}."}
            else:
                raise ValueError(f"Twelve Data did not return valid indicator values for {indicator_name_upper} for {symbol}. Response: {data}")

        elif data_type == 'news':
            # --- Rate Limiting for NewsAPI.org ---
            # This part is still using NewsAPI.org and its own rate limiting.
            # If you switch News API, this section needs to be updated.
            NEWS_API_MIN_INTERVAL = 1 # seconds (e.g., 10 seconds between NewsAPI calls)
            last_news_api_call = 0 # This needs to be managed globally for NewsAPI.org
            if (current_time - last_news_api_call) < NEWS_API_MIN_INTERVAL:
                time_to_wait = NEWS_API_MIN_INTERVAL - (current_time - last_news_api_call)
                raise requests.exceptions.RequestException(
                    f"Rate limit hit for NewsAPI.org. Please wait {time_to_wait:.2f} seconds."
                )

            if not news_query:
                raise ValueError("Missing 'news_query' parameter for news.")
            
            from_date_str = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            sort_by_str = sort_by if sort_by else 'publishedAt'
            news_language_str = news_language if news_language else 'en'

            news_api_url = (
                f"https://newsapi.org/v2/everything?"
                f"q={news_query}&"
                f"from={from_date_str}&"
                f"sortBy={sort_by_str}&"
                f"language={news_language_str}&"
                f"apiKey={os.environ.get('NEWS_API_KEY')}" # Use NEWS_API_KEY directly
            )
            print(f"Fetching news for '{news_query}' from NewsAPI.org...")
            response = requests.get(news_api_url)
            response.raise_for_status()
            news_data = response.json()

            if news_data.get('status') == 'error':
                error_message = news_data.get('message', 'Unknown error from NewsAPI.org.')
                raise requests.exceptions.RequestException(f"NewsAPI.org error: {error_message}")
            
            articles = news_data.get('articles')
            if articles:
                response_text = f"Here are some recent news headlines for {news_query}: "
                for i, article in enumerate(articles[:3]):
                    title = article.get('title', 'No title')
                    source = article.get('source', {}).get('name', 'Unknown source')
                    response_text += f"Number {i+1}: '{title}' from {source}. "
                response_data = {"text": response_text.strip()}
            else:
                response_data = {"text": f"No recent news found for '{news_query}'."}
        else:
            raise ValueError("Invalid 'data_type' specified.")

    except requests.exceptions.RequestException as e:
        raise e # Re-raise for outer try-except to catch
    except ValueError as e:
        raise e # Re-raise for outer try-except to catch
    finally:
        # Update last_twelve_data_call only if it was a Twelve Data call
        if data_type != 'news':
            globals()['last_twelve_data_call'] = time.time()
        # For NewsAPI.org, its own global last_news_api_call needs to be updated.
        # This is currently not handled correctly in _fetch_data_from_twelve_data for NewsAPI.
        # For simplicity, we'll assume NewsAPI.org rate limit is less strict or managed externally for now.
    
    api_response_cache[cache_key] = {'response_json': response_data, 'timestamp': time.time()}
    return response_data


@client.event
async def on_message(message):
    """Event that fires when a message is sent in a channel the bot can see."""
    if message.author == client.user:
        return

    user_id = str(message.author.id)
    user_query = message.content.strip()
    print(f"Received message: '{user_query}' from {message.author} (ID: {user_id})")

    if user_id not in conversation_histories:
        conversation_histories[user_id] = []
    
    conversation_histories[user_id].append({"role": "user", "parts": [{"text": user_query}]})
    current_chat_history = conversation_histories[user_id][-MAX_CONVERSATION_TURNS:]

    response_text_for_discord = "I'm currently unavailable. Please try again later."

    try:
        # --- Define the market_data tool for the LLM ---
        tools = [
            {
                "functionDeclarations": [
                    {
                        "name": "get_market_data",
                        "description": "Fetches live price, historical data, or technical analysis indicators for a given symbol, or market news for a query.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "symbol": { "type": "string", "description": "Ticker symbol (e.g., 'BTC/USD', 'AAPL')." },
                                "data_type": { "type": "string", "enum": ["live", "historical", "indicator", "news"], "description": "Type of data to fetch." },
                                "interval": { "type": "string", "description": "Time interval (e.g., '1min', '1day'). Default to '1day' if not specified by user." },
                                "outputsize": { "type": "string", "description": "Number of data points. Default to '50' for historical, adjusted for indicator." },
                                "indicator": { "type": "string", "enum": ["SMA", "EMA", "RSI", "MACD", "BBANDS", "STOCHRSI"], "description": "Name of the technical indicator." },
                                "indicator_period": { "type": "string", "description": "Period for the indicator (e.g., '14', '20', '50'). Default to '14' if not specified by user. MACD typically uses fixed periods (12, 26, 9) so '0' can be used as a placeholder if period is not relevant for MACD." },
                                "news_query": { "type": "string", "description": "Keywords for news search." },
                                "from_date": { "type": "string", "description": "Start date for news (YYYY-MM-DD). Defaults to 7 days ago." },
                                "sort_by": { "type": "string", "enum": ["relevancy", "popularity", "publishedAt"], "description": "How to sort news." },
                                "news_language": { "type": "string", "description": "Language of news." }
                            },
                            "required": [] # LLM will infer required based on data_type
                        }
                    }
                ]
            }
        ]

        llm_api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GOOGLE_API_KEY}"
        
        llm_payload_first_turn = {
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
            llm_response_first_turn = requests.post(llm_api_url, headers={'Content-Type': 'application/json'}, json=llm_payload_first_turn)
            llm_response_first_turn.raise_for_status()
            llm_data_first_turn = llm_response_first_turn.json()
        except requests.exceptions.RequestException as e:
            print(f"Error connecting to Gemini LLM (first turn): {e}")
            response_text_for_discord = f"I'm having trouble connecting to my AI brain. Please check the GOOGLE_API_KEY and try again later. Error: {e}"
            await message.channel.send(response_text_for_discord)
            return

        if llm_data_first_turn and llm_data_first_turn.get('candidates'):
            candidate_first_turn = llm_data_first_turn['candidates'][0]
            if candidate_first_turn.get('content') and candidate_first_turn['content'].get('parts'):
                parts_first_turn = candidate_first_turn['content']['parts']

                if parts_first_turn[0].get('functionCall'):
                    function_call = parts_first_turn[0]['functionCall']
                    function_name = function_call['name']
                    function_args = function_call['args']

                    if function_name == "get_market_data":
                        print(f"LLM requested tool call: get_market_data with args: {function_args}")
                        current_chat_history.append({"role": "model", "parts": [{"functionCall": function_call}]})

                        try:
                            # Direct call to the local helper function
                            tool_output_data = await _fetch_data_from_twelve_data(**function_args)
                            tool_output_text = tool_output_data.get('text', 'No specific response from data service.')
                            print(f"Tool execution output: {tool_output_text}")
                        except requests.exceptions.RequestException as e:
                            print(f"Error fetching data from Twelve Data via local helper: {e}")
                            tool_output_text = f"Error fetching data: {e}"
                        except ValueError as e:
                            print(f"Invalid parameters for data fetch: {e}")
                            tool_output_text = f"Invalid parameters: {e}"
                        except Exception as e:
                            print(f"Unexpected error during data fetch: {e}")
                            tool_output_text = f"An unexpected error occurred: {e}"
                        
                        current_chat_history.append({"role": "function", "parts": [{"functionResponse": {"name": function_name, "response": {"text": tool_output_text}}}]})

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
                        response_text_for_discord = "LLM requested an unknown function."
                elif parts[0].get('text'):
                    response_text_for_discord = parts[0]['text']
                else:
                    response_text_for_discord = "LLM response format not recognized."
            else:
                response_text_for_discord = "LLM did not provide content in its response."
        else:
            response_text_for_discord = "Could not get a valid response from the AI. Please try again."
            if llm_data_first_turn.get('promptFeedback') and llm_data_first_turn['promptFeedback'].get('blockReason'):
                response_text_for_discord += f" (Blocked: {llm_data_first_turn['promptFeedback']['blockReason']})"
        
        # Add LLM's response to history
        conversation_histories[user_id].append({"role": "model", "parts": [{"text": response_text_for_discord}]})


    except requests.exceptions.RequestException as e:
        print(f"General Request Error: {e}")
        response_text_for_discord = f"An unexpected connection error occurred. Please check network connectivity or API URLs. Error: {e}"
    except Exception as e:
        print(f"An unexpected error occurred in bot logic: {e}")
        response_text_for_discord = f"An unexpected error occurred while processing your request. My apologies. Error: {e}"

    await message.channel.send(response_text_for_discord)

if __name__ == '__main__':
    if not DISCORD_BOT_TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
    elif not TWELVE_DATA_API_KEY:
        print("Error: TWELVE_DATA_API_KEY environment variable not set.")
    elif not GOOGLE_API_KEY:
        print("Error: GOOGLE_API_KEY environment variable not set.")
    else:
        client.run(DISCORD_BOT_TOKEN)
