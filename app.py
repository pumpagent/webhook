# Import necessary libraries
from flask import Flask, jsonify, request
import requests
import os
import time # Import the time module for potential future caching or timestamping

# Initialize the Flask application
app = Flask(__name__)

# --- Twelve Data API Configuration ---
# Retrieve the Twelve Data API key from environment variables for security.
# It's crucial NOT to hardcode your API key directly in the code.
# You MUST set this environment variable on your Render dashboard.
TWELVE_DATA_API_KEY = os.environ.get('TWELVE_DATA_API_KEY')

# Define the webhook endpoint
# This endpoint now handles both live prices and historical data for crypto and stocks.
@app.route('/price_data', methods=['GET'])
def get_price_data():
    """
    This endpoint fetches live price or historical data for cryptocurrencies or stocks
    using the Twelve Data API.

    It requires a 'symbol' query parameter (e.g., 'BTC/USD', 'AAPL').
    It also accepts an optional 'data_type' parameter ('live' for current price,
    'historical' for historical data). Defaults to 'live'.
    For 'historical' data, 'interval' (e.g., '1min', '1day') and 'outputsize'
    (number of data points) are also required.

    It returns the data as a formatted string within a JSON object,
    suitable for Eleven Labs AI agents.
    """
    # Get parameters from the request
    symbol = request.args.get('symbol')
    data_type = request.args.get('data_type', 'live').lower() # Default to 'live'
    interval = request.args.get('interval') # For historical data
    outputsize = request.args.get('outputsize') # For historical data

    # Basic validation for missing symbol and API key
    if not symbol:
        return jsonify({"text": "Error: Missing 'symbol' parameter. Please specify a symbol (e.g., BTC/USD, AAPL)."}), 400
    
    if not TWELVE_DATA_API_KEY:
        print("Error: TWELVE_DATA_API_KEY environment variable not set.")
        return jsonify({"text": "Error: Server configuration issue. API key is missing."}), 500

    try:
        if data_type == 'live':
            # Twelve Data's API endpoint for real-time quotes
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

        elif data_type == 'historical':
            # --- FIX: Provide default values if interval or outputsize are not provided ---
            if not interval:
                interval = '1day' # Default interval if not specified
                print(f"Defaulting 'interval' to '{interval}' for historical data.")
            if not outputsize:
                outputsize = '1' # Default outputsize if not specified (latest data point)
                print(f"Defaulting 'outputsize' to '{outputsize}' for historical data.")
            
            # --- NEW FIX: Convert outputsize to integer ---
            try:
                outputsize = int(float(outputsize)) # Convert to float first to handle "7.0", then to int
            except (ValueError, TypeError):
                return jsonify({"text": "Error: 'outputsize' parameter must be a whole number (e.g., 7, not 7.0)."}), 400
            # --- END NEW FIX ---

            # Twelve Data's API endpoint for time series (historical data)
            api_url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"
            print(f"Fetching historical data for {symbol} with interval {interval} and outputsize {outputsize} from Twelve Data API...")
            response = requests.get(api_url)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from Twelve Data.')
                print(f"Twelve Data API error for symbol {symbol} historical data: {error_message}")
                return jsonify({"text": f"Could not retrieve historical data for {symbol}. Error: {error_message}"}), 500
            
            # Historical data is in the 'values' key
            historical_values = data.get('values')
            if historical_values:
                # For simplicity, let's return the latest closing price and the number of data points
                latest_data = historical_values[0] # Most recent data point is usually first
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
        else:
            return jsonify({"text": "Error: Invalid 'data_type' specified. Choose 'live' or 'historical'."}), 400

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
