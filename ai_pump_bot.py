import os
import discord
import requests

# Discord Bot Token (get this from Discord Developer Portal)
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
# Your Flask Webhook URL (e.g., from Render)
FLASK_WEBHOOK_URL = os.environ.get('FLASK_WEBHOOK_URL')

# Define Discord intents (crucial for message content)
intents = discord.Intents.default()
intents.message_content = True # Enable message content intent
intents.members = True       # Enable server members intent (if needed for user info)
intents.presences = True     # Enable presence intent (if needed)

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    """Event that fires when the bot successfully connects to Discord."""
    print(f'Logged in as {client.user} (ID: {client.user.id})')
    print('------')

@client.event
async def on_message(message):
    """Event that fires when a message is sent in a channel the bot can see."""
    # Ignore messages from the bot itself to prevent infinite loops
    if message.author == client.user:
        return

    # Check if the message starts with a specific prefix (e.g., '!')
    # or if the bot is mentioned. For simplicity, let's assume direct messages or mentions.
    # For a general chatbot, you might want to process all messages in certain channels.

    # For this example, we'll assume any message not from the bot is a query.
    # You might want to add logic here to only respond in specific channels
    # or if the bot is explicitly explicitly mentioned.

    user_query = message.content.strip()
    print(f"Received message: '{user_query}' from {message.author}")

    # Prepare parameters for your Flask webhook
    # You'll need to parse the user_query to extract 'symbol', 'data_type', 'indicator', etc.
    # This is the most complex part and depends on how you want users to phrase queries.
    # For example: "price BTC/USD", "RSI AAPL 14", "news Bitcoin"

    # --- Example: Simple parsing for demonstration ---
    params = {}
    response_text_for_discord = "I couldn't understand your request. Please format it like 'price BTC/USD' or 'RSI AAPL 14'."

    try:
        parts = user_query.split()
        if not parts:
            await message.channel.send(response_text_for_discord)
            return

        command = parts[0].lower()
        if command == "price" and len(parts) >= 2:
            params = {'data_type': 'live', 'symbol': parts[1]}
        elif command == "historical" and len(parts) >= 2:
            params = {'data_type': 'historical', 'symbol': parts[1]}
            if len(parts) >= 3: params['interval'] = parts[2]
            if len(parts) >= 4: params['outputsize'] = parts[3]
        elif command == "indicator" and len(parts) >= 4:
            params = {'data_type': 'indicator', 'symbol': parts[1], 'indicator': parts[2], 'indicator_period': parts[3]}
            if len(parts) >= 5: params['interval'] = parts[4]
            if len(parts) >= 6: params['outputsize'] = parts[5]
        elif command == "news" and len(parts) >= 2:
            params = {'data_type': 'news', 'news_query': ' '.join(parts[1:])}
        else:
            await message.channel.send(response_text_for_discord)
            return

        # Make a request to your Flask webhook
        if FLASK_WEBHOOK_URL:
            print(f"Sending request to webhook: {FLASK_WEBHOOK_URL} with params: {params}")
            webhook_response = requests.get(FLASK_WEBHOOK_URL, params=params)
            webhook_response.raise_for_status() # Raise an exception for HTTP errors
            data = webhook_response.json()
            response_text_for_discord = data.get('text', 'No response text from AI agent.')
        else:
            response_text_for_discord = "Error: Flask webhook URL is not configured."

    except requests.exceptions.RequestException as e:
        print(f"Error connecting to Flask webhook: {e}")
        response_text_for_discord = "I'm having trouble connecting to my data processing service. Please try again later."
    except Exception as e:
        print(f"An unexpected error occurred in bot logic: {e}")
        response_text_for_discord = "An unexpected error occurred while processing your request."

    # Send the response back to the Discord channel
    await message.channel.send(response_text_for_discord)

# Run the bot
if __name__ == '__main__':
    if not DISCORD_BOT_TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
    elif not FLASK_WEBHOOK_URL:
        print("Error: FLASK_WEBHOOK_URL environment variable not set.")
    else:
        client.run(DISCORD_BOT_TOKEN)
