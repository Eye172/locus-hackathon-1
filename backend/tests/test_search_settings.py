import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api_search import router


class SearchSettingsTest(unittest.TestCase):
    def test_save_validation_and_reset_in_isolated_file(self):
        app = FastAPI()
        app.include_router(router)
        with tempfile.TemporaryDirectory() as folder, patch('app.pipeline.search_plan._path', return_value=Path(folder) / 'plan.json'):
            client = TestClient(app)
            default = client.get('/api/search-plan').json()['plan']
            changed = {**default, 'posts_per_intent': 7}
            response = client.put('/api/search-plan', json=changed)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(client.get('/api/search-plan').json()['plan']['posts_per_intent'], 7)
            invalid = {**changed, 'intents': [changed['intents'][0], changed['intents'][0]]}
            self.assertEqual(client.put('/api/search-plan', json=invalid).status_code, 400)
            self.assertEqual(client.get('/api/search-plan').json()['plan']['posts_per_intent'], 7)
            self.assertEqual(client.put('/api/search-plan', json={'intents': 'invalid'}).status_code, 422)
            self.assertEqual(client.post('/api/search-plan/reset').status_code, 200)
            self.assertEqual(client.get('/api/search-plan').json()['plan'], default)
