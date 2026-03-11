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
    redirect_uri = url_for('callback', _external=True)
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

@app.route('/')
def index():
    if 'access_token' not in session:
        return render_template('index.html', logged_in=False)
    
    activities = fetch_activities(session['access_token'])
    if not isinstance(activities, list):
        activities = []
    
    # Get total activity count to calculate trophy rank
    stats = fetch_athlete_stats(session['access_token'], session['athlete']['id'])
    total_activities = 0
    if stats:
        total_activities = (
            stats.get('all_ride_totals', {}).get('count', 0) +
            stats.get('all_run_totals', {}).get('count', 0) +
            stats.get('all_swim_totals', {}).get('count', 0)
        )
    
    # Assign ranks (newest = total_activities, descending)
    current_rank = total_activities
    for activity in activities:
        activity['trophy_rank'] = current_rank
        current_rank -= 1

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
        
    # Get trophy rank from query param or default to ?
    trophy_rank = request.args.get('rank')
    
    # Decode polyline for map if available
    map_polyline = activity.get('map', {}).get('polyline') or activity.get('map', {}).get('summary_polyline')
    coordinates = []
    if map_polyline:
        coordinates = polyline.decode(map_polyline)
    
    return render_template('detail.html', 
                         activity=activity, 
                         coordinates=coordinates,
                         trophy_rank=trophy_rank,
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

if __name__ == '__main__':
    app.run(debug=True, port=5000)
