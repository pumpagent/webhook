import os
import discord
import requests
import json
import re # Import regex for parsing indicator values
import time # For rate limiting
from datetime import datetime, timedelta # Import for date handling

# --- API Keys and URLs (Set as Environment Variables on Render) ---
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
TWELVE_DATA_API_KEY = os.environ.get('TWELVE_DATA_API_KEY') # Directly used by the bot
NEWS_API_KEY = os.environ.get('NEWS_API_KEY') # Directly used by the bot

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

client = discord.Client(intents=intents)

# --- Rate Limiting & Caching Configuration ---
last_twelve_data_call = 0
TWELVE_DATA_MIN_INTERVAL = 10 # seconds (e.g., 10 seconds between API calls)
last_news_api_call = 0
NEWS_API_MIN_INTERVAL = 10 # seconds for news API as well
api_response_cache = {}
CACHE_DURATION = 10 # Cache responses for 10 seconds

# --- Conversation Memory (In-memory, volatile on bot restart) ---
conversation_histories = {} # Format: {user_id: [{"role": "user/model/function", "parts": [...]}, ...]}
MAX_CONVERSATION_TURNS = 10 # Keep last 10 turns (user + model/function) in memory for LLM context

# --- AUTHORIZED USERS (Add your Discord User IDs here) ---
# Messages from users NOT in this list will be ignored.
# You can get your Discord User ID by enabling Developer Mode (User Settings -> Advanced)
# then right-clicking your username and selecting "Copy ID".
AUTHORIZED_USER_IDS = ["YOUR_DISCORD_USER_ID_HERE", "ANOTHER_FRIEND_ID_HERE"] # <<< IMPORTANT: REPLACE WITH ACTUAL IDs >>>

# Discord message character limit
DISCORD_MESSAGE_MAX_LENGTH = 2000

def split_message(message_content, max_length=DISCORD_MESSAGE_MAX_LENGTH):
    """Splits a message into chunks that fit Discord's character limit."""
    if len(message_content) <= max_length:
        return [message_content]
    
    chunks = []
    while len(message_content) > 0:
        if len(message_content) <= max_length:
            chunks.append(message_content)
            break
        
        # Try to find a natural break point (e.g., last newline or sentence end)
        split_point = message_content[:max_length].rfind('\n')
        if split_point == -1:
            split_point = message_content[:max_length].rfind('. ')
        if split_point == -1:
            split_point = message_content[:max_length].rfind(' ')
        
        if split_point == -1 or split_point == 0: # No natural break, force split
            split_point = max_length
        
        chunks.append(message_content[:split_point])
        message_content = message_content[split_point:].lstrip() # Remove leading whitespace

    return chunks


