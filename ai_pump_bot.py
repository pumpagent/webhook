# Import necessary libraries
from flask import Flask, jsonify, request
import requests
import os
import time
import pandas as pd # Keep pandas for potential future use or other data processing
import ta # Keep ta for potential future use or other data processing
from datetime import datetime, timedelta # Import for date handling

# Initialize the Flask application
app = Flask(__name__) # Corrected: Use __name__ for Flask app name

# --- API Configurations ---
TWELVE_DATA_API_KEY = os.environ.get('TWELVE_DATA_API_KEY')
NEWS_API_KEY = os.environ.get('NEWS_API_KEY') # For NewsAPI.org

# --- Rate Limiting & Caching Configuration ---
# Store last successful API call timestamp for each type of external API
last_twelve_data_call = 0
last_news_api_call = 0

# Minimum time (in seconds) between calls to each API
# Adjusted to a more conservative limit to avoid 429 errors from Twelve Data
# Note: Each indicator call is a separate API request, so this limit applies per indicator fetch.
TWELVE_DATA_MIN_INTERVAL = 10 # seconds (e.g., 10 seconds between Twelve Data calls)
NEWS_API_MIN_INTERVAL = 1   # seconds (e.g., 10 seconds between NewsAPI calls)

# Simple in-memory cache for recent responses
# { (data_type, symbol, interval, indicator, indicator_period, news_query, from_date, sort_by, news_language): {'response_json': {}, 'timestamp': float} }
api_response_cache = {}
CACHE_DURATION = 10 # NEW: Cache responses for 10 seconds (instead of 300 seconds)

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
    - 'indicator': Name of the technical indicator (e.g., 'SMA', 'EMA', 'RSI', 'MACD', 'BBANDS', 'STOCHRSI').
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
    outputsize = request.args.get('outputsize') # outputsize will be handled by specific indicator calls if needed

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

        elif data_type == 'historical':
            # --- Rate Limiting for Twelve Data ---
            if (current_time - last_twelve_data_call) < TWELVE_DATA_MIN_INTERVAL:
                time_to_wait = TWELVE_DATA_MIN_INTERVAL - (current_time - last_twelve_data_call)
                print(f"Rate limit hit for Twelve Data. Waiting {time_to_wait:.2f} seconds.")
                return jsonify({"text": f"I'm currently experiencing high demand for market data. Please give me about {int(time_to_wait) + 1} seconds and try again."}), 429

            if not symbol:
                return jsonify({"text": "Error: Missing 'symbol' parameter for historical data. Please specify a symbol (e.g., BTC/USD, AAPL)."}), 400
            
            if not interval:
                interval = '1day'
                print(f"Defaulting 'interval' to '{interval}' for historical data.")
            
            if not outputsize:
                outputsize = '50'
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

            readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper()
            response_data = {
                "text": (
                    f"I have retrieved {len(historical_values)} data points for {readable_symbol} "
                    f"at {interval} intervals, covering from {historical_values[-1]['datetime']} to {historical_values[0]['datetime']}. "
                    f"This data includes Open, High, Low, and Close prices."
                )
            }
            globals()['last_twelve_data_call'] = time.time() # Update last call timestamp

        elif data_type == 'indicator':
            # --- Rate Limiting for Twelve Data ---
            if (current_time - last_twelve_data_call) < TWELVE_DATA_MIN_INTERVAL:
                time_to_wait = TWELVE_DATA_MIN_INTERVAL - (current_time - last_twelve_data_call)
                print(f"Rate limit hit for Twelve Data. Waiting {time_to_wait:.2f} seconds.")
                return jsonify({"text": f"I'm currently experiencing high demand for market data. Please give me about {int(time_to_wait) + 1} seconds and try again."}), 429

            if not symbol:
                return jsonify({"text": "Error: Missing 'symbol' parameter for indicator. Please specify a symbol."}), 400
            if not indicator:
                return jsonify({"text": "Error: 'indicator' parameter is required when 'data_type' is 'indicator'."}), 400
            if not indicator_period:
                return jsonify({"text": "Error: 'indicator_period' is required for technical indicators."}), 400
            
            indicator_name_upper = indicator.upper()
            base_api_url = "https://api.twelvedata.com/"
            indicator_endpoint = ""
            params = {
                'symbol': symbol,
                'interval': interval if interval else '1day',
                'apikey': TWELVE_DATA_API_KEY
            }
            
            readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper()
            
            # Map indicator and period to Twelve Data API endpoints and parameters
            if indicator_name_upper == 'RSI':
                indicator_endpoint = "rsi"
                params['time_period'] = str(indicator_period) # Ensure time_period is string
            elif indicator_name_upper == 'MACD':
                indicator_endpoint = "macd"
                # Twelve Data MACD uses fast_period, slow_period, signal_period
                # Defaulting to common values if not specified by indicator_period
                params['fast_period'] = 12
                params['slow_period'] = 26
                params['signal_period'] = 9
            elif indicator_name_upper == 'BBANDS':
                indicator_endpoint = "bbands"
                params['time_period'] = str(indicator_period) # Ensure time_period is string
                params['sd'] = 2 # Standard deviation, common default
            elif indicator_name_upper == 'STOCHRSI':
                indicator_endpoint = "stochrsi"
                params['time_period'] = str(indicator_period) # Ensure time_period is string
                # Twelve Data STOCHRSI uses fast_k_period, fast_d_period, rsi_time_period, stoch_time_period
                # Defaulting to common values
                params['fast_k_period'] = 3
                params['fast_d_period'] = 3
                params['rsi_time_period'] = str(indicator_period) # Use indicator_period for RSI base
                params['stoch_time_period'] = str(indicator_period) # Use indicator_period for Stoch base
            else:
                return jsonify({"text": f"Error: Indicator '{indicator}' not supported by direct API. Supported: RSI, MACD, BBANDS, STOCHRSI."}), 400

            api_url = f"{base_api_url}{indicator_endpoint}"
            print(f"Fetching {indicator_name_upper} for {symbol} from Twelve Data API with params: {params}...")
            response = requests.get(api_url, params=params)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from Twelve Data.')
                print(f"Twelve Data API error for {indicator_name_upper} for {symbol}: {error_message}")
                return jsonify({"text": f"Could not retrieve {indicator_name_upper} for {readable_symbol}. Error: {error_message}"}), 500
            
            # Parse the response based on indicator type
            indicator_value = None
            indicator_description = ""
            
            if indicator_name_upper == 'RSI':
                value = data.get('value')
                if value is not None:
                    indicator_value = float(value)
                    indicator_description = f"{indicator_period}-period Relative Strength Index"
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
                    indicator_description = f"{indicator_period}-period Bollinger Bands"
            elif indicator_name_upper == 'STOCHRSI':
                stochrsi_k = data.get('stochrsi') # Twelve Data returns %K as 'stochrsi'
                stochrsi_d = data.get('stochrsi_signal') # Twelve Data returns %D as 'stochrsi_signal'
                if all(v is not None for v in [stochrsi_k, stochrsi_d]):
                    indicator_value = {
                        'StochRSI_K': float(stochrsi_k),
                        'StochRSI_D': float(stochrsi_d)
                    }
                    indicator_description = f"{indicator_period}-period Stochastic Relative Strength Index"

            if indicator_value is not None:
                if isinstance(indicator_value, dict):
                    response_text = f"The {indicator_description} for {readable_symbol} is: "
                    for key, val in indicator_value.items():
                        response_text += f"{key}: {val:,.2f}. "
                    response_data = {"text": response_text.strip()}
                else:
                    response_data = {"text": f"The {indicator_description} for {readable_symbol} is {indicator_value:,.2f}."}
            else:
                print(f"Twelve Data did not return valid indicator values for {indicator_name_upper} for {symbol}. Response: {data}")
                return jsonify({"text": f"Could not retrieve {indicator_name_upper} for {readable_symbol}. Data might be unavailable or malformed."}), 500
            
            globals()['last_twelve_data_call'] = time.time() # Update last call timestamp

        elif data_type == 'news':
            # --- Rate Limiting for NewsAPI.org ---
            if (time.time() - last_news_api_call) < NEWS_API_MIN_INTERVAL:
                time_to_wait = NEWS_API_MIN_INTERVAL - (current_time - last_news_api_call)
                print(f"Rate limit hit for NewsAPI.org. Waiting {time_to_wait:.2f} seconds.")
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
