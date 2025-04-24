import datetime
import json
import logging
import os
import pytz
import requests
from typing import Dict, List, Optional
from flask import Flask, jsonify

# Configuration
AREA_CODE = 'EE'  # Estonia
LOW_PRICE_THRESHOLD = 7  # EUR/MWh
DATA_FILE = 'nordpool_data.json'
TIMEZONE = 'Europe/Tallinn'
NORDPOOL_API = 'https://dataportal-api.nordpoolgroup.com/api/DayAheadPrices'

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


def fetch_daily_prices() -> Optional[Dict]:
    """Fetch today's electricity prices from Nord Pool"""
    try:
        today = datetime.datetime.now(pytz.timezone(TIMEZONE)).date()
        params = {
            "date": today.isoformat(),
            "market": 'DayAhead',
            "deliveryArea": AREA_CODE,
            "currency": "EUR"
        }
        response = requests.get(NORDPOOL_API, params=params, timeout=10)
        if response.status_code != 200:
            logging.error(f"Error fetching prices: {response.text} {response.status_code}")
            return None

        data = json.loads(response.text)
        if not data or 'multiAreaEntries' not in data:
            logging.error(f"Not expected data from nordpool")
            return None

        # Extract prices for our area
        prices = []
        for entry in data['multiAreaEntries']:
            dt = datetime.datetime.fromisoformat(entry['deliveryStart'].replace('Z',''))
            hour_start = dt.replace(tzinfo=datetime.timezone.utc).astimezone(tz=pytz.timezone(TIMEZONE))
            price = entry['entryPerArea'][AREA_CODE]
            prices.append((hour_start, price))

        logging.info(f"Successfully fetched prices for {today}")
        return {
            'date': today.isoformat(),
            'prices': [{'datetime': dt.isoformat(), 'price': price} for dt, price in prices]
        }

    except Exception as e:
        logging.error(f"Error fetching prices: {str(e)}")
        return None