async def _fetch_data_from_twelve_data(data_type, symbol=None, interval=None, outputsize=None,
                                       indicator=None, indicator_period=None, news_query=None,
                                       from_date=None, sort_by=None, news_language=None):
    """
    Helper function to fetch data directly from Twelve Data API or NewsAPI.org.
    Includes rate limiting and caching.
    """
    global last_twelve_data_call, last_news_api_call

    cache_key = (data_type, symbol, interval, outputsize, indicator, indicator_period,
                 news_query, from_date, sort_by, news_language)
    current_time = time.time()

    # --- Check Cache First ---
    if cache_key in api_response_cache:
        cached_data = api_response_cache[cache_key]
        if (current_time - cached_data['timestamp']) < CACHE_DURATION:
            print(f"Serving cached response for {data_type} request to data service.")
            return cached_data['response_json']

    # --- Rate Limiting ---
    if data_type != 'news':
        if (current_time - last_twelve_data_call) < TWELVE_DATA_MIN_INTERVAL:
            time_to_wait = TWELVE_DATA_MIN_INTERVAL - (current_time - last_twelve_data_call)
            raise requests.exceptions.RequestException(
                f"Rate limit hit for data service. Please wait {time_to_wait:.2f} seconds."
            )
    else: # data_type == 'news'
        if (current_time - last_news_api_call) < NEWS_API_MIN_INTERVAL:
            time_to_wait = NEWS_API_MIN_INTERVAL - (current_time - last_news_api_call)
            raise requests.exceptions.RequestException(
                f"Rate limit hit for News API. Please wait {int(time_to_wait) + 1} seconds."
            )


    readable_symbol = symbol.replace('/', ' to ').replace(':', ' ').upper() if symbol else "N/A"
    response_data = {} # To store the final JSON response

    try:
        if data_type == 'live':
            if not symbol:
                raise ValueError("Missing 'symbol' parameter for live price.")
            api_url = f"https://api.twelvedata.com/quote?symbol={symbol}&apikey={TWELVE_DATA_API_KEY}"
            print(f"Fetching live price for {symbol} from data service...")
            response = requests.get(api_url)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from data service.')
                raise requests.exceptions.RequestException(f"Data service error for symbol {symbol}: {error_message}")
            
            current_price = data.get('close')
            if current_price is not None:
                formatted_price = f"${float(current_price):,.2f}"
                response_data = {"text": f"The current price of {readable_symbol} is {formatted_price}."}
            else:
                raise ValueError(f"Data service did not return a 'close' price for {symbol}. Response: {data}")

        elif data_type == 'historical':
            if not symbol:
                raise ValueError("Missing 'symbol' parameter for historical data.")
            
            interval_str = interval if interval else '1day'
            outputsize_str = outputsize if outputsize else '50'
            
            api_url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval_str}&outputsize={outputsize_str}&apikey={TWELVE_DATA_API_KEY}"
            print(f"Fetching data for {symbol} (interval: {interval_str}, outputsize: {outputsize_str}) from data service...")
            response = requests.get(api_url)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from data service.')
                raise requests.exceptions.RequestException(f"Data service error for symbol {symbol} historical data: {error_message}")
            
            historical_values = data.get('values')
            if not historical_values:
                raise ValueError(f"No data found for {symbol} with the specified interval and output size. Response: {data}")

            response_data = {
                "text": (
                    f"I have retrieved {len(historical_values)} data points for {readable_symbol} "
                    f"at {interval_str} intervals, covering from {historical_values[-1]['datetime']} to {historical_values[0]['datetime']}. "
                    f"This data includes Open, High, Low, and Close prices."
                )
            }

        elif data_type == 'indicator':
            if not all([symbol, indicator]): # indicator_period can be defaulted
                raise ValueError("Missing required parameters for indicator data (symbol, indicator).")
            
            indicator_name_upper = indicator.upper()
            base_api_url = "https://api.twelvedata.com/"
            indicator_endpoint = ""
            params = {
                'symbol': symbol,
                'interval': interval if interval else '1day', # Default interval
                'apikey': TWELVE_DATA_API_KEY
            }
            
            # Default indicator_period if not provided by LLM
            indicator_period_str = str(indicator_period) if indicator_period else '14' 

            # Map indicator and period to Twelve Data API endpoints and parameters
            if indicator_name_upper == 'RSI':
                indicator_endpoint = "rsi"
                params['time_period'] = indicator_period_str
            elif indicator_name_upper == 'MACD':
                indicator_endpoint = "macd"
                params['fast_period'] = 12
                params['slow_period'] = 26
                params['signal_period'] = 9
            elif indicator_name_upper == 'BBANDS':
                indicator_endpoint = "bbands"
                params['time_period'] = indicator_period_str
                params['sd'] = 2 # Standard deviation, common default
            elif indicator_name_upper == 'STOCHRSI':
                indicator_endpoint = "stochrsi"
                params['time_period'] = indicator_period_str
                params['fast_k_period'] = 3
                params['fast_d_period'] = 3
                params['rsi_time_period'] = indicator_period_str
                params['stoch_time_period'] = indicator_period_str
            elif indicator_name_upper == 'SMA':
                indicator_endpoint = "sma"
                params['time_period'] = indicator_period_str
            elif indicator_name_upper == 'EMA' or indicator_name_upper == 'MA': # Treat MA as EMA
                indicator_endpoint = "ema"
                params['time_period'] = indicator_period_str
            elif indicator_name_upper == 'VWAP': # Added VWAP
                indicator_endpoint = "vwap"
                # VWAP typically doesn't take a 'time_period' in the same way as other indicators
                # It's usually calculated over a session/day. TwelveData's API might just need symbol/interval.
                # If a period is provided, we can add it, otherwise, rely on default behavior.
                if indicator_period_str != '0': # If a period is explicitly given, use it
                    params['time_period'] = indicator_period_str
            elif indicator_name_upper == 'SUPERTREND': # Added SuperTrend
                indicator_endpoint = "supertrend"
                # SuperTrend uses time_period and factor
                supertrend_period = '10'
                supertrend_factor = '3'
                if indicator_period_str and ',' in indicator_period_str:
                    parts = indicator_period_str.split(',')
                    if len(parts) == 2:
                        supertrend_period = parts[0].strip()
                        supertrend_factor = parts[1].strip()
                elif indicator_period_str and indicator_period_str != '0':
                    supertrend_period = indicator_period_str # Use provided as period, factor default
                
                params['time_period'] = supertrend_period
                params['factor'] = supertrend_factor
            else:
                raise ValueError(f"Indicator '{indicator}' not supported by direct API.")

            api_url = f"{base_api_url}{indicator_endpoint}"
            print(f"Fetching {indicator_name_upper} for {symbol} from data service with params: {params}...")
            response = requests.get(api_url, params=params)
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'error':
                error_message = data.get('message', 'Unknown error from data service.')
                raise requests.exceptions.RequestException(f"Data service error for {indicator_name_upper} for {symbol}: {error_message}")
            
            indicator_value = None
            indicator_description = ""
            
            # --- CORRECTED PARSING FOR INDICATOR VALUES FROM 'values' LIST ---
            if data.get('values') and len(data['values']) > 0:
                latest_values = data['values'][0] # Get the most recent data point
                print(f"DEBUG: {indicator_name_upper} - latest_values: {latest_values}") # Debugging print

                if indicator_name_upper == 'RSI':
                    value = latest_values.get('rsi')
                    print(f"DEBUG: RSI - raw value: {value}, type: {type(value)}") # Debugging print
                    if value is not None:
                        try:
                            indicator_value = float(str(value).replace(',', '')) # Ensure string before replace
                            indicator_description = f"{indicator_period_str}-period Relative Strength Index"
                        except ValueError as ve:
                            print(f"DEBUG: ValueError during RSI float conversion: {ve} for value: '{value}'")
                            indicator_value = None # Ensure it's None if conversion fails
                elif indicator_name_upper == 'MACD':
                    macd = latest_values.get('macd')
                    signal = latest_values.get('signal')
                    histogram = latest_values.get('histogram')
                    print(f"DEBUG: MACD - raw macd: {macd}, signal: {signal}, histogram: {histogram}") # Debugging print
                    if all(v is not None for v in [macd, signal, histogram]):
                        try:
                            indicator_value = {
                                'MACD_Line': float(str(macd).replace(',', '')),
                                'Signal_Line': float(str(signal).replace(',', '')),
                                'Histogram': float(str(histogram).replace(',', ''))
                            }
                            indicator_description = "Moving Average Convergence D-I-vergence"
                        except ValueError as ve:
                            print(f"DEBUG: ValueError during MACD float conversion: {ve}")
                            indicator_value = None
                elif indicator_name_upper == 'BBANDS':
                    upper = latest_values.get('upper')
                    middle = latest_values.get('middle')
                    lower = latest_values.get('lower')
                    print(f"DEBUG: BBANDS - raw upper: {upper}, middle: {middle}, lower: {lower}") # Debugging print
                    if all(v is not None for v in [upper, middle, lower]):
                        try:
                            indicator_value = {
                                'Upper_Band': float(str(upper).replace(',', '')),
                                'Middle_Band': float(str(middle).replace(',', '')),
                                'Lower_Band': float(str(lower).replace(',', ''))
                            }
                            indicator_description = f"{indicator_period_str}-period Bollinger Bands"
                        except ValueError as ve:
                            print(f"DEBUG: ValueError during BBANDS float conversion: {ve}")
                            indicator_value = None
                elif indicator_name_upper == 'STOCHRSI':
                    stochrsi_k = latest_values.get('stochrsi')
                    stochrsi_d = latest_values.get('stochrsi_signal')
                    print(f"DEBUG: STOCHRSI - raw K: {stochrsi_k}, D: {stochrsi_d}") # Debugging print
                    if all(v is not None for v in [stochrsi_k, stochrsi_d]):
                        try:
                            indicator_value = {
                                'StochRSI_K': float(str(stochrsi_k).replace(',', '')),
                                'StochRSI_D': float(str(stochrsi_d).replace(',', ''))
                            }
                            indicator_description = f"{indicator_period_str}-period Stochastic Relative Strength Index"
                        except ValueError as ve:
                            print(f"DEBUG: ValueError during STOCHRSI float conversion: {ve}")
                            indicator_value = None
                elif indicator_name_upper == 'SMA':
                    value = latest_values.get('value')
                    print(f"DEBUG: SMA - raw value: {value}") # Debugging print
                    if value is not None:
                        try:
                            indicator_value = float(str(value).replace(',', ''))
                            indicator_description = f"{indicator_period_str}-period Simple Moving Average"
                        except ValueError as ve:
                            print(f"DEBUG: ValueError during SMA float conversion: {ve}")
                            indicator_value = None
                elif indicator_name_upper == 'EMA' or indicator_name_upper == 'MA': # Treat MA as EMA
                    value = latest_values.get('value')
                    print(f"DEBUG: EMA - raw value: {value}") # Debugging print
                    if value is not None:
                        try:
                            indicator_value = float(str(value).replace(',', ''))
                            indicator_description = f"{indicator_period_str}-period Exponential Moving Average"
                        except ValueError as ve:
                            print(f"DEBUG: ValueError during EMA float conversion: {ve}")
                            indicator_value = None
                elif indicator_name_upper == 'VWAP': # Added VWAP parsing
                    value = latest_values.get('vwap')
                    print(f"DEBUG: VWAP - raw value: {value}")
                    if value is not None:
                        try:
                            indicator_value = float(str(value).replace(',', ''))
                            indicator_description = "Volume Weighted Average Price"
                        except ValueError as ve:
                            print(f"DEBUG: ValueError during VWAP float conversion: {ve}")
                            indicator_value = None
                elif indicator_name_upper == 'SUPERTREND': # SuperTrend parsing
                    value = latest_values.get('supertrend')
                    print(f"DEBUG: SUPERTREND - raw value: {value}")
                    if value is not None:
                        try:
                            indicator_value = float(str(value).replace(',', ''))
                            # SuperTrend also has a 'trend' value (1 for uptrend, -1 for downtrend)
                            # We can potentially use this for assessment directly.
                            indicator_description = f"SuperTrend (Period: {params.get('time_period', 'N/A')}, Factor: {params.get('factor', 'N/A')})"
                        except ValueError as ve:
                            print(f"DEBUG: ValueError during SUPERTREND float conversion: {ve}")
                            indicator_value = None

            if indicator_value is not None:
                if isinstance(indicator_value, dict):
                    response_text = f"The {indicator_description} for {readable_symbol} is: "
                    for key, val in indicator_value.items():
                        response_text += f"{key}: {val:,.2f}. "
                    response_data = {"text": response_text.strip()}
                else:
                    response_data = {"text": f"The {indicator_description} for {readable_symbol} is {indicator_value:,.2f}."}
            else:
                raise ValueError(f"Data service did not return valid indicator values for {indicator_name_upper} for {symbol}. Raw latest_values: {latest_values}")

        elif data_type == 'news':
            # --- Rate Limiting for NewsAPI.org ---
            if (current_time - last_news_api_call) < NEWS_API_MIN_INTERVAL:
                time_to_wait = NEWS_API_MIN_INTERVAL - (current_time - last_news_api_call)
                raise requests.exceptions.RequestException(
                    f"Rate limit hit for News API. Please wait {int(time_to_wait) + 1} seconds."
                )

            if not news_query:
                raise ValueError("Missing 'news_query' parameter for news.")
            
            from_date_str = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            sort_by_str = sort_by if sort_by else 'publishedAt'
            news_language_str = news_language if news_language else 'en'

            news_api_url = (
                f"https://newsapi.org/v2/everything?"
                f"q={news_query}&"
                f"from={from_date_str}&"
                f"sortBy={sort_by_str}&"
                f"language={news_language_str}&"
                f"apiKey={NEWS_API_KEY}" # Use NEWS_API_KEY directly
            )
            print(f"Fetching news for '{news_query}' from News API...")
            response = requests.get(api_url)
            response.raise_for_status()
            news_data = response.json()

            if news_data.get('status') == 'error':
                error_message = news_data.get('message', 'Unknown error from News API.')
                raise requests.exceptions.RequestException(f"News API error: {error_message}")
            
            articles = news_data.get('articles')
            if articles:
                response_text = f"Here are some recent news headlines for {news_query}: "
                for i, article in enumerate(articles[:3]):
                    title = article.get('title', 'No title')
                    source = article.get('source', {}).get('name', 'Unknown source')
                    response_text += f"Number {i+1}: '{title}' from {source}. "
                response_data = {"text": response_text.strip()}
            else:
                response_data = {"text": f"No recent news found for '{news_query}'."}
        else:
            raise ValueError("Invalid 'data_type' specified.")

    except requests.exceptions.RequestException as e:
        raise e # Re-raise for outer try-except to catch
    except ValueError as e:
        raise e # Re-raise for outer try-except to catch
    finally:
        # Update last_twelve_data_call only if it was a Twelve Data call
        if data_type != 'news':
            globals()['last_twelve_data_call'] = time.time()
        else: # Update last_news_api_call for news type
            globals()['last_news_api_call'] = time.time()
    
    api_response_cache[cache_key] = {'response_json': response_data, 'timestamp': time.time()}
    return response_data


