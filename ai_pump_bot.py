import os
import discord
import requests
import json
import re
import time
from datetime import datetime, timedelta

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
            raise requests.exceptions.RequestException(
                f"Rate limit hit for data service. Please wait {time_to_wait:.2f} seconds."
            )
    else:
        if (current_time - last_news_api_call) < NEWS_API_MIN_INTERVAL:
            time_to_wait = NEWS_API_MIN_INTERVAL - (current_time - last_news_api_call)
            raise requests.exceptions.RequestException(
                f"Rate limit hit for News API. Please wait {int(time_to_wait) + 1} seconds."
            )

    readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper() if symbol else "N/A"
    response_data = {}

    try:
        if data_type == 'live':
            if not symbol:
                raise ValueError("Missing 'symbol' parameter for live price.")
            api_url = f"https://api.twelvedata.com/quote?symbol={symbol}&apikey={TWELVE_DATA_API_KEY}"
            print(f"Fetching live price for {symbol} from data service...")
            response = requests.get(api_url)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from data service.')
                raise requests.exceptions.RequestException(f"Data service error for symbol {symbol}: {error_message}")
            
            current_price = data.get('close')
            if current_price is not None:
                formatted_price = f"${float(current_price):,.2f}"
                response_data = {"text": f"The current price of {readable_symbol} is {formatted_price}."}
            else:
                raise ValueError(f"Data service did not return a 'close' price for {symbol}. Response: {data}")

        elif data_type == 'historical':
            if not symbol:
                raise ValueError("Missing 'symbol' parameter for historical data.")
            
            interval_str = interval if interval else '1day'
            outputsize_str = outputsize if outputsize else '50'
            
            api_url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval_str}&outputsize={outputsize_str}&apikey={TWELVE_DATA_API_KEY}"
            print(f"Fetching data for {symbol} (interval: {interval_str}, outputsize: {outputsize_str}) from data service...")
            response = requests.get(api_url)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from data service.')
                raise requests.exceptions.RequestException(f"Data service error for symbol {symbol} historical data: {error_message}")
            
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
                indicator_endpoint = "supertrent"
                params['time_period'] = indicator_period_str
                params['multiplier'] = indicator_multiplier_str
            elif indicator_name_upper == 'VWAP':
                indicator_endpoint = "vwap"
                # VWAP doesn't require extra parameters
            else:
                raise ValueError(f"Indicator '{indicator}' not supported by direct API.")

            api_url = f"{base_api_url}{indicator_endpoint}"
            print(f"Fetching {indicator_name_upper} for {symbol} from data service with params: {params}...")
            response = requests.get(api_url, params=params)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from data service.')
                raise requests.exceptions.RequestException(f"Data service error for {indicator_name_upper} for {symbol}: {error_message}")
            
            indicator_value = None
            indicator_description = ""
            latest_values = data.get('values', [{}])[0]
            
            print(f"DEBUG: {indicator_name_upper} - latest_values: {latest_values}")

            # --- Parsing Logic for All Indicators ---
            if indicator_name_upper == 'RSI':
                value = latest_values.get('rsi')
                if value is not None:
                    indicator_value = float(str(value).replace(',', ''))
                    indicator_description = f"{indicator_period_str}-period Relative Strength Index"
            elif indicator_name_upper == 'MACD':
                macd = latest_values.get('macd')
                signal = latest_values.get('signal')
                histogram = latest_values.get('histogram')
                if all(v is not None for v in [macd, signal, histogram]):
                    indicator_value = {
                        'MACD_Line': float(str(macd).replace(',', '')),
                        'Signal_Line': float(str(signal).replace(',', '')),
                        'Histogram': float(str(histogram).replace(',', ''))
                    }
                    indicator_description = "Moving Average Convergence Divergence"
            elif indicator_name_upper == 'BBANDS':
                upper = latest_values.get('upper')
                middle = latest_values.get('middle')
                lower = latest_values.get('lower')
                if all(v is not None for v in [upper, middle, lower]):
                    indicator_value = {
                        'Upper_Band': float(str(upper).replace(',', '')),
                        'Middle_Band': float(str(middle).replace(',', '')),
                        'Lower_Band': float(str(lower).replace(',', ''))
                    }
                    indicator_description = f"{indicator_period_str}-period Bollinger Bands"
            elif indicator_name_upper == 'STOCHRSI':
                stochrsi_k = latest_values.get('stochrsi')
                stochrsi_d = latest_values.get('stochrsi_signal')
                if all(v is not None for v in [stochrsi_k, stochrsi_d]):
                    indicator_value = {
                        'StochRSI_K': float(str(stochrsi_k).replace(',', '')),
                        'StochRSI_D': float(str(stochrsi_d).replace(',', ''))
                    }
                    indicator_description = f"{indicator_period_str}-period Stochastic Relative Strength Index"
            elif indicator_name_upper == 'SMA':
                value = latest_values.get('value')
                if value is not None:
                    indicator_value = float(str(value).replace(',', ''))
                    indicator_description = f"{indicator_period_str}-period Simple Moving Average"
            elif indicator_name_upper == 'EMA' or indicator_name_upper == 'MA':
                value = latest_values.get('value')
                if value is not None:
                    indicator_value = float(str(value).replace(',', ''))
                    indicator_description = f"{indicator_period_str}-period Exponential Moving Average"
            elif indicator_name_upper == 'SUPERTREND':
                supertrend = latest_values.get('supertrend')
                if supertrend is not None:
                    indicator_value = float(str(supertrend).replace(',', ''))
                    indicator_description = f"{indicator_period_str}-period Supertrend"
            elif indicator_name_upper == 'VWAP':
                vwap_value = latest_values.get('vwap')
                if vwap_value is not None:
                    indicator_value = float(str(vwap_value).replace(',', ''))
                    indicator_description = "Volume Weighted Average Price"
            else:
                raise ValueError(f"Unsupported indicator: {indicator_name_upper}")

            if indicator_value is not None:
                if isinstance(indicator_value, dict):
                    response_text = f"The {indicator_description} for {readable_symbol} is: "
                    for key, val in indicator_value.items():
                        response_text += f"{key}: {val:,.2f}. "
                    response_data = {"text": response_text.strip()}
                else:
                    response_data = {"text": f"The {indicator_description} for {readable_symbol} is {indicator_value:,.2f}."}
            else:
                raise ValueError(f"Data service did not return valid indicator values for {indicator_name_upper} for {symbol}. Raw latest_values: {latest_values}")

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
            response = requests.get(news_api_url) # Corrected variable name from api_url
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
                response_data = {"text": response_text.strip()}
            else:
                response_data = {"text": f"No recent news found for '{news_query}'."}
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
        'indicators': {},
        'overall_sentiment': 'Neutral',
        'summary': ''
    }
    
    try:
        # 1. Get Live Price
        live_data = await _fetch_data_from_twelve_data(data_type='live', symbol=symbol)
        current_price = float(re.search(r'\d+\.\d+', live_data['text'].replace(',', '')).group(0))
        assessment_data['live_price'] = current_price
        
        # 2. Get Indicators and store values
        indicators_to_check = ['RSI', 'MACD', 'BBANDS', 'SUPERTREND', 'SMA', 'EMA']
        
        for indicator in indicators_to_check:
            try:
                indicator_data = await _fetch_data_from_twelve_data(
                    data_type='indicator', symbol=symbol, indicator=indicator, interval='1day'
                )
                assessment_data['indicators'][indicator] = indicator_data
            except Exception as e:
                print(f"Failed to fetch {indicator} for {symbol}: {e}")
                assessment_data['indicators'][indicator] = {'text': f"Error fetching {indicator}."}

        # 3. Analyze the Indicators to determine sentiment
        bullish_score = 0
        bearish_score = 0
        neutral_score = 0

        # RSI Analysis
        rsi_text = assessment_data['indicators'].get('RSI', {}).get('text', '')
        rsi_value_match = re.search(r'is (\d+\.\d+)', rsi_text)
        if rsi_value_match:
            rsi_value = float(rsi_value_match.group(1))
            if rsi_value < 30:
                bullish_score += 1
            elif rsi_value > 70:
                bearish_score += 1
            else:
                neutral_score += 1

        # MACD Analysis
        macd_text = assessment_data['indicators'].get('MACD', {}).get('text', '')
        if 'MACD_Line' in macd_text and 'Signal_Line' in macd_text:
            macd_line = float(re.search(r'MACD_Line: ([-+]?\d+\.\d+)', macd_text).group(1))
            signal_line = float(re.search(r'Signal_Line: ([-+]?\d+\.\d+)', macd_text).group(1))
            if macd_line > signal_line:
                bullish_score += 1
            elif macd_line < signal_line:
                bearish_score += 1
            else:
                neutral_score += 1
        
        # BBANDS Analysis
        bbands_text = assessment_data['indicators'].get('BBANDS', {}).get('text', '')
        if current_price and 'Upper_Band' in bbands_text and 'Lower_Band' in bbands_text:
            upper_band = float(re.search(r'Upper_Band: ([-+]?\d+\.\d+)', bbands_text).group(1))
            lower_band = float(re.search(r'Lower_Band: ([-+]?\d+\.\d+)', bbands_text).group(1))
            if current_price > upper_band:
                bearish_score += 1
            elif current_price < lower_band:
                bullish_score += 1
            else:
                neutral_score += 1
        
        # Supertrend Analysis (assuming value above price is bearish, below is bullish)
        supertrend_text = assessment_data['indicators'].get('SUPERTREND', {}).get('text', '')
        if current_price and 'Supertrend' in supertrend_text:
            supertrend_value = float(re.search(r'is ([-+]?\d+\.\d+)', supertrend_text).group(1))
            if current_price > supertrend_value:
                bullish_score += 1
            elif current_price < supertrend_value:
                bearish_score += 1
            else:
                neutral_score += 1

        # SMA & EMA Analysis
        sma_text = assessment_data['indicators'].get('SMA', {}).get('text', '')
        ema_text = assessment_data['indicators'].get('EMA', {}).get('text', '')
        if current_price and 'Simple Moving Average' in sma_text:
            sma_value = float(re.search(r'is ([-+]?\d+\.\d+)', sma_text).group(1))
            if current_price > sma_value:
                bullish_score += 1
            else:
                bearish_score += 1
        
        if current_price and 'Exponential Moving Average' in ema_text:
            ema_value = float(re.search(r'is ([-+]?\d+\.\d+)', ema_text).group(1))
            if current_price > ema_value:
                bullish_score += 1
            else:
                bearish_score += 1
        
        # VWAP doesn't directly contribute to a simple Bullish/Bearish score, more for intraday trading.
        # It's better to provide its value as a fact rather than a score.
        
        # 4. Final Assessment
        if bullish_score > bearish_score + 1: # A slight buffer for a strong signal
            assessment_data['overall_sentiment'] = 'Bullish'
        elif bearish_score > bullish_score + 1:
            assessment_data['overall_sentiment'] = 'Bearish'
        else:
            assessment_data['overall_sentiment'] = 'Neutral'
            
        assessment_data['summary'] = (
            f"Based on a technical analysis of {symbol}, the overall sentiment is **{assessment_data['overall_sentiment']}**.\n"
            f"**Bullish Signals:** {bullish_score}\n"
            f"**Bearish Signals:** {bearish_score}\n"
            f"**Neutral Signals:** {neutral_score}\n"
            f"**Current Price:** ${current_price:,.2f}\n"
        )
        
    except Exception as e:
        assessment_data['overall_sentiment'] = 'Neutral'
        assessment_data['summary'] = f"An error occurred during the assessment: {e}"

    return {"text": json.dumps(assessment_data, indent=2)}


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
                                "indicator_period": { "type": "string", "description": "Period for the indicator (e.g., '14', '20', '50'). Default to '14' if not specified by user." },
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

                        # Handle the new 'perform_overall_assessment' tool
                        if function_name == "get_market_data":
                            if 'interval' not in function_args:
                                function_args['interval'] = '1day'
                            if 'indicator_period' not in function_args:
                                if function_args.get('indicator', '').upper() == 'MACD':
                                    function_args['indicator_period'] = '0'
                                else:
                                    function_args['indicator_period'] = '14'
                            
                            for key, value in function_args.items():
                                function_args[key] = str(value)
                            
                            try:
                                tool_output_data = await _fetch_data_from_twelve_data(**function_args)
                                tool_output_text = tool_output_data.get('text', 'No specific response from data service.')
                                print(f"Tool execution output: {tool_output_text}")
                            except Exception as e:
                                print(f"Error fetching data from data service: {e}")
                                tool_output_text = f"Error fetching data: {e}"
                            
                        elif function_name == "perform_overall_assessment":
                            try:
                                tool_output_data = await perform_overall_assessment(**function_args)
                                # The response will be a JSON string, so we need to process it
                                tool_output_dict = json.loads(tool_output_data['text'])
                                tool_output_text = tool_output_dict.get('summary', 'Could not generate a summary.')
                                tool_output_text += f"\n**Raw Data:** {tool_output_data['text']}"
                                print(f"Overall assessment output: {tool_output_text}")
                            except Exception as e:
                                print(f"Error performing overall assessment: {e}")
                                tool_output_text = f"Error performing assessment: {e}"

                        else:
                            tool_output_text = "AI requested an unknown function."

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
