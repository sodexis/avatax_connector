from odoo import api, models, _


class AccountTax(models.Model):
    _inherit = "account.tax"

    @api.model
    def _get_avalara_tax_domain(self, tax_rate, doc_type):
        res = super()._get_avalara_tax_domain(tax_rate, doc_type)
        is_expensed_tax = doc_type and 'Purchase' in doc_type
        res.append(("is_expensed_tax", "=", is_expensed_tax))
        return res

    @api.model
    def _get_avalara_tax_name(self, tax_rate, doc_type):
        name = super()._get_avalara_tax_name(tax_rate, doc_type)
        if doc_type and 'Purchase' in doc_type:
            name = _("AVT-Use {}%").format(str(tax_rate))
        return name