async def _perform_sentiment_analysis(symbol, interval_str):
    """
    Performs local sentiment assessment for multiple indicators and returns a combined text.
    """
    analysis_results = []
    overall_sentiment_score = 0 # +1 for bullish, -1 for bearish, 0 for neutral/missing

    # --- Fetch Live Price for BBANDS Context ---
    current_price_val = None
    try:
        live_price_data = await _fetch_data_from_twelve_data(data_type='live', symbol=symbol)
        price_text = live_price_data.get('text', '')
        match = re.search(r'\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)', price_text)
        if match:
            current_price_val = float(match.group(1).replace(',', ''))
            analysis_results.append(f"Current Price: ${current_price_val:,.2f}")
    except Exception as e:
        analysis_results.append(f"Current Price: Data Missing (Error: {e})")
        print(f"Error fetching live price for sentiment analysis: {e}")

    # --- Fetch and Analyze Indicators ---
    indicators_to_fetch = {
        'RSI': {'period': '14'},
        'MACD': {'period': '0'},
        'BBANDS': {'period': '20'},
        'STOCHRSI': {'period': '14'},
        'SMA': {'period': '50'}, # Default SMA period for analysis
        'EMA': {'period': '50'},  # Default EMA period for analysis
        'VWAP': {'period': '0'}, # VWAP doesn't use a period in the same way, '0' as placeholder
        'SUPERTREND': {'period': '10,3'} # Default SuperTrend period and factor
    }
    
    for indicator_name, params in indicators_to_fetch.items():
        indicator_period = params['period']

        try:
            indicator_data_json = await _fetch_data_from_twelve_data(
                data_type='indicator',
                symbol=symbol,
                indicator=indicator_name,
                indicator_period=indicator_period,
                interval=interval_str
            )
            indicator_text = indicator_data_json.get('text', f"{indicator_name} data N/A")
            
            assessment = "Neutral"
            # Extract value and perform assessment
            if "The" in indicator_text and "is" in indicator_text:
                if indicator_name == 'RSI':
                    try:
                        val_str = indicator_text.split(' is ')[-1].strip()
                        val = float(re.sub(r'[^\d.]', '', val_str))
                        if val > 70: assessment = "Bearish" # Overbought
                        elif val < 30: assessment = "Bullish" # Oversold
                    except ValueError: pass
                elif indicator_name == 'MACD':
                    if "MACD_Line:" in indicator_text and "Signal_Line:" in indicator_text:
                        try:
                            macd_line_val = float(re.sub(r'[^\d.-]', '', indicator_text.split('MACD_Line: ')[1].split('. ')[0].strip()))
                            signal_line_val = float(re.sub(r'[^\d.-]', '', indicator_text.split('Signal_Line: ')[1].split('. ')[0].strip()))
                            if macd_line_val > signal_line_val: assessment = "Bullish"
                            elif macd_line_val < signal_line_val: assessment = "Bearish"
                        except (ValueError, IndexError): pass
                elif indicator_name == 'BBANDS' and current_price_val is not None:
                    if "Upper_Band:" in indicator_text and "Lower_Band:" in indicator_text:
                        try:
                            upper_band = float(re.sub(r'[^\d.]', '', indicator_text.split('Upper_Band: ')[1].split('. ')[0].strip()))
                            lower_band = float(re.sub(r'[^\d.]', '', indicator_text.split('Lower_Band: ')[1].split('. ')[0].strip()))
                            if current_price_val > upper_band: assessment = "Bearish" # Price above upper band
                            elif current_price_val < lower_band: assessment = "Bullish" # Price below lower band
                            else: assessment = "Neutral" # Price within bands
                        except (ValueError, IndexError): pass
                elif indicator_name == 'STOCHRSI':
                    if "StochRSI_K:" in indicator_text and "StochRSI_D:" in indicator_text:
                        try:
                            stochrsi_k_val = float(re.sub(r'[^\d.]', '', indicator_text.split('StochRSI_K: ')[1].split('. ')[0].strip()))
                            stochrsi_d_val = float(re.sub(r'[^\d.]', '', indicator_text.split('StochRSI_D: ')[1].split('. ')[0].strip()))
                            if stochrsi_k_val > 80: assessment = "Bearish" # Overbought
                            elif stochrsi_k_val < 20: assessment = "Bullish" # Oversold
                            elif stochrsi_k_val > stochrsi_d_val: assessment = "Bullish" # K crossing above D
                            elif stochrsi_k_val < stochrsi_d_val: assessment = "Bearish" # K crossing below D
                        except (ValueError, IndexError): pass
                elif indicator_name == 'SMA' or indicator_name == 'EMA':
                    try:
                        val_str = indicator_text.split(' is ')[-1].strip()
                        val = float(re.sub(r'[^\d.]', '', val_str))
                        # For SMA/EMA, compare to current price to determine bullish/bearish
                        if current_price_val is not None:
                            if current_price_val > val: assessment = "Bullish" # Price above MA
                            elif current_price_val < val: assessment = "Bearish" # Price below MA
                        else:
                            assessment = "Neutral (Live Price Missing)"
                    except ValueError: pass
                elif indicator_name == 'VWAP': # VWAP assessment
                    try:
                        val_str = indicator_text.split(' is ')[-1].strip()
                        val = float(re.sub(r'[^\d.]', '', val_str))
                        if current_price_val is not None:
                            if current_price_val > val: assessment = "Bullish" # Price above VWAP
                            elif current_price_val < val: assessment = "Bearish" # Price below VWAP
                        else:
                            assessment = "Neutral (Live Price Missing)"
                    except ValueError: pass
                elif indicator_name == 'SUPERTREND': # SuperTrend assessment
                    # SuperTrend text might be "The SuperTrend (Period: 10, Factor: 3) for BTC/USD is: 123456.78."
                    # We need to compare current price to SuperTrend value
                    try:
                        # Extract the numerical value of SuperTrend
                        val_str = indicator_text.split(' is ')[-1].strip()
                        val = float(re.sub(r'[^\d.]', '', val_str))
                        if current_price_val is not None:
                            if current_price_val > val: assessment = "Bullish" # Price above SuperTrend
                            elif current_price_val < val: assessment = "Bearish" # Price below SuperTrend
                        else:
                            assessment = "Neutral (Live Price Missing)"
                    except ValueError: pass
            
            analysis_results.append(f"{indicator_name}: {assessment}")
            if assessment == "Bullish": overall_sentiment_score += 1
            elif assessment == "Bearish": overall_sentiment_score -= 1
        except Exception as e:
            analysis_results.append(f"{indicator_name}: Data Missing (Error: {e})")
            print(f"Error fetching/parsing {indicator_name}: {e}")

    # Determine overall sentiment based on score
    overall_sentiment = "Neutral"
    if overall_sentiment_score > 0: overall_sentiment = "Pump"
    elif overall_sentiment_score < 0: overall_sentiment = "Dump"
    
    if len(analysis_results) == 0 or all("Data Missing" in res for res in analysis_results):
        overall_sentiment = "Undetermined"

    # Formulate final response
    combined_analysis_text = f"Overall Outlook for {symbol} ({interval_str}): **{overall_sentiment}**\n\n"
    combined_analysis_text += "Individual Indicator Assessments:\n" + "\n".join(analysis_results)
    
    return combined_analysis_text


