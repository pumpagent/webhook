import os
import discord
import requests
import json
import re
import time
from datetime import datetime, timedelta
import asyncio

# --- API Keys and URLs (Set as Environment Variables on Render) ---
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
TWELVE_DATA_API_KEY = os.environ.get('TWELVE_DATA_API_KEY')
NEWS_API_KEY = os.environ.get('NEWS_API_KEY')

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

client = discord.Client(intents=intents)

# --- Rate Limiting & Caching Configuration ---
last_twelve_data_call = 0
TWELVE_DATA_MIN_INTERVAL = 1
last_news_api_call = 0
NEWS_API_MIN_INTERVAL = 1
api_response_cache = {}
CACHE_DURATION = 10

# --- Conversation Memory (In-memory, volatile on bot restart) ---
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
                print(f"Retrying in {delay} seconds...")
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

    if cache_key in api_response_cache:
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
                error_message = news_data.get('message', 'Unknown error from News API.')
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

# --- New Function for Overall Assessment ---
async def perform_overall_assessment(symbol):
    """
    Performs a technical analysis using multiple indicators to provide an overall assessment.
    Returns a dictionary with the assessment and a summary text.
    """
    assessment_data = {
        'symbol': symbol,
        'live_price': None,
        'indicator_details': [],
        'overall_sentiment': 'Neutral',
        'summary': ''
    }
    
    # 1. Get Live Price
    try:
        live_data_response = await _fetch_data_from_twelve_data(data_type='live', symbol=symbol)
        live_data = live_data_response['data']
        current_price = float(live_data.get('close', 0))
        assessment_data['live_price'] = current_price
    except Exception as e:
        print(f"Failed to fetch live price for {symbol}: {e}")
        assessment_data['overall_sentiment'] = 'Error'
        assessment_data['summary'] = f"Failed to get live price, cannot perform full assessment: {e}"
        return {"text": json.dumps(assessment_data, indent=2)}

    # 2. Get Indicators and store values
    indicators_to_check = {
        'RSI': {'period': '14', 'description': 'Relative Strength Index'},
        'MACD': {'period': '0', 'description': 'Moving Average Convergence Divergence'},
        'BBANDS': {'period': '20', 'description': 'Bollinger Bands'},
        'SUPERTREND': {'period': '10', 'multiplier': '3', 'description': 'Supertrend'},
        'SMA_50': {'indicator': 'SMA', 'period': '50', 'description': '50-period Simple Moving Average'},
        'SMA_200': {'indicator': 'SMA', 'period': '200', 'description': '200-period Simple Moving Average'},
        'EMA': {'period': '20', 'description': '20-period Exponential Moving Average'},
        'STOCHRSI': {'period': '14', 'description': 'Stochastic Relative Strength Index'},
        'VWAP': {'period': '0', 'description': 'Volume Weighted Average Price'},
    }
    
    for indicator_name, config in indicators_to_check.items():
        try:
            api_indicator_name = config.get('indicator', indicator_name)
            
            indicator_data_response = await _fetch_data_from_twelve_data(
                data_type='indicator', symbol=symbol, indicator=api_indicator_name,
                indicator_period=config['period'], indicator_multiplier=config.get('multiplier')
            )
            data = indicator_data_response['data']

            sub_assessment = "Neutral"
            value = None

            # --- Analysis Logic for each indicator ---
            if 'rsi' in data:
                value = float(data['rsi'])
                if value < 30: sub_assessment = "Bullish"
                elif value > 70: sub_assessment = "Bearish"
            elif 'macd' in data and 'signal' in data:
                macd_line = float(data['macd'])
                signal_line = float(data['signal'])
                if macd_line > signal_line: sub_assessment = "Bullish"
                elif macd_line < signal_line: sub_assessment = "Bearish"
            elif 'upper' in data and 'lower' in data and current_price is not None:
                upper_band = float(data['upper'])
                lower_band = float(data['lower'])
                if current_price > upper_band: sub_assessment = "Bearish"
                elif current_price < lower_band: sub_assessment = "Bullish"
            elif 'supertrend' in data and current_price is not None:
                supertrend_value = float(data['supertrend'])
                if current_price > supertrend_value: sub_assessment = "Bullish"
                else: sub_assessment = "Bearish"
            elif 'value' in data and current_price is not None:
                value = float(data['value'])
                if current_price > value: sub_assessment = "Bullish"
                else: sub_assessment = "Bearish"
            elif 'stochrsi' in data:
                stoch_k = float(data['stochrsi'])
                if stoch_k > 80: sub_assessment = "Bearish"
                elif stoch_k < 20: sub_assessment = "Bullish"
            elif 'vwap' in data and current_price is not None:
                value = float(data['vwap'])
                if current_price > value: sub_assessment = "Bullish"
                else: sub_assessment = "Bearish"
            
            assessment_data['indicator_details'].append({
                'name': config['description'],
                'value': value if value is not None else data,
                'assessment': sub_assessment
            })

        except Exception as e:
            print(f"Failed to fetch or parse {indicator_name} for {symbol}: {e}")
            assessment_data['indicator_details'].append({
                'name': config['description'],
                'value': 'N/A',
                'assessment': 'Error'
            })

    # 3. Final Assessment
    bullish_count = sum(1 for d in assessment_data['indicator_details'] if d['assessment'] == 'Bullish')
    bearish_count = sum(1 for d in assessment_data['indicator_details'] if d['assessment'] == 'Bearish')
    error_count = sum(1 for d in assessment_data['indicator_details'] if d['assessment'] == 'Error')
    neutral_count = sum(1 for d in assessment_data['indicator_details'] if d['assessment'] == 'Neutral')

    if bullish_count > (bearish_count + neutral_count) and bullish_count > 0:
        assessment_data['overall_sentiment'] = 'Bullish'
    elif bearish_count > (bullish_count + neutral_count) and bearish_count > 0:
        assessment_data['overall_sentiment'] = 'Bearish'
    elif bullish_count > 0 or bearish_count > 0:
        assessment_data['overall_sentiment'] = 'Neutral'
    else:
        assessment_data['overall_sentiment'] = 'Error'

    indicator_list = "\n".join([f"- **{d['name']}**: {d['assessment']}" for d in assessment_data['indicator_details'] if d['assessment'] != 'Error'])
    error_list = "\n".join([f"- {d['name']}" for d in assessment_data['indicator_details'] if d['assessment'] == 'Error'])
    
    summary_text = (
        f"Based on a technical analysis of several key indicators, the overall sentiment for {symbol} is **{assessment_data['overall_sentiment']}**.\n\n"
        f"**Live Price:** ${current_price:,.2f}\n\n"
        f"**Indicator Assessments:**\n"
        f"{indicator_list}\n"
    )

    if error_count > 0:
        summary_text += f"\n**Note:** I encountered errors fetching data for the following indicators:\n{error_list}"

    assessment_data['summary'] = summary_text
    
    return {"text": json.dumps(assessment_data, indent=2)}

