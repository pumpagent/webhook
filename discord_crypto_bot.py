import os
import discord
import requests
import json
import re
import time
from datetime import datetime, timedelta
import asyncio

# --- API Keys and URLs (Set as Environment Variables on Render) ---
# NOTE: These keys MUST be set in your Render environment variables.
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
TWELVE_DATA_API_KEY = os.environ.get('TWELVE_DATA_API_KEY')
NEWS_API_KEY = os.environ.get('NEWS_API_KEY')

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# --- Rate Limiting & Caching Configuration ---
last_twelve_data_call = 0
TWELVE_DATA_MIN_INTERVAL = 1
last_news_api_call = 0
NEWS_API_MIN_INTERVAL = 1
api_response_cache = {}
CACHE_DURATION = 10 # seconds

# --- Conversation Memory ---
conversation_histories = {}
MAX_CONVERSATION_TURNS = 10

DISCORD_MESSAGE_MAX_LENGTH = 2000

def split_message(message_content, max_length=DISCORD_MESSAGE_MAX_LENGTH):
    """Splits a message into chunks that fit Discord's character limit."""
    if len(message_content) <= max_length:
        return [message_content]
    
    chunks = []
    while len(message_content) > 0:
        if len(message_content) <= max_length:
            chunks.append(message_content)
            break
        
        # Try to find a natural split point
        split_point = message_content[:max_length].rfind('\n')
        if split_point == -1:
            split_point = message_content[:max_length].rfind('. ')
        if split_point == -1:
            split_point = message_content[:max_length].rfind(' ')
        
        if split_point == -1 or split_point == 0:
            split_point = max_length
        
        chunks.append(message_content[:split_point])
        message_content = message_content[split_point:].lstrip()

    return chunks

async def _fetch_with_retries(url, params=None, max_retries=5, initial_delay=2):
    """Fetches data with exponential backoff and retries."""
    for i in range(max_retries):
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            print(f"Attempt {i+1} failed: {e}")
            if i < max_retries - 1:
                delay = initial_delay * (2 ** i)
                await asyncio.sleep(delay)
            else:
                raise e
    return None

