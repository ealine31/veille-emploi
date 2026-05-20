#!/usr/bin/env python3
"""
Script à exécuter UNE SEULE FOIS sur ta machine pour obtenir le refresh token Gmail.

Prérequis :
  pip install google-auth-oauthlib

Étapes :
  1. Télécharge ce fichier sur ta machine
  2. Lance : python3 get_token.py
  3. Un navigateur s'ouvre → connecte-toi avec emilie.aline@gmail.com → Autoriser
  4. Copie les 3 valeurs affichées dans les secrets GitHub
"""

from google_auth_oauthlib.flow import InstalledAppFlow
import json, sys

print("=== Obtention du refresh token Gmail ===\n")

client_id     = input("Colle ton GMAIL_CLIENT_ID     : ").strip()
client_secret = input("Colle ton GMAIL_CLIENT_SECRET : ").strip()

client_config = {
    "installed": {
        "client_id":     client_id,
        "client_secret": client_secret,
        "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
        "token_uri":     "https://oauth2.googleapis.com/token",
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
    }
}

flow = InstalledAppFlow.from_client_config(
    client_config,
    scopes=["https://www.googleapis.com/auth/gmail.compose"],
)

print("\nUn navigateur va s'ouvrir. Connecte-toi avec emilie.aline@gmail.com et clique Autoriser.\n")
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

print("\n" + "="*55)
print("✅ Copie ces 3 valeurs dans les secrets GitHub :\n")
print(f"  GMAIL_CLIENT_ID     = {client_id}")
print(f"  GMAIL_CLIENT_SECRET = {client_secret}")
print(f"  GMAIL_REFRESH_TOKEN = {creds.refresh_token}")
print("="*55)
