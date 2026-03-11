import os
import requests
import polyline
from flask import Flask, render_template, redirect, url_for, session, request
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev_secret_key')

# Strava API Configuration
STRAVA_CLIENT_ID = os.getenv('STRAVA_CLIENT_ID')
STRAVA_CLIENT_SECRET = os.getenv('STRAVA_CLIENT_SECRET')
STRAVA_AUTH_URL = 'https://www.strava.com/oauth/authorize'
STRAVA_TOKEN_URL = 'https://www.strava.com/oauth/token'
STRAVA_API_URL = 'https://www.strava.com/api/v3'

def get_strava_auth_url():
    """Generates the Strava OAuth authorization URL."""
    # Force domain to likedge.top and use current port
    # Strava requires the domain to match the Authorization Callback Domain
    port = request.host.split(':')[-1] if ':' in request.host else '80'
    redirect_uri = f"http://likedge.top:{port}{url_for('callback')}"
    
    return (
        f"{STRAVA_AUTH_URL}?"
        f"client_id={STRAVA_CLIENT_ID}&"
        f"response_type=code&"
        f"redirect_uri={redirect_uri}&"
        f"approval_prompt=force&"
        f"scope=read,activity:read_all"
    )

def exchange_code_for_token(code):
    """Exchanges the authorization code for an access token."""
    response = requests.post(STRAVA_TOKEN_URL, data={
        'client_id': STRAVA_CLIENT_ID,
        'client_secret': STRAVA_CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code'
    })
    if response.status_code == 200:
        return response.json()
    return None

def fetch_activities(access_token):
    """Fetches the authenticated user's activities."""
    headers = {'Authorization': f'Bearer {access_token}'}
    # Get last 30 activities
    response = requests.get(f"{STRAVA_API_URL}/athlete/activities?per_page=30", headers=headers)
    if response.status_code == 200:
        return response.json()
    return []

def fetch_activity_detail(access_token, activity_id):
    """Fetches detailed activity data."""
    headers = {'Authorization': f'Bearer {access_token}'}
    response = requests.get(f"{STRAVA_API_URL}/activities/{activity_id}", headers=headers)
    if response.status_code == 200:
        return response.json()
    return None

def fetch_athlete_stats(access_token, athlete_id):
    """Fetches the authenticated user's stats."""
    headers = {'Authorization': f'Bearer {access_token}'}
    response = requests.get(f"{STRAVA_API_URL}/athletes/{athlete_id}/stats", headers=headers)
    if response.status_code == 200:
        return response.json()
    return None

# Custom filters for template
@app.template_filter('format_distance')
def format_distance(meters):
    """Converts meters to kilometers."""
    return f"{meters / 1000:.2f} km"

@app.template_filter('format_duration')
def format_duration(seconds):
    """Converts seconds to H:MM:SS format."""
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

@app.template_filter('format_date')
def format_date(date_str):
    """Formats ISO date string to readable format."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%b %d, %Y")
    except:
        return date_str

@app.template_filter('format_pace')
def format_pace(speed_mps):
    """Converts speed (m/s) to pace (min/km)."""
    if speed_mps <= 0:
        return "0:00"
    minutes_per_km = 16.666666666667 / speed_mps
    m = int(minutes_per_km)
    s = int((minutes_per_km - m) * 60)
    return f"{m}:{s:02d} /km"

@app.template_filter('format_speed')
def format_speed(speed_mps):
    """Converts speed (m/s) to km/h."""
    kmh = speed_mps * 3.6
    return f"{kmh:.1f} km/h"

@app.template_filter('format_month')
def format_month(date_str):
    """Formats ISO date string to Month Year."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%B %Y")
    except:
        return date_str

def calculate_predicted_calories(activity, athlete_weight=70):
    """
    Predicts calorie burn based on:
    Calories ≈ Distance (km) * Weight (kg) * Efficiency Coefficient * Speed Adjustment
    """
    distance_km = activity.get('distance', 0) / 1000
    avg_speed_mps = activity.get('average_speed', 0)
    act_type = activity.get('type', '').lower()
    
    # Efficiency Coefficients
    # Running is less efficient (burns more) per km than cycling
    if act_type in ['run', 'virtualrun', 'walk', 'hike']:
        base_coeff = 1.036
    elif act_type in ['ride', 'virtualride', 'ebikeride', 'handcycle']:
        base_coeff = 0.35 # Cycling is efficient
    else:
        base_coeff = 0.5 # Default for other

    # Speed Adjustment (User requested "Speed Coefficient")
    # Slightly increase burn for higher intensity
    # Reference speeds: Run 3m/s, Ride 7m/s
    if act_type in ['run', 'virtualrun']:
        speed_factor = 1 + max(0, (avg_speed_mps - 2.5) * 0.1)
    elif act_type in ['ride', 'virtualride']:
        speed_factor = 1 + max(0, (avg_speed_mps - 5.5) * 0.05)
    else:
        speed_factor = 1.0

    calories = distance_km * athlete_weight * base_coeff * speed_factor
    return int(calories)

