# Import necessary libraries
from flask import Flask, jsonify, request
import requests
import os

# Initialize the Flask application
app = Flask(__name__)

# Define the webhook endpoint
@app.route('/bitcoin_price', methods=['GET'])
def get_bitcoin_price():
    """
    This endpoint fetches the current Bitcoin price in USD from the CoinGecko API.
    It returns the price as a formatted string within a JSON object,
    suitable for Eleven Labs AI agents.
    """
    try:
        # Make a request to the CoinGecko API to get Bitcoin's price in USD
        # CoinGecko is a reliable and free API for cryptocurrency prices.
        response = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd")

        # Raise an HTTPError for bad responses (4xx or 5xx)
        response.raise_for_status()

        # Parse the JSON response from the API
        data = response.json()

        # Extract the Bitcoin price in USD
        # We use .get() with a default empty dictionary to safely access nested keys
        bitcoin_price = data.get('bitcoin', {}).get('usd')

        # Check if the price was successfully retrieved
        if bitcoin_price is not None:
            # Format the price for better readability (e.g., $65,432.10)
            formatted_price = f"${bitcoin_price:,.2f}"
            
            # Return the formatted price as a JSON response.
            # Eleven Labs webhooks often expect a 'text' field for speech synthesis.
            return jsonify({"text": f"The current Bitcoin price is {formatted_price} US dollars."})
        else:
            # If the price couldn't be found in the API response, return an error
            return jsonify({"text": "Could not retrieve Bitcoin price. The API response was unexpected."}), 500

    except requests.exceptions.RequestException as e:
        # Handle network-related errors (e.g., connection refused, DNS error)
        print(f"Error fetching Bitcoin price: {e}")
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
