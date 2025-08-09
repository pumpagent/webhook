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
            elif indicator_name_upper == 'SAR': # Parabolic SAR
                indicator_endpoint = "sarext" # Twelve Data uses sarext for Parabolic SAR Extended
                # Default parameters for sarext if not provided by LLM
                params['start_value'] = 0.02
                params['offset'] = 0.02
                params['max_value'] = 0.2
            elif indicator_name_upper == 'PIVOT_POINTS': # Pivot Points
                indicator_endpoint = "pivot_points"
                # Twelve Data API for pivot_points usually needs a type, e.g., 'fibonacci', 'woodie', 'classic'
                # For simplicity, we'll assume 'classic' or let the API default if not specified.
                # The screenshot shows PIVOT_POINTS_HL, which implies High/Low based.
                # We'll rely on the API's default calculation if no type is given.
            elif indicator_name_upper == 'ULTOSC': # Ultimate Oscillator
                indicator_endpoint = "ultosc"
                # Default periods for Ultimate Oscillator
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
    # Only include the indicators requested for overall analysis
    indicators_to_check = {
        'RSI': {'period': '14', 'description': 'Relative Strength Index'},
        'MACD': {'period': '0', 'description': 'Moving Average Convergence Divergence'},
        'SUPERTREND': {'period': '10', 'multiplier': '3', 'description': 'Supertrend'},
        'SMA_50': {'indicator': 'SMA', 'period': '50', 'description': '50-period Simple Moving Average'},
        'SMA_200': {'indicator': 'SMA', 'period': '200', 'description': '200-period Simple Moving Average'},
        'EMA': {'period': '20', 'description': '20-period Exponential Moving Average'},
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
                if value >= 30 and value <= 85:
                    sub_assessment = "Bullish"
                elif value < 30 or value > 85:
                    sub_assessment = "Bearish"
            elif 'macd' in data and 'signal' in data:
                macd_line = float(data['macd'])
                signal_line = float(data['signal'])
                if macd_line > signal_line:
                    sub_assessment = "Bullish"
                elif macd_line < signal_line:
                    sub_assessment = "Bearish"
            elif 'supertrend' in data and current_price is not None:
                supertrend_value = float(data['supertrend'])
                if current_price > supertrend_value: sub_assessment = "Bullish"
                else: sub_assessment = "Bearish"
            elif 'value' in data and current_price is not None:
                value = float(data['value'])
                if current_price > value:
                    sub_assessment = "Bullish"
                else:
                    sub_assessment = "Bearish"
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
    error_list = "\
