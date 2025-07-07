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
    - 'outputsize': Number of data points. Defaults to '1' for historical, adjusted for indicator.
    - 'indicator': Name of the technical indicator (e.g., 'SMA', 'EMA', 'RSI', 'MACD').
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
    # Get parameters from the request
    symbol = request.args.get('symbol') # Used for price/TA
    data_type = request.args.get('data_type', 'live').lower()

    # Parameters for price/TA (historical/indicator)
    interval = request.args.get('interval')
    outputsize = request.args.get('outputsize') # Only relevant for historical, not direct indicator calls

    # Parameters for indicator
    indicator = request.args.get('indicator') # Old parameter name
    indicator_period = request.args.get('indicator_period') # Old parameter name

    # Parameters for news
    news_query = request.args.get('news_query')
    from_date = request.args.get('from_date')
    sort_by = request.args.get('sort_by', 'publishedAt')
    news_language = request.args.get('news_language', 'en')

    # Basic validation for API keys
    if (data_type != 'news' and not TWELVE_DATA_API_KEY) or \
       (data_type == 'news' and not NEWS_API_KEY):
        print(f"Error: Missing API key for {data_type} data.")
        return jsonify({"text": "Error: Server configuration issue. API key is missing."}), 500

    try:
        if data_type == 'live':
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
                    return jsonify({"text": f"The current price of {readable_symbol} is {formatted_price}."})
                except ValueError:
                    print(f"Twelve Data returned invalid price format for {symbol}: {current_price}")
                    return jsonify({"text": f"Could not parse live price for {symbol}. Invalid format received."}), 500
            else:
                print(f"Twelve Data did not return a 'close' price for {symbol}. Response: {data}")
                return jsonify({"text": f"Could not retrieve live price for {symbol}. The symbol might be invalid or not found."}), 500

        elif data_type == 'historical' or data_type == 'indicator':
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
                    # Attempt to convert directly to int first (for clean integers)
                    indicator_period = int(indicator_period)
                except ValueError:
                    # If direct int conversion fails, try float then int (for "14.0")
                    try:
                        indicator_period = int(float(indicator_period))
                    except (ValueError, TypeError):
                        # If both fail, return a specific error
                        return jsonify({"text": f"Error: The indicator period '{indicator_period}' must be a whole number (e.g., 14, 20, 50). Please avoid decimals or text."}), 400
                # --- END: Enhanced indicator_period parsing ---

                # Ensure outputsize is sufficient for the indicator period
                # Fetch at least 2x the period for safety, or a minimum of 50 if period is small
                required_outputsize = max(indicator_period * 2, 50) 
                if outputsize: # If outputsize is provided by AI agent, use it if sufficient
                    try:
                        outputsize = int(float(outputsize)) 
                    except (ValueError, TypeError):
                        return jsonify({"text": "Error: 'outputsize' parameter must be a whole number (e.g., 7, not 7.0)."}), 400
                    outputsize = max(outputsize, required_outputsize)
                else: # If outputsize not provided, use calculated required_outputsize
                    outputsize = required_outputsize
                print(f"Adjusted 'outputsize' to '{outputsize}' for indicator calculation.")
            else: # data_type == 'historical'
                # For general historical data, default to a reasonable outputsize if not provided
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
            df['close'] = pd.to_numeric(df['close']) # Ensure 'close' column is numeric
            df = df.iloc[::-1].reset_index(drop=True) # Reverse order to oldest first, reset index

            readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper()

            if data_type == 'historical':
                # Simplified response for historical data to avoid overwhelming AI agent
                # Explicitly state OHLC data is provided for its analysis.
                response_text = (
                    f"I have retrieved {len(historical_values)} data points for {readable_symbol} "
                    f"at {interval} intervals, covering from {df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}. "
                    f"This data includes Open, High, Low, and Close prices, which can be used for candlestick analysis by the agent."
                )
                return jsonify({"text": response_text.strip()})
            
            elif data_type == 'indicator':
                indicator_value = None
                indicator_name = indicator.upper()

                if indicator_name == 'SMA':
                    if len(df) < indicator_period:
                        return jsonify({"text": f"Not enough data points ({len(df)}) to calculate {indicator_period}-period SMA for {readable_symbol}. Need at least {indicator_period} data points."}), 400
                    df['SMA'] = ta.trend.sma_indicator(df['close'], window=indicator_period)
                    indicator_value = df['SMA'].iloc[-1] # Get the latest SMA value
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
                    if len(df) < 34: # MACD typically needs at least 26 (slow EMA) + some buffer
                        return jsonify({"text": f"Not enough data points ({len(df)}) to calculate MACD for {readable_symbol}. Need at least 34 data points."}), 400
                    
                    # FIX: Corrected parameter names for ta.trend.macd
                    macd_line = ta.trend.macd(df['close'], window_fast=12, window_slow=26, window_signal=9)
                    macd_signal_line = ta.trend.macd_signal(df['close'], window_fast=12, window_slow=26, window_signal=9)
                    macd_histogram = ta.trend.macd_diff(df['close'], window_fast=12, window_slow=26, window_signal=9)
                    
                    indicator_value = {
                        'MACD_Line': macd_line.iloc[-1],
                        'Signal_Line': macd_signal_line.iloc[-1],
                        'Histogram': macd_histogram.iloc[-1]
                    }
                    indicator_description = "Moving Average Convergence D-I-vergence" # Added hyphen for better vocalization
                else:
                    return jsonify({"text": f"Error: Indicator '{indicator}' not supported. Supported indicators: SMA, EMA, RSI, MACD."}), 400

                if indicator_value is not None:
                    if isinstance(indicator_value, dict):
                        response_text = f"The {indicator_description} for {readable_symbol} is: "
                        for key, val in indicator_value.items():
                            response_text += f"{key}: {val:,.2f}. "
                        return jsonify({"text": response_text.strip()})
                    else:
                        return jsonify({"text": f"The {indicator_description} for {readable_symbol} is {indicator_value:,.2f}."})
                else:
                    return jsonify({"text": f"Could not calculate {indicator_name} for {readable_symbol}. Data might be insufficient or invalid."}), 500
        
        elif data_type == 'news':
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
                return jsonify({"text": response_text.strip()})
            else:
                return jsonify({"text": f"No recent news found for '{news_query}'."})

        else:
            return jsonify({"text": "Error: Invalid 'data_type' specified. Choose 'live', 'historical', 'indicator', or 'news'."}), 400

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
