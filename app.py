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
TWELVE_DATA_MIN_INTERVAL = 10 # seconds (e.g., 10 seconds between Twelve Data calls)
NEWS_API_MIN_INTERVAL = 10    # seconds (e.g., 10 seconds between NewsAPI calls)

# Simple in-memory cache for recent responses
# { (data_type, symbol, interval, indicator, indicator_period, news_query, from_date, sort_by, news_language, indicator_source): {'response_json': {}, 'timestamp': float} }
api_response_cache = {}
CACHE_DURATION = 300 # Cache responses for 300 seconds (5 minutes)

# Define the webhook endpoint
@app.route('/market_data', methods=['GET']) # Endpoint for all data types
def get_market_data():
    """
    This endpoint fetches live price, historical data, technical analysis indicators,
    or market news using Twelve Data and NewsAPI.org.

    Required parameters:
    - 'symbol': Ticker symbol (e.g., 'BTC/USD', 'AAPL') for price/TA, or
                keywords (e.g., 'Bitcoin', 'inflation') for news.

    Optional parameters:
    - 'data_type': 'live' (default), 'historical', 'indicator', or 'news'.

    For 'historical' or 'indicator' data:
    - 'interval': Time interval (e.g., '1min', '1day'). Defaults to '1day'.
    - 'outputsize': Number of data points. Defaults to '50' for historical, adjusted for indicator.
    - 'indicator': Name of the technical indicator (e.g., 'SMA', 'EMA', 'RSI', 'MACD', 'BBANDS', 'PVT', 'STOCHRSI').
                   Requires 'data_type' to be 'indicator'.
    - 'indicator_period': Period for the indicator (e.g., '14', '20', '50').
                          Required if 'indicator' is specified.
    - 'indicator_source': 'local' (default, uses pandas/ta) or 'twelvedata' (uses Twelve Data API).

    For 'news' data:
    - 'news_query': Keywords for news search.
    - 'from_date': Start date for news (YYYY-MM-DD). Defaults to 7 days ago.
    - 'sort_by': How to sort news ('relevancy', 'popularity', 'publishedAt'). Defaults to 'publishedAt'.
    - 'news_language': Language of news (e.g., 'en'). Defaults to 'en'.

    Returns: Formatted string within a JSON object for Eleven Labs.
    """
    global last_twelve_data_call, last_news_api_call # Declare global to modify timestamps

    # Get parameters from the request
    symbol = request.args.get('symbol')
    data_type = request.args.get('data_type', 'live').lower()

    interval = request.args.get('interval')
    outputsize = request.args.get('outputsize')

    indicator = request.args.get('indicator')
    indicator_period = request.args.get('indicator_period')
    indicator_source = request.args.get('indicator_source', 'local').lower()

    news_query = request.args.get('news_query')
    from_date = request.args.get('from_date')
    sort_by = request.args.get('sort_by', 'publishedAt')
    news_language = request.args.get('news_language', 'en')

    # Create a cache key for the current request (include new parameter)
    cache_key = (data_type, symbol, interval, indicator, indicator_period, news_query, from_date, sort_by, news_language, indicator_source)
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
                    print(f"Twelve Data returned invalid price format for {symbol}: {current_time}")
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
                return jsonify({"text": f"Please wait a moment. I'm fetching new data, but there's a slight delay due to API limits. Try again in {int(time_to_wait) + 1} seconds."}), 429

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

                # Handle indicator source: local (pandas/ta) vs. twelvedata API
                if indicator_source == 'local': # Use pandas/ta for local calculation
                    required_outputsize = max(indicator_period * 2, 50) 
                    if outputsize:
                        try:
                            outputsize = int(float(outputsize)) 
                        except (ValueError, TypeError):
                            return jsonify({"text": "Error: 'outputsize' parameter must be a whole number (e.g., 7, not 7.0)."}), 400
                        outputsize = max(outputsize, required_outputsize)
                    else:
                        outputsize = required_outputsize
                    print(f"Adjusted 'outputsize' to '{outputsize}' for local indicator calculation.")

                    api_url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"
                    print(f"Fetching data for {symbol} (interval: {interval}, outputsize: {outputsize}) from Twelve Data API for local calculation...")
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
                        return jsonify({"text": f"No data found for {symbol} with the specified interval and output size for local indicator calculation. The symbol or parameters might be incorrect."}), 500

                    df = pd.DataFrame(historical_values)
                    df['close'] = pd.to_numeric(df['close'])
                    df['high'] = pd.to_numeric(df['high']) # Ensure high is numeric for BBANDS
                    df['low'] = pd.to_numeric(df['low'])   # Ensure low is numeric for BBANDS
                    df['open'] = pd.to_numeric(df['open']) # Ensure open is numeric for PVT
                    df['volume'] = pd.to_numeric(df['volume']) # Ensure volume is numeric for PVT
                    df = df.iloc[::-1].reset_index(drop=True)

                    indicator_value = None
                    indicator_name = indicator.upper()
                    readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper()

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
                        
                        macd_line = ta.trend.macd(df['close'], window_fast=12, window_slow=26)
                        macd_signal_line = ta.trend.macd_signal(df['close'], window_fast=12, window_slow=26, window_sign=9)
                        macd_histogram = ta.trend.macd_diff(df['close'], window_fast=12, window_slow=26, window_sign=9)
                        
                        indicator_value = {
                            'MACD_Line': macd_line.iloc[-1],
                            'Signal_Line': macd_signal_line.iloc[-1],
                            'Histogram': macd_histogram.iloc[-1]
                        }
                        indicator_description = "Moving Average Convergence D-I-vergence"
                    elif indicator_name == 'BBANDS': # Bollinger Bands
                        window_dev = 2 # Standard deviation for Bollinger Bands
                        if len(df) < indicator_period:
                            return jsonify({"text": f"Not enough data points ({len(df)}) to calculate {indicator_period}-period Bollinger Bands for {readable_symbol}. Need at least {indicator_period} data points."}), 400
                        
                        bb_hband = ta.volatility.bollinger_hband(df['close'], window=indicator_period, window_dev=window_dev)
                        bb_mband = ta.volatility.bollinger_mband(df['close'], window=indicator_period, window_dev=window_dev)
                        bb_lband = ta.volatility.bollinger_lband(df['close'], window=indicator_period, window_dev=window_dev)

                        upper_band = bb_hband.iloc[-1]
                        middle_band = bb_mband.iloc[-1]
                        lower_band = bb_lband.iloc[-1]

                        if pd.isna(upper_band) or pd.isna(middle_band) or pd.isna(lower_band):
                            return jsonify({"text": f"Could not calculate {indicator_period}-period Bollinger Bands for {readable_symbol}. The data series might be too short or contain invalid values for the period."}), 500

                        indicator_value = {
                            'Upper_Band': upper_band,
                            'Middle_Band': middle_band,
                            'Lower_Band': lower_band
                        }
                        indicator_description = f"{indicator_period}-period Bollinger Bands"
                    elif indicator_name == 'PVT': # NEW: Price Volume Trend
                        if len(df) < 2: # PVT needs at least 2 data points
                            return jsonify({"text": f"Not enough data points ({len(df)}) to calculate Price Volume Trend for {readable_symbol}. Need at least 2 data points."}), 400
                        
                        df['PVT'] = ta.volume.pvt(df['close'], df['volume'])
                        indicator_value = df['PVT'].iloc[-1]
                        indicator_description = "Price Volume Trend"
                    elif indicator_name == 'STOCHRSI': # NEW: Stochastic RSI
                        # STOCHRSI typically needs window_rsi, window_stoch, window_k, window_d
                        # Defaulting to common values: 14, 14, 3, 3
                        window_rsi = indicator_period # Use indicator_period for window_rsi
                        window_stoch = 14
                        window_k = 3
                        window_d = 3

                        if len(df) < max(window_rsi, window_stoch): 
                            return jsonify({"text": f"Not enough data points ({len(df)}) to calculate Stochastic RSI for {readable_symbol}. Need at least {max(window_rsi, window_stoch)} data points."}), 400
                        
                        df['STOCHRSI_K'] = ta.momentum.stochrsi_k(df['close'], window=window_rsi, smooth1=window_stoch, smooth2=window_k)
                        df['STOCHRSI_D'] = ta.momentum.stochrsi_d(df['close'], window=window_rsi, smooth1=window_stoch, smooth2=window_d)
                        
                        stochrsi_k = df['STOCHRSI_K'].iloc[-1]
                        stochrsi_d = df['STOCHRSI_D'].iloc[-1]

                        if pd.isna(stochrsi_k) or pd.isna(stochrsi_d):
                            return jsonify({"text": f"Could not calculate Stochastic RSI for {readable_symbol}. Data might be insufficient or contain invalid values for the period."}), 500

                        indicator_value = {
                            'StochRSI_K': stochrsi_k,
                            'StochRSI_D': stochrsi_d
                        }
                        indicator_description = f"{indicator_period}-period Stochastic RSI"

                    else:
                        return jsonify({"text": f"Error: Indicator '{indicator}' not supported for local calculation. Supported: SMA, EMA, RSI, MACD, BBANDS, PVT, STOCHRSI."}), 400

                    if indicator_value is not None:
                        if isinstance(indicator_value, dict):
                            response_text = f"The {indicator_description} for {readable_symbol} is: "
                            for key, val in indicator_value.items():
                                response_text += f"{key}: {val:,.2f}. "
                            response_data = {"text": response_text.strip()}
                        else:
                            response_data = {"text": f"The {indicator_description} for {readable_symbol} is {indicator_value:,.2f}."}
                    else:
                        return jsonify({"text": f"Could not calculate {indicator_name} for {readable_symbol}. Data might be insufficient or invalid for local calculation."}), 500

                elif indicator_source == 'twelvedata': # Fetch indicator directly from Twelve Data API
                    indicator_name_lower = indicator.lower()
                    api_url = (
                        f"https://api.twelvedata.com/{indicator_name_lower}?"
                        f"symbol={symbol}&"
                        f"interval={interval}&"
                        f"time_period={indicator_period}&" # Use time_period for indicator period
                        f"apikey={TWELVE_DATA_API_KEY}"
                    )
                    print(f"Fetching {indicator} for {symbol} (period: {indicator_period}, interval: {interval}) from Twelve Data API directly...")
                    response = requests.get(api_url)
                    response.raise_for_status()
                    data = response.json()

                    if data.get('status') == 'error':
                        error_message = data.get('message', 'Unknown error from Twelve Data.')
                        print(f"Twelve Data API error for {indicator} for {symbol}: {error_message}")
                        return jsonify({"text": f"Could not retrieve {indicator} for {symbol}. Error: {error_message}"}), 500
                    
                    indicator_values_td = data.get('values')
                    if indicator_values_td:
                        latest_indicator_data_td = indicator_values_td[0]
                        
                        readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper()
                        indicator_name_upper = indicator.upper()

                        if indicator_name_upper in ['SMA', 'EMA', 'RSI']:
                            indicator_value_td = latest_indicator_data_td.get(indicator_name_lower)
                            if indicator_value_td is not None:
                                try:
                                    formatted_value_td = f"{float(indicator_value_td):,.2f}"
                                    response_data = {"text": f"The {indicator_name_upper} ({indicator_period}-period, {interval}) for {readable_symbol} is {formatted_value_td}."}
                                except ValueError:
                                    print(f"Twelve Data returned invalid indicator format for {indicator_name}: {indicator_value_td}")
                                    return jsonify({"text": f"Could not parse {indicator_name} for {readable_symbol}. Invalid format received from Twelve Data."}), 500
                            else:
                                return jsonify({"text": f"Could not find {indicator_name_upper} value for {readable_symbol} in Twelve Data API response."})
                        elif indicator_name_upper == 'MACD':
                            macd_line_td = latest_indicator_data_td.get('macd')
                            macd_signal_td = latest_indicator_data_td.get('macd_signal')
                            macd_diff_td = latest_indicator_data_td.get('macd_diff')

                            if all(v is not None for v in [macd_line_td, macd_signal_td, macd_diff_td]):
                                try:
                                    response_text = (
                                        f"The MACD for {readable_symbol} ({interval}) is: "
                                        f"MACD Line: {float(macd_line_td):,.2f}, "
                                        f"Signal Line: {float(macd_signal_td):,.2f}, "
                                        f"Histogram: {float(macd_diff_td):,.2f}."
                                    )
                                    response_data = {"text": response_text}
                                except ValueError:
                                    print(f"Twelve Data returned invalid MACD format: {latest_indicator_data_td}")
                                    return jsonify({"text": f"Could not parse MACD values for {readable_symbol}. Invalid format received from Twelve Data."}), 500
                            else:
                                return jsonify({"text": f"Could not find all MACD components for {readable_symbol} in Twelve Data API response."})
                        elif indicator_name_upper == 'BBANDS': # Bollinger Bands for Twelve Data direct
                            # Twelve Data's direct BBANDS endpoint returns 'upper', 'middle', 'lower'
                            upper_band_td = latest_indicator_data_td.get('upper')
                            middle_band_td = latest_indicator_data_td.get('middle')
                            lower_band_td = latest_indicator_data_td.get('lower')
                            
                            if all(v is not None for v in [upper_band_td, middle_band_td, lower_band_td]):
                                try:
                                    response_text = (
                                        f"The {indicator_period}-period Bollinger Bands for {readable_symbol} ({interval}) are: "
                                        f"Upper Band: {float(upper_band_td):,.2f}, "
                                        f"Middle Band: {float(middle_band_td):,.2f}, "
                                        f"Lower Band: {float(lower_band_td):,.2f}."
                                    )
                                    response_data = {"text": response_text}
                                except ValueError:
                                    print(f"Twelve Data returned invalid BBANDS format: {latest_indicator_data_td}")
                                    return jsonify({"text": f"Could not parse Bollinger Bands for {readable_symbol}. Invalid format received from Twelve Data."}), 500
                            else:
                                return jsonify({"text": f"Could not find all Bollinger Bands components for {readable_symbol} in Twelve Data API response."})
                        else:
                            return jsonify({"text": f"Error: Indicator '{indicator}' not supported for direct Twelve Data fetching."}), 400
                    else:
                        return jsonify({"text": f"No indicator data found for {indicator} for {symbol} with the specified parameters from Twelve Data API. The symbol or parameters might be incorrect."}), 500
                else:
                    return jsonify({"text": f"Error: Invalid 'indicator_source' specified. Choose 'local' or 'twelvedata'."}), 400
            globals()['last_twelve_data_call'] = time.time() # Update last call timestamp

        elif data_type == 'news':
            # --- Rate Limiting for NewsAPI ---
            if (current_time - last_news_api_call) < NEWS_API_MIN_INTERVAL:
                time_to_wait = NEWS_API_MIN_INTERVAL - (current_time - last_news_api_call)
                print(f"Rate limit hit for NewsAPI. Waiting {time_to_wait:.2f} seconds.")
                return jsonify({"text": f"Please wait a moment. I'm fetching new news, but there's a slight delay due to API limits. Try again in {int(time_to_wait) + 1} seconds."}), 429

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
            response = requests.get(api_url)
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
