"""
MLPA's budgets and service key, as Otari's config.yml declares them.

Under Otari, MLPA never writes budgets or keys: otari_config.yaml declares
them and Otari applies them at every start, and MLPA's startup check has to
find all of it in the running Otari.
"""

from mlpa.core.services.otari_service import OtariService


class TestOtariConfig:
    async def test_startup_check_finds_the_key_and_every_budget(self, mocker):
        """
        create_budget runs at startup in place of LiteLLM's budget upsert.
        Otari must accept MLPA's service key, which also manages the end
        users the admin routes touch, and the key must be able to assign
        every service type's budget. It only logs, so a broken contract here
        would otherwise start MLPA cleanly.
        """
        logger = mocker.patch("mlpa.core.services.otari_service.logger")
        service = OtariService()
        await service.connect()
        try:
            await service.create_budget()
        finally:
            await service.disconnect()

        logger.error.assert_not_called()