# --- NEW: Candlestick Pattern Analysis Function ---
async def analyze_candlestick_patterns(symbol, interval='1day', outputsize='100'):
    """
    Analyzes historical data for common candlestick patterns.
    """
    patterns_found = []
    
    try:
        historical_data_response = await _fetch_data_from_twelve_data(
            data_type='historical', 
            symbol=symbol, 
            interval=interval, 
            outputsize=outputsize
        )
        historical_values = historical_data_response['data'].get('values', [])
        
        if len(historical_values) < 2:
            return {"text": f"Not enough historical data to analyze candlestick patterns for {symbol}."}
        
        # Helper function to convert data to floats
        def convert_to_float(candle):
            return {k: float(v) for k, v in candle.items() if k not in ['datetime']}

        # Loop through the data to find patterns
        for i in range(len(historical_values) - 1):
            current_candle = convert_to_float(historical_values[i])
            previous_candle = convert_to_float(historical_values[i+1])
            
            open_c = current_candle['open']
            high_c = current_candle['high']
            low_c = current_candle['low']
            close_c = current_candle['close']
            datetime_c = historical_values[i]['datetime']
            
            open_p = previous_candle['open']
            close_p = previous_candle['close']

            # Check for Doji (open and close are very close)
            if abs(open_c - close_c) < (high_c - low_c) * 0.1:
                patterns_found.append({"pattern": "Doji", "date": datetime_c, "description": "A sign of indecision in the market."})

            # Check for Bullish Engulfing
            if close_c > open_c and open_p > close_p and open_c < close_p and close_c > open_p:
                patterns_found.append({"pattern": "Bullish Engulfing", "date": datetime_c, "description": "A bullish reversal pattern where a large green candle engulfs the previous red candle."})

            # Check for Bearish Engulfing
            if close_c < open_c and open_p < close_p and open_c > close_p and close_c < open_p:
                patterns_found.append({"pattern": "Bearish Engulfing", "date": datetime_c, "description": "A bearish reversal pattern where a large red candle engulfs the previous green candle."})
                
            # Check for Hammer/Hanging Man (Small body, long lower shadow)
            body_size = abs(open_c - close_c)
            total_range = high_c - low_c
            lower_shadow = min(open_c, close_c) - low_c
            
            if body_size < total_range * 0.3 and lower_shadow > body_size * 2:
                # Hammer or Hanging Man, depending on the trend
                pattern_name = "Hammer" if close_c > previous_candle['close'] else "Hanging Man"
                patterns_found.append({"pattern": pattern_name, "date": datetime_c, "description": "A potential reversal pattern with a small body and a long lower shadow."})
                
    except Exception as e:
        return {"text": f"An error occurred during candlestick pattern analysis: {e}"}
        
    if not patterns_found:
        return {"text": f"No common candlestick patterns found in the last {outputsize} data points for {symbol}."}
    
    return {"text": json.dumps({"symbol": symbol, "patterns": patterns_found}, indent=2)}

