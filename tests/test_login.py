import unittest
from unittest.mock import Mock, patch

from selenium.webdriver.common.keys import Keys

from estrategia_downloader import app


class LoginTest(unittest.TestCase):
    def _run_login(self, *, auto_submit):
        driver = Mock()
        email = Mock()
        password = Mock()
        button = Mock()
        button.is_displayed.return_value = True
        button.is_enabled.return_value = True
        driver.find_elements.return_value = [button]
        wait = Mock()
        wait.until.side_effect = [email, password]

        with (
            patch.object(app, "WebDriverWait", return_value=wait),
            patch.object(app, "_painel_carregado", return_value=False),
            patch.object(app, "_executar_selenium", side_effect=[True, True]),
        ):
            app.do_login(
                driver,
                "pessoa@example.test",
                "segredo",
                submeter_automaticamente=auto_submit,
            )
        return driver, email, password, button

    def test_explicit_auto_submit_clicks_the_visible_submit_button(self):
        _driver, email, password, button = self._run_login(auto_submit=True)
        email.send_keys.assert_called_once_with("pessoa@example.test")
        password.send_keys.assert_called_once_with("segredo")
        button.click.assert_called_once_with()

    def test_default_flow_does_not_submit_the_form(self):
        driver, _email, password, button = self._run_login(auto_submit=False)
        driver.find_elements.assert_not_called()
        button.click.assert_not_called()
        self.assertNotIn(
            Keys.ENTER,
            [call.args[0] for call in password.send_keys.call_args_list],
        )


if __name__ == "__main__":
    unittest.main()
