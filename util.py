import json
import logging
import random
import string
import time
import urllib

import requests
from flask import session

import settings
from settings import *

def chunk_list(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

def generate_random_string(length):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


def query_artist_spotify(artist=None):
    """Make request for data on `artist`."""

    if artist is None:
        return artist

    payload = {'q': artist, 'type': 'artist', 'limit': '50'}
    headers = {
        'Authorization': f"Bearer {session.get('spotify_access_token')}",
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }

    return requests.get(SPOTIFY_SEARCH_ENDPOINT, params=payload, headers=headers)


def get_paginated_track_list(playlist: dict, playlist_id: str, cursor: str = None):
    # get the playlist from amazon API (including tracks)
    url = f"{AMAZON_BASE_ENDPOINT}/playlists/{playlist_id}/tracks"
    if cursor:
        url += f"?cursor={cursor}"
    headers = {
        "Authorization": f"Bearer {AMAZON_TOKEN}",
        "x-api-key": AMAZON_X_API_KEY,
        "Content-Type": "application/json",
    }

    response = requests.get(url, headers=headers)
    edges = json.loads(response.text)['data']['playlist']['tracks']['edges']
    if not playlist:
        playlist = json.loads(response.text)['data']['playlist']
    else:
        playlist['tracks']['edges'] += edges

    if len(edges) == 50:
        # pagination
        cursor = edges[len(edges) - 1]['cursor']
        # avoid rate-limiting
        time.sleep(0.5)
        playlist = get_paginated_track_list(playlist=playlist, playlist_id=playlist_id, cursor=cursor)

    return playlist

# Example place to add this might be at the end of get_paginated_track_list or right before calling add_tracks_to_spotify_playlist
logging.debug(f"Total tracks prepared for migration: {len(settings.TRACK_TRANSLATION)}")

def create_spotify_playlist(name: str):
    """Creates a spotify playlist with the given name if it doesn't exist yet."""
    # get current user's id
    headers = {
        'Authorization': f"Bearer {session.get('spotify_access_token')}",
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }
    resp = requests.get(SPOTIFY_ME_ENDPOINT, headers=headers)
    user_id = json.loads(resp.text)['id']
    
    endpoint = f"{SPOTIFY_BASE_ENDPOINT}/users/{user_id}/playlists"
    headers = {
        "Authorization": f"Bearer {session['spotify_access_token']}",
        "Content-Type": "application/json",
    }

    data = {
        "name": name,
        "description": "Playlist migrated using Amazify",
        "public": False
    }

    response = requests.post(endpoint, headers=headers, json=data)
    if response.status_code != 201:
        logging.error("could not create new playlist in spotify")
        logging.error(response.text)

    payload = json.loads(response.text)
    playlist_id = payload['id']
    settings.DESTINATION_PLAYLIST_URL = payload['external_urls']['spotify']
    session['destination'] = settings.DESTINATION_PLAYLIST_URL
    return playlist_id

def add_tracks_to_spotify_playlist(spotify_access_token: str, playlist_id: str):
    logging.debug(f"Preparing to migrate {len(settings.TRACK_TRANSLATION)} tracks.")
    track_chunks = list(chunk_list(settings.TRACK_TRANSLATION, 100))
    total_tracks = len(settings.TRACK_TRANSLATION)
    logging.debug(f"Total tracks to migrate: {total_tracks}. Split into {len(track_chunks)} chunks.")
    
    count = 0  # Initialize the count variable before the loop

    for chunk_index, chunk in enumerate(track_chunks, start=1):
        track_ids = []  # Initialize track_ids here, at the start of each chunk processing
        logging.debug(f"Processing chunk {chunk_index}/{len(track_chunks)} with {len(chunk)} tracks.")

        for track in chunk:
            # Update progress
            count += 1
            settings.PROGRESS = (count / total_tracks) * 100
            logging.debug(f"Progress: {settings.PROGRESS:.2f}%. Processing track {count}/{total_tracks}: {track['artist']} - {track['title']}")

            # Get track id
            url = f'{SPOTIFY_BASE_ENDPOINT}/search'
            params = {'q': f'{track["title"]} - {track["artist"]}', 'type': 'track', 'limit': 1}
            headers = {'Authorization': f'Bearer {spotify_access_token}'}
            response = requests.get(url, params=params, headers=headers)
            results = json.loads(response.text)['tracks']['items']
            
            if results:
                spotify_track_id = results[0]['id']
                track_ids.append(f'spotify:track:{spotify_track_id}')
                track['translation'] = {'artist': results[0]["artists"][0]["name"], 'title': results[0]["name"]}
                logging.debug(f"Found Spotify track ID: {spotify_track_id} for {track['artist']} - {track['title']}")
            else:
                logging.error(f"Could not find the track on Spotify, skipping... {track['artist']} - {track['title']}")
                settings.FAILED_TRACKS += f"<li>{track['artist']} - {track['title']}</li>"
        
        # Add chunk of tracks to playlist
        if track_ids:
            logging.debug(f"Adding {len(track_ids)} tracks to Spotify playlist (Chunk {chunk_index}/{len(track_chunks)})")
            url = f'{SPOTIFY_BASE_ENDPOINT}/playlists/{playlist_id}/tracks'
            data = {'uris': track_ids}
            response = requests.post(url, headers=headers, json=data)
            if response.status_code not in [200, 201]:
                logging.error(f"Couldn't add tracks to playlist, response code {response.status_code}")
                logging.error(response.text)
            else:
                logging.debug(f"Successfully added {len(track_ids)} tracks to playlist.")
        else:
            logging.warning(f"No valid Spotify track IDs found in chunk {chunk_index}. Skipping this chunk.")
        
        # Delay next request to avoid hitting rate limits
        logging.debug("Waiting 10 seconds before processing the next chunk to respect rate limits.")
        time.sleep(10)