@client.event
async def on_message(message):
    """Event that fires when a message is sent in a channel the bot can see."""
    if message.author == client.user:
        return
    
    AUTHORIZED_USER_IDS = ["918556208217067561", "YOUR_FRIEND_DISCORD_ID_2"]
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
                                "indicator": { "type": "string", "enum": ["SMA", "EMA", "RSI", "MACD", "BBANDS", "STOCHRSI", "SUPERTREND", "VWAP"], "description": "Name of the technical indicator. Required if data_type is 'indicator'." },
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
                        "name": "perform_overall_assessment",
                        "description": "Provides a comprehensive technical analysis of an asset and gives a final sentiment of 'Bullish', 'Bearish', or 'Neutral' based on multiple indicators.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "symbol": { "type": "string", "description": "Ticker symbol (e.g., 'BTC/USD', 'AAPL'). This is required." }
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

                            elif function_name == "perform_overall_assessment":
                                tool_output_data_raw = await perform_overall_assessment(**function_args)
                                tool_output_text = json.dumps(tool_output_data_raw, indent=2)
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
                                print(f"LLM second turn: No text content in response. Full response: {llm_data_second_turn}")
                                block_reason = llm_data_second_turn.get('promptFeedback', {}).get('blockReason', 'unknown')
                                response_text_for_discord = f"AI could not generate a response. This might be due to content policy. Block reason: {block_reason}. Please try rephrasing."
                        else:
                            response_text_for_discord = "Could not get a valid second response from the AI."

                    elif parts_first_turn[0].get('text'):
                        response_text_for_discord = parts_first_turn[0]['text']
                    else:
                        print(f"LLM first turn: No text content in response. Full response: {llm_data_first_turn}")
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
