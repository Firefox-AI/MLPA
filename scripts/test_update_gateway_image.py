"""Checks for the narrow Compose edit used by gateway promotion."""

import unittest

from update_gateway_image import IMAGE_REPOSITORY, update_image


class GatewayImageTests(unittest.TestCase):
    def test_only_litellm_changes(self):
        before = (
            "# Preserve this comment\nservices:\n"
            "  postgres:\n    image: postgres:18\n"
            "  litellm:\n    image: upstream:old\n    platform: linux/amd64\n"
        )
        image = IMAGE_REPOSITORY + "@sha256:" + "a" * 64
        self.assertEqual(
            update_image(before, image), before.replace("upstream:old", image)
        )
        self.assertEqual(
            update_image(update_image(before, image), image),
            update_image(before, image),
        )

    def test_rejects_mutable_or_foreign_images(self):
        for image in (IMAGE_REPOSITORY + ":latest", "other/image@sha256:" + "a" * 64):
            with self.subTest(image=image), self.assertRaises(ValueError):
                update_image("  litellm:\n    image: old\n", image)

    def test_fails_if_compose_layout_changes(self):
        image = IMAGE_REPOSITORY + "@sha256:" + "a" * 64
        for compose in ("services: {}\n", "  litellm:\n    image: old\n" * 2):
            with self.subTest(compose=compose), self.assertRaises(ValueError):
                update_image(compose, image)
