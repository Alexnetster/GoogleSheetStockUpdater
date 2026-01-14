import os
import json
from google.oauth2.service_account import Credentials

def get_google_credentials(env_var_name='GOOGLE_CREDENTIALS_JSON', file_path='credentials.json'):
    """
    Load Google Service Account Credentials.
    Priority:
    1. Environment Variable (JSON string)
    2. Local File (credentials.json)
    """
    env_creds = os.getenv(env_var_name)
    
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/calendar'
    ]

    if env_creds:
        try:
            info = json.loads(env_creds)
            return Credentials.from_service_account_info(info, scopes=scopes)
        except json.JSONDecodeError:
            print(f"Error: Environment variable {env_var_name} contains invalid JSON.")
            raise
    else:
        if os.path.exists(file_path):
            return Credentials.from_service_account_file(file_path, scopes=scopes)
        else:
            raise FileNotFoundError(f"Credentials not found. Set {env_var_name} or provide {file_path}.")