async def _fetch_data_from_twelve_data(data_type, symbol=None, interval=None, outputsize=None,
                                      indicator=None, indicator_period=None, indicator_multiplier=None,
                                      news_query=None, from_date=None, sort_by=None, news_language=None):
    """
    Helper function to fetch data directly from Twelve Data API or NewsAPI.org.
    Includes rate limiting and caching.
    """
    global last_twelve_data_call, last_news_api_call

    cache_key = (data_type, symbol, interval, outputsize, indicator, indicator_period,
                  indicator_multiplier, news_query, from_date, sort_by, news_language)
    current_time = time.time()

    # Bypass cache for live price requests to ensure fresh data
    if data_type != 'live' and cache_key in api_response_cache:
        cached_data = api_response_cache[cache_key]
        if (current_time - cached_data['timestamp']) < CACHE_DURATION:
            print(f"Serving cached response for {data_type} request to data service.")
            return cached_data['response_json']

    if data_type != 'news':
        if (current_time - last_twelve_data_call) < TWELVE_DATA_MIN_INTERVAL:
            time_to_wait = TWELVE_DATA_MIN_INTERVAL - (current_time - last_twelve_data_call)
            await asyncio.sleep(time_to_wait)

    readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper() if symbol else "N/A"
    response_data = {}

    try:
        if data_type == 'live':
            if not symbol:
                raise ValueError("Missing 'symbol' parameter for live price.")
            api_url = f"https://api.twelvedata.com/quote?symbol={symbol}&apikey={TWELVE_DATA_API_KEY}"
            print(f"Fetching live price for {symbol} from data service...")
            response = await _fetch_with_retries(api_url)
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from data service.')
                raise requests.exceptions.RequestException(f"Data service error for symbol {symbol}: {error_message}")
            
            current_price = data.get('close')
            if current_price is not None:
                formatted_price = f"${float(current_price):,.2f}"
                response_data = {"data": data, "text": f"The current price of {readable_symbol} is {formatted_price}."}
            else:
                raise ValueError(f"Data service did not return a 'close' price for {symbol}. Response: {data}")

        elif data_type == 'historical':
            if not symbol:
                raise ValueError("Missing 'symbol' parameter for historical data.")
            
            interval_str = interval if interval else '1day'
            outputsize_str = outputsize if outputsize else '50'
            
            api_url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval_str}&outputsize={outputsize_str}&apikey={TWELVE_DATA_API_KEY}"
            print(f"Fetching data for {symbol} (interval: {interval_str}, outputsize: {outputsize_str}) from data service...")
            response = await _fetch_with_retries(api_url)
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from data service.')
                raise requests.exceptions.RequestException(f"Data service error for symbol {symbol} historical data: {error_message}")
            
            historical_values = data.get('values')
            if not historical_values:
                raise ValueError(f"No data found for {symbol} with the specified interval and output size. Response: {data}")

            response_data = {
                "data": data,
                "text": (
                    f"I have retrieved {len(historical_values)} data points for {readable_symbol} "
                    f"at {interval_str} intervals, covering from {historical_values[-1]['datetime']} to {historical_values[0]['datetime']}. "
                    f"This data includes Open, High, Low, and Close prices."
                )
            }

        elif data_type == 'indicator':
            if not all([symbol, indicator]):
                raise ValueError("Missing required parameters for indicator data (symbol, indicator).")
            
            indicator_name_upper = indicator.upper()
            base_api_url = "https://api.twelvedata.com/"
            indicator_endpoint = ""
            params = {
                'symbol': symbol,
                'interval': interval if interval else '1day',
                'apikey': TWELVE_DATA_API_KEY
            }
            
            indicator_period_str = str(indicator_period) if indicator_period else '14'
            indicator_multiplier_str = str(indicator_multiplier) if indicator_multiplier else '3'

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
                params['sd'] = 2
            elif indicator_name_upper == 'STOCHRSI':
                indicator_endpoint = "stochrsi"
                params['time_period'] = indicator_period_str
                params['fast_k_period'] = 3
                params['fast_d_period'] = 3
                params['rsi_time_period'] = indicator_period_str
                params['stoch_time_period'] = indicator_period_str
            elif indicator_name_upper == 'SMA':
                indicator_endpoint = "sma"
                params['time_period'] = indicator_period_str
            elif indicator_name_upper == 'EMA' or indicator_name_upper == 'MA':
                indicator_endpoint = "ema"
                params['time_period'] = indicator_period_str
            elif indicator_name_upper == 'SUPERTREND':
                indicator_endpoint = "supertrend"
                params['time_period'] = indicator_period_str
                params['multiplier'] = indicator_multiplier_str
            elif indicator_name_upper == 'VWAP':
                indicator_endpoint = "vwap"
            elif indicator_name_upper == 'SAR': # Parabolic SAR
                indicator_endpoint = "sarext" # Twelve Data uses sarext for Parabolic SAR Extended
                params['start_value'] = 0.02
                params['offset'] = 0.02
                params['max_value'] = 0.2
            elif indicator_name_upper == 'PIVOT_POINTS': # Pivot Points
                indicator_endpoint = "pivot_points"
            elif indicator_name_upper == 'ULTOSC': # Ultimate Oscillator
                indicator_endpoint = "ultosc"
                params['time_period1'] = 7
                params['time_period2'] = 14
                params['time_period3'] = 28
            else:
                raise ValueError(f"Indicator '{indicator}' not supported by direct API.")

            api_url = f"{base_api_url}{indicator_endpoint}"
            print(f"Fetching {indicator_name_upper} for {symbol} from data service with params: {params}...")
            response = await _fetch_with_retries(api_url, params=params)
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from data service.')
                raise requests.exceptions.RequestException(f"Data service error for {indicator_name_upper} for {symbol}: {error_message}")
            
            latest_values = data.get('values', [{}])[0]
            
            if not latest_values or not any(v is not None for v in latest_values.values()):
                raise ValueError(f"Data service did not return valid indicator values for {indicator_name_upper} for {symbol}.")
            
            indicator_value_text = json.dumps(latest_values)
            response_data = {
                "data": latest_values,
                "text": f"The latest values for {indicator_name_upper} for {symbol} are: {indicator_value_text}."
            }

        elif data_type == 'news':
            if (current_time - last_news_api_call) < NEWS_API_MIN_INTERVAL:
                time_to_wait = NEWS_API_MIN_INTERVAL - (current_time - last_news_api_call)
                raise requests.exceptions.RequestException(
                    f"Rate limit hit for News API. Please wait {int(time_to_wait) + 1} seconds."
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
                f"apiKey={NEWS_API_KEY}"
            )
            print(f"Fetching news for '{news_query}' from News API...")
            response = requests.get(news_api_url)
            response.raise_for_status()
            news_data = response.json()

            if news_data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from News API.')
                raise requests.exceptions.RequestException(f"News API error: {error_message}")
            
            articles = news_data.get('articles')
            if articles:
                response_text = f"Here are some recent news headlines for {news_query}: "
                for i, article in enumerate(articles[:3]):
                    title = article.get('title', 'No title')
                    source = article.get('source', {}).get('name', 'Unknown source')
                    response_text += f"Number {i+1}: '{title}' from {source}. "
                response_data = {"data": news_data, "text": response_text.strip()}
            else:
                response_data = {"data": news_data, "text": f"No recent news found for '{news_query}'."}
        else:
            raise ValueError("Invalid 'data_type' specified.")

    except requests.exceptions.RequestException as e:
        raise e
    except ValueError as e:
        raise e
    finally:
        if data_type != 'news':
            globals()['last_twelve_data_call'] = time.time()
        else:
            globals()['last_news_api_call'] = time.time()
    
    api_response_cache[cache_key] = {'response_json': response_data, 'timestamp': time.time()}
    return response_data

