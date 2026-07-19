import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orion.services.connect import ConnectService, ConnectBriefingProvider, ConnectStatus, DiscordWebhookClient, GmailClient, MailSummary
from orion.services.email import EmailAccount, EmailProvider, EmailService


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


class FakeMailAdapter:
    configured = True
    connected = True

    def __init__(self, key):
        self.key = key
        self.display_name = "Gmail" if key == "gmail" else "Outlook / Microsoft 365"

    def account(self):
        return EmailAccount(self.key, self.key, f"{self.key}@example.test")

    def unread_count(self): return 1


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

    def test_connect_center_reports_both_provider_neutral_mail_adapters(self):
        email = EmailService((
            EmailProvider("gmail", "Gmail", FakeMailAdapter("gmail"), True),
            EmailProvider("microsoft", "Outlook / Microsoft 365", FakeMailAdapter("microsoft"), True),
        ))
        statuses = ConnectService(email, FakeDiscord()).statuses()
        self.assertEqual([item.key for item in statuses], ["gmail", "microsoft", "discord"])
        self.assertTrue(all(item.healthy for item in statuses))
        self.assertIn("read-only", statuses[0].detail)

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

class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeVault:
    def __init__(self, values):
        self.store = values


class FakeDiagnostics:
    bot_name = 'Orion#9675'


class FakeDiscordRuntime:
    diagnostics = FakeDiagnostics()


class DiscordBotConnectStatusTests(unittest.TestCase):
    def test_status_uses_two_way_discord_bot_configuration(self):
        config = FakeConfig({
            'connect.discord_bot.enabled': True,
            'connect.discord_bot.owner_user_ids': ['586250300705210390'],
            'connect.discord_bot.allowed_channel_ids': ['1526972631646081064'],
        })
        service = ConnectService(
            FakeGmail(),
            DiscordWebhookClient(''),
            vault=FakeVault({'discord_bot': 'secret'}),
            config_manager=config,
            discord_bot_runtime=lambda: FakeDiscordRuntime(),
        )

        discord = next(item for item in service.statuses() if item.key == 'discord')
        self.assertTrue(discord.configured)
        self.assertTrue(discord.healthy)
        self.assertIn('Orion#9675', discord.detail)

    def test_configured_offline_bot_is_not_reported_as_missing(self):
        config = FakeConfig({'connect.discord_bot.enabled': True})
        service = ConnectService(
            FakeGmail(),
            DiscordWebhookClient(''),
            vault=FakeVault({'discord_bot': 'secret'}),
            config_manager=config,
            discord_bot_runtime=lambda: None,
        )

        discord = next(item for item in service.statuses() if item.key == 'discord')
        self.assertTrue(discord.configured)
        self.assertFalse(discord.healthy)
        self.assertIn('restart Orion', discord.detail)


if __name__ == '__main__':
    unittest.main()
