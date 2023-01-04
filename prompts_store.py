#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pickle, logging, random
import os, os.path
import base64
import time
import subprocess
from collections import defaultdict

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from apiclient.discovery import build
from oauth2client.service_account import ServiceAccountCredentials

from bot import DummyServer

SCOPES = ['https://www.googleapis.com/auth/drive']

CLIENT_CONFIG = {"installed": {
    "client_id": os.environ.get('GOOGLE_API_CLIENT_ID'),
    "client_secret": os.environ.get('GOOGLE_API_CLIENT_SECRET'),
    "auth_uri":"https://accounts.google.com/o/oauth2/auth",
    "token_uri":"https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs",
    "redirect_uris":["urn:ietf:wg:oauth:2.0:oob","http://localhost"]
}}

class PromptsStore:
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    base_folder_id = os.environ.get("GOOGLE_DRIVE_BASE_FOLDER_ID")
    prompts = {}

        
    def _load_bot_config(self):
        def _row_to_key_val(row):
            if len(row) == 4:
                return row[0], row[1], row[3]
            elif len(row) > 4:
                return row[0], row[1], row[3:]
            else:
                raise ValueError("Config is missing a value!")


        self.config = defaultdict(str)
        sheet = self.sheets_service.spreadsheets()

        configs = sheet.values().get(spreadsheetId=self.spreadsheet_id, range=f"{os.environ.get('CONFIG_SHEET_NAME')}!A2:Z", majorDimension='ROWS').execute().get('values', [])
        for conf in configs:
            key, subkey, val = _row_to_key_val(conf)
            if key in self.config.keys():
                if type(self.config[key]) is dict:
                    self.config[key][subkey] = val
                else:
                    oldkey, oldval = _row_to_key_val(self.config[key])
                    self.config[key] = {oldkey: oldval, subkey: val}
            elif subkey:
                self.config[key] = {subkey: val}
            else:
                self.config[key] = val



    def _load_text_prompts(self):
        sheet = self.sheets_service.spreadsheets()

        for lang in ['ua']:
            self.prompts[lang] = {}
            result = sheet.values().get(spreadsheetId=self.spreadsheet_id, range=f"База-{lang}!B1:E", majorDimension='COLUMNS').execute()
            prompts = result.get('values', [])
            for col in prompts:
                self.prompts[lang][col[0]] = col[1:]

    def _load_image_prompts(self):
        self.folders = {'all': []}
        folders = self.drive_service.list(q=f"\'{self.base_folder_id}\' in parents and mimeType = \'application/vnd.google-apps.folder\'", fields="files(id, name)").execute()
        for folder in folders.get('files', []):
            folder['name'] = folder['name'].lower()
            if folder['name'] != 'all' and folder['id'] != self.base_folder_id:
                images =  self.drive_service.list(pageSize=1000, q=f"\'{folder['id']}\' in parents and mimeType contains \'image/\'", fields="files(id, name, webContentLink)").execute()['files']
                self.folders[folder['name']] = images
                self.folders['all'] += images
            


    def __init__(self):
        """Connect to google sheet and loads the prompt"""
        creds = None
        logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)
        # The file token.pickle stores the user's access and refresh tokens, and is
        # created automatically when the authorization flow completes for the first
        # time.
        if os.path.exists('token.pickle.b64'):
            with open('token.pickle.b64', 'rb') as token_file:
                creds = pickle.loads(base64.b64decode(token_file.read()))
                
        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                DummyServer.kill()
                flow = InstalledAppFlow.from_client_config(CLIENT_CONFIG, SCOPES)
                time.sleep(5)
                host = os.environ.get["HOSTNAME"]
                port = 80
                creds = flow.run_local_server(host=host, port=port)
                time.sleep(5)
                DummyServer.init()

            with open('token.pickle.b64', 'wb') as token_file:
                token_file.write(base64.b64encode(pickle.dumps(creds)))

        self.drive_service = build('drive', 'v3', credentials=creds).files()
        self.sheets_service = build('sheets', 'v4', credentials=creds)

        self._load_bot_config()
        self._load_text_prompts()
        self._load_image_prompts()
 
    def random_text(self, lang):
        return {k: random.choice(v) for k, v in self.prompts[lang].items()}

    def random_image(self, cat):
        if cat not in self.folders.keys():
            cat = 'all'
        num = random.randint(0, len(self.folders[cat]) - 1)
        image_prompt = self.folders[cat][num]
        image_prompt['cat'] = cat
        image_prompt['num'] = num

        return image_prompt

    def get_stats(self):
        stats = {}
        for lang in self.prompts:
            for header in self.prompts[lang]:
                stats[f"{lang}-{header}"] = len(self.prompts[lang][header])
        for folder in self.folders:
            stats[f'image-{folder}'] = len(self.folders[folder])
        return {**self.config, **stats}
