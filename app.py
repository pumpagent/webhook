# Import necessary libraries
from flask import Flask, jsonify, request
import requests
import os
import time
import pandas as pd # Import pandas for data manipulation
import ta # Import the 'ta' library for technical analysis indicators
from datetime import datetime, timedelta # Import for date handling

# Initialize the Flask application
app = Flask(__name__)

# --- API Configurations ---
TWELVE_DATA_API_KEY = os.environ.get('TWELVE_DATA_API_KEY')
NEWS_API_KEY = os.environ.get('NEWS_API_KEY') # For NewsAPI.org

# --- Rate Limiting & Caching Configuration ---
# Store last successful API call timestamp for each type of external API
last_twelve_data_call = 0
last_news_api_call = 0

# Minimum time (in seconds) between calls to each API
# Adjust these values based on the free tier limits of Twelve Data and NewsAPI.org
# A conservative limit for free tiers might be 5-10 seconds to avoid hitting limits too quickly.
TWELVE_DATA_MIN_INTERVAL = 1 # seconds (e.g., 10 seconds between Twelve Data calls)
NEWS_API_MIN_INTERVAL = 1   # seconds (e.g., 10 seconds between NewsAPI calls)

# Simple in-memory cache for recent responses
# { (data_type, symbol, interval, indicator, indicator_period, news_query, from_date, sort_by, news_language): {'response_json': {}, 'timestamp': float} }
api_response_cache = {}
CACHE_DURATION = 300 # NEW: Cache responses for 300 seconds (5 minutes) to reduce API calls

