import argparse
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

parser = argparse.ArgumentParser(description='Authorize Inbox Zero with your own Google desktop OAuth client.')
parser.add_argument('client_file', type=Path)
parser.add_argument('--output', type=Path, default=Path.home() / '.config/inbox-zero/gmail.json')
args = parser.parse_args()
os.umask(0o077)
flow = InstalledAppFlow.from_client_secrets_file(str(args.client_file), scopes=['https://www.googleapis.com/auth/gmail.modify'])
credentials = flow.run_local_server(port=0, open_browser=True, authorization_prompt_message='Open this Google consent page: {url}', success_message='Inbox Zero is authorized. You can close this page.')
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(credentials.to_json())
args.output.chmod(0o600)
print(f'Gmail authorization saved to {args.output}. No credential values were printed.')
