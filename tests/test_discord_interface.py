import unittest

from orion.interfaces.discord import DiscordAccessPolicy, DiscordMessageHandler


class DiscordInterfaceTests(unittest.TestCase):
    def test_policy_parses_and_restricts_user_ids(self):
        policy = DiscordAccessPolicy.from_values(["123", 456, "bad"])
        self.assertTrue(policy.permits(123))
        self.assertTrue(policy.permits(456))
        self.assertFalse(policy.permits(999))

    def test_server_context_restricts_channels_and_roles(self):
        policy = DiscordAccessPolicy.from_values([123], [1001], [2002])
        self.assertTrue(policy.permits_server_context(1001, [2002, 3003]))
        self.assertFalse(policy.permits_server_context(9999, [2002]))
        self.assertFalse(policy.permits_server_context(1001, [3003]))

    def test_empty_channel_and_role_lists_do_not_add_restrictions(self):
        policy = DiscordAccessPolicy.from_values([123])
        self.assertTrue(policy.permits_server_context(9999, []))

    def test_authorized_message_uses_orion_brain(self):
        handler = DiscordMessageHandler(lambda prompt: f"Orion: {prompt}", DiscordAccessPolicy.from_values([123]))
        self.assertEqual(handler.handle(123, "hello"), "Orion: hello")

    def test_authorized_message_can_use_shared_request_router(self):
        routed = []
        handler = DiscordMessageHandler(
            lambda prompt: routed.append(prompt) or "Live weather",
            DiscordAccessPolicy.from_values([123]),
        )
        self.assertEqual(handler.handle(123, "what is the weather?"), "Live weather")
        self.assertEqual(routed, ["what is the weather?"])

    def test_unauthorized_message_never_reaches_brain(self):
        called = []
        handler = DiscordMessageHandler(lambda prompt: called.append(prompt), DiscordAccessPolicy.from_values([123]))
        response = handler.handle(999, "run tests")
        self.assertIn("not authorized", response)
        self.assertEqual(called, [])

class DiscordChannelWideAccessTests(unittest.TestCase):
    def test_channel_members_can_converse_but_are_not_owners(self):
        policy = DiscordAccessPolicy.from_values([123], [1001], (), True)
        self.assertTrue(policy.permits_conversation(999, is_dm=False, channel_id=1001))
        self.assertFalse(policy.is_owner(999))
        self.assertFalse(policy.permits_conversation(999, is_dm=True))

    def test_non_owner_sensitive_request_is_blocked(self):
        called = []
        policy = DiscordAccessPolicy.from_values([123], [1001], (), True)
        handler = DiscordMessageHandler(lambda prompt: called.append(prompt) or "done", policy)
        response = handler.handle(999, "git push the latest commit")
        self.assertIn("Only an Orion owner", response)
        self.assertEqual(called, [])

    def test_non_owner_information_request_reaches_router(self):
        called = []
        policy = DiscordAccessPolicy.from_values([123], [1001], (), True)
        handler = DiscordMessageHandler(lambda prompt: called.append(prompt) or "88°F", policy)
        self.assertEqual(handler.handle(999, "ask what is the weather?"), "88°F")
        self.assertEqual(called, ["what is the weather?"])