@app.route('/')
def index():
    if 'access_token' not in session:
        return render_template('index.html', logged_in=False)
    
    activities = fetch_activities(session['access_token'])
    if not isinstance(activities, list):
        activities = []
    
    # Get athlete weight or default
    athlete = session.get('athlete', {})
    weight = athlete.get('weight', 70)
    if weight is None or weight == 0:
        weight = 70

    # Calculate Calories
    for activity in activities:
        # Check if kilojoules already exists (from power meter), convert to kcal if so
        # 1 kJ = 0.239 kcal. Strava usually gives kJ for rides with power.
        if activity.get('kilojoules'):
            activity['predicted_calories'] = int(activity['kilojoules'] * 0.239 / 0.24) # ~1:1 kJ to kcal for metabolic work roughly
            # Actually Strava kJ is mechanical work. Metabolic efficiency is ~24%. So 1kJ mechanical ~= 1kcal metabolic.
            activity['predicted_calories'] = int(activity['kilojoules']) 
        else:
            activity['predicted_calories'] = calculate_predicted_calories(activity, weight)

    # Group activities by type
    grouped_activities = {
        'ride': [],
        'run': [],
        'other': []
    }
    
    # Group activities by month
    # Format: {'Month Year': [activities]}
    grouped_by_month = {}
    
    for activity in activities:
        # Type grouping
        act_type = activity.get('type', '').lower()
        if act_type in ['ride', 'virtualride', 'ebikeride', 'handcycle']:
            grouped_activities['ride'].append(activity)
        elif act_type in ['run', 'virtualrun', 'walk', 'hike']:
            grouped_activities['run'].append(activity)
        else:
            grouped_activities['other'].append(activity)
            
        # Month grouping
        date_str = activity.get('start_date_local')
        if date_str:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
                month_key = dt.strftime("%B %Y")
                if month_key not in grouped_by_month:
                    grouped_by_month[month_key] = []
                grouped_by_month[month_key].append(activity)
            except:
                pass

    return render_template('index.html', 
                         logged_in=True, 
                         activities=activities, 
                         grouped_activities=grouped_activities,
                         grouped_by_month=grouped_by_month,
                         user=session.get('athlete'))

@app.route('/activity/<int:activity_id>')
def activity_detail(activity_id):
    if 'access_token' not in session:
        return redirect(url_for('index'))
    
    activity = fetch_activity_detail(session['access_token'], activity_id)
    if not activity:
        return "Activity not found", 404
        
    # Get trophy rank from query param (legacy)
    # Now we want predicted calories.
    # Calculate calories for this single activity
    athlete = session.get('athlete', {})
    weight = athlete.get('weight', 70)
    if weight is None or weight == 0:
        weight = 70
        
    if activity.get('kilojoules'):
        predicted_calories = int(activity['kilojoules'])
    else:
        predicted_calories = calculate_predicted_calories(activity, weight)
    
    # Decode polyline for map if available
    map_polyline = activity.get('map', {}).get('polyline') or activity.get('map', {}).get('summary_polyline')
    coordinates = []
    if map_polyline:
        coordinates = polyline.decode(map_polyline)
    
    return render_template('detail.html', 
                         activity=activity, 
                         coordinates=coordinates,
                         predicted_calories=predicted_calories,
                         user=session.get('athlete'))

@app.route('/login')
def login():
    return redirect(get_strava_auth_url())

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return redirect(url_for('index'))
    
    token_data = exchange_code_for_token(code)
    if token_data:
        session['access_token'] = token_data['access_token']
        session['athlete'] = token_data['athlete']
        session['expires_at'] = token_data['expires_at']
        session['refresh_token'] = token_data['refresh_token']
    
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

import sys

# ... (existing imports and code) ...

if __name__ == '__main__':
    port = 5000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print(f"Invalid port number: {sys.argv[1]}. Using default port 5000.")
    
    app.run(debug=True, port=port,host='0.0.0.0')