# Define the webhook endpoint
@app.route('/market_data', methods=['GET']) # Endpoint for all data types
def get_market_data():
    """
    This endpoint fetches live price, historical data, technical analysis indicators,
    or market news using Twelve Data and NewsAPI.org.
    It includes rate limiting and caching to manage API call frequency.

    Required parameters:
    - 'symbol': Ticker symbol (e.g., 'BTC/USD', 'AAPL') for price/TA, or
                keywords (e.g., 'Bitcoin', 'inflation') for news.

    Optional parameters:
    - 'data_type': 'live' (default), 'historical', 'indicator', or 'news'.

    For 'historical' or 'indicator' data:
    - 'interval': Time interval (e.g., '1min', '1day'). Defaults to '1day'.
    - 'outputsize': Number of data points. Defaults to '1' for historical, adjusted for indicator.
    - 'indicator': Name of the technical indicator (e.g., 'SMA', 'EMA', 'RSI', 'MACD', 'BBANDS').
                    Requires 'data_type' to be 'indicator'.
    - 'indicator_period': Period for the indicator (e.g., '14', '20', '50').
                            Required if 'indicator' is specified.

    For 'news' data:
    - 'news_query': Keywords for news search.
    - 'from_date': Start date for news (YYYY-MM-DD). Defaults to 7 days ago.
    - 'sort_by': How to sort news ('relevancy', 'popularity', 'publishedAt'). Defaults to 'publishedAt'.
    - 'news_language': Language of news (e.g., 'en'). Defaults to 'en'.

    Returns: Formatted string within a JSON object for Eleven Labs.
    """
    global last_twelve_data_call, last_news_api_call # Declare global to modify timestamps

    # Get parameters from the request
    symbol = request.args.get('symbol') # Used for price/TA
    data_type = request.args.get('data_type', 'live').lower()

    interval = request.args.get('interval')
    outputsize = request.args.get('outputsize')

    indicator = request.args.get('indicator')
    indicator_period = request.args.get('indicator_period')

    news_query = request.args.get('news_query')
    from_date = request.args.get('from_date')
    sort_by = request.args.get('sort_by', 'publishedAt')
    news_language = request.args.get('news_language', 'en')

    # Create a cache key for the current request
    cache_key = (data_type, symbol, interval, indicator, indicator_period, news_query, from_date, sort_by, news_language)
    current_time = time.time()

    # --- Check Cache First ---
    if cache_key in api_response_cache:
        cached_data = api_response_cache[cache_key]
        if (current_time - cached_data['timestamp']) < CACHE_DURATION:
            print(f"Serving cached response for {data_type} request.")
            return jsonify(cached_data['response_json'])

    # Basic validation for API keys
    if (data_type != 'news' and not TWELVE_DATA_API_KEY) or \
       (data_type == 'news' and not NEWS_API_KEY):
        print(f"Error: Missing API key for {data_type} data.")
        return jsonify({"text": "Error: Server configuration issue. API key is missing."}), 500

    try:
        response_data = {} # To store the final JSON response

        if data_type == 'live':
            # --- Rate Limiting for Twelve Data ---
            if (current_time - last_twelve_data_call) < TWELVE_DATA_MIN_INTERVAL:
                time_to_wait = TWELVE_DATA_MIN_INTERVAL - (current_time - last_twelve_data_call)
                print(f"Rate limit hit for Twelve Data. Waiting {time_to_wait:.2f} seconds.")
                # NEW: More conversational rate limit message
                return jsonify({"text": f"I'm currently experiencing high demand for live market data. Please give me about {int(time_to_wait) + 1} seconds and try again."}), 429
            
            if not symbol:
                return jsonify({"text": "Error: Missing 'symbol' parameter for live price. Please specify a symbol (e.g., BTC/USD, AAPL)."}), 400
            api_url = f"https://api.twelvedata.com/quote?symbol={symbol}&apikey={TWELVE_DATA_API_KEY}"
            print(f"Fetching live price for {symbol} from Twelve Data API...")
            response = requests.get(api_url)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from Twelve Data.')
                print(f"Twelve Data API error for symbol {symbol}: {error_message}")
                return jsonify({"text": f"Could not retrieve live price for {symbol}. Error: {error_message}"}), 500
            
            current_price = data.get('close')
            if current_price is not None:
                try:
                    formatted_price = f"${float(current_price):,.2f}"
                    readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper() 
                    response_data = {"text": f"The current price of {readable_symbol} is {formatted_price}."}
                except ValueError:
                    print(f"Twelve Data returned invalid price format for {symbol}: {current_price}")
                    return jsonify({"text": f"Could not parse live price for {symbol}. Invalid format received."}), 500
            else:
                print(f"Twelve Data did not return a 'close' price for {symbol}. Response: {data}")
                return jsonify({"text": f"Could not retrieve live price for {symbol}. The symbol might be invalid or not found."}), 500
            globals()['last_twelve_data_call'] = time.time() # Update last call timestamp

        elif data_type == 'historical' or data_type == 'indicator':
            # --- Rate Limiting for Twelve Data ---
            if (current_time - last_twelve_data_call) < TWELVE_DATA_MIN_INTERVAL:
                time_to_wait = TWELVE_DATA_MIN_INTERVAL - (current_time - last_twelve_data_call)
                print(f"Rate limit hit for Twelve Data. Waiting {time_to_wait:.2f} seconds.")
                # NEW: More conversational rate limit message
                return jsonify({"text": f"I'm currently experiencing high demand for market data. Please give me about {int(time_to_wait) + 1} seconds and try again."}), 429

            if not symbol:
                return jsonify({"text": "Error: Missing 'symbol' parameter for historical data. Please specify a symbol (e.g., BTC/USD, AAPL)."}), 400
            
            # Set default interval if not provided
            if not interval:
                interval = '1day'
                print(f"Defaulting 'interval' to '{interval}' for historical/indicator data.")
            
            # For indicators, ensure enough data points are fetched
            if data_type == 'indicator':
                if not indicator:
                    return jsonify({"text": "Error: 'indicator' parameter is required when 'data_type' is 'indicator'."}), 400
                if not indicator_period:
                    return jsonify({"text": "Error: 'indicator_period' is required for technical indicators."}), 400
                
                # --- START: Enhanced indicator_period parsing ---
                try:
                    indicator_period = int(indicator_period)
                except ValueError:
                    try:
                        indicator_period = int(float(indicator_period))
                    except (ValueError, TypeError):
                        return jsonify({"text": f"Error: The indicator period '{indicator_period}' must be a whole number (e.g., 14, 20, 50). Please avoid decimals or text."}), 400
                # --- END: Enhanced indicator_period parsing ---

                # Adjust required_outputsize based on the indicator and period
                if indicator.upper() == 'BBANDS':
                    # Bollinger Bands need enough data for the SMA and standard deviation
                    required_outputsize = max(indicator_period * 2, 50) # Ensure sufficient data for calculation
                else:
                    required_outputsize = max(indicator_period * 2, 50) # General case for other indicators
                
                if outputsize:
                    try:
                        outputsize = int(float(outputsize)) 
                    except (ValueError, TypeError):
                        return jsonify({"text": "Error: 'outputsize' parameter must be a whole number (e.g., 7, not 7.0)."}), 400
                    outputsize = max(outputsize, required_outputsize)
                else:
                    outputsize = required_outputsize
                print(f"Adjusted 'outputsize' to '{outputsize}' for indicator calculation.")
            else: # data_type == 'historical'
                if not outputsize:
                    outputsize = '50' # Default to 50 data points for candlestick analysis
                    print(f"Defaulting 'outputsize' to '{outputsize}' for historical data.")
                try:
                    outputsize = int(float(outputsize)) 
                except (ValueError, TypeError):
                    return jsonify({"text": "Error: 'outputsize' parameter must be a whole number (e.g., 7, not 7.0)."}), 400


            api_url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"
            print(f"Fetching data for {symbol} (interval: {interval}, outputsize: {outputsize}) from Twelve Data API...")
            response = requests.get(api_url)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from Twelve Data.')
                print(f"Twelve Data API error for symbol {symbol} historical data: {error_message}")
                return jsonify({"text": f"Could not retrieve data for {symbol}. Error: {error_message}"}), 500
            
            historical_values = data.get('values')
            if not historical_values:
                print(f"Twelve Data returned no values for {symbol}. Response: {data}")
                return jsonify({"text": f"No data found for {symbol} with the specified interval and output size. The symbol or parameters might be incorrect."}), 500

            # Convert to pandas DataFrame for TA calculations
            df = pd.DataFrame(historical_values)
            df['close'] = pd.to_numeric(df['close'])
            df = df.iloc[::-1].reset_index(drop=True)

            readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper()

            if data_type == 'historical':
                response_data = {
                    "text": (
                        f"I have retrieved {len(historical_values)} data points for {readable_symbol} "
                        f"at {interval} intervals, covering from {df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}. "
                        f"This data includes Open, High, Low, and Close prices, which can be used for candlestick analysis by the agent."
                    )
                }
            
            elif data_type == 'indicator':
                indicator_value = None
                indicator_name = indicator.upper()

                if indicator_name == 'SMA':
                    if len(df) < indicator_period:
                        return jsonify({"text": f"Not enough data points ({len(df)}) to calculate {indicator_period}-period SMA for {readable_symbol}. Need at least {indicator_period} data points."}), 400
                    df['SMA'] = ta.trend.sma_indicator(df['close'], window=indicator_period)
                    indicator_value = df['SMA'].iloc[-1]
                    indicator_description = f"{indicator_period}-period Simple Moving Average"
                elif indicator_name == 'EMA':
                    if len(df) < indicator_period:
                        return jsonify({"text": f"Not enough data points ({len(df)}) to calculate {indicator_period}-period EMA for {readable_symbol}. Need at least {indicator_period} data points."}), 400
                    df['EMA'] = ta.trend.ema_indicator(df['close'], window=indicator_period)
                    indicator_value = df['EMA'].iloc[-1]
                    indicator_description = f"{indicator_period}-period Exponential Moving Average"
                elif indicator_name == 'RSI':
                    if len(df) < indicator_period * 2: 
                        return jsonify({"text": f"Not enough data points ({len(df)}) to calculate {indicator_period}-period RSI for {readable_symbol}. Need at least {indicator_period * 2} data points."}), 400
                    df['RSI'] = ta.momentum.rsi(df['close'], window=indicator_period)
                    indicator_value = df['RSI'].iloc[-1]
                    indicator_description = f"{indicator_period}-period Relative Strength Index"
                elif indicator_name == 'MACD':
                    if len(df) < 34:
                        return jsonify({"text": f"Not enough data points ({len(df)}) to calculate MACD for {readable_symbol}. Need at least 34 data points."}), 400
                    
                    # FIX: Corrected parameter names for ta.trend.macd based on GitHub issue
                    # The 'ta' library's macd function uses 'window_fast', 'window_slow', and 'window_signal'
                    # The GitHub issue states: macd() does NOT take window_sign. It's for macd_signal and macd_diff.
                    macd_line = ta.trend.macd(df['close'], window_fast=12, window_slow=26) # Removed window_signal/window_sign
                    macd_signal_line = ta.trend.macd_signal(df['close'], window_fast=12, window_slow=26, window_sign=9)
                    macd_histogram = ta.trend.macd_diff(df['close'], window_fast=12, window_slow=26, window_sign=9)
                    
                    indicator_value = {
                        'MACD_Line': macd_line.iloc[-1],
                        'Signal_Line': macd_signal_line.iloc[-1],
                        'Histogram': macd_histogram.iloc[-1]
                    }
                    indicator_description = "Moving Average Convergence D-I-vergence"
                elif indicator_name == 'BBANDS':
                    # Bollinger Bands calculation
                    # Default window_dev (standard deviation multiplier) is 2.0
                    if len(df) < indicator_period: # Need at least 'indicator_period' for the SMA
                        return jsonify({"text": f"Not enough data points ({len(df)}) to calculate {indicator_period}-period Bollinger Bands for {readable_symbol}. Need at least {indicator_period} data points."}), 400
                    
                    df['BB_High'] = ta.volatility.bollinger_hband(df['close'], window=indicator_period, window_dev=2)
                    df['BB_Low'] = ta.volatility.bollinger_lband(df['close'], window=indicator_period, window_dev=2)
                    df['BB_Mid'] = ta.volatility.bollinger_mband(df['close'], window=indicator_period, window_dev=2)

                    indicator_value = {
                        'Upper_Band': df['BB_High'].iloc[-1],
                        'Middle_Band': df['BB_Mid'].iloc[-1],
                        'Lower_Band': df['BB_Low'].iloc[-1]
                    }
                    indicator_description = f"{indicator_period}-period Bollinger Bands"
                else:
                    return jsonify({"text": f"Error: Indicator '{indicator}' not supported. Supported indicators: SMA, EMA, RSI, MACD, BBANDS."}), 400

                if indicator_value is not None:
                    if isinstance(indicator_value, dict):
                        response_text = f"The {indicator_description} for {readable_symbol} is: "
                        for key, val in indicator_value.items():
                            response_text += f"{key}: {val:,.2f}. "
                        response_data = {"text": response_text.strip()}
                    else:
                        response_data = {"text": f"The {indicator_description} for {readable_symbol} is {indicator_value:,.2f}."}
                else:
                    return jsonify({"text": f"Could not calculate {indicator_name} for {readable_symbol}. Data might be insufficient or invalid."}), 500
            globals()['last_twelve_data_call'] = time.time() # Update last call timestamp

        elif data_type == 'news':
            # --- Rate Limiting for NewsAPI ---
            if (time.time() - last_news_api_call) < NEWS_API_MIN_INTERVAL:
                time_to_wait = NEWS_API_MIN_INTERVAL - (current_time - last_news_api_call)
                print(f"Rate limit hit for NewsAPI. Waiting {time_to_wait:.2f} seconds.")
                return jsonify({"text": f"Please wait a moment. I'm fetching new news, but there's a slight delay due to API limits. Try again in {int(time_to_wait) + 1} seconds."}), 429 # 429 Too Many Requests

            if not news_query:
                return jsonify({"text": "Error: Missing 'news_query' parameter for news. Please specify keywords for the news search."}), 400
            
            if not from_date:
                from_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
                print(f"Defaulting 'from_date' to '{from_date}' for news search.")

            news_api_url = (
                f"https://newsapi.org/v2/everything?"
                f"q={news_query}&"
                f"from={from_date}&"
                f"sortBy={sort_by}&"
                f"language={news_language}&"
                f"apiKey={NEWS_API_KEY}"
            )
            print(f"Fetching news for '{news_query}' from NewsAPI.org (from: {from_date}, sort: {sort_by})...")
            response = requests.get(news_api_url)
            response.raise_for_status()
            news_data = response.json()

            if news_data.get('status') == 'error':
                error_message = news_data.get('message', 'Unknown error from NewsAPI.org.')
                print(f"NewsAPI.org error: {error_message}")
                return jsonify({"text": f"Could not retrieve news. Error: {error_message}"}), 500
            
            articles = news_data.get('articles')
            if articles:
                response_text = f"Here are some recent news headlines for {news_query}: "
                for i, article in enumerate(articles[:3]): # Limit to top 3 articles
                    title = article.get('title', 'No title')
                    source = article.get('source', {}).get('name', 'Unknown source')
                    response_text += f"Number {i+1}: '{title}' from {source}. "
                response_data = {"text": response_text.strip()}
            else:
                response_data = {"text": f"No recent news found for '{news_query}'."}
            globals()['last_news_api_call'] = time.time() # Update last call timestamp

        else:
            return jsonify({"text": "Error: Invalid 'data_type' specified. Choose 'live', 'historical', 'indicator', or 'news'."}), 400

        # Cache the successful response before returning
        api_response_cache[cache_key] = {'response_json': response_data, 'timestamp': time.time()}
        return jsonify(response_data)

    except requests.exceptions.RequestException as e:
        print(f"Error connecting to API: {e}")
        return jsonify({"text": "Error connecting to the data service. Please check your internet connection or try again later."}), 500
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return jsonify({"text": "An unexpected error occurred while processing your request. Please try again later."}), 500

# This block ensures the Flask app runs when the script is executed directly.
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
