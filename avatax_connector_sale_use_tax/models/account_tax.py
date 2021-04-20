from odoo import api, fields, models, _


class AccountTax(models.Model):
    _inherit = "account.tax"

    is_expensed_tax = fields.Boolean(
        help="Tax is an expense supported by the company. "
        "Invoices don't include it in customer totals."
    )
    expense_account_id = fields.Many2one(
        "account.account",
        domain=[("deprecated", "=", False)],
        string="Tax Expense Tax",
        ondelete="restrict",
        help="Account for supported tax expense."
        " The Tax Account definition should be used for the owed tax.",
    )

    def _avatax_amount_compute_all(self):
        avatax_amount = super()._avatax_amount_compute_all()
        avatax_line = self.env.context.get("avatax_line")
        if avatax_line and avatax_line.tax_amt_expense:
            avatax_amount = avatax_line.tax_amt_expense
        return avatax_amount

    def compute_all(
        self, price_unit, currency=None, quantity=1.0, product=None, partner=None
    ):
        """
        Remove supported tax amounts from the "Total Included",
        so that the tax amount is not added to the invoice amounts.
        """
        taxes = super().compute_all(price_unit, currency, quantity, product, partner)
        avatax_line = self.env.context.get("avatax_line")
        taxes["total_expense"] = 0
        for tax_line in taxes["taxes"]:
            tax = self.browse(tax_line["id"])
            if tax.is_expensed_tax and tax.amount:
                amount = (
                    avatax_line and avatax_line.tax_amt_expense or tax_line["amount"]
                )
                taxes["total_included"] = taxes["total_excluded"]
                taxes["total_expense"] = amount
                tax_line["amount_expense"] = amount
                tax_line["amount"] = 0
        return taxes

    @api.model
    def _get_avalara_tax_domain(self, tax_rate, doc_type):
        res = super()._get_avalara_tax_domain(tax_rate, doc_type)
        is_expensed_tax = doc_type and "Purchase" in doc_type
        res.append(("is_expensed_tax", "=", is_expensed_tax))
        return res

    @api.model
    def _get_avalara_tax_name(self, tax_rate, doc_type):
        name = super()._get_avalara_tax_name(tax_rate, doc_type)
        if doc_type and "Purchase" in doc_type:
            name = _("AVT-Use {}%").format(str(tax_rate))
        return name