@client.event
async def on_message(message):
    """Event that fires when a message is sent in a channel the bot can see."""
    if message.author == client.user:
        return

    user_id = str(message.author.id)
    # --- NEW: Check if user is authorized ---
    if AUTHORIZED_USER_IDS and str(user_id) not in AUTHORIZED_USER_IDS:
        print(f"Ignoring message from unauthorized user: {user_id}")
        return # Ignore message if user is not authorized

    user_query = message.content.strip()
    print(f"Received message: '{user_query}' from {message.author} (ID: {user_id})")

    if user_id not in conversation_histories:
        conversation_histories[user_id] = []
    
    conversation_histories[user_id].append({"role": "user", "parts": [{"text": user_query}]})
    current_chat_history = conversation_histories[user_id][-MAX_CONVERSATION_TURNS:]

    response_text_for_discord = "I'm currently unavailable. Please try again later."

    try:
        # --- Define the market_data tool for the LLM ---
        tools = [
            {
                "functionDeclarations": [
                    {
                        "name": "get_market_data",
                        "description": (
                            "Fetches live price, historical data, or technical analysis indicators for a given symbol, or market news for a query. "
                            "If the user asks for a general outlook, sentiment, or bullish/bearish assessment for a symbol (e.g., 'Is BTC bullish?', 'Outlook for ETH?', 'Sentiment for SOL?'), "
                            "call this tool with `data_type='indicator'` and **do not provide a specific `indicator` parameter**. "
                            "Default `interval` is '1day'. Default `indicator_period` is '14' (or '0' for MACD). "
                            "If the user asks for a price prediction or price target, use `data_type='indicator'` and the symbol."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "symbol": { "type": "string", "description": "Ticker symbol (e.g., 'BTC/USD', 'AAPL'). This is required." },
                                "data_type": { "type": "string", "enum": ["live", "historical", "indicator", "news"], "description": "Type of data to fetch (live, historical, indicator, news). This is required." },
                                "interval": { "type": "string", "description": "Time interval (e.g., '1min', '1day'). Default to '1day' if not specified by user. Try to infer from context." },
                                "outputsize": { "type": "string", "description": "Number of data points. Default to '50' for historical, adjusted for indicator." },
                                "indicator": { "type": "string", "enum": ["SMA", "EMA", "RSI", "MACD", "BBANDS", "STOCHRSI", "VWAP", "SUPERTREND"], "description": "Name of the technical indicator. Required if data_type is 'indicator' AND a specific indicator is requested by the user." },
                                "indicator_period": { "type": "string", "description": "Period for the indicator (e.g., '14', '20', '50'). Default to '14' if not specified by user. MACD typically uses fixed periods (12, 26, 9) so '0' can be used as a placeholder if period is not relevant for MACD. For SUPERTREND, this can be 'time_period,factor' (e.g., '10,3')." },
                                "news_query": { "type": "string", "description": "Keywords for news search." },
                                "from_date": { "type": "string", "description": "Start date for news (YYYY-MM-DD). Defaults to 7 days ago." },
                                "sort_by": { "type": "string", "enum": ["relevancy", "popularity", "publishedAt"], "description": "How to sort news." },
                                "news_language": { "type": "string", "description": "Language of news." }
                            },
                            "required": ["symbol", "data_type"] # Explicitly make these required
                        }
                    }
                ]
            }
        ]

        llm_api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GOOGLE_API_KEY}"
        
        llm_payload_first_turn = {
            "contents": current_chat_history,
            "tools": tools,
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
        }

        try:
            llm_response_first_turn = requests.post(llm_api_url, headers={'Content-Type': 'application/json'}, json=llm_payload_first_turn)
            llm_response_first_turn.raise_for_status()
            llm_data_first_turn = llm_response_first_turn.json()
        except requests.exceptions.RequestException as e:
            print(f"Error connecting to Gemini LLM (first turn): {e}")
            response_text_for_discord = f"I'm having trouble connecting to my AI brain. Please check the GOOGLE_API_KEY and try again later. Error: {e}"
            for chunk in split_message(response_text_for_discord):
                await message.channel.send(chunk)
            return

        if llm_data_first_turn and llm_data_first_turn.get('candidates'):
            candidate_first_turn = llm_data_first_turn['candidates'][0]
            if candidate_first_turn.get('content') and candidate_first_turn['content'].get('parts'):
                parts_first_turn = candidate_first_turn['content']['parts']
                if parts_first_turn:
                    if parts_first_turn[0].get('functionCall'):
                        function_call = parts_first_turn[0]['functionCall']
                        function_name = function_call['name']
                        function_args = function_call['args']

                        if function_name == "get_market_data":
                            print(f"LLM requested tool call: get_market_data with args: {function_args}")
                            current_chat_history.append({"role": "model", "parts": [{"functionCall": function_call}]})

                            # --- Check if LLM is asking for a general indicator analysis (e.g., "outlook" or no specific indicator) ---
                            if function_args.get('data_type') == 'indicator' and not function_args.get('indicator'):
                                symbol_for_analysis = function_args.get('symbol')
                                interval_for_analysis = function_args.get('interval', '1day') # Use default if not provided
                                if symbol_for_analysis:
                                    tool_output_text = await _perform_sentiment_analysis(symbol_for_analysis, interval_for_analysis)
                                else:
                                    tool_output_text = "Please specify a symbol for analysis."
                            else: # Standard tool call for specific data or news
                                # Pre-populate missing args with defaults before calling helper
                                if 'interval' not in function_args:
                                    function_args['interval'] = '1day'
                                if 'indicator_period' not in function_args:
                                    if function_args.get('indicator', '').upper() == 'MACD':
                                        function_args['indicator_period'] = '0'
                                    else:
                                        function_args['indicator_period'] = '14'
                                
                                for key, value in function_args.items():
                                    function_args[key] = str(value)

                                try:
                                    tool_output_data = await _fetch_data_from_twelve_data(**function_args)
                                    tool_output_text = tool_output_data.get('text', 'No specific response from data service.')
                                    print(f"Tool execution output: {tool_output_text}")
                                except requests.exceptions.RequestException as e:
                                    print(f"Error fetching data from data service via local helper: {e}")
                                    tool_output_text = f"Error fetching data: {e}"
                                except ValueError as e:
                                    print(f"Invalid parameters for data fetch: {e}")
                                    tool_output_text = f"Invalid parameters: {e}"
                                except Exception as e:
                                    print(f"Unexpected error during data fetch: {e}")
                                    tool_output_text = f"An unexpected error occurred: {e}"
                            
                            current_chat_history.append({"role": "function", "parts": [{"functionResponse": {"name": function_name, "response": {"text": tool_output_text}}}]})

                            llm_payload_second_turn = {
                                "contents": current_chat_history,
                                "tools": tools,
                                "safetySettings": [
                                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                                ]
                            }
                            # --- CORRECTED: System instruction for final response (including disclaimer) ---
                            # Rewritten using simple string concatenation for robustness
                            system_instruction_text = (
                                "You are a technical analysis bot named Pump. "
                                "Always start your response with: 'Disclaimer: This information is for informational purposes only and does not constitute financial advice. Always conduct your own research before making investment decisions.' "
                                "Then, based on the provided data or tool output, provide a concise and direct answer to the user's query. "
                                "If the tool output is a comprehensive sentiment analysis, present the overall outlook (Pump, Dump, Neutral, or Undetermined) and then the individual indicator assessments. "
                                "Do not ask follow-up questions unless absolutely necessary due to missing critical information."
                            )
                            llm_payload_second_turn["contents"].insert(0, {"role": "system", "parts": [{"text": system_instruction_text}]})

                            try:
                                llm_response_second_turn = requests.post(llm_api_url, headers={'Content-Type': 'application/json'}, json=llm_payload_second_turn)
                                llm_response_second_turn.raise_for_status()
                                llm_data_second_turn = llm_response_second_turn.json()
                            except requests.exceptions.RequestException as e:
                                print(f"Error connecting to AI brain (second turn after tool): {e}")
                                response_text_for_discord = f"I received the data, but I'm having trouble processing it with my AI brain. Please try again later. Error: {e}"
                                for chunk in split_message(response_text_for_discord):
                                    await message.channel.send(chunk)
                                return

                            if llm_data_second_turn and llm_data_second_turn.get('candidates'):
                                final_candidate = llm_data_second_turn['candidates'][0]
                                if final_candidate.get('content') and final_candidate['content'].get('parts'):
                                    response_text_for_discord = final_candidate['content']['parts'][0].get('text', 'No conversational response from AI.')
                                else:
                                    print(f"LLM second turn: No text content in response. Full response: {llm_data_second_turn}")
                                    block_reason = llm_data_second_turn.get('promptFeedback', {}).get('blockReason', 'unknown')
                                    response_text_for_discord = f"AI could not generate a response. This might be due to content policy. Block reason: {block_reason}. Please try rephrasing."
                            else:
                                response_text_for_discord = "Could not get a valid second response from the AI."
                        else:
                            response_text_for_discord = "AI requested an unknown function."
                    elif parts_first_turn[0].get('text'):
                        response_text_for_discord = parts_first_turn[0]['text']
                    else:
                        print(f"LLM first turn: No text content in response. Full response: {llm_data_first_turn}")
                        block_reason = llm_data_first_turn.get('promptFeedback', {}).get('blockReason', 'unknown')
                        response_text_for_discord = f"AI could not generate a response. This might be due to content policy. Block reason: {block_reason}. Please try rephrasing."
                else:
                    response_text_for_discord = "AI did not provide content in its response."
            else:
                response_text_for_discord = "Could not get a valid response from the AI. Please try again."
                if llm_data_first_turn.get('promptFeedback') and llm_data_first_turn['promptFeedback'].get('blockReason'):
                    response_text_for_discord += f" (Blocked: {llm_data_first_turn['promptFeedback']['blockReason']})"
            
            conversation_histories[user_id].append({"role": "model", "parts": [{"text": response_text_for_discord}]})


    except requests.exceptions.RequestException as e:
        print(f"General Request Error: {e}")
        response_text_for_discord = f"An unexpected connection error occurred. Please check network connectivity or API URLs. Error: {e}"
    except Exception as e:
        print(f"An unexpected error occurred in bot logic: {e}")
        response_text_for_discord = f"An unexpected error occurred while processing your request. My apologies. Error: {e}"

    # Split and send the response to Discord
    for chunk in split_message(response_text_for_discord):
        await message.channel.send(chunk)

if __name__ == '__main__':
    if not DISCORD_BOT_TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
    elif not TWELVE_DATA_API_KEY:
        print("Error: TWELVE_DATA_API_KEY environment variable not set.")
    elif not GOOGLE_API_KEY:
        print("Error: GOOGLE_API_KEY environment variable not set.")
    else:
        client.run(DISCORD_BOT_TOKEN)