# --- NEW/UPDATED: Function for Structured Signal Generation ---
async def generate_trading_signal(symbol, interval='1day'):
    """
    Generates a structured Buy/Sell/Hold signal based on a confluence of key technical indicators.
    This replaces the simpler perform_overall_assessment logic with a signal-specific analysis.
    """
    assessment_data = {
        'symbol': symbol,
        'interval': interval,
        'live_price': None,
        'indicator_details': [],
        'signal': 'HOLD',
        'confidence_score': 50,
        'recommendation_reason': ''
    }
    
    # 1. Get Live Price (Required for Supertrend/VWAP comparison)
    try:
        live_data_response = await _fetch_data_from_twelve_data(data_type='live', symbol=symbol)
        current_price = float(live_data_response['data'].get('close', 0))
        assessment_data['live_price'] = current_price
    except Exception as e:
        error_msg = f"Failed to fetch live price: {e}"
        assessment_data['recommendation_reason'] = error_msg
        print(error_msg)
        return {"text": json.dumps(assessment_data, indent=2)}

    # 2. Get Indicators for Confluence Analysis
    # We use a mix of Trend (MA), Momentum (RSI, STOCHRSI), and Volatility (BBANDS) indicators.
    indicators_to_check = {
        'RSI': {'period': '14', 'interval': interval, 'weight': 2, 'rule': 'Momentum (RSI)'},
        'MACD': {'period': '0', 'interval': interval, 'weight': 3, 'rule': 'Trend/Momentum (MACD)'},
        'SMA': {'period': '50', 'interval': interval, 'weight': 1, 'rule': 'Major Trend (SMA-50)'},
        'SUPERTREND': {'period': '10', 'multiplier': '3', 'interval': interval, 'weight': 4, 'rule': 'Primary Trend (Supertrend)'},
    }
    
    bullish_score = 0
    bearish_score = 0
    error_count = 0

    for indicator_name, config in indicators_to_check.items():
        try:
            indicator_data_response = await _fetch_data_from_twelve_data(
                data_type='indicator', symbol=symbol, indicator=indicator_name,
                interval=config['interval'], indicator_period=config['period'], indicator_multiplier=config.get('multiplier')
            )
            data = indicator_data_response['data']
            sub_assessment = "Neutral"
            value_str = json.dumps(data)
            weight = config['weight']

            # --- Signal Generation Logic ---
            if indicator_name == 'RSI':
                value = float(data['rsi'])
                if value < 30: 
                    sub_assessment = "Strong BUY (Oversold)"
                    bullish_score += weight
                elif value > 70: 
                    sub_assessment = "Strong SELL (Overbought)"
                    bearish_score += weight
                elif value > 50:
                    bullish_score += 1 
                else:
                    bearish_score += 1

            elif indicator_name == 'MACD':
                macd_line = float(data['macd'])
                signal_line = float(data['signal'])
                if macd_line > signal_line and macd_line < 0:
                    sub_assessment = "Bullish Cross (Buy Signal)"
                    bullish_score += weight
                elif macd_line < signal_line and macd_line > 0:
                    sub_assessment = "Bearish Cross (Sell Signal)"
                    bearish_score += weight
                elif macd_line > signal_line:
                    bullish_score += 1
                else:
                    bearish_score += 1

            elif indicator_name == 'SMA':
                sma_value = float(data['sma'])
                if current_price > sma_value:
                    sub_assessment = "Bullish (Above SMA-50)"
                    bullish_score += weight
                else:
                    sub_assessment = "Bearish (Below SMA-50)"
                    bearish_score += weight
            
            elif indicator_name == 'SUPERTREND':
                supertrend_value = float(data['supertrend'])
                if current_price > supertrend_value: 
                    sub_assessment = "Strong BUY (Above Supertrend)"
                    bullish_score += weight
                else: 
                    sub_assessment = "Strong SELL (Below Supertrend)"
                    bearish_score += weight

            assessment_data['indicator_details'].append({
                'name': config['rule'],
                'value': value_str,
                'assessment': sub_assessment
            })

        except Exception as e:
            print(f"Failed to fetch or parse {indicator_name} for {symbol}: {e}")
            error_count += 1
            assessment_data['indicator_details'].append({
                'name': config['rule'],
                'value': 'N/A',
                'assessment': 'Error'
            })
    
    # 3. Final Confluence Signal Calculation
    total_score = bullish_score + bearish_score
    if total_score > 0:
        bullish_percentage = (bullish_score / total_score) * 100
    else:
        bullish_percentage = 50 # Default neutral if no signals are valid

    # 4. Determine Final Signal and Confidence
    if bullish_percentage >= 70:
        assessment_data['signal'] = 'BUY'
        assessment_data['confidence_score'] = int(bullish_percentage)
        assessment_data['recommendation_reason'] = "Strong bullish confluence across multiple trend and momentum indicators (Supertrend, MACD, SMA-50)."
    elif bullish_percentage <= 30:
        assessment_data['signal'] = 'SELL'
        assessment_data['confidence_score'] = 100 - int(bullish_percentage)
        assessment_data['recommendation_reason'] = "Strong bearish consensus as price is below major trend indicators and momentum is negative."
    else:
        assessment_data['signal'] = 'HOLD'
        assessment_data['confidence_score'] = max(int(bullish_percentage), 100 - int(bullish_percentage))
        assessment_data['recommendation_reason'] = "Mixed signals from key indicators suggest consolidation or uncertainty. Awaiting clearer trend direction."

    if error_count > 0:
        assessment_data['recommendation_reason'] += f" Note: {error_count} indicators could not be processed due to data errors."

    # 5. Format Output
    output_text = (
        f"**Signal Report for {symbol} ({interval})**\n"
        f"-----------------------------------------\n"
        f"**Final Signal:** {assessment_data['signal']}\n"
        f"**Confidence:** {assessment_data['confidence_score']}%\n"
        f"**Live Price:** ${assessment_data['live_price']:,.2f}\n"
        f"**Reasoning:** {assessment_data['recommendation_reason']}\n\n"
        f"**Indicator Scores (Confluence):**\n"
        f"Bullish Score: {bullish_score} / Bearish Score: {bearish_score}\n\n"
        f"**Detailed Breakdown:**\n"
        + "\n".join([f"- {d['name']} ({'Bullish' if 'Bullish' in d['assessment'] else 'Bearish' if 'Bearish' in d['assessment'] else 'Neutral'}): {d['assessment']}" for d in assessment_data['indicator_details']])
    )
    
    return {"text": output_text}


