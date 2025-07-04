# Import necessary libraries
from flask import Flask, jsonify, request
import requests
import os

# Initialize the Flask application
app = Flask(__name__)

# Define the webhook endpoint
# This endpoint now accepts a 'symbol' parameter to fetch different cryptocurrencies.
@app.route('/crypto_price', methods=['GET'])
def get_crypto_price():
    """
    This endpoint fetches the current cryptocurrency price in USD from the CoinGecko API.
    It requires a 'symbol' query parameter (e.g., 'bitcoin', 'ethereum').
    It returns the price as a formatted string within a JSON object,
    suitable for Eleven Labs AI agents.
    """
    # Get the cryptocurrency symbol from the request arguments (e.g., /crypto_price?symbol=bitcoin)
    symbol = request.args.get('symbol')

    # Basic validation for missing symbol
    if not symbol:
        return jsonify({"text": "Error: Missing 'symbol' parameter. Please specify a cryptocurrency symbol (e.g., bitcoin, ethereum)."}), 400

    try:
        # Make a request to the CoinGecko API to get the specified crypto's price in USD
        # CoinGecko uses the cryptocurrency's ID (e.g., 'bitcoin', 'ethereum')
        api_url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol}&vs_currencies=usd"
        
        response = requests.get(api_url)

        # Raise an HTTPError for bad responses (4xx or 5xx)
        response.raise_for_status()

        # Parse the JSON response from the API
        data = response.json()

        # Extract the cryptocurrency price in USD
        # We use .get() with a default empty dictionary to safely access nested keys
        # The key in the response will be the 'symbol' itself (e.g., data['bitcoin']['usd'])
        crypto_price = data.get(symbol, {}).get('usd')

        # Check if the price was successfully retrieved
        if crypto_price is not None:
            # Format the price for better readability (e.g., $65,432.10)
            formatted_price = f"${crypto_price:,.2f}"
            
            # Return the formatted price as a JSON response.
            # Eleven Labs webhooks often expect a 'text' field for speech synthesis.
            return jsonify({"text": f"The current price of {symbol.replace('-', ' ')} is {formatted_price} US dollars."})
        else:
            # If the price couldn't be found in the API response, return an error
            # This might happen if the symbol is invalid or not found on CoinGecko
            print(f"CoinGecko did not return a price for {symbol}. Response: {data}")
            return jsonify({"text": f"Could not retrieve price for {symbol}. The cryptocurrency might not be listed or the symbol is incorrect."}), 500

    except requests.exceptions.RequestException as e:
        # Handle network-related errors (e.g., connection refused, DNS error)
        print(f"Error fetching cryptocurrency price from CoinGecko: {e}")
        return jsonify({"text": "Error connecting to the price service. Please check your internet connection or try again later."}), 500
    except Exception as e:
        # Catch any other unexpected errors
        print(f"An unexpected error occurred: {e}")
        return jsonify({"text": "An unexpected error occurred while processing your request. Please try again later."}), 500

# This block ensures the Flask app runs when the script is executed directly.
if __name__ == '__main__':
    # Get the port from environment variables (common for deployment platforms)
    # or default to 5000 for local development.
    port = int(os.environ.get('PORT', 5000))
    # Run the Flask application, accessible from any IP address (0.0.0.0)
    app.run(host='0.0.0.0', port=port)
