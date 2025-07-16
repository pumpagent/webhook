# Import necessary libraries
from flask import Flask, jsonify, request
import requests
import os
import time
# Removed pandas and ta imports as they are no longer used for local calculations
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
TWELVE_DATA_MIN_INTERVAL = 1 # seconds (e.g., 10 seconds between Twelve Data calls)
NEWS_API_MIN_INTERVAL = 1   # seconds (e.g., 10 seconds between NewsAPI calls)

# Simple in-memory cache for recent responses
# { (data_type, symbol, interval, indicator, indicator_period, news_query, from_date, sort_by, news_language): {'response_json': {}, 'timestamp': float} }
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

    For 'historical' data:
    - 'interval': Time interval (e.g., '1min', '1day'). Defaults to '1day'.
    - 'outputsize': Number of data points. Defaults to '50'.

    For 'indicator' data:
    - 'indicator': Name of the technical indicator (e.g., 'SMA', 'EMA', 'RSI', 'MACD', 'BBANDS', 'STOCH').
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
    symbol = request.args.get('symbol')
    data_type = request.args.get('data_type', 'live').lower()

    interval = request.args.get('interval')
    outputsize = request.args.get('outputsize')

    indicator = request.args.get('indicator')
    indicator_period = request.args.get('indicator_period')
    # Removed indicator_source as it's no longer needed

    news_query = request.args.get('news_query')
    from_date = request.args.get('from_date')
    sort_by = request.args.get('sort_by', 'publishedAt')
    news_language = request.args.get('news_language', 'en')

    # Create a cache key for the current request (removed indicator_source from key)
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
            # Define readable_symbol early to ensure it's always available for error messages
            readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper() if symbol else "N/A"

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

                # Now, directly call Twelve Data's indicator endpoint
                indicator_name_lower = indicator.lower()
                
                # Adjust API URL and parsing based on specific indicator requirements for Twelve Data
                if indicator_name_lower == 'macd':
                    api_url = (
                        f"https://api.twelvedata.com/macd?"
                        f"symbol={symbol}&"
                        f"interval={interval}&"
                        f"fast_period=12&slow_period=26&signal_period=9&" # Using Twelve Data's common default periods
                        f"apikey={TWELVE_DATA_API_KEY}"
                    )
                elif indicator_name_lower == 'bbands':
                    api_url = (
                        f"https://api.twelvedata.com/bbands?"
                        f"symbol={symbol}&"
                        f"interval={interval}&"
                        f"time_period={indicator_period}&"
                        f"std_dev=2&" # Standard deviation for BBANDS
                        f"apikey={TWELVE_DATA_API_KEY}"
                    )
                elif indicator_name_lower in ['sma', 'ema', 'rsi', 'stoch']: # Added 'stoch' here
                    api_url = (
                        f"https://api.twelvedata.com/{indicator_name_lower}?"
                        f"symbol={symbol}&"
                        f"interval={interval}&"
                        f"time_period={indicator_period}&" # Use time_period for indicator period
                        f"apikey={TWELVE_DATA_API_KEY}"
                    )
                # Handle indicators not directly available from Twelve Data's API
                elif indicator_name_lower in ['pvt', 'stochrsi']: # PVT and STOCHRSI are not direct endpoints
                     return jsonify({"text": f"Error: {indicator.upper()} is not available as a direct indicator from Twelve Data API. Please select a different indicator."}), 400
                else:
                    return jsonify({"text": f"Error: Indicator '{indicator}' not supported for direct Twelve Data fetching. Supported: SMA, EMA, RSI, MACD, BBANDS, STOCH."}), 400 # Updated supported list

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
                    
                    indicator_name_upper = indicator.upper()

                    if indicator_name_upper in ['SMA', 'EMA', 'RSI']:
                        indicator_value_td = latest_indicator_data_td.get(indicator_name_lower)
                        if indicator_value_td is not None:
                            try:
                                formatted_value_td = f"{float(indicator_value_td):,.2f}"
                                # NEW: Simplified response for single-value indicators
                                response_data = {"text": f"The {indicator_name_upper} for {readable_symbol} is {formatted_value_td}."}
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
                                    f"The MACD for {readable_symbol} is: "
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
                        upper_band_td = latest_indicator_data_td.get('upper')
                        middle_band_td = latest_indicator_data_td.get('middle')
                        lower_band_td = latest_indicator_data_td.get('lower')
                        
                        if all(v is not None for v in [upper_band_td, middle_band_td, lower_band_td]):
                            try:
                                response_text = (
                                    f"The Bollinger Bands for {readable_symbol} are: " # Simplified description
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
                    elif indicator_name_upper == 'STOCH': # Stochastic Oscillator from Twelve Data
                        stoch_k_td = latest_indicator_data_td.get('stoch_k')
                        stoch_d_td = latest_indicator_data_td.get('stoch_d')

                        if all(v is not None for v in [stoch_k_td, stoch_d_td]):
                            try:
                                response_text = (
                                    f"The Stochastic Oscillator for {readable_symbol} is: " # Simplified description
                                    f"Stoch K: {float(stoch_k_td):,.2f}, "
                                    f"Stoch D: {float(stoch_d_td):,.2f}."
                                )
                                response_data = {"text": response_text}
                            except ValueError:
                                print(f"Twelve Data returned invalid STOCH format: {latest_indicator_data_td}")
                                return jsonify({"text": f"Could not parse Stochastic Oscillator for {readable_symbol}. Invalid format received from Twelve Data."}), 500
                            else:
                                return jsonify({"text": f"No Stochastic Oscillator data found for {readable_symbol} in Twelve Data API response."}), 500
                        else:
                            return jsonify({"text": f"No Stochastic Oscillator data found for {readable_symbol} in Twelve Data API response."}), 500
                    else:
                        return jsonify({"text": f"Error: Indicator '{indicator}' not supported for direct Twelve Data fetching. Supported: SMA, EMA, RSI, MACD, BBANDS, STOCH."}), 400
                else:
                    return jsonify({"text": f"No indicator data found for {indicator} for {symbol} with the specified parameters from Twelve Data API. The symbol or parameters might be incorrect."}), 500
            else: # data_type == 'historical'
                if not outputsize:
                    outputsize = '50' # Default to 50 data points for candlestick analysis
                    print(f"Defaulting 'outputsize' to '{outputsize}' for historical data.")
                try:
                    outputsize = int(float(outputsize)) 
                except (ValueError, TypeError):
                    return jsonify({"text": "Error: 'outputsize' parameter must be a whole number (e.g., 7, not 7.0)."}), 400


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

                response_data = {
                    "text": (
                        f"I have retrieved {len(historical_values)} data points for {readable_symbol} "
                        f"at {interval} intervals, covering from {historical_values[0]['datetime']} to {historical_values[-1]['datetime']}. "
                        f"This data includes Open, High, Low, Close, and Volume prices."
                    )
                }
            globals()['last_twelve_data_call'] = time.time() # Update last call timestamp

        elif data_type == 'news':
            # Define readable_symbol here as well for consistency in error messages
            readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper() if symbol else "N/A"

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