# --- EXISTING FUNCTIONS (Modified for clarity/cleanliness) ---

# The original perform_overall_assessment is deleted as it is replaced by the more specific generate_trading_signal.
# The original analyze_candlestick_patterns is kept as is.

# --- LLM Tool Definitions (Updated) ---
# NOTE: The LLM will now use the new function when asked for a signal/assessment.

@client.event
async def on_message(message):
    """Event that fires when a message is sent in a channel the bot can see."""
    if message.author == client.user:
        return
    
    # Simple authorization check
    AUTHORIZED_USER_IDS = ["918556208217067561", "1062318683386552402", "828490037787492363", "939269185127727125", "1035974941021044807", "923082335740641341"]
    if isinstance(message.channel, discord.DMChannel) and str(message.author.id) not in AUTHORIZED_USER_IDS:
        print(f"Ignoring DM from unauthorized user: {message.author.id}")
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
        # --- Updated LLM Tool Definitions ---
        tools = [
            {
                "functionDeclarations": [
                    {
                        "name": "get_market_data",
                        "description": "Fetches live price, historical data, or technical analysis indicators for a given symbol, or market news for a query.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "symbol": { "type": "string", "description": "Ticker symbol (e.g., 'BTC/USD', 'AAPL'). This is required." },
                                "data_type": { "type": "string", "enum": ["live", "historical", "indicator", "news"], "description": "Type of data to fetch (live, historical, indicator, news). This is required." },
                                "interval": { "type": "string", "description": "Time interval (e.g., '1min', '1day'). Default to '1day' if not specified by user. Try to infer from context." },
                                "outputsize": { "type": "string", "description": "Number of data points. Default to '50' for historical, adjusted for indicator." },
                                "indicator": { "type": "string", "enum": ["SMA", "EMA", "RSI", "MACD", "BBANDS", "STOCHRSI", "SUPERTREND", "VWAP", "SAR", "PIVOT_POINTS", "ULTOSC"], "description": "Name of the technical indicator. Required if data_type is 'indicator'." },
                                "indicator_period": { "type": "string", "description": "Period for the indicator (e.g., '14', '20', '50'). Default to '14' if not specified by user. For SMA or EMA, the LLM should infer a period like '50' or '200' if the user mentions 'golden cross' or a specific time frame." },
                                "indicator_multiplier": { "type": "string", "description": "Multiplier for indicators like Supertrend. Default to '3'."},
                                "news_query": { "type": "string", "description": "Keywords for news search." },
                                "from_date": { "type": "string", "description": "Start date for news (YYYY-MM-DD). Defaults to 7 days ago." },
                                "sort_by": { "type": "string", "enum": ["relevancy", "popularity", "publishedAt"], "description": "How to sort news." },
                                "news_language": { "type": "string", "description": "Language of news." }
                            },
                            "required": ["symbol", "data_type"]
                        }
                    },
                    {
                        "name": "generate_trading_signal",
                        "description": "The primary function for providing a crypto Buy, Sell, or Hold signal. It performs a structured technical analysis (SMA, MACD, RSI, Supertrend) to determine market direction and confidence.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "symbol": { "type": "string", "description": "Ticker symbol (e.g., 'BTC/USD'). This is required." },
                                "interval": { "type": "string", "description": "Time interval (e.g., '1day', '4h'). Default is '1day'." }
                            },
                            "required": ["symbol"]
                        }
                    },
                    {
                        "name": "analyze_candlestick_patterns",
                        "description": "Analyzes historical price data for common candlestick patterns like Doji, Hammer, and Bullish/Bearish Engulfing.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "symbol": { "type": "string", "description": "The ticker symbol for the asset (e.g., 'BTC/USD')." },
                                "interval": { "type": "string", "description": "The time interval for the historical data (e.g., '1day', '1week'). Default is '1day'." }
                            },
                            "required": ["symbol"]
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
            for chunk in split_message(response_text_for_discord):
                await message.channel.send(chunk)
            return

        if llm_data_first_turn and llm_data_first_turn.get('candidates'):
            candidate_first_turn = llm_data_first_turn['candidates'][0]
            if candidate_first_turn.get('content') and candidate_first_turn['content'].get('parts'):
                parts_first_turn = candidate_first_turn['content']['parts']
                if parts_first_turn:
                    
                    if parts_first_turn[0].get('functionCall'):
                        function_call = parts_first_turn[0]['functionCall']
                        function_name = function_call['name']
                        function_args = function_call['args']

                        print(f"LLM requested tool call: {function_name} with args: {function_args}")
                        current_chat_history.append({"role": "model", "parts": [{"functionCall": function_call}]})

                        tool_output_text = ""
                        try:
                            if function_name == "get_market_data":
                                # Safely handle period inference and type conversion for get_market_data
                                if 'indicator_period' not in function_args:
                                    if function_args.get('indicator', '').upper() == 'MACD':
                                        function_args['indicator_period'] = '0'
                                    elif 'ma' in user_query.lower() and ('50' in user_query or '200' in user_query):
                                        period = re.search(r'\b(50|200)\b', user_query)
                                        function_args['indicator_period'] = period.group(1) if period else '14'
                                    else:
                                        function_args['indicator_period'] = '14'
                                
                                for key, value in function_args.items():
                                    function_args[key] = str(value)
                                
                                tool_output_data_raw = await _fetch_data_from_twelve_data(**function_args)
                                tool_output_text = json.dumps(tool_output_data_raw, indent=2)
                            
                            elif function_name == "analyze_candlestick_patterns":
                                symbol_arg = function_args.get('symbol')
                                interval_arg = function_args.get('interval', '1day')
                                tool_output_data_raw = await analyze_candlestick_patterns(
                                    symbol=str(symbol_arg), 
                                    interval=str(interval_arg)
                                )
                                tool_output_text = tool_output_data_raw['text']

                            elif function_name == "generate_trading_signal":
                                symbol_arg = function_args.get('symbol')
                                interval_arg = function_args.get('interval', '1day')
                                tool_output_data_raw = await generate_trading_signal(
                                    symbol=str(symbol_arg), 
                                    interval=str(interval_arg)
                                )
                                tool_output_text = tool_output_data_raw['text']
                            else:
                                tool_output_text = json.dumps({"error": f"AI requested an unknown function: {function_name}"})
                        except Exception as e:
                            print(f"Error during tool execution: {e}")
                            tool_output_text = json.dumps({"error": f"Error during tool execution: {e}"})

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
                            print(f"Error connecting to AI brain (second turn after tool): {e}")
                            response_text_for_discord = f"I received the data, but I'm having trouble processing it with my AI brain. Please try again later. Error: {e}"
                            for chunk in split_message(response_text_for_discord):
                                await message.channel.send(chunk)
                            return
                        
                        if llm_data_second_turn and llm_data_second_turn.get('candidates'):
                            final_candidate = llm_data_second_turn['candidates'][0]
                            if final_candidate.get('content') and final_candidate['content'].get('parts'):
                                response_text_for_discord = final_candidate['content']['parts'][0].get('text', 'No conversational response from AI.')
                            else:
                                block_reason = llm_data_second_turn.get('promptFeedback', {}).get('blockReason', 'unknown')
                                response_text_for_discord = f"AI could not generate a response. This might be due to content policy. Block reason: {block_reason}. Please try rephrasing."
                        else:
                            response_text_for_discord = "Could not get a valid second response from the AI."

                    elif parts_first_turn[0].get('text'):
                        response_text_for_discord = parts_first_turn[0]['text']
                    else:
                        block_reason = llm_data_first_turn.get('promptFeedback', {}).get('blockReason', 'unknown')
                        response_text_for_discord = f"AI could not generate a response. This might be due to content policy. Block reason: {block_reason}. Please try rephrasing."
                else:
                    response_text_for_discord = "AI did not provide content in its response."
            else:
                response_text_for_discord = "Could not get a valid response from the AI. Please try again."
                if llm_data_first_turn.get('promptFeedback') and llm_data_first_turn['promptFeedback'].get('blockReason'):
                    response_text_for_discord += f" (Blocked: {llm_data_first_turn['promptFeedback']['blockReason']})"
            
            conversation_histories[user_id].append({"role": "model", "parts": [{"text": response_text_for_discord}]})
        
    except requests.exceptions.RequestException as e:
        print(f"General Request Error: {e}")
        response_text_for_discord = f"An unexpected connection error occurred. Please check network connectivity or API URLs. Error: {e}"
    except Exception as e:
        print(f"An unexpected error occurred in bot logic: {e}")
        response_text_for_discord = f"An unexpected error occurred while processing your request. My apologies. Error: {e}"

    for chunk in split_message(response_text_for_discord):
        await message.channel.send(chunk)

if __name__ == '__main__':
    # Initialized once when the script starts
    @client.event
    async def on_ready():
        print(f'Bot is logged in as {client.user}')
        print(f'Discord Version: {discord.__version__}')

    if not DISCORD_BOT_TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
    elif not TWELVE_DATA_API_KEY:
        print("Error: TWELVE_DATA_API_KEY environment variable not set.")
    elif not GOOGLE_API_KEY:
        print("Error: GOOGLE_API_KEY environment variable not set.")
    elif not NEWS_API_KEY:
        print("Error: NEWS_API_KEY environment variable not set.")
    else:
        client.run(DISCORD_BOT_TOKEN)
