from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TelegramWatchdogTests(unittest.TestCase):
    def test_recovers_vaprizziobot_after_two_failed_checks(self) -> None:
        source = (ROOT / "deploy" / "openclaw-telegram-watchdog.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("Telegram vaprizziobot (vaprizziobot):", source)
        self.assertIn("running, connected", source)
        self.assertIn("failures < 2", source)
        self.assertIn("systemctl restart openclaw-gateway.service", source)

    def test_systemd_units_reference_the_stock_watchdog(self) -> None:
        service = (ROOT / "deploy" / "openclaw-telegram-watchdog.service").read_text(
            encoding="utf-8"
        )
        timer = (ROOT / "deploy" / "openclaw-telegram-watchdog.timer").read_text(
            encoding="utf-8"
        )
        self.assertIn("ExecStart=/usr/local/sbin/openclaw-telegram-watchdog", service)
        self.assertIn("Unit=openclaw-telegram-watchdog.service", timer)
        self.assertIn("OnUnitActiveSec=1min", timer)


if __name__ == "__main__":
    unittest.main()
