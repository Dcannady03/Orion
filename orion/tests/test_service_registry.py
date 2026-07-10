"""Tests for Orion's central service registry."""

import unittest

from orion.services.registry import ServiceRegistry


class ServiceRegistryTests(unittest.TestCase):
    def test_register_get_find_and_remove(self):
        registry = ServiceRegistry()
        service = object()

        self.assertIs(registry.register("Workspace", service), service)
        self.assertIs(registry.get("workspace"), service)
        self.assertIs(registry["WORKSPACE"], service)
        self.assertTrue(registry.contains("workspace"))
        self.assertIn("workspace", registry)
        self.assertIsNone(registry.find("missing"))
        self.assertIs(registry.remove("workspace"), service)
        self.assertEqual(len(registry), 0)

    def test_duplicate_registration_requires_replace(self):
        registry = ServiceRegistry()
        registry.register("memory", object())
        with self.assertRaises(KeyError):
            registry.register("memory", object())

        replacement = object()
        registry.register("memory", replacement, replace=True)
        self.assertIs(registry.get("memory"), replacement)

    def test_type_check_and_detached_snapshot(self):
        registry = ServiceRegistry()
        registry.register("count", 3)
        self.assertEqual(registry.get("count", int), 3)
        with self.assertRaises(TypeError):
            registry.get("count", str)

        snapshot = registry.snapshot()
        snapshot["count"] = 4
        self.assertEqual(registry.get("count"), 3)

    def test_rejects_invalid_registration(self):
        registry = ServiceRegistry()
        with self.assertRaises(ValueError):
            registry.register("", object())
        with self.assertRaises(ValueError):
            registry.register("bad/name", object())
        with self.assertRaises(ValueError):
            registry.register("valid", None)
        with self.assertRaises(KeyError):
            registry.get("missing")


if __name__ == "__main__":
    unittest.main()
