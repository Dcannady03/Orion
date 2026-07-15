import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orion.services.connect import ConnectService, ConnectBriefingProvider, ConnectStatus, DiscordWebhookClient, GmailClient, MailSummary


class FakeGmail:
    token_path = Path('token.json')
    credentials_path = Path('credentials.json')
    def profile(self): return {'emailAddress': 'daniel@example.com'}
    def unread_count(self): return 3
    def list_messages(self, query='in:inbox', limit=10):
        return [MailSummary('1', 'Hello', 'Jess', 'Test message', True)]


class FakeDiscord:
    webhook_url = 'https://discord.example/webhook'
    def health(self): return {'name': 'Orion Dev'}
    def send(self, content): self.last = content


class ConnectTests(unittest.TestCase):
    def test_statuses_unify_gmail_and_discord(self):
        service = ConnectService(FakeGmail(), FakeDiscord())
        statuses = service.statuses()
        self.assertEqual([s.key for s in statuses], ['gmail', 'discord'])
        self.assertTrue(all(s.healthy for s in statuses))

    def test_briefing_reports_unread_mail(self):
        item = ConnectBriefingProvider(ConnectService(FakeGmail(), FakeDiscord())).get_briefing()[0]
        self.assertEqual(item.title, 'Connect')
        self.assertIn('3 unread emails', item.message)

    def test_discord_post_uses_json_webhook(self):
        client = DiscordWebhookClient('https://example.test/webhook')
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        with patch('orion.services.connect.urlopen', return_value=response) as opened:
            client.send('Hello Orion')
        request = opened.call_args.args[0]
        self.assertEqual(request.method, 'POST')
        self.assertIn(b'Hello Orion', request.data)
        self.assertEqual(request.get_header('User-agent'), DiscordWebhookClient.USER_AGENT)


    def test_discord_health_uses_user_agent(self):
        client = DiscordWebhookClient('https://example.test/webhook')
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"name":"Orion"}'
        with patch('orion.services.connect.urlopen', return_value=response) as opened:
            info = client.health()
        request = opened.call_args.args[0]
        self.assertEqual(request.method, 'GET')
        self.assertEqual(request.get_header('User-agent'), DiscordWebhookClient.USER_AGENT)
        self.assertEqual(info['name'], 'Orion')

    def test_gmail_requires_connection_when_noninteractive(self):
        with tempfile.TemporaryDirectory() as temp:
            client = GmailClient(str(Path(temp)/'credentials.json'), str(Path(temp)/'token.json'))
            with self.assertRaises((ConnectionError, RuntimeError)):
                client.profile()


if __name__ == '__main__':
    unittest.main()
