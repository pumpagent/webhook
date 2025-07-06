# Import necessary libraries
from flask import Flask, jsonify, request
import requests
import os
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

    For 'historical' data:
    - 'interval': Time interval (e.g., '1min', '1day'). Defaults to '1day'.
    - 'outputsize': Number of data points. Defaults to '1'.

    For 'indicator' data:
    - 'indicator_name': Name of TA indicator (e.g., 'sma', 'ema', 'rsi', 'macd').
                        Required if 'data_type' is 'indicator'.
    - 'indicator_period': Period for the indicator (e.g., '14', '20', '50').
                          Required if 'data_type' is 'indicator'.
                          Twelve Data uses 'time_period' for this.
    - 'interval': Time interval for indicator data (e.g., '1min', '1day'). Defaults to '1day'.

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
    indicator_name = request.args.get('indicator_name') # New parameter name
    indicator_period = request.args.get('indicator_period') # Twelve Data uses 'time_period' for this

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

        elif data_type == 'historical':
            if not symbol:
                return jsonify({"text": "Error: Missing 'symbol' parameter for historical data. Please specify a symbol (e.g., BTC/USD, AAPL)."}), 400
            
            if not interval:
                interval = '1day'
                print(f"Defaulting 'interval' to '{interval}' for historical data.")
            if not outputsize:
                outputsize = '1'
                print(f"Defaulting 'outputsize' to '{outputsize}' for historical data.")
            try:
                outputsize = int(float(outputsize)) 
            except (ValueError, TypeError):
                return jsonify({"text": "Error: 'outputsize' parameter must be a whole number (e.g., 7, not 7.0)."}), 400

            api_url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"
            print(f"Fetching historical data for {symbol} (interval: {interval}, outputsize: {outputsize}) from Twelve Data API...")
            response = requests.get(api_url)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from Twelve Data.')
                print(f"Twelve Data API error for symbol {symbol} historical data: {error_message}")
                return jsonify({"text": f"Could not retrieve historical data for {symbol}. Error: {error_message}"}), 500
            
            historical_values = data.get('values')
            if historical_values:
                latest_data = historical_values[0]
                latest_close = latest_data.get('close')
                datetime_str = latest_data.get('datetime')

                readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper()
                if latest_close is not None and datetime_str is not None:
                    try:
                        formatted_close = f"${float(latest_close):,.2f}"
                        return jsonify({"text": f"The latest closing price for {readable_symbol} at {datetime_str} was {formatted_close}. You requested {len(historical_values)} data points."})
                    except ValueError:
                        print(f"Twelve Data returned invalid historical price format for {symbol}: {latest_close}")
                        return jsonify({"text": f"Could not parse historical price for {symbol}. Invalid format received."}), 500
                else:
                    return jsonify({"text": f"Historical data for {readable_symbol} found, but latest closing price or datetime could not be extracted."})
            else:
                print(f"Twelve Data returned no historical values for {symbol}. Response: {data}")
                return jsonify({"text": f"No historical data found for {symbol} with the specified interval and output size. The symbol or parameters might be incorrect."}), 500
            
        elif data_type == 'indicator': # NEW: Direct call to Twelve Data's /technical_indicators endpoint
            if not symbol:
                return jsonify({"text": "Error: Missing 'symbol' parameter for indicator. Please specify a symbol (e.g., BTC/USD, AAPL)."}), 400
            if not indicator_name:
                return jsonify({"text": "Error: 'indicator_name' parameter is required for technical indicators."}), 400
            if not indicator_period:
                return jsonify({"text": "Error: 'indicator_period' is required for technical indicators."}), 400
            
            # Set default interval if not provided for indicator
            if not interval:
                interval = '1day'
                print(f"Defaulting 'interval' to '{interval}' for indicator data.")

            # Twelve Data's /technical_indicators endpoint
            # Note: Twelve Data uses 'time_period' for indicator period
            api_url = (
                f"https://api.twelvedata.com/{indicator_name}?"
                f"symbol={symbol}&"
                f"interval={interval}&"
                f"time_period={indicator_period}&" # Use time_period for indicator period
                f"apikey={TWELVE_DATA_API_KEY}"
            )
            print(f"Fetching {indicator_name} for {symbol} (period: {indicator_period}, interval: {interval}) from Twelve Data API...")
            response = requests.get(api_url)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from Twelve Data.')
                print(f"Twelve Data API error for {indicator_name} for {symbol}: {error_message}")
                return jsonify({"text": f"Could not retrieve {indicator_name} for {symbol}. Error: {error_message}"}), 500
            
            # Twelve Data indicator responses usually have 'values' key
            indicator_values = data.get('values')
            if indicator_values:
                # The latest indicator value is usually the first in the 'values' array
                latest_indicator_data = indicator_values[0]
                
                # Extract the indicator value (e.g., 'sma', 'ema', 'rsi', 'macd' components)
                # The key for the value depends on the indicator. For SMA, EMA, RSI it's usually the indicator name itself.
                # For MACD, it has 'macd', 'macd_signal', 'macd_diff'.
                
                readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper()
                indicator_name_upper = indicator_name.upper()

                if indicator_name_upper in ['SMA', 'EMA', 'RSI']:
                    indicator_value = latest_indicator_data.get(indicator_name.lower()) # Key is lowercase indicator name
                    if indicator_value is not None:
                        try:
                            formatted_value = f"{float(indicator_value):,.2f}"
                            return jsonify({"text": f"The {indicator_name_upper} ({indicator_period}-period, {interval}) for {readable_symbol} is {formatted_value}."})
                        except ValueError:
                            print(f"Twelve Data returned invalid indicator format for {indicator_name}: {indicator_value}")
                            return jsonify({"text": f"Could not parse {indicator_name} for {readable_symbol}. Invalid format received."}), 500
                    else:
                        return jsonify({"text": f"Could not find {indicator_name_upper} value for {readable_symbol} in the API response."})
                elif indicator_name_upper == 'MACD':
                    macd_line = latest_indicator_data.get('macd')
                    macd_signal = latest_indicator_data.get('macd_signal')
                    macd_diff = latest_indicator_data.get('macd_diff')
                    
                    if all(v is not None for v in [macd_line, macd_signal, macd_diff]):
                        try:
                            response_text = (
                                f"The MACD for {readable_symbol} ({interval}) is: "
                                f"MACD Line: {float(macd_line):,.2f}, "
                                f"Signal Line: {float(macd_signal):,.2f}, "
                                f"Histogram: {float(macd_diff):,.2f}."
                            )
                            return jsonify({"text": response_text})
                        except ValueError:
                            print(f"Twelve Data returned invalid MACD format: {latest_indicator_data}")
                            return jsonify({"text": f"Could not parse MACD values for {readable_symbol}. Invalid format received."}), 500
                    else:
                        return jsonify({"text": f"Could not find all MACD components for {readable_symbol} in the API response."})
                else:
                    return jsonify({"text": f"Error: Indicator '{indicator_name}' not supported by this webhook."}), 400
            else:
                return jsonify({"text": f"No indicator data found for {indicator_name} for {symbol} with the specified parameters. The symbol or parameters might be incorrect."}), 500

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
