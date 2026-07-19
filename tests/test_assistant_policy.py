import unittest

from assistant_policy import TEMPO_ASSISTANT_POLICY


class AssistantTempoPolicyTests(unittest.TestCase):
    def test_tool_is_the_only_service_call_confirmation_authority(self):
        self.assertIn(
            "Do not ask for confirmation before calling tempo_call_service",
            TEMPO_ASSISTANT_POLICY,
        )
        self.assertIn("including zero-cost reads", TEMPO_ASSISTANT_POLICY)
        self.assertIn("confirmation_required", TEMPO_ASSISTANT_POLICY)

    def test_generic_images_prefer_fal_charge_endpoint(self):
        self.assertIn("service ID `fal`", TEMPO_ASSISTANT_POLICY)
        self.assertIn("/fal-ai/flux/schnell", TEMPO_ASSISTANT_POLICY)
        self.assertIn("$0.003", TEMPO_ASSISTANT_POLICY)


if __name__ == "__main__":
    unittest.main()
