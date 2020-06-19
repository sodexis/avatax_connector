from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestConfig(TransactionCase):

    def setUp(self, *args, **kwargs):
        super().setUp(*args, **kwargs)
        self.avatax_config = self.env.user.company_id.get_avatax_config_company()

    def test_ping_success(self):
        "Ping Avatax Success"
        active_id = self.avatax_config.id
        Wizard = self.env["avalara.salestax.ping"]
        res = Wizard.with_context(active_id=active_id).ping()
        self.assertTrue(res)

    def test_ping_fail(self):
        "Ping Avatax Fail"
        active_id = self.avatax_config.id
        Wizard = self.env["avalara.salestax.ping"]
        self.avatax_config.account_number = "X"
        with self.assertRaises(UserError):
            Wizard.with_context(active_id=active_id).ping()
