# Import necessary libraries
from flask import Flask, jsonify, request
import requests
import os
import time
import pandas as pd # Import pandas for data manipulation
import ta # Import the 'ta' library for technical analysis indicators

# Initialize the Flask application
app = Flask(__name__)

# --- Twelve Data API Configuration ---
# Retrieve the Twelve Data API key from environment variables for security.
# It's crucial NOT to hardcode your API key directly in the code.
# You MUST set this environment variable on your Render dashboard.
TWELVE_DATA_API_KEY = os.environ.get('TWELVE_DATA_API_KEY')

# Define the webhook endpoint
@app.route('/price_data', methods=['GET'])
def get_price_data():
    """
    This endpoint fetches live price, historical data, or technical analysis indicators
    for cryptocurrencies or stocks using the Twelve Data API.

    Required parameters:
    - 'symbol': Ticker symbol (e.g., 'BTC/USD', 'AAPL').

    Optional parameters:
    - 'data_type': 'live' (default), 'historical', or 'indicator'.
    - 'interval': Time interval for historical/indicator data (e.g., '1min', '1day').
                  Defaults to '1day' for historical/indicator if not specified.
    - 'outputsize': Number of data points (e.g., '1', '30', '100').
                    Defaults to '1' for historical, or sufficient for indicator.
    - 'indicator': Name of the technical indicator (e.g., 'SMA', 'EMA', 'RSI', 'MACD').
                   Requires 'data_type' to be 'indicator'.
    - 'indicator_period': Period for the indicator (e.g., '14', '20', '50').
                          Required if 'indicator' is specified.

    Returns: Formatted string within a JSON object for Eleven Labs.
    """
    # Get parameters from the request
    symbol = request.args.get('symbol')
    data_type = request.args.get('data_type', 'live').lower()
    interval = request.args.get('interval')
    outputsize = request.args.get('outputsize')
    indicator = request.args.get('indicator')
    indicator_period = request.args.get('indicator_period')

    # Basic validation for missing symbol and API key
    if not symbol:
        return jsonify({"text": "Error: Missing 'symbol' parameter. Please specify a symbol (e.g., BTC/USD, AAPL)."}), 400
    
    if not TWELVE_DATA_API_KEY:
        print("Error: TWELVE_DATA_API_KEY environment variable not set.")
        return jsonify({"text": "Error: Server configuration issue. API key is missing."}), 500

    try:
        if data_type == 'live':
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
                    # Replace '/' with ' to ' for better speech (e.g., BTC to USD)
                    readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper() 
                    return jsonify({"text": f"The current price of {readable_symbol} is {formatted_price}."})
                except ValueError:
                    print(f"Twelve Data returned invalid price format for {symbol}: {current_price}")
                    return jsonify({"text": f"Could not parse live price for {symbol}. Invalid format received."}), 500
            else:
                print(f"Twelve Data did not return a 'close' price for {symbol}. Response: {data}")
                return jsonify({"text": f"Could not retrieve live price for {symbol}. The symbol might be invalid or not found."}), 500

        elif data_type == 'historical' or data_type == 'indicator':
            # Set default interval and outputsize if not provided
            if not interval:
                interval = '1day'
                print(f"Defaulting 'interval' to '{interval}' for historical/indicator data.")
            
            # For indicators, ensure enough data points are fetched
            if data_type == 'indicator':
                if not indicator:
                    return jsonify({"text": "Error: 'indicator' parameter is required when 'data_type' is 'indicator'."}), 400
                if not indicator_period:
                    return jsonify({"text": "Error: 'indicator_period' is required for technical indicators."}), 400
                
                try:
                    # Convert indicator_period to integer
                    indicator_period = int(indicator_period)
                except (ValueError, TypeError):
                    return jsonify({"text": "Error: 'indicator_period' must be a whole number."}), 400

                # Ensure outputsize is sufficient for the indicator period
                # Fetch at least 2x the period for safety, or a minimum of 50 if period is small
                required_outputsize = max(indicator_period * 2, 50) 
                if outputsize:
                    try:
                        # Convert to float first to handle "7.0", then to int
                        outputsize = int(float(outputsize)) 
                    except (ValueError, TypeError):
                        return jsonify({"text": "Error: 'outputsize' parameter must be a whole number (e.g., 7, not 7.0)."}), 400
                    outputsize = max(outputsize, required_outputsize)
                else:
                    outputsize = required_outputsize
                print(f"Adjusted 'outputsize' to '{outputsize}' for indicator calculation.")
            else: # data_type == 'historical'
                if not outputsize:
                    outputsize = '1'
                    print(f"Defaulting 'outputsize' to '{outputsize}' for historical data.")
                try:
                    # Convert to float first to handle "7.0", then to int
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
                latest_data = historical_values[0] # Most recent data point is first from API
                latest_close = latest_data.get('close')
                datetime_str = latest_data.get('datetime')
                if latest_close is not None and datetime_str is not None:
                    try:
                        formatted_close = f"${float(latest_close):,.2f}"
                        return jsonify({"text": f"The latest closing price for {readable_symbol} at {datetime_str} was {formatted_close}. You requested {len(historical_values)} data points."})
                    except ValueError:
                        print(f"Twelve Data returned invalid historical price format for {symbol}: {latest_close}")
                        return jsonify({"text": f"Could not parse historical price for {symbol}. Invalid format received."}), 500
                else:
                    return jsonify({"text": f"Historical data for {readable_symbol} found, but latest closing price or datetime could not be extracted."})
            
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
                    # RSI typically needs more data than just the period for accurate calculation from scratch
                    # A common practice is to ensure at least 2*period data points.
                    if len(df) < indicator_period * 2: 
                        return jsonify({"text": f"Not enough data points ({len(df)}) to calculate {indicator_period}-period RSI for {readable_symbol}. Need at least {indicator_period * 2} data points."}), 400
                    df['RSI'] = ta.momentum.rsi(df['close'], window=indicator_period)
                    indicator_value = df['RSI'].iloc[-1]
                    indicator_description = f"{indicator_period}-period Relative Strength Index"
                elif indicator_name == 'MACD':
                    # MACD requires default fast=12, slow=26, signal=9.
                    # For simplicity, if indicator_period is provided, we can use it for the fast EMA.
                    # More robust implementation would need separate fast/slow/signal periods.
                    # MACD calculation requires a longer history than the periods themselves.
                    if len(df) < 34: # Typical MACD needs at least 26 (slow EMA) + some buffer
                        return jsonify({"text": f"Not enough data points ({len(df)}) to calculate MACD for {readable_symbol}. Need at least 34 data points."}), 400
                    
                    macd_line = ta.trend.macd(df['close'], window_fast=12, window_slow=26, window_sign=9)
                    macd_signal_line = ta.trend.macd_signal(df['close'], window_fast=12, window_slow=26, window_sign=9)
                    macd_histogram = ta.trend.macd_diff(df['close'], window_fast=12, window_slow=26, window_sign=9)
                    
                    # Return all three MACD components
                    indicator_value = {
                        'MACD_Line': macd_line.iloc[-1],
                        'Signal_Line': macd_signal_line.iloc[-1],
                        'Histogram': macd_histogram.iloc[-1]
                    }
                    indicator_description = "Moving Average Convergence Divergence"
                else:
                    return jsonify({"text": f"Error: Indicator '{indicator}' not supported. Supported indicators: SMA, EMA, RSI, MACD."}), 400

                if indicator_value is not None:
                    if isinstance(indicator_value, dict): # For indicators like MACD with multiple values
                        response_text = f"The {indicator_description} for {readable_symbol} is: "
                        for key, val in indicator_value.items():
                            response_text += f"{key}: {val:,.2f}. "
                        return jsonify({"text": response_text.strip()})
                    else: # For single-value indicators
                        return jsonify({"text": f"The {indicator_description} for {readable_symbol} is {indicator_value:,.2f}."})
                else:
                    return jsonify({"text": f"Could not calculate {indicator_name} for {readable_symbol}. Data might be insufficient or invalid."}), 500
        else:
            return jsonify({"text": "Error: Invalid 'data_type' specified. Choose 'live', 'historical', or 'indicator'."}), 400

    except requests.exceptions.RequestException as e:
        print(f"Error connecting to Twelve Data API: {e}")
        return jsonify({"text": "Error connecting to the data service. Please check your internet connection or try again later."}), 500
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return jsonify({"text": "An unexpected error occurred while processing your request. Please try again later."}), 500

# This block ensures the Flask app runs when the script is executed directly.
if __name__ == '__main__':
    # Get the port from environment variables (common for deployment platforms)
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
