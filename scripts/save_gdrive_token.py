import os
from google_auth_oauthlib.flow import InstalledAppFlow

# Use the client_secret file already in the repo
client_secret_path = 'client_secret_272953376689-1p3f658rsu9qmir1v3ivhdag6nmtnjs7.apps.googleusercontent.com.json'
scopes = ['https://www.googleapis.com/auth/drive.readonly']

flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, scopes=scopes)
# Authorization code provided by the user
auth_code = '4/1AXlqoi49zTJYkLTzW5EtaD7rEx_SY0UMbMJU6j1_CMxR2rZpseMzEzv4wcY'
flow.fetch_token(code=auth_code)
creds = flow.credentials
# Save credentials JSON for later runs
with open('gdrive_token.json','w') as f:
    f.write(creds.to_json())
print('✅ Credentials saved to gdrive_token.json')