def load_cached_data() -> Optional[Dict]:
    """Load cached price data from file."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading cached data: {e}")
    return None


def save_data_to_cache(data: Dict) -> None:
    """Save price data to cache file."""
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.error(f"Error saving cached data: {e}")


def should_fetch_new_data() -> bool:
    """Check if we need to fetch new data (after midnight)."""
    cached_data = load_cached_data()
    if not cached_data:
        return True

    try:
        cached_date = datetime.date.fromisoformat(cached_data['date'])
        today = datetime.datetime.now(pytz.timezone(TIMEZONE)).date()
        return cached_date < today
    except Exception as e:
        logging.error(f"Error checking cache date: {e}")
        return True


def calculate_financial_impact(period: Dict, prices: List[Dict]) -> float:
    """Calculate the net financial impact of turning off during a specific period.
    Positive value = profit from turning off (avoided losses)
    Negative value = loss from turning off (missed profits)"""
    net_impact = 0.0

    for price in prices:
        try:
            price_time = datetime.datetime.fromisoformat(price['datetime']).astimezone(pytz.timezone(TIMEZONE))
            price_value = float(price['price'])

            if period['start'] <= price_time < period['end']:
                # Financial impact is (LOW_PRICE_THRESHOLD - price)
                net_impact += (LOW_PRICE_THRESHOLD - price_value)
        except Exception as e:
            logging.error(f"Error calculating financial impact: {e}")

    return net_impact


def find_optimal_off_period(data: Dict) -> Optional[Dict]:
    """Find the optimal off period considering financial impact."""
    prices = data.get('prices', [])
    if not prices:
        return None

    # Consider ALL possible periods (not just below threshold)
    # We need to evaluate every possible continuous period
    all_periods = []

    # Generate all possible continuous periods (1-24 hours)
    for start_idx in range(len(prices)):
        for end_idx in range(start_idx + 1, len(prices) + 1):
            period_prices = prices[start_idx:end_idx]
            start_time = datetime.datetime.fromisoformat(period_prices[0]['datetime']).astimezone(
                pytz.timezone(TIMEZONE))
            end_time = datetime.datetime.fromisoformat(period_prices[-1]['datetime']).astimezone(
                pytz.timezone(TIMEZONE)) + datetime.timedelta(hours=1)

            period = {
                'start': start_time,
                'end': end_time,
                'duration': end_idx - start_idx,
                'average_price': sum(float(p['price']) for p in period_prices) / (end_idx - start_idx)
            }
            period['financial_impact'] = calculate_financial_impact(period, prices)
            all_periods.append(period)

    if not all_periods:
        return None

    # Find the period with maximum financial impact
    best_period = max(
        all_periods,
        key=lambda x: x['financial_impact']
    )

    # Only return if there's actual benefit
    return best_period if best_period['financial_impact'] > 0 else None

def determine_daily_schedule(data: Dict) -> Optional[Dict]:
    """Determine the optimal off/on schedule for the day."""
    if not data or 'prices' not in data:
        return None

    # Find the best off period
    off_period = find_optimal_off_period(data)
    if not off_period:
        return None

    # The on time is immediately after the off period ends
    on_time = off_period['end']

    return {
        'off_time': off_period['start'].isoformat(),
        'on_time': on_time.isoformat(),
        'financial_impact': off_period['financial_impact']
    }


def check_for_scheduled_actions() -> Optional[Dict]:
    """Check if current time matches any scheduled actions or needs retry."""
    cached_data = load_cached_data()
    if not cached_data or 'schedule' not in cached_data:
        return None

    now = datetime.datetime.now(pytz.timezone(TIMEZONE))
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    current_state = cached_data.get('current_state', 'on')

    try:
        schedule = cached_data['schedule']
        off_time = datetime.datetime.fromisoformat(schedule['off_time']).astimezone(pytz.timezone(TIMEZONE))
        on_time = datetime.datetime.fromisoformat(schedule['on_time']).astimezone(pytz.timezone(TIMEZONE))

        # Case 1: We're exactly at the off time
        if current_hour == off_time:
            return {'action': 'off'}

        # Case 2: We're exactly at the on time
        if current_hour == on_time:
            return {'action': 'on'}

        # Case 3: We're past off time but still in 'on' state (missed trigger)
        if off_time < current_hour < on_time and current_state == 'on':
            logging.info(f"Retrying missed off trigger (current: {current_hour}, scheduled off: {off_time})")
            return {'action': 'off', 'retry': True}

        # Case 4: We're past on time but still in 'off' state (missed trigger)
        if (current_hour > on_time and
                current_state == 'off'):
            logging.info(f"Retrying missed on trigger (current: {current_hour}, scheduled on: {on_time})")
            return {'action': 'on', 'retry': True}

    except Exception as e:
        logging.error(f"Error checking scheduled actions: {e}")

    return None

def create_flask_app():
    """Create a Flask web application for the price monitor."""
    app = Flask(__name__)

    @app.route('/check-prices', methods=['GET'])
    def check_prices():
        """Endpoint to check prices and trigger actions."""
        try:
            # Check if we need to fetch new data and calculate schedule
            if should_fetch_new_data():
                logging.info("Fetching new price data...")
                new_data = fetch_daily_prices()
                if new_data:
                    # Calculate daily schedule
                    schedule = determine_daily_schedule(new_data)
                    if schedule:
                        new_data['schedule'] = schedule
                        new_data['last_schedule_update'] = datetime.datetime.now(pytz.timezone(TIMEZONE)).isoformat()
                        new_data['trigger_attempts'] = {}  # Reset attempts

                    # Initialize state if not exists
                    cached_data = load_cached_data() or {}
                    new_data['current_state'] = cached_data.get('current_state', 'on')
                    save_data_to_cache(new_data)

            # Check if we should trigger any scheduled actions
            cached_data = load_cached_data() or {}
            trigger_action = check_for_scheduled_actions()

            if trigger_action:
                # Track trigger attempts
                if 'trigger_attempts' not in cached_data:
                    cached_data['trigger_attempts'] = {}

                action_key = trigger_action['action']
                if trigger_action.get('retry'):
                    action_key = f"retry_{trigger_action['action']}"

                cached_data['trigger_attempts'][action_key] = cached_data['trigger_attempts'].get(action_key, 0) + 1
                save_data_to_cache(cached_data)

                logging.info(f"Triggering action: {trigger_action}")

                # Update current state in cache
                cached_data['current_state'] = 'off' if trigger_action.get('action') == 'off' else 'on'
                cached_data['last_trigger'] = {
                    'time': datetime.datetime.now(pytz.timezone(TIMEZONE)).isoformat(),
                    'action': trigger_action.get('action'),
                    'retry': trigger_action.get('retry', False)
                }
                save_data_to_cache(cached_data)

                return jsonify({
                    'status': 'success',
                    'action': trigger_action,
                    'message': f"Action triggered: {trigger_action['action']}",
                    'retry': trigger_action.get('retry', False)
                })

            return jsonify({
                'status': 'success',
                'action': None,
                'message': "No scheduled actions to trigger"
            })

        except Exception as e:
            logging.error(f"Error in check-prices endpoint: {e}")
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500

    @app.route('/status', methods=['GET'])
    def get_status():
        """Endpoint to get current status."""
        cached_data = load_cached_data() or {}
        return jsonify({
            'status': 'success',
            'data': cached_data
        })

    return app

app = create_flask_app()
if __name__ == "__main__":
    # For running locally
    app.run(host='0.0.0.0', port = 80)

